#requires -Version 7.0
param(
    [string]$BuildRoot = (Join-Path $env:USERPROFILE 'NZRdsp-build'),
    [string]$RuntimeRoot = (Join-Path $env:USERPROFILE 'NZRdsp-runtime'),
    [ValidateRange(1, 32)][int]$Jobs = 4,
    [string]$Cmake = (Join-Path $env:ProgramFiles 'CMake\bin\cmake.exe')
)
$ErrorActionPreference = 'Stop'
$pin = '636ca0ef517a4db087a6a08a6a8a5e704e21f836'
$repository = 'https://github.com/mtytel/vital.git'
$projectRoot = Split-Path $PSScriptRoot -Parent
$source = Join-Path ([IO.Path]::GetFullPath($BuildRoot)) 'vendor\vital'
$build = Join-Path ([IO.Path]::GetFullPath($BuildRoot)) 'vendor\vital-build'
$runtime = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) 'spectral'

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable exited with $LASTEXITCODE" }
}

New-Item -ItemType Directory -Force (Split-Path $source -Parent), $runtime | Out-Null
if (-not (Test-Path -LiteralPath $source)) {
    Invoke-Checked git @('clone', '--filter=blob:none', '--no-checkout', $repository, $source)
} else {
    $remote = & git -C $source remote get-url origin
    if ($LASTEXITCODE -ne 0 -or $remote -ne $repository) { throw 'Unexpected source repository' }
    $dirty = & git -C $source status --porcelain
    if ($LASTEXITCODE -ne 0 -or $dirty) { throw 'Preserve local source changes before building' }
}
Invoke-Checked git @('-C', $source, 'checkout', '--detach', $pin)
Invoke-Checked $Cmake @('-S', (Join-Path $projectRoot 'native\vital'), '-B', $build,
    '-G', 'Visual Studio 17 2022', '-A', 'x64', "-DVITAL_SOURCE_ROOT=$($source.Replace('\','/'))")
Invoke-Checked $Cmake @('--build', $build, '--config', 'Release', '--target', 'nzrdsp-spectral-native', '--parallel', "$Jobs")
$binary = Join-Path $runtime 'nzrdsp-spectral-native.exe'
Copy-Item -LiteralPath (Join-Path $build 'Release\nzrdsp-spectral-native.exe') -Destination $binary
Copy-Item -LiteralPath (Join-Path $source 'LICENSE') -Destination (Join-Path $runtime 'ENGINE-LICENSE.txt')
Copy-Item -LiteralPath (Join-Path $source 'README.md') -Destination (Join-Path $runtime 'UPSTREAM-README.md')
foreach ($file in @('main.cpp', 'output.h', 'CMakeLists.txt')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "native\vital\$file") -Destination $runtime
}
$hash = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
$provenance = [ordered]@{
    repository=$repository; source_commit=$pin; engine_version='1.0.6'; executable_sha256=$hash;
    source_directory=$source; build_directory=$build;
    adapter_sha256=(Get-FileHash -LiteralPath (Join-Path $runtime 'main.cpp') -Algorithm SHA256).Hash.ToLowerInvariant();
    writer_sha256=(Get-FileHash -LiteralPath (Join-Path $runtime 'output.h') -Algorithm SHA256).Hash.ToLowerInvariant();
    cmake_sha256=(Get-FileHash -LiteralPath (Join-Path $runtime 'CMakeLists.txt') -Algorithm SHA256).Hash.ToLowerInvariant();
    startup_overlay_sha256=(Get-FileHash -LiteralPath (Join-Path $build 'synth_base-offline.cpp') -Algorithm SHA256).Hash.ToLowerInvariant();
    header_overlay_sha256=(Get-FileHash -LiteralPath (Join-Path $build 'JuceHeader.h') -Algorithm SHA256).Hash.ToLowerInvariant();
    cmake=((& $Cmake --version) | Select-Object -First 1);
    gui=$false; personal_startup_config=$false; sample_rates=@(8000,192000); maximum_block_size=64
}
$provenance | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $runtime 'build.json') -Encoding utf8
$configDir = Join-Path $env:LOCALAPPDATA 'NZRdsp'
New-Item -ItemType Directory -Force $configDir | Out-Null
$configPath = Join-Path $configDir 'runtime.json'
$config = if (Test-Path -LiteralPath $configPath) { Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json -AsHashtable } else { @{} }
if (-not $config.ContainsKey('engines')) { $config['engines'] = @{} }
$config['engines']['vital'] = @{executable=$binary;source_commit=$pin;executable_sha256=$hash;engine_version='1.0.6'}
$temporary = Join-Path $configDir ('.runtime-' + [guid]::NewGuid().ToString('N') + '.json')
$config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding utf8
[IO.File]::Move($temporary, $configPath, $true)
Write-Output "Built NZRdsp spectral renderer: $binary"
Write-Output 'This is the internal native backend; see docs/VITAL_BACKEND.md for its request format and remaining CLI work.'
