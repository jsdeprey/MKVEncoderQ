# MKV Encoder Q 6.0.3

- Waiting-file Audio Streams choices can be changed while another file encodes. The active sampling/encoding file is locked; the queue lock protects the transition. A dialog left open cannot change a file after it starts processing.
- Rename: Whole filename uses Replace as the new base name, disables Find, preserves the extension and retains preview, numbering and collision validation. Reset clears the checkbox.
- Independent encoder.log, cleaner.log and rename.log in the shared log folder. Each tab has Logging settings with maximum size (default 10 MB), older files to keep (default 5), and Open Log Folder. The existing Cleaner log-folder setting supplies the shared folder. Settings persist in log_settings.json.
- Encoder logs include command, settings, HandBrake output, verification and commit results. HandBrake failures include a recent-output excerpt and log path in File Details. Rotation can overwrite older job details, so save a log when reporting an issue.
- Cleaner per-run records now feed its rotating log. Optional JSON output becomes one bounded cleaner-latest.json snapshot (up to the configured size) rather than an unlimited series. Old timestamped Cleaner reports are pruned to the configured history count when a Cleaner run starts. Rotation applies going forward; existing old log files are not resized.
- HandBrake failures register retained partial output for Play, Compare and Delete. New temporary outputs have a small .job.json ownership record so they can be found after restart. Records are removed on commit, acceptance or deletion.
- Right-click discovers retained files from new ownership records, including in a separate output folder. Legacy filenames are matched only for an exact, unique source stem in the same folder, with an inactive writer check. Multiple matches appear under Choose retained output; select one and right-click again for actions. Truncated legacy filenames and ambiguous sources require manual handling.
- Accept remains restricted to outputs recorded in this session as duration-only verification failures. Recovered partial files cannot be accepted merely because they exist.
- Includes 6.0.2 queue restart fix. Per-video audio overrides remain session-only. Embedded caption behavior is unchanged.

## Build and manual checks

Run build.bat on Windows.

1. While encoding one file, change a waiting file to All Streams, then choose individual tracks. Confirm the active file is locked and the waiting file uses its new policy.
2. Replace a whole filename, confirm preview/extension, reset, and test duplicate-name rejection.
3. Check each tab's Logging settings and folder. Trigger a failed job; inspect the error excerpt and encoder.log.
4. Compare and delete a retained failed output while an unrelated video encodes. Restart and check retained discovery; use Choose retained output for multiple matches.
5. Confirm an active output cannot be deleted, and an incomplete HandBrake failure cannot be accepted.
6. Test Stop All / Start with a populated queue.

Native Windows playback and window layout require local validation.
