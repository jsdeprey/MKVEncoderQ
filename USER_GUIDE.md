# MKV Encoder Q User Guide

MKV Encoder Q combines three independent tools in one Windows application:

- **Encoder** — inspects videos, estimates possible savings, and converts approved files to H.265/HEVC.
- **Cleaner** — uses MKVToolNix to clean selected metadata from MKV files.
- **Rename** — previews and applies safe single-file or batch filename changes.

Each tab has its own input folder, settings, queue, and controls. The Encoder and Cleaner may work at the same time. Files sent from Encoder to Cleaner wait when their encoded output is not ready yet.

## Required programs

The application expects these external command-line programs:

| Feature | Required program |
|---|---|
| Inspect media | `ffprobe.exe` |
| Sample and encode video | `HandBrakeCLI.exe` |
| Clean MKV files | `mkvmerge.exe` and `mkvpropedit.exe` from MKVToolNix |

`HandBrakeCLI.exe` and `ffprobe.exe` may be copied beside `MKVEncoderQ.exe` or configured in Encoder Settings. The Cleaner can find a normal MKVToolNix installation or use a configured folder. A missing MKVToolNix installation disables Cleaner features without disabling Encoder or Rename.

## Encoder workflow

1. Choose an input folder or use the configured default.
2. Decide whether newly found files should be checked using **New files selected**.
3. Optionally enable **Inspect files when added** to build a sortable media inventory first.
4. Use the filters and sorting controls to find candidates.
5. Click the **Encode All** heading to check or clear the eligible rows currently visible through the filters. Hidden rows are unselected.
6. Check the individual files you want and press **Start**.
7. After each completion, right-click it and select **Play Video**.
8. When satisfied, right-click the completed row and select **Delete Backup File** if backups are enabled.

If **Start** is pressed while a directory inventory is still running, the
request is retained and begins only after inventory finishes. When **Inspect
files first** is enabled, Start also waits for the complete inspection cycle
and applies the selected media filters before claiming the first file. Turn
inspection off when you intentionally want Start to proceed after inventory
without waiting for new media probes; files lacking cached details cannot
match an active media filter and remain excluded.

The inspection gate covers the running probe worker, the file currently being
probed, queued files, results waiting to be applied to the interface, and the
final filter handoff. This prevents Start from seeing a brief false-idle state
between those phases.

The Codec, Resolution, Rating, and Sort controls lock as soon as **Start** is
accepted, including while it is waiting for inventory or inspection. Choose
the desired filters before pressing Start. Use **Stop All** to cancel a pending
Start and unlock the controls.

If a periodic Active Directory rescan is underway when Start is pressed, the
background rescan is cancelled and Start uses the last completed inventory.
Cancelling a partial rescan does not mark unvisited files as missing.

After stopping a session, changing the input folder starts a separate inventory
view. Rows hidden by the old folder's filters, along with its saved row order
and missing-file display state, are discarded before the new folder is loaded.
You may press Start during the new folder's inventory or inspection; the request
will resume after that folder's final filter pass completes.

At the end of inspection, the status briefly reports **applying filters**. A
pending Start resumes automatically only after that final filter pass finishes;
**Complete** means the queue is ready to begin.

Inspection results remain part of the active inspection cycle until the
interface has consumed them and updated the corresponding rows. If files are
discovered during that handoff, they join the same cycle rather than starting
a competing inspection worker. Background directory scanning pauses through
this complete inspection-and-filter handoff so it cannot keep Start waiting.

Immediately before the worker starts, the visible Encode checkboxes are copied
into the processing queue and every hidden row is excluded. The Encoder log
records how many visible files were accepted. **Stop All** may be used while
Start is waiting for inventory or inspection; it cancels that pending request
without starting later.

### A practical 20-file NAS batch

Set **New files selected** to OFF, or click **Encode All** until all eligible files are unchecked. Inspect and sort the folder, select approximately 20 candidates, and start the queue. Review completed outputs with **Play Video**, delete each approved backup, and then select the next group. Filters are locked during encoding so review rows do not move unexpectedly.

Sampling, encoding, and progress updates preserve the file's current visible
position. A file remains where the selected sort mode or manual drag order put
it instead of moving to the bottom when its status changes.

For fresh installations, **Inspect files when added** starts ON. Existing
installations retain their saved choice. Start, Stop All, Pause/Resume, and
Cancel File share a colored marker that moves beneath the current action as
soon as the click is accepted. In a filtered library, **Encode All** affects
only matching visible rows and explicitly excludes hidden rows from processing.

## Directory scanning and queue selection

**Active Directory Scanning** controls continued discovery, not encoding:

- OFF — the selected/default folder is scanned once and the populated list is
  retained without repeatedly walking the directory tree. Start processes that
  list.
- ON — the initial scan works the same way, then the folder continues to be
  checked for new, moved, or removed files.

Large directory trees use two distinct stages. First, the program builds the
complete recursive inventory. The status reports top-level folders immediately,
then folders scanned, all filesystem files checked (including subtitles and
sidecars), and supported videos found. Rows are displayed in small acknowledged
batches so thousands of updates cannot overwhelm the interface. An exact
percentage is not possible during this first stage because the recursive folder
total is not known until traversal reaches the end.

The separate **Directory inventory** progress bar uses the quickly available
top-level folder count as an estimate. This is especially representative for a
library organized as one movie folder per video. Nested artwork or season
folders can make the estimate imperfect, so final folder, file, and supported
video totals replace it when traversal finishes. The Active Directory Scanning
line reports only whether repeated scans are enabled.

Inventory uses an amber progress bar and has its own **Cancel Inventory**
button. Media inspection remains visibly waiting, with its Cancel button
disabled, until inventory is complete. Cancelling inventory stops discovery
without deleting or modifying any video.

After inventory is complete, optional media inspection begins with a fixed video
total. Its progress therefore remains a determinate **X of Y**, and the final
counts stay visible. Inventory and media inspection never run at the same time.
Continued scans respect the configured rescan interval;
while a repeat inventory runs, the last complete total remains visible until the
replacement inventory is ready.

Repeated scanning automatically becomes gentler as the library grows. Small
folders retain the configured interval. Inventories with at least 250 items wait
at least one minute, 1,000 items wait at least five minutes, and 3,000 items wait
at least fifteen minutes between scans. A scan that takes unusually long also
extends the next delay. Large repeat scans yield between directories and are
throttled further while encoding so background discovery does not compete with
HandBrake, the interface, or a NAS. Automatic rescans never overlap initial
inventory or media inspection. An unchanged rescan updates only the small Active
Directory Scanning status and leaves the completed inventory and inspection
summaries steady.

The Active Directory Scanning line identifies **Normal mode** or
**Large-library mode**, shows the approximate time until the next scan and the
last completed scan, and notes when background scanning is throttled during an
encode. The countdown updates at a restrained rate to avoid distracting UI
flicker. When Active Directory Scanning is enabled, **Scan Now** requests one
immediate scan without changing the adaptive interval used afterward. It is
temporarily unavailable while a directory scan is already in progress,
preventing overlapping scans.

**New files selected** controls the default Encode checkbox for newly discovered or dropped files. When media filters are active, a new file remains excluded until inspection confirms that it matches those filters. Matching files are then selected automatically; nonmatching files remain excluded.

The **Encode All** heading changes eligible rows visible through the current media filters and unchecks hidden rows. **Start** repeats that safety check so an invisible row cannot encode because it was selected before the filter was applied. **Force All** continues to control force-analysis decisions, but a hidden unselected file is not encoded.

Above the list, **Files: total / filtered / selected** shows the complete queue,
the number currently visible under the media filters, and the number of visible
files currently checked for encoding.

Dragging the handle at the left of eligible rows changes queue order. Completed and active rows are protected. Filtering and sorting are disabled during an active encoding session to keep the displayed order stable.

## Media inspection

**Inspect files when added** launches one background `ffprobe` worker. It reads existing container metadata; it does not decode, sample, or encode the video. One worker is used to avoid overwhelming a NAS.

The inspection display shows the current file, completed count, failures, progress, and estimated remaining time. It can be cancelled independently of encoding. Results are cached using full path, file size, and modification time. A changed file is inspected again automatically.

The inspection summary always retains the discovered, inspected, succeeded,
and failed totals. If inspection temporarily catches up while directory
discovery is still running, it says so instead of incorrectly reporting that
the full inspection is complete. Final totals remain visible after completion.

Inspection supplies the Media and Rating columns and enables filters for codec, resolution, and rating. The list can be sorted by size, rating, or filename. If automatic inspection is off, Media and Rating are filled naturally when each file reaches normal Encoder analysis.

### H.264 WASTEFUL rating

For H.264 only, the program divides video bitrate by frame pixels:

`bitrate density = video bitrate / (width × height)`

The file is marked **WASTEFUL** above these thresholds:

| Resolution category | Threshold |
|---|---:|
| 720p, width at least 1280 | 0.15 bits/second per pixel |
| 1080p, width at least 1920 | 0.20 bits/second per pixel |
| 4K, width at least 3840 | 0.25 bits/second per pixel |

This is a candidate flag, not proof that the video should be encoded. Grain, noise, motion, rain, smoke, water, and other complex content can legitimately require more bitrate. Normal sampling still makes the encode/skip decision.

### H.265 REVIEW SIZE rating

H.265/HEVC files are not called Wasteful. Instead, an unusually large file is marked **REVIEW SIZE** using file size, duration, and resolution:

| Resolution category | Two-hour review threshold |
|---|---:|
| SD | 1.5 GiB |
| 720p | 3 GiB |
| 1080p | 5 GiB |
| 4K | 15 GiB |

The limit scales with duration. For example, the 1080p threshold is about 2.5 GiB for one hour, 5 GiB for two hours, and 7.5 GiB for three hours. **REVIEW SIZE** never checks or forces a file; it only helps locate files worth examining. Missing required metadata produces **UNKNOWN**.

## Sampling and projected savings

Unless Force Encode is selected, the Encoder creates short test encodes before deciding whether a file is worthwhile.

- The default is three 10-second samples.
- Progressive mode begins at widely separated positions—the midpoint, then the quarter and three-quarter points by default.
- If the video is too short, sample length is reduced so the samples fit.
- With the default **Progressively add one sample** behavior, the first analysis uses the configured sample count (three by default). Each **Reanalyze** retains those results, tests one new part of the video, and recalculates the combined estimate, up to the configured maximum (six by default).
- The new timestamps form a stable, space-filling sequence: adding sample 4 does not move or rerun samples 1–3.
- Settings can select **Use a new sample set** to restore the earlier behavior where every reanalysis uses a completely new group of timestamps.
- Changing a sampling or encoding setting starts a fresh progressive sample set so unlike results are not mixed.
- Samples use the configured resolution quality, encoder preset, bit depth, crop, denoise, and audio rules.
- Subtitles are excluded from temporary samples because they are not needed for the size estimate. Full encodes handle configured stream preservation and are verified afterward.

For each sample:

`projected full size = sample output size / sampled seconds × full duration`

The final projection uses the total bytes from all samples divided by the total sampled time. Projected savings compares that estimate with the original file size. If projected savings meets **Minimum projected savings**, the file is recommended for encoding; otherwise it is skipped.

Samples can disagree substantially. A quiet scene may compress extremely well, while grain, action, smoke, water, darkness, or rapid camera movement may produce a much larger estimate. The individual sample lines help show whether the final average is representative.

During final verification, ordinary subtitle tracks must all remain present.
Embedded EIA-608/708 caption services are counted the way HandBrake exposes
them: several services contained in one video stream may become one selectable
closed-caption track. This avoids rejecting a valid output merely because
ffprobe lists CC1–CC4 separately while HandBrake consolidates them.

### Reanalyze and Force Encode

**Reanalyze** adds one sample with the default progressive mode. The Action column stays direct—**Reanalyze**, **Queued**, or **Reanalyzing...**—while the sampling log reports the previous, new, and maximum sample counts. Multiple reanalyses may be queued while another file is encoding. The row stays in place while its reanalysis keeps internal priority.

**Force Encode** skips the sample-based savings decision but still probes the file and applies safety checks. It should be used when you have already decided to encode regardless of projected savings. Dolby Vision Profile 5 remains protected because it cannot be safely converted by this workflow.

The final **File Details...** item in the Encoder right-click menu opens a selectable summary of the row, including its full path, queue state, size and projections, inspection results, codec, resolution, bitrate, duration, rating, completed output, and any failure or inspection error. A completed output that fails verification is retained for inspection; File Details reports its exact path, existence, size, timestamp, and complete error while the table displays **Failed — see File Details**. A later successful retry removes that superseded retained output. Cancelling an encode immediately removes its incomplete temporary file. Use **Copy All** to place the complete summary on the clipboard.

## Important Encoder settings

- **Minimum projected savings** — required estimated reduction before normal encoding. Default: 10%.
- **Number of samples** and **Seconds per sample** — more or longer samples can improve representation but take longer.
- **Quality RF by resolution** — lower RF means higher quality and generally larger output.
- **Encoder preset** — slower presets can improve compression efficiency but take longer.
- **Denoise** — may improve compression on noisy sources, but strong filtering can remove real detail.
- **Audio streams to keep** — All, First, Default, or First English.
- **Audio passthrough codecs** — selected compatible formats are copied; unsupported formats fall back to AAC.
- **Backup originals** — preserves the original using the configured backup suffix before the verified output replaces it.
- **Output folder** — blank means the input file's folder.
- **File stability wait** — prevents partially copied files from being processed too early.

## Audio behavior

The default **First English** mode keeps the first stream tagged `eng`, `en`, or `english`. If no English tag exists, the first audio stream is kept and a warning is shown so the output is not left without audio.

Auto passthrough permits AAC, AC3, E-AC3, TrueHD, DTS, and DTS-HD by default. Other formats are converted to AAC using the original channel count and configured bitrate rules.

## Backups, completion, and verification

The encoded file is verified before the original is replaced or backed up. Verification checks important properties such as duration and stream counts. A verification failure leaves the original protected and reports the reason.

With backups enabled, **Delete Backup File** is available only on completed rows and always displays a confirmation warning. Deleting a backup is permanent. With backups disabled, completed outputs are tracked so Active Directory Scanning does not add the new output back into the queue.

When the Rename tab changes the name of a completed video, its existing `.bak`
backup is renamed in the same protected transaction. Undo restores both names.
If the proposed backup name already exists, neither file is renamed.

Completed Decision/Result text begins with the actual final output size. If the
output is more than 1 MiB larger than the source, the row is highlighted in
amber and reports both the size and percentage increase. This is a review
warning; the verified encode remains successfully Completed.

For a completed file whose backup is still present, right-click and choose
**Revert to Backup...** to restore the original. The confirmation identifies
the original and encoded paths. The encoded output is retained temporarily
until the backup restoration succeeds, so a failed restore can roll back. A
successful restore marks the row **Reverted**, changes Backup to **Restored**,
and leaves Encode unchecked. This operation permanently removes the encoded
output and cannot be undone by MKV Encoder Q.

The Encoder table uses the **Backup** column instead of a redundant per-row Progress column; current encode progress remains in the large display below the table. **[ Present ]** means the backup exists. Click that cell to check it and open the deletion warning. After deletion through the application it changes to **Deleted**. Other states include **Disabled**, **Not created** for a skipped encode, **Not found** when a completed or failed encode was expected to have one, and **—** when no backup is expected yet.

To make active work easy to locate without the old Progress column, the complete row is highlighted: blue while sampling, green while encoding, and amber while paused. The Action column also shows the matching active state.

## Cleaner tab

The Cleaner works only on MKV files and uses MKVToolNix. Files can be added from its own folder, drag-and-drop, or the Encoder right-click menu. **Send All Files in This Directory to Cleaner** adds every eligible Encoder row from the clicked file's exact directory after confirmation; filtered-out rows are included, missing rows are excluded, and subdirectories are not included. A file still waiting, sampling, or encoding appears in Cleaner as waiting for its encoded output. Completed files can be cleaned immediately, even while Encoder works on other files.

The Cleaner page has separate **Video**, **Audio**, and **Subtitles** checkboxes under **Remove track names**. These remove only the optional display name attached to the selected track types. Language codes, codecs, and the actual video, audio, and subtitle streams are preserved.

Choose **Clean Again** to inspect the selected file again using the one-run
checkboxes currently shown on the Cleaner tab. This allows another cleanup
pass after enabling an option such as **Video** track-name removal. **Remove
All** clears every Cleaner row; if queued or waiting requests exist, the app
asks before cancelling them. It never deletes video files.

**Clean Selected** cleans or re-cleans the highlighted available MKV files.
**Clean All** confirms the count and cleans or re-cleans every currently
available MKV in the Cleaner list. Both use the one-run options currently
visible above the list. Files still waiting for an encode output remain queued
and are not treated as failures.

**Clear subtitle flags: Default** and **Forced** are independent. Each option
clears that flag from every subtitle track in the files cleaned during that
run. Selecting both turns both flags off without removing subtitle tracks. The
Cleaner reinspects the file for **Clean Again**, so a previously cleaned file
is processed again if any requested flag is still enabled; it reports Already
Clean only when the requested changes are already satisfied. These operations
normally use `mkvpropedit` to modify the MKV header in place without remuxing or
re-encoding.

The Encoder's **Clean After Encode** switch applies to every file that finishes
successfully while the switch is enabled, including files already listed before
it was turned on. Skipped, failed, and cancelled encodes are not sent. The
Encoder log confirms each Cleaner handoff, automatic handoff does not change
tabs, and the switch remembers its state in `config.json` (on by default in a
new installation). Existing installations retain their saved choice.

**Subtitles off unless audio is marked foreign** clears the default-track flag
from subtitle tracks when the primary audio is English or its language
is untagged/unknown. With English audio, an explicitly foreign forced subtitle
has both Default and Forced cleared, while an English forced subtitle is left
alone as intentional. Only audio explicitly
tagged as a foreign language retains its existing subtitle defaults. The checkbox applies to
the current run; its starting value is controlled in Cleaner Settings. This is
normally an in-place MKV header edit and does not remux or re-encode the file.

Main-page changes apply to one cleaning run only and then return to the defaults configured under Cleaner Settings.

Use the Cleaner preview/status information before applying operations. Encoder and Cleaner have separate default folders and separate Settings buttons.

## Rename tab

Files can be added individually, as a folder, through drag-and-drop, or from Encoder/Cleaner right-click menus. Busy files are excluded with a warning rather than renamed underneath an active process.

Right-click a Rename row to open its containing folder, copy its filename, or
copy only the containing directory's name. These commands use the row that was
right-clicked and do not modify the file.

Available operations include:

- Literal Find/Replace with optional case sensitivity
- Prefix and suffix
- Remove a selected number of characters from the beginning or end
- Case conversion
- Preserve `720p`, `1080p`, and `2160p` capitalization during case conversion
- Normalize `S01E01`
- Convert `1x02` to `S01E02`
- Sequential numbering
- Convert spaces to periods

The proposed filename column updates live. **Rename Checked Files** applies the preview to all checked rows; **Rename Selected File** changes only the highlighted row. The complete batch is validated for collisions, invalid Windows names, and missing files before anything is moved. The most recent successful batch can be undone.

When several options are enabled, operations are applied in this general order: character removal, Find/Replace, prefix/suffix, case conversion, episode normalization, numbering, then Spaces to dots. This allows Find/Replace and Spaces to dots to work together predictably.

## Common statuses

| Status | Meaning |
|---|---|
| Not selected | File is listed but its Encode checkbox is off |
| Waiting / Waiting to scan | Eligible and awaiting processing |
| Checking file | Waiting for file-stability confirmation |
| Sampling | Test encodes are estimating savings |
| Encoding | HandBrakeCLI is creating the full output |
| Paused | Current HandBrake encode is paused |
| Completed | Output passed verification |
| Skipped | Analysis did not recommend encoding or a protected condition was found |
| Failed | Inspection, sampling, encoding, or verification returned an error |
| Missing | The represented file is no longer present at its expected path |

Skipped, failed, and cancelled files can be reanalyzed or force-encoded when appropriate. Missing rows are disabled until the file returns or the row is removed. Automatic directory scans and **Scan Now** update newly removed files to **Missing** after the scan completes.

### Action column

The Action column provides a quick visual state and the most useful row action:

- `[ Sampling... ]`, `[ Encoding... ]`, and `[ Paused ]` identify the active file and are informational.
- `[ Play ]` opens a completed file's verified output in the default media player.
- `[ Reanalyze ]` reruns sampling for a skipped, failed, or cancelled file.
- `[ Queued ]` identifies a pending reanalysis and can be clicked again to cancel that request.
- A dash means no separate quick action is needed for that row.

## Building the Windows executables

Run `build.bat` from the extracted release folder. It installs the required Python build packages and creates:

- `dist\MKVEncoderQ.exe`
- `dist\MKVEncoderQ-CLI.exe`
- `dist\USER_GUIDE.html`

Keep required external tools in their configured locations or beside the executable.

