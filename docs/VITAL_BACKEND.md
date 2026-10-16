# Spectral engine backend

Issue #2 has a Windows x64 internal renderer around the [pinned Vital source](https://github.com/mtytel/vital/tree/636ca0ef517a4db087a6a08a6a8a5e704e21f836). It uses the native HeadlessSynth, preset loader, wavetable/sample resources, modulation graph and SoundEngine. It does not open an editor, audio device or MIDI device. This is not yet the proposed `vital-cli` / `nzrdsp vital` command, and no GUI reference comparison has been completed.

## Build

Use PowerShell 7, Git, CMake and Visual Studio 2022 x64 C++ tools with a Windows SDK:

```powershell
.\scripts\build-vital.ps1 -BuildRoot D:\NZRdsp-build -RuntimeRoot D:\NZRdsp-runtime
```

The script checks out source commit `636ca0ef517a4db087a6a08a6a8a5e704e21f836` outside this repository. The bundled JUCE modules are part of that source tree. CMake rejects the wrong revision or tracked source edits. A build copy disables the constructor's personal startup-configuration hook. A generated headless header replaces its placeholder version `99999.9.9` with `1.0.6`, matching the pinned source's plugin/standalone headers. The DSP and native preset code are unchanged. SSE2 selection follows the upstream Windows build; no proprietary IPP dependency is required.

The executable is named `nzrdsp-spectral-native.exe`, installed in `RuntimeRoot/spectral`. The build record stores source/executable/adapter/writer/CMake/overlay hashes and build locations. The setup adds `engines.vital` to the existing host runtime JSON without changing Surge or Dexed entries. It copies the engine license, upstream README and adapter sources next to the binary. Source and adapter use GPL-3.0-or-later; bundled dependencies retain their own notices. Factory presets, wavetables, accounts and services are not bundled. The upstream README distinguishes code, trademarks, services and separately licensed factory content.

## Internal request interface

Pass one UTF-8 JSON request file, limited to 1 MiB:

```powershell
& D:\NZRdsp-runtime\spectral\nzrdsp-spectral-native.exe request.json
```

The last stdout line is JSON. Errors go to stderr with a nonzero exit. Requests accept `action`, optional `preset`, `parameters` and `seed`, plus the render fields below. Unknown fields fail. All paths must be absolute; JSON strings escape Windows backslashes in the normal way. Inspection can use the initialized patch without a preset.

| Field | Accepted values / default |
|---|---|
| `action` | `parameters`, `save`, `render`; required |
| `preset` | Optional native `.vital` JSON; at most 64 MiB |
| `parameters` | Object of native control names and finite raw numeric values |
| `seed` | Integer 0 through 1,000,000,000; default 0 |
| `output` | New writable absolute path; required for save/render |
| `sample_rate` | Integer 8000 through 192000; default 48000 |
| `note` | MIDI integer 0 through 127; default 36 |
| `velocity` | Integer 1 through 127; default 100 |
| `duration_frames` | Integer 1 through 600 seconds at the selected rate; default 2 seconds |
| `tail_frames` | Integer 0 through 60 seconds at the selected rate; default 2 seconds |
| `bpm` | Finite 5 through 900; default 120 |

The `parameters` action returns metadata and current raw values for all 772 controls in this build: name, minimum, maximum, default, indexed flag, scale, display name and units. These are native values, not normalized host values or necessarily the displayed physical unit. For example, the raw tempo control is beats per second; render `bpm` overrides that control through the native host-tempo path. Indexed edits must be integers. Unknown names, nonnumeric values and out-of-range values fail before output creation.

`save` writes the native JSON state after loading and overrides, including the engine's embedded resources and modulation state. The native loader performs its version migrations/checks. A future feature-version fixture is rejected; this does not promise current commercial binary/preset compatibility with this older public source.

Example render request:

```json
{
  "action": "render",
  "output": "D:/NZRdsp-renders/spectral.wav",
  "note": 36,
  "velocity": 100,
  "duration_frames": 96000,
  "tail_frames": 96000,
  "sample_rate": 48000,
  "bpm": 176,
  "seed": 17,
  "parameters": {"osc_1_random_phase": 0, "osc_1_transpose": -12}
}
```

## Rendering and limits

Note-on is at frame zero. The renderer splits processing at the note-off boundary, then releases at that boundary; this prevents an on/off pair within the first block from overwriting the pending trigger. Native processing uses at most 64 frames per call, including exact partial calls at note-off and at the output end. The output frame count is duration plus tail. There is no preroll, forced fade, normalization, resampling or latency trimming. Audio is stereo Float32 WAV with a fact chunk. RIFF sizes and samples are checked; audio memory is bounded by a processing block rather than output duration.

The result records source/version/seed, note, velocity, rate, BPM, duration/tail/total frames, format, maximum block size, peak, RMS and samples above unity. Seed selects the native RandomGenerator initialization sequence before constructing the synth. Identical tested requests repeat byte-for-byte in fresh processes on this build; cross-version/compiler/platform equivalence is not established.

The engine is not generally invariant to block partition. In a local initialized-patch diagnostic, processing the first 63 samples together versus as part of a 64-frame call changed the prefix by about 0.00866 at peak. The timing regression therefore compares held and released prefixes with the same processing partition, checks exact frame counts, and verifies release completion. A different GUI/host block size cannot be assumed to null exactly. A reference comparison must record and match this detail rather than silently align, normalize or loosen the comparison.

Output creation is exclusive. Existing WAVs and presets are preserved. Handled write/processing errors remove the output created by that invocation. This internal process has no timeout, staging-directory publication, SHA verification or final sidecar; forced termination may leave a partial file. Those controls belong in the pending Rust wrapper. Run native development requests in disposable output directories, outside user music.

## Verification

```powershell
$env:NZRDSP_VITAL_NATIVE = 'D:\NZRdsp-runtime\spectral\nzrdsp-spectral-native.exe'
python -X utf8 -m unittest python.tests.test_vital_native -v
```

Use the existing runtime Python with NumPy and SoundFile. The four explicit native checks pass on the installed Windows build:

- Native metadata, selected oscillator/filter edits, native save/load, exact direct-versus-restored audio, seeded repeatability and changed sound after edits.
- Nonempty finite stereo Float32 at 8/22.05/44.1/48/96/192 kHz, exact partial frame counts, independent decoded peak/RMS, released tails and octave pitch checks near 440/880 Hz.
- Note-off boundaries at frames 1/63/64/65/127/128/129, matching held-prefix processing and finished release tails.
- Existing output/preset preservation, invalid fields/types/ranges, indexed controls, malformed requests and a future-version preset rejected before output creation.

Both build-tree and installed executables passed. The setup was executed successfully, the source checkout stayed clean, and existing Surge/Dexed runtime entries were checked unchanged. This is backend/adapter evidence, not independent GUI audio parity. Issue #2 remains open for the Rust CLI/alias, verified manifests/deadlines/publication, broader preset/modulation fixtures and the requested GUI reference comparison.
