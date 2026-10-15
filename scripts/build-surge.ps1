param(
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$BuildRoot = (Join-Path $env:USERPROFILE 'NZRdsp-build'),
    [string]$RuntimeRoot = (Join-Path $env:USERPROFILE 'NZRdsp-runtime'),
    [ValidateRange(1, 32)][int]$Jobs = 4
)
$ErrorActionPreference = 'Stop'
$pin = 'd8b4a7bf97b39d0558830cd4799642247349bea2'
$repository = 'https://github.com/surge-synthesizer/surge.git'
$projectRoot = Split-Path $PSScriptRoot -Parent
$source = Join-Path $BuildRoot 'vendor\surge'
$build = Join-Path $BuildRoot 'vendor\surge-build'

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable exited with $LASTEXITCODE" }
}

$pythonVersion = & $Python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne '3.12') { throw 'This build is verified with Python 3.12 x64' }
New-Item -ItemType Directory -Force (Split-Path $source -Parent), $RuntimeRoot | Out-Null
if (-not (Test-Path -LiteralPath $source)) {
    Invoke-Checked git @('clone', '--filter=blob:none', '--no-checkout', $repository, $source)
} else {
    $remote = & git -C $source remote get-url origin
    if ($LASTEXITCODE -ne 0 -or $remote -ne $repository) { throw 'Unexpected source repository' }
    $dirty = & git -C $source status --porcelain
    if ($LASTEXITCODE -ne 0 -or $dirty) { throw 'Preserve local source edits before rebuilding' }
}
Invoke-Checked git @('-C', $source, 'checkout', '--detach', $pin)
Invoke-Checked git @('-C', $source, 'submodule', 'update', '--init', '--recursive', '--depth', '1')
Invoke-Checked cmake @('-S', $source, '-B', $build, '-G', 'Visual Studio 17 2022', '-A', 'x64',
    '-DSURGE_BUILD_PYTHON_BINDINGS=ON', '-DSURGE_SKIP_JUCE_FOR_RACK=ON', '-DSURGE_BUILD_XT=OFF',
    '-DSURGE_BUILD_FX=OFF', '-DSURGE_BUILD_CLAP=OFF', '-DSURGE_BUILD_TESTRUNNER=OFF', "-DPYTHON_EXECUTABLE=$Python")
Invoke-Checked cmake @('--build', $build, '--config', 'Release', '--target', 'surgepy', '--parallel', "$Jobs")

$venv = Join-Path $RuntimeRoot '.venv'
if (-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts\python.exe'))) {
    Invoke-Checked $Python @('-m', 'venv', $venv)
}
$runtimePython = Join-Path $venv 'Scripts\python.exe'
Invoke-Checked $runtimePython @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', (Join-Path $projectRoot 'runtime\requirements-surge.txt'))
$module = @(Get-ChildItem -LiteralPath (Join-Path $build 'src\surge-python\Release') -Filter 'surgepy*.pyd')
if ($module.Count -ne 1) { throw 'Expected one native Surge Python module' }
$moduleDir = Join-Path $RuntimeRoot 'surge'
New-Item -ItemType Directory -Force $moduleDir | Out-Null
Copy-Item -LiteralPath $module[0].FullName -Destination $moduleDir
$binary = Join-Path $moduleDir $module[0].Name
$hash = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
$submodules = @(& git -C $source submodule status --recursive)
$provenance = [ordered]@{repository=$repository;source_commit=$pin;submodules=$submodules;
    module_sha256=$hash;python=$runtimePython;cmake=((& cmake --version) | Select-Object -First 1)}
$provenance | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $moduleDir 'build.json') -Encoding utf8
Copy-Item -LiteralPath (Join-Path $source 'LICENSE') -Destination (Join-Path $moduleDir 'SURGE-LICENSE.txt')

$configDir = Join-Path $env:LOCALAPPDATA 'NZRdsp'
New-Item -ItemType Directory -Force $configDir | Out-Null
$configPath = Join-Path $configDir 'runtime.json'
$config = if (Test-Path -LiteralPath $configPath) { Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json -AsHashtable } else { @{} }
$config['python'] = $runtimePython
if (-not $config.ContainsKey('engines')) { $config['engines'] = @{} }
$config['engines']['surge'] = @{module_dir=$moduleDir;source_commit=$pin;module_sha256=$hash;data_dir=(Join-Path $source 'resources\data')}
$temporary = Join-Path $configDir ('.runtime-' + [guid]::NewGuid().ToString('N') + '.json')
$config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding utf8
[IO.File]::Move($temporary, $configPath, $true)
Write-Output "NZRdsp Surge runtime configured: $configPath"
