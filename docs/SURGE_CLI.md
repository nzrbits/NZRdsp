# Surge offline renderer

NZRdsp's Rust CLI runs a pinned native Surge XT engine in a separate Python process. It loads native .fxp patches, renders notes, exposes numeric parameter paths and writes float32 stereo WAV plus a render manifest. Audio is streamed in bounded batches; it is not accumulated as a full song in memory.

## Build

Use Python 3.12 x64, CMake and Visual Studio 2022 C++ Build Tools. From PowerShell 7:

```powershell
.\scripts\build-surge.ps1 -Python C:\path\to\python.exe -BuildRoot D:\NZRdsp-build -RuntimeRoot D:\NZRdsp-runtime
cargo build --release --manifest-path cli/Cargo.toml --locked
```

The script fetches the pinned source and submodules, builds surgepy, installs the locked Python dependencies and writes `%LOCALAPPDATA%/NZRdsp/runtime.json`. Build/runtime directories are separate from source. The native module's commit and SHA256 are checked before use. The script writes configuration through PowerShell because Microsoft Store Python can redirect AppData writes into its private package cache.

Surge source is GPLv3. Building it locally does not require redistributing factory presets. If packaging the engine, include the corresponding GPL source and notices; factory/third-party assets need their own distribution review. This repository contains build instructions and the adapter, not vendored plugin binaries or factory data.

## Use

```powershell
surge-cli --init-preset D:\renders\init.fxp
surge-cli --preset D:\renders\init.fxp --list-params
surge-cli --preset D:\renders\init.fxp --note 36 --velocity 100 --duration 2 --tail 1 --param volume=-6 -o D:\renders\bass.wav
nzrdsp surge --preset D:\renders\init.fxp --note 48 --duration 1 -o D:\renders\lead.wav
```

Use a new output filename. Existing WAV, manifest and preset files are preserved. `--runtime` or `NZRDSP_RUNTIME` can select another runtime config. Native numeric parameters use the raw ranges printed by --list-params, not guessed display units. Integer and toggle controls reject fractional values. Presets retain their native modulation and effect state; the CLI does not provide a full modulation-matrix editor.

The manifest records native version/commit/module hash, preset hash, applied values, rate, block size, note, frame counts, peak, RMS and WAV hash. Note-off rounds upward to a native 32-sample block. The WAV contains that actual note length plus the requested tail. Tempo follows the native default or loaded preset; a BPM override and random-seed control are not exposed. Missing external preset assets and GUI parity require separate checks.

## Verification

The Windows build used Surge d8b4a7bf97b39d0558830cd4799642247349bea2, Python 3.12.10, CMake 4.3.3 and MSVC 19.44. The setup script was rerun successfully against the existing pinned source/build. Two Rust parser tests and four native end-to-end tests pass: preset save/load, finite stereo WAV at 44.1/48 kHz, exact frame counts and manifests, parameter validation, preservation of existing outputs and patch bytes, and module-hash rejection. A deterministic initialized patch matches direct native single-block processing within 0.000001 absolute sample error. No device playback or native dialogs were used. This does not establish GUI sample identity for arbitrary patches.

Run the native checks using the configured runtime's Python:

```powershell
cargo test --manifest-path cli/Cargo.toml --locked
$env:NZRDSP_SURGE_CLI = 'D:\path\to\surge-cli.exe'
$env:NZRDSP_RUNTIME = "$env:LOCALAPPDATA\NZRdsp\runtime.json"
& D:\path\to\runtime\Scripts\python.exe -m unittest discover -s python/tests -v
```

Issue #7 remains open for preset browser metadata and the separate GUI comparison. The CLI commands are not yet exposed as Tauri tabs.

The shared Rust worker manager now captures output concurrently, uses a 30-minute deadline for Surge and owns the staging directory. The four native Surge checks pass after this extraction. A separate opt-in Rust test verifies termination of a sleeping Python worker; no native audio is played.
