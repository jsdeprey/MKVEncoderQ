# v5.1.60 safety-fix test checklist

Use disposable copies of videos for these tests. Turn on Force Encode when
needed so the savings decision does not skip a test file.

## Manual Windows checks

1. **Same-folder MKV, backups off:** Encode a short MKV. Confirm the final MKV
   plays, has the expected duration, audio and subtitles, and no .bak exists.
2. **Same-folder MP4, backups off:** Encode an MP4. Confirm a playable MKV
   appears and the MP4 is removed only after output commit succeeds.
3. **Separate output folder, backups off:** Repeat with both MKV and MP4,
   including a source in a nested folder. Confirm the relative folder layout
   is preserved, the final MKV plays, and the source is removed on success.
4. **Cancel:** Cancel an encode before completion. Confirm the original is
   unchanged and the cancelled temporary output is removed.
5. **Backups on:** Repeat MKV and MP4 encodes. Confirm the original bytes are
   retained in the usual .bak file and the final MKV plays.
6. **Existing output:** For a different output path, pre-create a destination
   MKV. Confirm encoding refuses to overwrite it and leaves the source intact.
7. **Interface smoke check:** Confirm the version reads 5.1.60, settings load,
   and the Encoder, Cleaner and Rename tabs open normally.

## Automated regression checks

Run from the extracted source folder:

```console
py -m unittest discover -s tests
```

The new `tests/test_encode_commit.py` uses real temporary files and mocks
HandBrake/probe calls. It exercises real verification and filesystem replacement.
It covers 31 scenarios across four test methods:

| Scenario | Expected result |
| --- | --- |
| MKV / MP4, same / separate folder (4) | Verified output commits successfully; no backup is created |
| Encode, probe, verification or commit failure; cancellation, for each combination (20) | Original bytes remain intact; source deletion is never attempted |
| Source deletion failure at each different destination (3) | Exception propagates; committed output and original both remain |
| Backups enabled for each combination (4) | Existing backup behavior remains intact |

Successful same-path MKV replacement must never unlink the source first.
For different paths, tests assert the verified output is already present at its
final destination when source deletion is attempted. Failed temporary files
remain available for inspection under the existing rules; cancellation removes
the temporary file.

Validation in the development environment: all 341 tests passed. The new
regression suite failed against the original 5.1.59 encoder as expected.
These automated tests do not run a real video encode or the Windows GUI.

## Build

On Windows, run `build.bat` using the existing build process. It produces
`dist/MKVEncoderQ.exe`, `dist/MKVEncoderQ-CLI.exe` and the HTML user guide.
This package contains source, not newly compiled Windows executables.
