# 6.0 Beta 4 — Track menus and mute toggle

Run build.bat on Windows, then launch the new dist/MKVEncoderQ.exe.
Keep your existing config/tool paths and matching VLC installation.

1. Open Compare with Backup on a pair with multiple audio/subtitle tracks.
2. Right-click the original's video, heading, filename or audio-status text.
   Audio track should list the tracks exposed by VLC for that file.
3. Choose another audio track on the audible side. Confirm that it changes.
   Then choose a track on the silent side: it should remain silent until you
   use its Listen button (or Listen to this video in its right-click menu).
4. Choose Subtitles on each side independently. Check different languages,
   then Off. Audio selection and shared playback controls should stay intact.
5. Separate the windows and rejoin them. Confirm right-click menus work in both
   layouts, and each file's chosen tracks remain selected after the reload.
6. Listen to Backup at a recognizable shared volume, then click Mute Both. It
   should stay red and read Unmute Both. Click it again: Backup should resume
   at that volume, and the button should return to its ordinary appearance.
7. Repeat with Encoded, after seeks, and after changing light/dark theme.
   Clicking a Listen button while muted should select that source directly.
8. If right-clicking the rendered video is intercepted by VLC/Windows, use the
   same video's heading/filename/audio-status area; it opens the same menu.

The menu operates only on tracks in the clicked file. This beta does not add
external subtitle-file loading or the full VLC Video/effects menus. A selected
audio-track radio item on a silent video is its saved preference, not a claim
that it is audible; the VLC-reported status text remains the state indicator.

All 408 automated tests pass. Coverage includes muted-source restoration,
button state, independent track choices, delayed VLC readbacks, subtitle Off,
failed selection handling and restored subtitles after layout changes. Real
Windows/VLC rendering, right-click delivery and track switching need your test.
Playback does not modify either movie, backup, or encoding configuration.
