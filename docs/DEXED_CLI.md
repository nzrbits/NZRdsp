# Dexed SysEx tools

`nzrdsp dexed` currently inspects and edits DX7 SysEx. Native Dexed note rendering, WAV output and audio reference comparison are still pending under #6. No plugin, Python runtime or audio device is required for these commands.

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
