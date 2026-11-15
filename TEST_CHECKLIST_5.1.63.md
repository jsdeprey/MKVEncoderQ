# v5.1.63 — Play Backup

Build with build.bat on Windows and run the new dist/MKVEncoderQ.exe.
Keep your existing config and external tool paths as usual.

1. Right-click a movie with an original .bak: Play Backup appears immediately
   below Play Video and opens that .bak using its Windows association.
2. With .bak associated with VLC, confirm VLC shows the backup path.
3. Right-click a file with no backup: Play Backup should be disabled.
4. Check a completed output in a separate folder: Play Video opens the output;
   Play Backup opens the backup beside its original source.
5. These playback actions remain usable while an unrelated video encodes.

Backup naming and encoding behavior are unchanged. No player configuration
or comparison player is added in this release. The 378 automated tests pass;
Windows file-association playback needs testing on your computer.
