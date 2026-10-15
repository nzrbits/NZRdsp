# NZRdsp working guide

Use `D:/music-dev/NZRdsp` as the canonical standalone checkout. It tracks `nzrbits/NZRdsp`. NZRtools and NZR Tools are former names of this project. NZRstudio's sole checkout remains `X:/music-dev/NZRstudio`.

Check git status before editing and preserve unrelated work. The user's current request covers all issues in NZRdsp and NZRstudio. The queues have different scopes: Studio has engine/editor defects; NZRdsp has 20 plugin CLI feature projects.

Do not close a feature issue after merely adding a command stub or a generic effect. Record implemented parameters, missing capabilities, tests and reference comparison evidence. Do not claim proprietary algorithm parity without a comparison. Keep commercial binaries, presets, licenses, user audio, model weights and build caches out of Git.

Standalone files are under frontend/ and src-tauri/. Studio's newer embedded editor is under crates/nzr-studio/web/nzrtools in the Studio checkout; the legacy URL and message identifiers remain compatible. Changes to shared DSP need an explicit import/version step until extraction provides a common source.

Use background checks without native file dialogs or audible playback. Keep music projects unchanged during source work. Document verification and remaining work in HANDOFF.md.
