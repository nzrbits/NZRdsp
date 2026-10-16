# Dexed SysEx tools

`nzrdsp dexed` currently inspects and edits DX7 SysEx. A separate native Dexed backend now renders notes to WAV. Its connection to this Rust command and an independent audio reference comparison are still pending under #6. No plugin, Python runtime or audio device is required for these commands.

## Commands

Build with `cargo build --manifest-path cli/Cargo.toml --locked`, then use the resulting `nzrdsp` executable:

```powershell
nzrdsp dexed --list-params
nzrdsp dexed --sysex bank.syx --list-voices
nzrdsp dexed --sysex bank.syx --voice 31
nzrdsp dexed --sysex bank.syx --voice 31 --param algorithm=31 --param op1.output_level=99 --name NEURO --save-sysex edited.syx
```

Without an export path, edits are a dry run printed as JSON. Inspection includes all 155 unpacked bytes, named values/ranges, name, selected zero-based voice, source format and one-based MIDI channel. `--save-sysex` writes a new single-voice message, retaining the source channel. Its parent directory must exist. Existing files are preserved; the input is never rewritten.

Supported inputs are exactly one complete Yamaha DX7 single-voice message (163 bytes) or a packed 32-voice bank (4104 bytes). Single-voice files have only voice index 0. Header, channel, declared byte count, actual length, 7-bit data, checksum and every voice's parameter ranges are checked. Truncation, trailing/concatenated messages, raw cartridge data, MIDI files and out-of-range programs are rejected. The bounded reader reads at most 4105 bytes. Reserved packed bits are ignored according to the source mapping; invalid semantic values are rejected rather than repaired with Dexed's corrupt-data heuristics.

## Values

The layout has 145 synthesis values and ten name bytes. These are not 155 independent synth knobs. All raw bytes are addressable by the keys from `--list-params`:

- `op1` through `op6`: EG rates/levels, keyboard scaling, sensitivity, level, frequency mode/coarse/fine and detune. Packed storage starts with operator 6; keys use the musical operator number.
- `pitch_eg.rate1` through `rate4` and `pitch_eg.level1` through `level4`.
- `algorithm`, `feedback`, `osc_key_sync`, `lfo.*` and `transpose`.
- `name.byte1` through `name.byte10` preserve raw seven-bit names. Nonprintable bytes appear as replacement characters in the display name; the byte array remains authoritative.

Values are stored raw integers. Algorithm is 0 through 31; JSON also shows the musician-facing 1 through 32 value. Detune is 0 through 14 with neutral 7; transpose is 0 through 48 with neutral 24. Frequency mode is 0 for ratio and 1 for fixed. `--name` accepts up to ten printable ASCII bytes and pads with spaces. Duplicate assignments and conflicting name-byte edits fail before publication.

## Source and verification

Mapping and bounds were checked against Dexed commit `fea2f0f0f2c397799f59b27eb11c038cd34b22c5`: [Cartridge.cpp](https://github.com/asb2m10/dexed/blob/fea2f0f0f2c397799f59b27eb11c038cd34b22c5/source/core/Cartridge.cpp), [Program.h](https://github.com/asb2m10/dexed/blob/fea2f0f0f2c397799f59b27eb11c038cd34b22c5/source/core/Program.h) and [parameter definitions](https://github.com/asb2m10/dexed/blob/fea2f0f0f2c397799f59b27eb11c038cd34b22c5/source/parameter/DexedApvts.cpp).

Four Rust tests check schema offsets/uniqueness, maxima, all channels/voice indices, packed bitfields, truncation/header/checksum/range errors, transactional edits and existing-output preservation. Three optional CLI tests check bank/voice editing, exported reload, invalid files/edits and an independent compiled upstream unpacker.

For the upstream comparison, obtain Cartridge.cpp from that pinned commit outside the checkout. SHA256 must be `b703cd36e052953556878e06332e1d835fb23660771f3ba08ee08ea7b85430a7`. Generate a harness without modifying the two original unpacking functions:

```powershell
python -X utf8 scripts/prepare-dexed-oracle.py --source D:\build\Cartridge.cpp --output D:\build\oracle.cpp
```

Compile the generated file from a Visual Studio x64 developer shell with `cl /nologo /EHsc /std:c++17 /O2 D:\build\oracle.cpp /Fe:D:\build\oracle.exe /Fo:D:\build\oracle.obj`. Keep the generated GPL-derived harness and its binaries with the external source/build artifacts. Then run:

```powershell
$env:NZRDSP_NATIVE_CLI = 'D:\build\nzrdsp.exe'
$env:NZRDSP_DEXED_ORACLE = 'D:\build\oracle.exe'
python -X utf8 -m unittest python.tests.test_dexed_sysex -v
```

The recorded local run compared 96 deterministic synthetic voices across three generated banks, all 14880 unpacked bytes equal to the compiled upstream function. All three CLI checks pass; seven Rust tests pass with the unrelated opt-in worker timeout test skipped. This verifies the SysEx mapping for those valid inputs. Hardware/GUI/audio parity and the native renderer are not verified by this test.

## Native backend

Windows x64 has a separate internal renderer built around the pinned Dexed audio processor, including Modern, Mark I and OPL engines. The upstream JUCE/UI dependencies are compiled and linked, but no editor, audio output device or MIDI device is opened. A generated build copy of PluginProcessor.cpp disables constructor calls that load preferences/startup cartridges, resolve personal application folders and register external MTS tuning. It initializes the native Init program instead. The FM/voice/effect processing code is retained. The external source checkout is left unchanged.

Build with Visual Studio 2022 C++ tools, Windows SDK, Git, CMake and PowerShell 7:

```powershell
.\scripts\build-dexed.ps1 -BuildRoot D:\NZRdsp-build -RuntimeRoot D:\NZRdsp-runtime
```

The script clones the pinned source and exact submodules outside this repo, builds the internal console target, installs the executable/license/build record and adds `engines.dexed` to the existing NZRdsp runtime configuration. Other engine entries are retained. CMake rejects a wrong revision or tracked source/submodule edits. The build record includes executable/adapter/constructor-overlay hashes, source pin, submodule pins and build paths. Source is available from the linked upstream repository and this adapter directory; no binary or factory bank is committed here. Dexed and this native adapter use GPL-3.0-or-later; MSFA retains its source notices.

The internal executable currently takes positional arguments:

```text
nzrdsp-dexed-native.exe voice.syx output.wav note velocity duration_frames tail_frames rate block engine seed
```

Input must be a valid single-voice SysEx file, produced by `--save-sysex` if starting with a bank. Engine 0 selects Modern, 1 Mark I, 2 OPL. It streams stereo Float32 WAV in bounded blocks, without normalization. The requested frame count is exact, including partial blocks. Output is opened exclusively: existing files are preserved. Errors after file creation remove that process's partial output. It prints native log lines followed by one JSON result containing source pin, timing, engine, note/velocity, block size, seed, frame count, peak and RMS. This internal executable does not yet produce the final render sidecar or have a subprocess deadline; those belong to the pending Rust command adapter. Forced process termination can leave a partial output, so development runs should use an isolated temporary directory.

Accepted sample rates are 22050 through 192000 Hz. The source's internal FM processing quantum is 64 samples; note timing retains that processor's behavior. Tests exposed broken attack/release or silence at 8 kHz in the pinned upstream core. Lower rates are rejected before output creation; there is no silent resampling fallback or claim to fix the upstream low-rate DSP.

Three opt-in native tests exercise all three engines at 22.05/44.1/48/96/192 kHz, partial blocks, exact WAV format/frame counts, byte-identical repeated rendering, release tails, stereo duplication, native frequency changes and invalid-input/existing-file preservation. They also compare the reported peak/RMS to decoded samples. Run with the installed executable and Python's NumPy/SoundFile environment:

```powershell
$env:NZRDSP_DEXED_NATIVE = 'D:\NZRdsp-runtime\dexed\nzrdsp-dexed-native.exe'
python -X utf8 -m unittest python.tests.test_dexed_native -v
```

The three native checks pass locally. They establish backend behavior for these synthetic fixtures, not hardware, GUI or other-emulator audio parity. Issue #6 remains open for the Rust render interface, final manifests, subprocess controls and independent reference recordings.
