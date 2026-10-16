# Spectral engine CLI

`nzrdsp vital` and `vital-cli` inspect, edit and render through the pinned public Vital engine. The Windows native build uses source commit `636ca0ef517a4db087a6a08a6a8a5e704e21f836`, with version 1.0.6 taken from that source's plugin/standalone headers. It is older than current commercial releases. No editor, audio device or MIDI device opens. [Backend build and behavior](VITAL_BACKEND.md) describe source isolation, initialization and timing.

## Build and runtime

```powershell
.\scripts\build-vital.ps1 -BuildRoot D:\NZRdsp-build -RuntimeRoot D:\NZRdsp-runtime
cargo build --manifest-path cli/Cargo.toml --locked
```

Setup writes `engines.vital` in the host runtime config, retaining other engines. Commands read `--runtime FILE`, then `NZRDSP_RUNTIME`, then `LocalAppData/NZRdsp/runtime.json`. The entry needs the executable path, SHA256, source commit and engine version. Each operation verifies source/version and executable SHA256 before launching the backend and checks the executable again afterward.

The executable distribution is named NZRdsp spectral renderer. Upstream engine/dependency licenses and factory-content/service restrictions are described in the backend guide and copied build notices. No factory presets or commercial binaries are committed here. Tauri tabs do not yet call this CLI.

## Commands

```powershell
nzrdsp vital --list-params
nzrdsp vital --preset D:\patches\bass.vital --list-params
vital-cli --preset D:\patches\bass.vital --save-preset D:\patches\bass-edited.vital --param osc_1_transpose=-12
vital-cli --preset D:\patches\bass.vital --note 36 --duration 2 --tail 1 --bpm 176 --seed 17 -o D:\renders\bass.wav
```

Choose exactly one operation: `--list-params`, `--save-preset FILE`, or `--output FILE`/`-o FILE`. Omit `--preset` for native initialization. Preset paths may be relative; captured input is rebased to an absolute temporary path with its original filename. Inputs are limited to 64 MiB. The command renders from the captured bytes, preserving their SHA256 in the result; later source-file changes do not change that invocation.

`--list-params` returns all 772 native controls with current values, bounds, defaults, indexed flags, scales, display names and units. Repeated `--param name=value` edits use raw values, not normalized host values. Unknown names, duplicate assignments, nonfinite/overflow values, range violations and fractional indexed edits fail. Indexed checks happen before Float32 rounding, so a fractional number cannot become an accepted integer merely by rounding. The native engine also validates its controls.

For example, `osc_1_transpose=-12` is an indexed semitone offset, while raw `volume` has the engine's nonlinear scale. Use metadata rather than treating every display unit as the raw storage unit. Render `--bpm` sets the native host clock after static edits; it overrides the raw `beats_per_minute` preset control for rendering. That raw control stores beats per second.

| Option | Default | Accepted value |
|---|---|---|
| `--note` | 36 | Integer MIDI 0–127 |
| `--velocity` | 100 | Integer 1–127 |
| `--duration` | 2 | Finite positive seconds, at most 600; must round to a nonzero frame count |
| `--tail` | 2 | Finite seconds, 0–60 |
| `--rate` | 48000 | Integer 8000–192000 Hz |
| `--bpm` | 120 | Finite 5–900; passed as native Float32 |
| `--seed` | 0 | Integer 0–1,000,000,000; applies to every operation |
| `--timeout` | 120 | Integer 1–3600 seconds shared across the native subprocesses |

Note, velocity, duration, tail, rate and BPM options apply to WAV rendering. They conflict with inspection/preset saving. No arbitrary block-size option is exposed: native calls use at most 64 frames and split at note-off/output end. Duration and tail round separately to nearest frames. The WAV has exactly their sum, with no automatic fade, preroll, normalization, resampling or latency trimming. The engine is sensitive to block partition; its backend guide records the observed limit. Seeded repeatability is tested on this build, not guaranteed across compilers/platforms or current product binaries.

`--save-preset` uses native state serialization, including wavetables, LFO shapes, modulation routing and sample resources. For a loaded 1.0.6 preset whose sample metadata is unchanged, the adapter retains the original embedded PCM payload rather than quantizing it again. This is possible because numeric CLI controls do not edit sample data. Newly generated floating-point sample data and migrated older states still use the native PCM serialization; export is not universally lossless for every source state. Source presets remain unchanged.

## Publication and manifests

WAV and preset exports each create an adjacent JSON sidecar, such as `bass.wav.json` or `bass-edited.vital.json`. Both destination paths must be new. The native backend works in an owned temporary directory. A shared subprocess deadline kills/reaps an overlong worker; handled failures and timeout discard staging without publishing either final file. Hashing, input capture and file-validation I/O are not preempted by the subprocess deadline.

Rust checks the returned source/version/action/seed and render timing/format fields. WAV verification streams the exact Float32 stereo header and sample count, rejects nonfinite samples, computes SHA256/peak/RMS/above-unity count, and compares those measurements with the backend. Preset verification decodes the saved JSON, checks its version/settings and all expected raw control values. Shared WAV verification and publication helpers are also used by Dexed.

Publication uses no-overwrite renames. The manifest is published first; a handled failure publishing its WAV/preset removes that newly published manifest. Existing output and manifest files are preserved. Two separate files are not a crash-atomic transaction: abrupt termination during final publication can leave an orphan manifest.

The stdout result and sidecar agree. They record input/output/executable identities and hashes, source/version, action, requested parameter overrides, all preset control values after edits, seed, output paths, initialization/timing policy, native timing/metrics, and independently decoded WAV measurements. `preset_parameter_values` describes static preset edits before the render host-tempo override; final render BPM appears in `native.bpm`. Input preset bytes/resources are referenced by path/hash rather than embedded into the sidecar. Preserve that preset for reproduction. A preset name can change with its filename through the native loader, without changing the tested audio.

## Verification

```powershell
cargo test --manifest-path cli/Cargo.toml --locked
$env:NZRDSP_NATIVE_CLI = 'D:\NZRdsp-build\debug\nzrdsp.exe'
$env:NZRDSP_RUNTIME = 'D:\test-runtime.json'
python -X utf8 -m unittest python.tests.test_vital_cli -v
```

Use Python with NumPy and SoundFile. With Microsoft Store Python, copy the actual host runtime JSON to a temporary path outside AppData using PowerShell before setting `NZRDSP_RUNTIME`; its private AppData view may contain a stale config. Rust reads the host config directly.

Three new Rust checks cover command/alias/conflicts, assignment parsing and indexed checks before rounding. Six explicit CLI tests cover direct-backend WAV equality at 8/44.1/48/192 kHz, alias equality, Unicode paths, preset save/reload and source preservation, exact manifest/hash/metrics, invalid requests/runtime identities, existing-file protection, failed native processes and an actual long render terminated at a one-second deadline with staging cleanup. A generated preset fixture includes a sine wavetable, PCM sample, asymmetric LFO and active modulation routing; resource state and rendered audio survive export, and disabling its modulation changes the audio.

The combined local run passes 31 integration checks across metadata, Surge, installed Saturn/Pro-Q references, Dexed and this engine. All 13 ordinary Rust CLI tests pass; two existing opt-in worker timeout tests remain ignored in the default run. After adding the post-inspection executable hash guard, all six spectral CLI checks and the 13 ordinary Rust tests pass again.

These checks establish adapter behavior. They do not establish parity with a GUI/reference product. Issue #2 remains open for the requested independent GUI comparison, with matched source version, resources, initialization and block partition.
