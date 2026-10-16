#requires -Version 7.0
param(
    [string]$BuildRoot = (Join-Path $env:USERPROFILE 'NZRdsp-build'),
    [string]$RuntimeRoot = (Join-Path $env:USERPROFILE 'NZRdsp-runtime'),
    [ValidateRange(1, 32)][int]$Jobs = 4,
    [string]$Cmake = (Join-Path $env:ProgramFiles 'CMake\bin\cmake.exe')
)
$ErrorActionPreference = 'Stop'
$pin = 'fea2f0f0f2c397799f59b27eb11c038cd34b22c5'
$repository = 'https://github.com/asb2m10/dexed.git'
$projectRoot = Split-Path $PSScriptRoot -Parent
$source = Join-Path ([IO.Path]::GetFullPath($BuildRoot)) 'vendor\dexed'
$build = Join-Path ([IO.Path]::GetFullPath($BuildRoot)) 'vendor\dexed-build'
$runtime = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) 'dexed'

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable exited with $LASTEXITCODE" }
}

New-Item -ItemType Directory -Force (Split-Path $source -Parent), $runtime | Out-Null
if (-not (Test-Path -LiteralPath $source)) {
    Invoke-Checked git @('clone', '--filter=blob:none', '--no-checkout', $repository, $source)
} else {
    $remote = & git -C $source remote get-url origin
    if ($LASTEXITCODE -ne 0 -or $remote -ne $repository) { throw 'Unexpected Dexed source repository' }
    $dirty = & git -C $source status --porcelain
    if ($LASTEXITCODE -ne 0 -or $dirty) { throw 'Preserve local Dexed source changes before building' }
}
Invoke-Checked git @('-C', $source, 'checkout', '--detach', $pin)
Invoke-Checked git @('-C', $source, 'submodule', 'update', '--init', '--recursive', '--depth', '1')
Invoke-Checked $Cmake @('-S', (Join-Path $projectRoot 'native\dexed'), '-B', $build,
    '-G', 'Visual Studio 17 2022', '-A', 'x64', "-DDEXED_SOURCE_ROOT=$($source.Replace('\','/'))")
Invoke-Checked $Cmake @('--build', $build, '--config', 'Release', '--target', 'nzrdsp-dexed-native', '--parallel', "$Jobs")
$built = Join-Path $build 'nzrdsp-dexed-native_artefacts\Release\nzrdsp-dexed-native.exe'
$binary = Join-Path $runtime 'nzrdsp-dexed-native.exe'
Copy-Item -LiteralPath $built -Destination $binary
Copy-Item -LiteralPath (Join-Path $source 'LICENSE') -Destination (Join-Path $runtime 'DEXED-LICENSE.txt')
Copy-Item -LiteralPath (Join-Path $projectRoot 'native\dexed\main.cpp') -Destination (Join-Path $runtime 'renderer-main.cpp')
$hash = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
$provenance = [ordered]@{
    repository=$repository; source_commit=$pin; executable_sha256=$hash;
    submodules=@(& git -C $source submodule status --recursive);
    processor_overlay_sha256=(Get-FileHash -LiteralPath (Join-Path $build 'PluginProcessor-offline.cpp') -Algorithm SHA256).Hash.ToLowerInvariant();
    adapter_sha256=(Get-FileHash -LiteralPath (Join-Path $projectRoot 'native\dexed\main.cpp') -Algorithm SHA256).Hash.ToLowerInvariant();
    cmake=((& $Cmake --version) | Select-Object -First 1);
    source_directory=$source; build_directory=$build;
    windows_initialization_disabled=@('preferences', 'startup_cartridges', 'app_directory', 'MTS_registration');
    minimum_sample_rate=22050; maximum_sample_rate=192000
}
$provenance | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $runtime 'build.json') -Encoding utf8
$configDir = Join-Path $env:LOCALAPPDATA 'NZRdsp'
New-Item -ItemType Directory -Force $configDir | Out-Null
$configPath = Join-Path $configDir 'runtime.json'
$config = if (Test-Path -LiteralPath $configPath) { Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json -AsHashtable } else { @{} }
if (-not $config.ContainsKey('engines')) { $config['engines'] = @{} }
$config['engines']['dexed'] = @{executable=$binary;source_commit=$pin;executable_sha256=$hash}
$temporary = Join-Path $configDir ('.runtime-' + [guid]::NewGuid().ToString('N') + '.json')
$config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding utf8
[IO.File]::Move($temporary, $configPath, $true)
Write-Output "Built Dexed native renderer: $binary"
Write-Output 'The Rust SysEx command does not invoke this backend yet.'
