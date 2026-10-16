# NZRdsp

NZRdsp is the project previously named NZR Tools / NZRtools. The standalone Tauri app currently contains Synth and Crusher. NZRstudio embeds a newer sample editor with EQ, edit history and voice generation; those host integrations are not yet shared with this standalone app.

The working checkout is `D:/music-dev/NZRdsp`, connected to [nzrbits/NZRdsp](https://github.com/nzrbits/NZRdsp). The old `nzr-synth` directory was renamed after preserving the Crusher edits and creating a verified Git bundle. The legacy application identifier stays `com.nzr.tools` to preserve OS storage compatibility; the visible application name and Rust package are NZRdsp.

## Development

The [Surge CLI](docs/SURGE_CLI.md) renders native Surge XT patches through a pinned headless engine. Build the Rust commands with `cargo build --manifest-path cli/Cargo.toml --locked`; build/setup instructions and native verification are in the guide. The Tauri tabs do not yet expose these commands.

The [VST3 reference CLI](docs/VST_CLI.md) processes WAVs through installed native effects, lists raw parameters and saves/loads VST3 snapshots. Saturn 2 and Pro-Q 4 have native render checks; this is reference-host progress for their issues, not an independent DSP port.

The [Dexed CLI](docs/DEXED_CLI.md) inspects/edits DX7 banks and renders notes through native Modern, Mark I and OPL engines. `nzrdsp dexed` and `dexed-cli` produce Float32 WAV with manifests, hash/version checks and subprocess deadlines. Independent hardware/verified-emulator audio comparison remains pending.

Run `cargo check --manifest-path src-tauri/Cargo.toml --locked` to check the host. A Tauri build uses `src-tauri/tauri.conf.json` and the existing `frontend` assets. Build products and generated schemas are not source.

## Issue work

The repository's 20 existing issues request synthesizer/effect CLIs and comparison against their reference products. They are feature projects, not fixes already delivered by the current Synth/Crusher UI. The queue is tracked in [docs/issue-audit.json](docs/issue-audit.json). Implemented controls must be verified before an issue is closed; a generic approximation must not be labeled as a verified clone of a commercial plugin.

[Plugin scope notes](docs/PLUGINS.md) correct preset formats, engine/control assumptions and reference-host requirements for all twenty issues. They distinguish researched plans from implemented commands.

Shared editor code will have one source and explicit host adapters. Keep Tauri file handling separate from NZRstudio's revision-checked Apply API. Preserve the current Studio EQ, history and voice features when extracting shared modules.
