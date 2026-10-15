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

## Preset metadata

```powershell
surge-cli --preset D:\renders\init.fxp --metadata
surge-cli --list-presets D:\Surge\patches_factory --query bass
```

Metadata reports stored name, category, author, comment, license, tags, revision, file path and SHA256. A catalog searches these descriptive fields and relative paths without loading the synth. It recursively scans FXP files, skips directory symlinks/junctions and reports per-file or directory errors without hiding valid matches. Query matching is case-insensitive and supports Unicode. Audio generation still uses the native loader.

The reader checks the native FXP identifiers, declared chunk/embedded sizes and bounded XML metadata. Current sub3 and legacy XML payloads follow the pinned [native patch I/O](https://github.com/surge-synthesizer/surge/blob/d8b4a7bf97b39d0558830cd4799642247349bea2/src/common/SurgeSynthesizerIO.cpp) and [patch storage](https://github.com/surge-synthesizer/surge/blob/d8b4a7bf97b39d0558830cd4799642247349bea2/src/common/SurgePatch.cpp). It reads the metadata element, not the entire DSP XML as a strict schema: a supplied factory patch contains a numeric character reference elsewhere that a strict XML parser rejects. Catalog success is not proof that every external asset or DSP-state field will load. Render manifests retain the preset metadata as well as its hash.

The configured factory data directory is set before importing the native extension. This ordering is required in the tested Windows build; a regression checks the native reported factory path. With the supplied resources the initialized patch is Init Saw, rather than the resource-free fallback Init.

## Verification

The Windows build used Surge d8b4a7bf97b39d0558830cd4799642247349bea2, Python 3.12.10, CMake 4.3.3 and MSVC 19.44. The setup script was rerun successfully against the existing pinned source/build. Verification includes Rust argument and exact numeric-JSON checks; five native checks cover save/load/render, parameters/errors, direct native comparison, module hashes and metadata/catalog search. Two metadata-parser checks cover legacy/current payloads, Unicode/tags and malformed headers/sizes/XML declarations. The configured factory library scan reads all 641 patches without errors and finds 62 bass-query matches. A deterministic initialized patch matches direct native single-block processing within 0.000001 absolute sample error. No device playback or native dialogs were used. This does not establish GUI sample identity for arbitrary patches.

Run the native checks using the configured runtime's Python:

```powershell
cargo test --manifest-path cli/Cargo.toml --locked
$env:NZRDSP_SURGE_CLI = 'D:\path\to\surge-cli.exe'
New-Item -ItemType Directory -Force D:\NZRdsp-tests | Out-Null
Copy-Item -LiteralPath "$env:LOCALAPPDATA\NZRdsp\runtime.json" -Destination D:\NZRdsp-tests\runtime.json
$env:NZRDSP_RUNTIME = 'D:\NZRdsp-tests\runtime.json'
& D:\path\to\runtime\Scripts\python.exe -m unittest discover -s python/tests -v
```

For Microsoft Store Python, keep this test snapshot outside AppData: Python can read an older package-private overlay there while the Rust CLI reads the host file. Copying through PowerShell and passing the same snapshot to both keeps the fixture authoritative without changing the installed runtime config.

Issue #7's preset metadata/catalog work is implemented. The issue remains open for the separate GUI reference comparison. The CLI commands are not yet exposed as Tauri tabs.

The shared Rust worker manager captures output concurrently, uses a 30-minute deadline for Surge and owns the staging directory. Five native Surge checks pass with it; the combined eleven-test metadata/Surge/VST run also passes. A separate opt-in Rust test verifies termination of a sleeping Python worker; no native audio is played.

Rust JSON parsing enables exact float round trips. This fixes a measured one-ULP difference between a native metric in the sidecar and the CLI's JSON output; tests retain exact numeric equality rather than hiding it with a tolerance.
