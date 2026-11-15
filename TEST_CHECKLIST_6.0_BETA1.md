# MKV Encoder Q 6.0 Beta 1

## Build

Run build.bat on Windows, then run dist/MKVEncoderQ.exe. The build installs
and includes the python-vlc interface. Copy your existing config.json beside
the new executable to keep settings; retain the usual external encoder tools.

The comparison engine requires installed VLC 3.x with the same architecture as
Python/the executable, normally 64-bit. Your normal installed VLC should be
detected. You do not need to associate .bak files with VLC for comparison.
If VLC is absent or incompatible, the comparison process shows an explanatory
error; the Encoder continues to work. Alternatively, put a complete matching
portable VLC distribution in a VLC subfolder beside the executable, including
libvlc.dll, libvlccore.dll and its plugins directory. No VLC runtime is bundled
in this source ZIP.

## First test

1. Keep your working 5.1.x executable available. Use copies for initial testing.
2. Right-click a completed movie with its original .bak still present.
3. Choose Compare with Backup. The original should appear on the left and
   the encoded movie on the right, with labels and filenames.
4. Play/Pause both videos. Drag the shared timeline, then use ±10 s. Check that
   both reach the same scene. Resync reapplies the encoded video's timestamp
   to both players. This prototype does not promise frame-perfect synchronization.
5. If needed, adjust Backup offset in milliseconds and click Apply. A positive
   value places the backup further ahead. Audio defaults to the encoded movie;
   use the selector to hear Backup or mute both.
6. Click Separate windows. Drag either video window onto another monitor. The
   original controls window continues to control both. Use Side by side to
   reunite them. Closing a detached video window also requests rejoining.
7. Compare two 4K files. Look for dropped frames, unexpected colors/HDR appearance,
   seeking problems and audio echo. Hardware decoding is requested automatically,
   but performance and HDR parity with standalone VLC must be checked on your PC.
8. Try while another file encodes. The comparison process must not replace the
   Encoder worker or its progress. Both activities can compete for PC resources.
9. Close the comparison controls window. Both videos and player process should
   close while MKVEncoderQ stays open. Confirm both files are unchanged.

Layout switching briefly stops/recreates the video surfaces, then restores the
position and playback state. At end-of-file or playback error, close and reopen
comparison to restart. This beta has no comparison-side deletion/acceptance
buttons, zoom, fullscreen controls or automatic monitor placement.

## Automated validation

py -m unittest discover -s tests

Expected: 388 tests, OK. New tests cover shared commands, positive/negative
sync offsets, unequal duration limits, audio selection, launch arguments,
independent process ownership and stopping playback before destroying surfaces.
Native VLC rendering, Windows file associations, HDR and real-media performance
cannot be validated by these mocked tests. Report what happens on your PC.
