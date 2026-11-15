# MKV Encoder Q 6.0.1

- Removed beta labeling.
- Temporary inspection failures remain eligible for automatic retry after the initial five retries. Another inspection is queued after 30 seconds, without requiring a new directory event or manual Inspect. Repeated temporary errors remain retryable; cancelling inspection or changing directories stops pending work.
- Audio Streams shows Custom selection — tracks … with a selection marker. Reopening the dialog retains checked tracks.
- Rename preserves the original extension in backups for every supported source format. Undo reverses the exact recorded file and backup transaction. Encoder source references preserve the original extension.
- Existing audio overrides remain session-only and editable while the queue is stopped.

Closed-caption behavior is unchanged; the reported embedded-caption case is still under investigation. Existing incorrectly renamed backups are not automatically repaired.

## Manual checks

1. Copy a large video into the watched folder for longer than one minute. Confirm inspection recovers automatically after the copy finishes.
2. Choose specific audio tracks, reopen Audio Streams and confirm the custom selection marker and checked tracks.
3. Encode an MP4 with backups, rename the MKV, then Undo: verify MP4.bak extension, exact filenames, File Details, Play Backup and comparison. Repeat with AVI or MPG.
4. Confirm cancelling inspection prevents automatic retries, and changing directories does not revive old retry jobs.
