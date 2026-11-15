# 6.0 Beta 3 — Reported audio status instead of sound meters

Run build.bat on Windows and use the newly compiled dist/MKVEncoderQ.exe.
Keep the same config/tool setup and matching VLC installation.

1. Open Compare with Backup. Encoded should start with AUDIO ON, its audio
   track ID and volume percentage. Backup should show audio track disabled.
2. Click Listen to Backup. Its reported state should become AUDIO ON, and the
   encoded video's track should become disabled. Confirm by listening too.
3. Click Mute Both: both should show track disabled. Click either Listen button
   to restore that source alone at the shared volume setting.
4. Set Volume to zero: the selected source should show SILENT. Pausing should
   show the reported Paused state, not AUDIO ON. Resume and check again.
5. Seek, detach, drag windows between monitors, and rejoin them. Both the controls
   window and each video window should show the same reported state for that file.
6. Check dark/light themes. The enabled playing source is green, off states are
   subdued, and unavailable/unconfirmed states are amber. Videos are unchanged.

The display reads VLC's audio track, mute, volume and playback state. It does
not echo the requested Listen selection. If VLC reports both tracks enabled,
both indicators can show AUDIO ON; that would expose a backend-state problem.
It is not loopback recording or proof of what reaches the speakers. Confirm any
suspect behavior by listening and send a recording if needed.

No sound-level meters or additional meter decoders run in this version. The
Beta 2 change that disables unselected audio tracks remains. Encoding defaults
to All Streams for fresh configs; saved choices remain intact.

All 400 automated tests pass, including reading actual player states, unknown
status handling, pause/mute/zero-volume reporting and independent indicators.
Native Windows/VLC behavior still needs testing on your PC. Neither movie nor
backup is modified by the comparison player.
