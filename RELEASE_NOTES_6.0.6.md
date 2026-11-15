# MKV Encoder Q 6.0.6

On the Encoder tab, check **Cleanup** to see videos with an associated backup or retained encode. Cleanup takes precedence over the Codec, Resolution, and Rating filters. Uncheck it to return to those filters. Turn Cleanup off before starting the encode queue.

The Backup column shows **Present** for matching backups, including a uniquely matched original extension after an in-place conversion to MKV. Retained outputs are labeled on the video row. Use the right-click menu to review or delete them. A retained output that reached verification can still be accepted after a fresh check.

Cleanup refreshes when the folder scan finishes. Files need an associated video in the current inventory. For separate output folders, retained encodes with ownership records can be found from their original source rows. Backups whose completed video is outside the inventory may require a separate folder review.

## Windows test checklist

1. In Encoder, choose **Wasteful** and scan a folder with a normal video, an MKV with a backup, and a failed encode with a retained output.
2. Check **Cleanup**. Confirm it shows the backup and retained-output rows even if their ratings differ. Restart the app, scan the same folder, and check **Cleanup** again.
3. Test a converted MKV with a same-folder MP4 or AVI backup. Confirm the Backup column shows **Present** and the delete confirmation identifies the original backup path.
4. Use the retained-output menu to compare or delete. Confirm a row disappears after its last leftover is deleted, and confirm its video still plays.
5. Uncheck **Cleanup** and confirm **Wasteful** is still selected.
