# NZRdsp handoff

docs/PLUGINS.md now records source-linked corrections and implementation/reference requirements for all twenty plugin issues. These are researched scopes, not completed engines. The longer deep-research report and claim review remain unfinished in Documents/NZRdsp_Plugin_Research_20261016; do not claim that report is delivered.

The user confirmed NZRdsp and NZRtools are the same project and chose NZRdsp as its name. The canonical checkout was moved from D:/music-dev/nzr-synth to D:/music-dev/NZRdsp. Original Crusher edits were committed as 587bef5 before the rename; the verified backup bundle is D:/music-dev-build/NZRstudio/NZRdsp-before-rename.bundle. Historical branches remain available. Main is the development branch, with origin pointing to nzrbits/NZRdsp.

Visible titles and the Cargo package name now use NZRdsp; the legacy Tauri identifier com.nzr.tools is retained for compatibility. Generated schemas are ignored. The standalone editor remains older than Studio's embedded implementation. Do not silently overwrite the richer Studio implementation when extracting shared code.

All 20 GitHub issues remain open. Issue #7 has a working native Surge renderer: cli/ contains the Rust commands, python/surge_worker.py the bounded streaming adapter, scripts/build-surge.ps1 the reproducible build/setup, and docs/SURGE_CLI.md the interface and limits. The script successfully reused the pinned source/build and configured D:/music-dev/NZRdsp-runtime. Build artifacts remain under D:/music-dev-build/NZRdsp. Native output is not played during checks.

Two Rust parser tests and four native end-to-end tests pass: native preset save/load, WAV at 44.1/48 kHz, parameter validation, output preservation, module-hash rejection and direct native block comparison. The deterministic initialized-patch comparison agrees within 0.000001 absolute sample error. GUI parity and preset browser metadata are still pending, so #7 is not closed. The CLI is not integrated into Tauri tabs yet.

FabFilter Saturn 2 and Pro-Q 4 binaries were found in the normal VST3 folder; this does not verify activation or algorithm parity. No commercial plugin binary has been executed for issue work yet. No reverse engineering MCP tools were exposed in tool metadata during this inspection.
