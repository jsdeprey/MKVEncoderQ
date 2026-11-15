# v5.1.61 — practical testing

## Build and use

Extract this source ZIP to a new folder and run `build.bat` on Windows.
Use the new `dist/MKVEncoderQ.exe`. Keep your previous version available.
Copy your existing config.json alongside the new executable to retain settings;
the configured external tools must also be available there or at their saved paths.

This is a source package, not newly compiled Windows executables. Automated
tests run here without a Windows desktop or a real media encode.

## Start with these checks

1. **Temporary names:** Force Encode a disposable copy. While encoding, check
   the output directory. Expect `.video-optimizer-<source stem>-<8 hex digits>.mkv`.
   Long names are shortened only as needed for Windows/NAS path limits.
2. **Completed output path:** Stop All, save a separate output folder in
   Encoder Settings, and Start again. Encode a disposable MP4. The completed
   row should remain, showing the MKV's actual output name and folder.
   Play Video should use that output. File Details should include the original source.
3. **Rename position:** Select Largest First. Let Active Directory Scanning
   append a new large file at the bottom. Rename it through Rename. It should
   stay in the same position. Repeat with an active filter; clearing the filter
   should preserve the full-list order. Explicitly choose a Sort option to reorder.
4. **Normal success and Cancel:** Check that a normal encode completes and
   Cancel leaves the original intact, as in 5.1.60.

## When a verification failure occurs

Only completed retained encodes tracked in the current application session have
these menu actions. Old temporary files are not automatically imported. Keep the
application open while checking a failed output. Stop All first if the Encoder
is still running or watching for new files.

- Right-click the failed row and choose **Play Retained Output**. Check the
  beginning, middle and end, audio, and subtitles in your player.
- **Accept Retained Output...** is enabled only if duration was the sole failed
  check. Confirming re-probes the original and retained encode and checks codec,
  resolution/crop rules, bit depth, HDR/Dolby Vision, audio and subtitles again.
  Any non-duration failure blocks acceptance, even if the earlier failure was
  duration-only. Unknown durations also block acceptance.
- A successful acceptance saves the original bytes as `.bak`, promotes the
  retained encode to the regular output filename, and marks the row Completed.
  File Details and the activity log show both durations and their difference.
- **Delete Retained Output...** asks for confirmation and removes only that
  retained encode. The original movie remains untouched.
- Retained files keep their leading dot. The application checks and corrects
  unintended Read-only on its retained output, including after promotion.
  Confirm playback and writable attributes on your NAS.

## Safety behavior

- Existing backups and unrelated destination files block acceptance.
- The backup is copied with exclusive creation before replacement; large files
  can take time and require room for an additional original-sized copy. This
  runs in the background. Closing is blocked while acceptance is active.
- A failed commit preserves the original, completed backup, and retained encode.
- A failed backup copy leaves the original and retained encode untouched; a
  partial `.bak` may remain. The error identifies it. Inspect it before deciding
  what to do; the application will not overwrite it on a retry.
- If removing a source at a different path fails after commit, the output and
  backup remain, and the activity log states that the source also remains.
- The 5.1.60 automatic `--no-backup` commit-order fix remains in place.

You do not need to manufacture broken files or simulate NAS failures yourself.
The automated tests cover those failure paths with disposable files.

## Automated validation

```console
py -m unittest discover -s tests
```

Expected: **363 tests, OK**. Injected failures deliberately print error messages.
Coverage includes acceptance restrictions, audio/subtitle/HDR/Dolby Vision loss,
existing-file collisions, backup/commit/deletion failures, changed source files,
temporary naming/path limits, Read-only correction, original-safe retained
deletion, background acceptance, output/source details, and full-list rename order.
