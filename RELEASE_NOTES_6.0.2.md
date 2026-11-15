# 6.0.2 — queue restart fix

Single-file reanalysis now restricts the existing queue instead of replacing it. Returning to a normal run clears the restriction while preserving waiting rows, selections and session audio overrides. Unrelated waiting files are no longer added to the completed-file protection set during reanalysis.

Regression coverage: 19-file cancel/reanalysis/restart, selected waiting files retained in order, and restricted runs do not claim other files.

Build using build.bat. Test by stopping a queue, changing audio choices, and restarting; also reanalyze a cancelled file then Stop All and Start the full queue.

Logging improvements, retained-file discovery, Whole filename rename, and editing waiting-file audio during encoding remain planned for a later update.
