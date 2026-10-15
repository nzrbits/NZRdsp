# NZRdsp

NZRdsp is the project previously named NZR Tools / NZRtools. The standalone Tauri app currently contains Synth and Crusher. NZRstudio embeds a newer sample editor with EQ, edit history and voice generation; those host integrations are not yet shared with this standalone app.

The working checkout is `D:/music-dev/NZRdsp`, connected to [nzrbits/NZRdsp](https://github.com/nzrbits/NZRdsp). The old `nzr-synth` directory was renamed after preserving the Crusher edits and creating a verified Git bundle. The legacy application identifier stays `com.nzr.tools` to preserve OS storage compatibility; the visible application name and Rust package are NZRdsp.

## Development

Run `cargo check --manifest-path src-tauri/Cargo.toml --locked` to check the host. A Tauri build uses `src-tauri/tauri.conf.json` and the existing `frontend` assets. Build products and generated schemas are not source.

## Issue work

The repository's 20 existing issues request synthesizer/effect CLIs and comparison against their reference products. They are feature projects, not fixes already delivered by the current Synth/Crusher UI. The queue is tracked in [docs/issue-audit.json](docs/issue-audit.json). Implemented controls must be verified before an issue is closed; a generic approximation must not be labeled as a verified clone of a commercial plugin.

Shared editor code will have one source and explicit host adapters. Keep Tauri file handling separate from NZRstudio's revision-checked Apply API. Preserve the current Studio EQ, history and voice features when extracting shared modules.
