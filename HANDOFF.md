# NZRdsp handoff

The user confirmed NZRdsp and NZRtools are the same project and chose NZRdsp as its name. The canonical checkout was moved from D:/music-dev/nzr-synth to D:/music-dev/NZRdsp. Original Crusher edits were committed as 587bef5 before the rename; the verified backup bundle is D:/music-dev-build/NZRstudio/NZRdsp-before-rename.bundle. Historical branches remain available. Main is the development branch, with origin pointing to nzrbits/NZRdsp.

Visible titles and the Cargo package name now use NZRdsp; the legacy Tauri identifier com.nzr.tools is retained for compatibility. Generated schemas are ignored. The standalone editor remains older than Studio's embedded implementation. Do not silently overwrite the richer Studio implementation when extracting shared code.

All 20 GitHub issues are still pending implementation and verification. FabFilter Saturn 2 and Pro-Q 4 binaries were found in the normal VST3 folder; this does not verify activation or algorithm parity. No plugin binary has been executed for issue work yet. No reverse engineering MCP tools were exposed in tool metadata during this inspection.
