# 6.0 Beta 5

Build using build.bat. The Encoder menu now reads Play Both - Compare.

- With matching installed VLC and a completed movie/backup pair: enabled.
- Without VLC, without its plugins, or with only a mismatched architecture:
  disabled. Normal encoding and ordinary Play Video/Play Backup still work.
- A complete portable VLC folder beside the executable is also detected.
- Runtime detection is not a full playback test; native load failures still
  produce an error in the isolated comparison process.

All 413 automated tests pass. Test actual Windows discovery on your machine.
All Beta 4 track-menu and mute-toggle changes remain included.
