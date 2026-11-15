# v5.1.62 testing

Extract the source ZIP to a new folder, run `build.bat` on Windows, and use
the new `dist/MKVEncoderQ.exe`. Copy your existing config.json alongside the
new executable if you want to retain settings. Ensure your configured
HandBrakeCLI, ffprobe and MKVToolNix are available.

## Inspection retries

1. Enable Inspect New Files and Active Directory Scanning. Copy several
   disposable videos into the input directory while watching the list.
2. If inspection encounters a temporary copy/lock error, the activity log should
   show retry attempts. The last file can recover without another file arriving.
3. A persistent failure remains visible after five retries (delays of 2, 4, 8,
   16 and 30 seconds, plus time spent in ffprobe). It does not retry forever.
4. Once copying has finished, right-click a row with an inspection error and
   choose Inspect File. This now clears the stale error and queues a fresh probe.
   A successfully inspected file can also be explicitly inspected again.
5. Cancel Inspection during a retry wait. The wait should end promptly, and
   Inspect File can be used later for another attempt.

These retries run in the inspection worker. A locked file can delay later
inspections while its bounded retries run, but the interface remains responsive.

## Retained outputs while encoding continues

1. Keep the application open after an encode fails verification and retains its
   output. Start/continue encoding another disposable video.
2. Right-click the failed row. Play Retained Output and Delete Retained Output
   should be available even while that different video is encoding.
3. On a duration-only failure, Accept Retained Output should also be available.
   Other verification failures must continue to block acceptance.
4. Play and check the retained output. Accept it if appropriate. Expect the
   original bytes in .bak, promoted output at the normal filename, and acceptance
   details in File Details and the activity log. The unrelated encode should
   continue with its progress and cancellation controls unchanged.
5. On a different failed disposable copy, choose Delete Retained Output and
   confirm. Only the retained encode should disappear; its original must remain.

The application reserves the affected source, retained output, destination,
and backup against conflicting actions. It blocks same-file conflicts, not
the entire encode queue. Closing or changing input directories must wait for
an in-progress acceptance to finish. Existing .bak and unrelated destination
files are never overwritten. Old retained outputs are still tracked only within
their current application session; this release does not add recovery imports.

Manual acceptance still requires additional space and time to copy the original
to an exclusive backup before committing output. If backing up or committing
fails, inspect the reported paths; the original remains protected.

## Automated tests

```console
py -m unittest discover -s tests
```

Expected: **378 tests, OK**. Deliberately injected failures print error messages.
Tests cover both concurrent actions, same-file exclusions, queue/Cleaner
reservations, independent worker ownership, bounded and cancellable inspection
retries, fresh manual probes, and files changing during inspection, alongside
the existing safety suite.

This package contains source and the Windows build script. The automated checks
here do not run Windows executables, the Windows GUI, or real NAS/video encodes.
