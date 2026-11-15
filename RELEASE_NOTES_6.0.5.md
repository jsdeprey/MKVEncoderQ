# MKV Encoder Q 6.0.5

- The encoder requests all detected subtitle tracks. Missing subtitle tracks and language differences are warnings in the Encoder Activity Log and encoder.log. Other verification differences still prevent automatic replacement.
- **Accept Retained Output** works for completed HandBrake encodes that reached verification. The dialog shows fresh verification results before asking whether to replace the original. Accepting keeps the original as a backup. An existing backup blocks acceptance. Partial output from a HandBrake failure or an interrupted run can be compared or deleted.
- Older retained outputs can be accepted when their .job.json identifies a verification failure for that exact file. Older records lack the original encode settings, so reverification uses the current Encoder settings and per-file audio choice. Keep the sidecar beside the retained MKV.
- **Clean Folder** processes the selected folder independently of the Cleaner list. **Clean Selected**, **Clean All**, and **Clean This File** use the options currently visible. An earlier Cleaner result does not prevent another pass.
- A waiting MKV can be cleaned during another encode. Cleaner reserves the affected paths while it works and releases them back to the existing Encoder queue. Files busy in Encoder are skipped in manually started Cleaner batches. **Clean After Encode** can still process the replacement when it finishes.

## Windows test checklist

1. Extract the ZIP and run `build.bat`. Open `dist\MKVEncoderQ.exe` and confirm the title shows 6.0.5.
2. For a retained Babylon Berlin episode, keep the original, retained MKV and .job.json together. In Encoder, right-click the original and choose **Play Both - Compare**. Choose **Accept Retained Output**, review the results and first choose **No**. Confirm both files still exist. Repeat and choose **Yes**, then check that the retained video is now the final MKV and the original is in the backup.
3. Re-encode a copy of a Babylon episode. A subtitle language difference should appear as a warning and the job should complete if the other checks pass. Play or inspect the finished file to check the tracks you care about.
4. While encoding a different video, clean a waiting MKV using **Default** and **Forced** under **Clear subtitle flags**. Verify the flags in MKVToolNix. Repeat using **Clean Selected** or **Clean All**, and confirm the waiting file keeps its Encoder position and audio setting. Also try **Clean Folder** with rows already in the Cleaner list.
5. If Titanic fails again during sampling, save encoder.log. The log records the sample timestamp, command, exit status and full HandBrake output.
