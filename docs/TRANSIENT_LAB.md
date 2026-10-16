# Transient Lab

`nzrdsp transient-lab` is an independent differential-envelope experiment for drum shaping. It is not SPL Transient Designer, has no vendor preset support and makes no claim of matching its sound. Issue #13 remains open for a measured reference comparison.

[SPL describes](https://spl.audio/en/spl-produkt/transient-designer-plugin/) direct attack/sustain processing and additional limiter, parallel and sidechain features in the Plus product. Those statements establish reference capabilities, not the constants or topology below. This experiment does not implement the Plus feature set. The linked [manufacturer manual](https://files.plugin-alliance.com/products/spl_transient_designer_plus/spl_transient_designer_plus_manual.pdf) remains reference material; its download timed out during this implementation pass.

## Run

Build the existing Rust CLI with `cargo build --manifest-path cli/Cargo.toml --locked`. Use the configured Python runtime with the existing pinned `runtime/requirements-surge.txt` packages. No plugin, audio device or GUI is needed.

```powershell
nzrdsp transient-lab -i kick.wav -o shaped.wav --attack-db 6 --sustain-db -8 --output-db -3
nzrdsp transient-lab --runtime D:\runtime.json -i input.wav -o neutral.wav
```

Attack accepts -15 through +15 dB, Sustain -24 through +24 dB and Output -60 through +24 dB. Zero is neutral for all three. `--block` accepts 1..8192 frames (default 512); `--timeout` bounds the hidden worker (default 120 seconds). Runtime resolution follows the existing CLI: `--runtime`, NZRDSP_RUNTIME, then LocalAppData/NZRdsp/runtime.json.

Inputs are nonempty mono/stereo audio readable by libsndfile at 8..192 kHz, limited to 600 seconds. Output is Float32 WAV with the same rate, channels and frame count, no resampling, normalization, lookahead or added tail. Values above unity remain in the float file; there is no limiter. Existing output or sidecar paths are rejected.

## Independent detector

The linked channel peak drives four causal attack/release followers. Each sample uses an exponential coefficient derived from the sample rate and the selected attack/release time. This lab fixes the following times; they are NZRdsp choices, not recovered SPL values.

| Follower | Attack (ms) | Release (ms) |
|---|---:|---:|
| Attack fast | 0.2 | 30 |
| Attack slow | 10 | 30 |
| Sustain short | 1 | 60 |
| Sustain long | 1 | 240 |

The positive difference between the attack followers is normalized by the fast follower. The positive difference between the sustain followers is normalized by the long follower. These weights scale the two dB controls; Output adds a static dB trim before conversion to linear gain. Both channels receive the same gain. Detector state persists across blocks and starts from zero for each command. Envelope state below the explicit numerical floor $10^{-30}$ is cleared.

This is a fixed detector, without frequency selection, independent stereo modes, external sidechain, mix control or a soft clipper. Level-independent behavior was checked over a hundredfold input-level change on the synthetic fixture; that does not establish every possible input or silence-floor case.

## Output evidence

The JSON sidecar includes algorithm ID, explicit `vendor_parity:false`, worker hash, dependency/library versions, source/output hashes, source subtype, controls, detector times, block/rate/channel/frame counts, zero latency and output peak/RMS/above-unity count. Metrics describe stored Float32 samples. Chunk-level floating-point reduction may change the last bits of RMS with block size even when WAV samples match exactly.

Processing and hashing stream in bounded blocks. Publication refuses existing paths. A handled failure publishing the WAV removes only the newly published manifest. The file pair is not crash-atomic; a process crash between the two publications can leave the manifest without audio.

## Verification

On 2026-10-16, all five tests in `python/tests/test_transient_lab.py` pass. They cover neutral sample equality, onset/decay response, silence, linked antiphase stereo, hundredfold level scaling, exact sample equality across block sizes, actual CLI PCM24 input/Float32 output at 8/44.1/48/96 kHz, mono output gain, provenance and failed-output protection. Tests use synthetic audio and no playback.

```powershell
$env:NZRDSP_NATIVE_CLI = 'D:\path\to\nzrdsp.exe'
& D:\path\to\runtime\Scripts\python.exe -m unittest discover -s python/tests -p test_transient_lab.py -v
```

The worker uses only the existing pinned NumPy/soundfile runtime. The test creates a temporary minimal runtime JSON; it does not overwrite the machine configuration. No installed NZRstudio/NZRdsp runtime or user music was changed.
