# Native VST3 reference renderer

`nzrdsp vst` runs an installed VST3 effect in a separate process. It reads audio in bounded chunks, processes the native plugin and writes Float32 WAV plus a JSON manifest. This is a reference host, not an independent reimplementation of the plugin's algorithms. It does not open an editor or an audio device.

## Setup

Build the Rust CLI and install the optional host package into the Python runtime already configured in LocalAppData/NZRdsp/runtime.json:

```powershell
cargo build --manifest-path cli/Cargo.toml --locked
& D:\path\to\runtime\Scripts\python.exe -m pip install -r runtime/requirements-vst.txt
```

The host is pinned to Pedalboard 0.9.26; the worker rejects another version. The installed plugin must be available independently. This repository contains neither commercial plugin binaries nor vendor presets.

## Commands

```powershell
nzrdsp vst --plugin 'C:\Program Files\Common Files\VST3\FabFilter Saturn 2.vst3' --list-params
nzrdsp vst --plugin 'C:\Program Files\Common Files\VST3\FabFilter Saturn 2.vst3' --save-preset D:\renders\reference.vstpreset
nzrdsp vst --plugin 'C:\Program Files\Common Files\VST3\FabFilter Saturn 2.vst3' --preset D:\renders\reference.vstpreset -i D:\renders\input.wav -o D:\renders\processed.wav --tail 2
```

Use `--param host_key=value` with a finite normalized value from zero to one. These are raw host values, not dB/Hz or the native raw units used by Surge. `--list-params` reports the keys, raw values and host descriptors. Host-inferred ranges/units are descriptive; raw normalized values are used for assignments. See the [Pedalboard parameter API](https://spotify.github.io/pedalboard/reference/pedalboard.html#pedalboard.AudioProcessorParameter).

`--preset` accepts VST3 .vstpreset state. It does not parse .ffp, .SerumPreset, .nksf or other vendor containers. Save a new host snapshot with `--save-preset`; an optional input preset and parameter overrides can be applied first. `--name` selects a plugin within a multi-plugin module. Existing output and sidecar files are rejected before plugin initialization.

Rendering currently accepts mono/stereo effects at the input file's rate, from 8 to 384 kHz. `--tail` adds zero input after the source; it is an explicit length rather than automatic tail detection. Native processing remains continuous across chunks. Extra zero input drains host latency buffering, and the output is checked against the requested frame count. `--block` selects a native host block size from 1 to 8192. No resampling, level normalization or arbitrary tail cropping is applied.

`--timeout` bounds the worker (default 120 seconds). Rust captures output concurrently, terminates a timed-out worker and owns its staging directory. Existing files are not overwritten. Surge now shares this worker management, with a 30-minute bound. The worker is a process boundary; this does not establish compatibility with every plugin or control external helper processes launched by a vendor.

## Manifest

The sidecar records native name/version/identifier, module hashes, host version, input and preset hashes, initial-state hash, raw parameter snapshot, applied normalized values, sample rate, block size, source/tail/output frames, reported latency, peak/RMS, samples above unity and WAV hash. Inputs and preset bytes are checked again before publication. The saved host snapshot is the reproducible state input; a state hash alone cannot restore a configuration.

There is no explicit BPM/playhead override in this adapter. Tempo-synced LFOs and sequencers need a separate transport-aware reference path. Native randomness or other unexposed state can affect repeatability. MIDI instrument rendering and sidechain buses are not implemented here. These limits keep the commercial feature issues open.

## Verification

On this workstation, Saturn 2 version 2.1.3.0 and Pro-Q 4 version 4.1.3.0 load without an editor. Their parameter inventories contain 956 and 605 controls respectively. Four native checks pass: state/direct-render comparison, sample-exact bypass, output/error protection, and nonzero-latency Linear Phase processing in Pro-Q 4. The latter preserves the requested frame count and the impulse's original sample position. The streamed CLI output matches a direct native-host render within 0.000001 absolute sample error for the saved default fixtures. This establishes those fixtures, not arbitrary-preset or GUI parity.

Native checks are opt-in:

```powershell
$env:NZRDSP_RUNTIME = "$env:LOCALAPPDATA\NZRdsp\runtime.json"
$env:NZRDSP_NATIVE_CLI = 'D:\path\to\nzrdsp.exe'
$env:NZRDSP_VST_TEST_PLUGINS = ConvertTo-Json -Compress -InputObject @('C:\path\Saturn 2.vst3', 'C:\path\Pro-Q 4.vst3')
& D:\path\to\runtime\Scripts\python.exe -m unittest discover -s python/tests -p test_vst_cli.py -v
$env:NZRDSP_TEST_PYTHON = 'D:\path\to\runtime\Scripts\python.exe'
cargo test --manifest-path cli/Cargo.toml --locked hung_worker_is_terminated -- --ignored
```

The ignored Rust test exercises a genuinely sleeping Python worker and checks termination. It does not require a plugin. Native fixtures and rendered audio remain outside Git.
