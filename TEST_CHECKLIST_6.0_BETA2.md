# 6.0 Beta 2 — Audio controls, live meters and themes

Build with build.bat on Windows, then use the new dist/MKVEncoderQ.exe with
your existing config/tool paths and matching VLC installation. Keep Beta 1
available for comparison. No .bak association is needed for embedded playback.

1. Open Compare with Backup. Encoded audio is selected initially. Confirm you
   hear only one movie, including during startup and after seeking.
2. Click Listen to Backup, then Listen to Encoded. Each is one click; the other
   audio track should be disabled. Both videos should keep playing.
3. Move the shared Volume slider. Switch sources and confirm the same volume
   percentage is retained. Mute Both should silence both files. Selecting either
   Listen button should restore only that source at the current volume.
4. Watch the independent Backup and Encoded meters. They should respond to
   sound in both files, even for the unselected source or while Mute Both is set.
   Listening/off marks the audible source. The meter values are decoded stereo
   peak dBFS before the volume slider; changing volume should not scale them.
5. Seek to dialogue, silence and louder sections. Check both meters recover
   after seeking, pausing/resuming and detaching/rejoining video windows.
6. Confirm the saved app theme is used when launching comparison. Switch the
   player Theme between dark and light; check controls, dropdowns, file labels
   and detached windows remain readable. The player selector changes only the
   player, not the Encoder's saved theme. Native Windows title bars may differ.
7. Repeat with a 4K pair from the NAS. Check playback remains smooth while the
   meters run. Metering adds two silent audio-only decoders and extra file reads,
   not additional video decoders. It is an experimental feature; report delays,
   repeated sounds, unusual readings, or unavailable/no-data indicators.

Meters are visual peak indicators, not integrated loudness measurements or an
objective quality comparison. They use the default audio track of each file,
downmixed to stereo; multichannel downmix and seek buffering can affect results.
A −60 dBFS reading is the display floor. No data/unavailable does not mean silence.

All 397 automated tests pass. Tests cover audio-track disabling/restoration,
late initialization, volume retention/clamping, decoded PCM peak calculations,
independent meter playback commands and theme palettes, plus the existing suite.
Actual Windows/VLC audio, callback timing and rendered themes need testing on
real hardware. Playback reads files and does not change the movies or backups.
