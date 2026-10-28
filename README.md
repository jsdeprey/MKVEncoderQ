# MKV Encoder Q 5.1

Created by Jeff Deprey.

MKV Encoder Q 5.1 combines the Encoder, MKV Cleaner, and batch Rename
applications in one Windows interface. The **Encoder**, **Cleaner**, and **Rename** tabs retain their own input
folders, Settings buttons, configurations, queues, progress displays, and
controls.

Version 5.1.60 fixes only the Encoder's --no-backup commit ordering.
Verified same-path MKV output replaces the original directly. For MP4-to-MKV
or a separate output folder, the verified output is committed before the
original is deleted. Regression tests cover all four source/destination
combinations, injected encode/probe/verification/commit/deletion failures,
cancellation, and existing backup behavior. All other behavior is unchanged.

Version 5.1.59 separates an idle Active Directory Scanning watcher from active
Encoder work in the interface. When the queue becomes empty, the Start control
now changes from green **Running** to gray **Waiting**, the status bar reports
**Watching for new files**, and the active-control marker clears. Sampling or
encoding a newly discovered file restores the normal **Running** indicators.
The existing Stop/close protection continues to distinguish idle monitoring
from actual sampling or encoding.

Version 5.1.58 corrects the Cleaner tab's vertical layout so its seven-line
**Activity Log** remains fully visible at common 1080p window sizes. The Found
Files and Worker Status tables now use smaller requested minimum heights while
continuing to expand when additional vertical space is available.

Version 5.1.57 adds matching compact, seven-line **Activity Log** panels to the
Cleaner and Rename tabs. Cleaner moves Worker Status upward and records
discovery, inspection decisions, cleanup methods, read-only corrections,
skips, failures, and run summaries. Rename records file additions and removals,
each old-to-new filename, failures, and undo operations. Both logs retain their
tab's session history, scroll automatically, support text selection and copy,
and follow the current light or dark appearance.

Version 5.1.56 hardens Rename and Cleaner behavior on NAS/SMB storage. A file
renamed while a long directory inventory is running is no longer incorrectly
marked Missing when that older inventory completes. Rename now records whether
each source was writable and restores writability if the NAS unexpectedly adds
the Read-only attribute during the temporary rename; intentionally read-only
sources remain read-only. Cleaner also detects and clears Read-only before an
actual cleanup, reports the correction in the interface and run log, and gives
a specific preflight error when share permissions prevent the change.

Version 5.1.55 adds two independent filename-marker checkboxes under Encoder
**Settings → Appearance**. The orange diamond can be enabled separately for
filenames containing spaces and filenames missing both a four-digit year and
an episode identifier; both remain enabled by default, and saving refreshes
the current list immediately. A new **Home** button above **Remove Missing
Files** returns the stopped Encoder to its configured default input directory,
safely retires old-folder inventory and inspection work, and starts a fresh
directory inventory. Home is unavailable while a session is starting or
running.

Version 5.1.54 makes manually arranged Encoder rows authoritative. An order
created with the drag handles or Move commands now survives filtering, view
refreshes, and filename changes—including renaming other rows while a file is
encoding. Hidden filtered rows retain their places in the complete stored
order. Choosing a new **Sort** option deliberately replaces the manual order,
and selecting a different input directory starts a fresh order.

Version 5.1.53 streamlines Cleaner handoffs and repeated cleanup. The Encoder
right-click menu can send every eligible video row from the clicked file's
exact directory to Cleaner, including filtered-out rows, while leaving missing
files excluded and pending encodes safely waiting for their MKV outputs. The
Cleaner tab adds **Clean Selected** and confirmed **Clean All** buttons beside
**Remove All**; both reuse the visible one-run cleanup options, and unavailable
files waiting for encoding remain queued.

Version 5.1.52 refines the filename-format indicator so standard television
episode identifiers such as `S01E01` do not require a four-digit year; spaces
remain an independent reason for the small orange diamond. **Clean After
Encode** now defaults ON for fresh installations while existing saved choices
remain unchanged. The Rename-tab right-click menu adds **Open File Location**,
**Copy Filename**, and **Copy Directory Name** for the clicked row.

Version 5.1.51 adds separate Cleaner options to clear the **Default** and
**Forced** flags from every subtitle track. These are fast in-place MKV header
edits with separate saved defaults and per-run checkboxes. **Clean Again**
reinspects current metadata, so a previously cleaned file runs again whenever
one of the requested flags is still enabled and skips only when no requested
change remains. This release also gives more width to Decision/Result, keeps
fixed-size Encoder columns compact, marks filenames containing spaces or
missing a four-digit year with a small orange diamond, and extends the existing
backup deletion command to all eligible selected rows with one confirmation.
The reported Encode All selection issue is intentionally not changed in this
release.

Version 5.1.50 is a stability and state-ownership release for large filtered
inventories. The complete directory inventory now remains authoritative while
filters control only visibility and eligible selection. Full and incremental
filter updates share one transition path, **Encode All** and **Start** use the
live inventory selection instead of rendered-row state, and hidden files cannot
enter the encode snapshot. Changing filters during inspection can no longer
lose detached rows; clearing a filter restores the complete inventory and keeps
file counts, checkboxes, and Decision/Result text synchronized. Rename now
reapplies the active view after migrating a path, and retired inspection workers
cannot deliver results into a newly selected directory session. Regression
coverage includes a 3,098-file inspection/filter cycle and cross-directory
worker retirement.

Version 5.1.49 introduces one centralized new-directory session reset. Changing
the input root after stopping now clears the previous directory's rows,
ordering, selections, Force/Clean and reanalysis queues, inspection work,
missing/failure display state, dropped-file entries, progress, and pending UI
events as one transaction. Settings, filters, switches, the activity log,
inspection cache, and completed-output safety history remain available.
Stopping and restarting the same directory does not reset its session.

Version 5.1.48 fixes stale selection display when switching from a processed
folder to a parent folder containing the same files. Per-folder checkbox state
is now cleared when the inventory root changes, keeping the table consistent
with the new live queue and restoring reliable **Encode All** behavior for UNC
paths. Active media filters are also applied incrementally during inventory and
inspection: uninspected rows remain hidden under media-dependent filters, and
matching rows appear as their inspection results arrive.

Version 5.1.47 fixes selection state being reversed by filter and inspection
updates. Rows automatically unchecked because a filter hides them are now
remembered consistently whether or not their media inspection has completed,
and are restored when a later filter makes them visible. A manual checkbox or
**Encode All** choice clears that automatic marker, so subsequent status events
cannot silently reselect a file the user unchecked.

Version 5.1.46 fixes **Encode All** failing to deselect visible files during an
active encode when the file table is scrolled down. The header now determines
its next state from the checkboxes actually displayed in the table, then
synchronizes that state to the live queue, so a second click reliably clears
the visible selection even while queue updates are arriving.

Version 5.1.45 preserves each visible file's current sorted or manually arranged
position when sampling, encoding, or other incremental status updates arrive.
Previously, refreshing a visible row reattached it at the bottom of the table;
rows are now moved only when they must be restored after being hidden by a
filter.

Version 5.1.44 closes a false-idle window that could let **Start** begin before
enabled media inspection had finished. Start now checks the inspection worker,
active file, queued files, reserved result handoffs, and overall inspection
cycle instead of depending on only one cycle flag. When **Inspect files when
added** is off, these inspection gates remain bypassed so processing can start
immediately after directory inventory.

Version 5.1.43 fixes deferred Start becoming permanently stuck after changing
from a previously processed folder to another directory. Changing directories
now removes both visible and filtered-out rows and clears the old row-order,
filter-exclusion, and missing-file display state before the new inventory is
created. The final NAS inspection can therefore apply its filter and release
the pending Start request without referencing a deleted row from the prior
folder.

Version 5.1.42 fixes the inspection handoff that could leave **Start** waiting
after the interface reported that inspection had finished. Inspection results
remain reserved until the interface consumes them, newly queued work stays in
one coordinated inspection cycle, and Active Directory scanning waits until
inspection, filter application, and pending-Start handoff are fully complete.
This release also retains the 5.1.41 protection against partial scans marking
unvisited files as **Missing**.

Version 5.1.41 fixes Start colliding with a periodic Active Directory rescan.
A quiet background rescan no longer delays Start; it is cancelled and encoding
uses the last completed inventory. A cancelled partial directory walk can no
longer mark files it did not reach as **Missing**, which had disabled their
Encode checkboxes and left Start waiting after inspection.

Version 5.1.40 locks the media filters immediately when **Start** is accepted,
including while Start is waiting for directory inventory or media inspection.
This prevents changing a filter during the pending-Start interval from making
the visible checked rows differ from the queue that will actually run. **Stop
All** still cancels the pending request and unlocks the filters.

Version 5.1.39 fixes the filtered checkbox/queue mismatch visible after a large
NAS inspection. Immediately before processing starts, the visible Encode
checkboxes are synchronized into the real queue and hidden rows are excluded;
the log records the number accepted. This prevents Running from incorrectly
entering Queue idle while a visible checked row still says Waiting. **Stop
All** can also cancel a deferred Start, and its scheduled callback cannot
restart the cancelled request.

Version 5.1.38 fixes deferred Start remaining stuck after inspection visibly
completed. The processed completion event now authoritatively releases the
inspection gate instead of waiting on the worker thread's final shutdown. The
pending Start resumes immediately after filters are applied, and the inspection
line distinguishes **applying filters** from fully complete.

Version 5.1.37 makes deferred Start wait for the complete directory inventory
and, when **Inspect files when added** is enabled, the complete media-inspection
cycle before encoding begins. The finished inventory is filtered before the
queue is claimed, preventing previously selected hidden files from starting.
With inspection disabled, Start waits only for inventory and then uses the
visible files supported by existing or cached media information.

Version 5.1.36 makes the active media filter an encode boundary instead of
only a visual filter. **Encode All** now selects visible matching rows and
unselects hidden rows; **Start** performs the same safety check before work
begins. With **New Files Selected** and Active Directory Scanning enabled,
newly discovered files are selected only after inspection confirms that they
match the active codec, resolution, and rating filters.

Version 5.1.35 is reconstructed directly from the verified 5.1.30 release
instead of the incorrect development checkpoint used for 5.1.31. It adds the
idle queue display and direct **Reanalyze** labels, makes **Clean After Encode**
apply reliably to every successful completion while enabled without changing
tabs, and fixes completed non-MKV-to-MKV renames so the Encoder row, queue
references, and original backup all follow the new name without rediscovery or
duplicate rows.

The Encoder scans
video libraries, estimates the space savings from HEVC conversion, and sends
approved jobs to HandBrakeCLI. It keeps the source resolution and timing and
verifies each completed MKV before touching the original.

The Cleaner tab contains the MKV Cleaner 3.2 workflow for inspecting and safely
removing selected Matroska metadata with MKVToolNix. Encoder settings remain in
`config.json`; Cleaner settings are stored independently in
`cleaner_config.json`.

Version 5.1.30 fixes **Clean Again** so it always reinspects the file with the
Cleaner checkboxes currently visible instead of reusing an earlier **Already
clean** decision. The Cleaner tab adds **Remove All** for clearing its displayed
rows and pending requests without deleting media. The Encoder tab adds a
remembered **Clean After Encode** toggle, off by default, which marks newly
added files for automatic Cleaner handoff after a successful encode without
switching away from the Encoder tab.

Version 5.1.29 completes HandBrake-aware subtitle verification: extra caption
tracks created by HandBrake are accepted, while the languages and multiplicity
of ordinary source subtitle tracks must remain represented. Cancelled encodes
now delete their partial `.video-optimizer-…mkv` files; completed outputs that
fail verification remain available for inspection and are documented in
**File Details** with their path, size, timestamp, and full error. The table
shows the compact **Failed — see File Details** result. A later successful retry
removes the superseded retained output. Cleaner now clears both Default and
Forced from explicitly foreign forced subtitles when the primary audio is
English, leaves intentional English forced subtitles unchanged, and keeps all
subtitle tracks enabled and selectable.

Version 5.1.28 fixes verification of videos containing multiple embedded
EIA-608/708 closed-caption services. ffprobe may expose CC1–CC4 as separate
subtitle streams while HandBrake consolidates them into one selectable caption
source. Verification now follows HandBrake's interpretation while continuing
to require every ordinary SRT, ASS, PGS, or VobSub track. Valid encodes such as
four SRT tracks plus four embedded caption services becoming five HandBrake
subtitle tracks are accepted; genuinely missing subtitles still fail.

Version 5.1.27 adds progressive reanalysis. The first analysis uses the normal
sample count (three by default), then each **Reanalyze** click retains those
results and tests one new, unique part of the video up to a configurable maximum
(six by default). The combined estimate is recalculated from every retained
sample. Settings can switch back to the legacy entirely-new sample set behavior.
Changing a sampling or encoding setting safely starts a new progressive set.

Version 5.1.26 keeps a file in its existing visible row when **Reanalyze** is
clicked during a running session. Reanalysis retains internal priority and
runs before ordinary waiting work, but queuing, starting, cancelling, or
finishing it no longer moves the row to the bottom of a filtered or sorted
list.

Version 5.1.25 fixes dark-mode hover colors for table headings and checkboxes;
preserves the real Decision/Result reason for skipped, failed, cancelled, and
reverted files when late inspection or inventory updates arrive; and restores
**Media inspected — Ready** after an immediate Cleaner reservation ends. A
periodic reconciliation safeguard now promotes completed Encoder outputs that
are still shown as **Waiting for Encode**, including the two-stage workflow
where the original is cleaned immediately and its encoded replacement must be
cleaned again. The Encoder table uses the shorter **Savings**, **Source size**,
and **Est. output** headers. Skipped files now report **Not created** instead of
the misleading **Not found** in the Backup column.

Version 5.1.24 allows a waiting Encoder file to be cleaned immediately without
discarding its separate clean-after-encode request. Cleaner atomically reserves
that path, Encoder continues with other eligible files, and the reservation is
released after Cleaner succeeds, fails, or is cancelled; files already
sampling, encoding, or paused remain protected. Dragging a row or using the
right-click movement commands now updates the real processing order instead of
being overwritten by the previous filter/sort order. Hidden filtered rows keep
their relative positions while visible rows follow the order shown onscreen.

Version 5.1.23 keeps every operational reference synchronized when Rename
changes a file path, including queued and staged media inspections, cached
inspection results, reanalysis state, manual-file roots, pending GUI events,
and Encoder-to-Cleaner handoffs. Only the isolated **Undo Last Rename** history
intentionally retains the former name. Changing input directories after a
stopped session now discards obsolete display events from the previous folder.
Active filename, progress, pause, stop, and completion updates receive priority
over bulk inventory traffic, and one invalid display event can no longer stop
all later interface updates while HandBrake continues running. Start also
immediately replaces stale session details with **Starting next selected
file…**.

Version 5.1.22 fixes removed files remaining visually unchanged after automatic
directory scans or **Scan Now**. The inventory already identified them as
missing internally, but idle discovery only emitted row updates for files that
were rediscovered. It now explicitly reports newly missing entries at the end
of a completed scan, so their rows become **Missing**, gray, and non-interactive.

Version 5.1.21 makes **Inspect files when added** ON by default for fresh
installations while preserving existing users' saved selection. The main
processing controls now share one colored marker that moves immediately below
Running, Stopping, Paused, or Cancelling, with matching live button text so
every click is visibly acknowledged. Start no longer appears unresponsive when
a quiet large-NAS rescan is active: it displays **Starting…**, cancels that
nonessential repeat scan, and uses the completed inventory. **Encode All** and
**Force All** update the complete in-memory queue immediately but repaint only
visible rows; filtered rows receive the correct state when shown again. This
avoids thousands of hidden Tk table updates while retaining global behavior.

Version 5.1.20 preserves the final output size, savings, elapsed time, and
larger-than-source warning in completed Encoder rows when Active Directory
Scanning refreshes the inventory; Decision/Result no longer falls back to
redundant **Completed** text. Large NAS libraries no longer pause the interface
for several seconds after a Rename operation because queue synchronization
reuses known canonical paths instead of resolving thousands of network paths.
**Scan Now** is placed before the changing scan-status text so it remains
stationary. The Cleaner adds a run-only subtitle-default option, enabled by
default and configurable in Cleaner Settings: ordinary subtitles are turned
off by default unless the primary audio is explicitly tagged as a foreign
language. English, untagged, and unknown audio use subtitle-off behavior;
explicitly foreign forced subtitles have both Default and Forced cleared, while
English forced subtitles are left unchanged as intentional. The normal path uses `mkvpropedit` for an
in-place MKV header update without remuxing or re-encoding.

Version 5.1.19 makes the requested Encoder table layout changes as a separate
checkpoint: **Action** now appears before **Backup**, and **Encode All** now
appears before **Force All**. The visible column order is centralized in one
definition, and hover/click handling resolves columns by logical name instead
of hardcoded screen positions, making later rearrangement safer without
changing the row-data format.

Version 5.1.18 expands the Active Directory Scanning status without changing
its adaptive timing. It identifies Normal or Large-library mode, shows the
approximate time until the next scan and the last completed scan, and reports
when background scanning is throttled during encoding. A permanently visible
**Scan Now** button requests an immediate non-overlapping directory scan while
leaving the later automatic schedule unchanged.

Version 5.1.17 keeps Cleaner and Rename responsive while a large Encoder
inventory or media inspection continues. Waiting rows no longer perform
per-file backup checks against the NAS, routine row notifications reuse their
already canonical queue paths, and Encoder list painting receives a smaller
time slice while another tab is active. **Send to Rename** now switches tabs
immediately and defers its trusted-path handoff instead of revalidating the
same network file on Tk's UI thread.

Version 5.1.16 puts the actual completed output size first in Decision/Result.
An output more than 1 MiB larger than its source reports the size and percentage
increase and highlights the successful completed row in amber for review. The
Encoder right-click menu also adds a protected single-file **Revert to Backup**
action. It restores the original, removes the encoded output only after the
restore succeeds, rolls back a failed restore, and leaves the row marked
Reverted/Restored and safely unchecked for optional future processing. No table
columns are moved in this release.

Version 5.1.15 makes the Encoder Decision/Result text consistently follow the
current Encode selection after media inspection. An unchecked inspected file
shows **Excluded from processing**; checking it immediately restores **Media
inspected — Ready**; unchecking it restores the excluded result. Inspection
failures remain visible regardless of selection. This release is also the clean
checkpoint before the file-table column mapping is refactored.

Version 5.1.14 makes Active Directory Scanning adaptive for large libraries.
Small folders retain quick polling, while large folder/file inventories wait
longer between passes and yield between directory checks. Repeat scans are
incremental and especially gentle during encoding, never overlap initial
inventory or media inspection, and leave the completed displays steady when
nothing changed. The Encoder heading reports total, filtered, and selected file
counts. Rename transactions move an existing `.bak` with its completed video,
including undo and collision protection. The User Guide is now a formatted
local HTML page that opens without requiring a Markdown file association.

Version 5.1.13 fixes startup inventory changing the selected application tab.
Cleaner and Rename remain enabled and accessible while Encoder inventory runs,
so background updates cannot switch the user back to Encoder. Directory
inventory, optional media inspection, file-list loading, progress reporting,
and Active Directory Scanning behavior are otherwise unchanged from 5.1.11.

Version 5.1.11 hardens the large-NAS performance fixes with stress coverage for
3,500-file queues. Encoding progress, Force All, existing-list inspection, and
inventory ingestion no longer perform hidden per-file UNC path resolution.
Known queue paths are reused through the sampling/encoding handoff, so frequent
HandBrake progress messages cannot trigger network filesystem lookups. The
release was regression-tested across selection, filtering, ordering, queue
claims, inspection handoff, and a 3,039-folder/3,497-file inventory.

Version 5.1.10 fixes long interface freezes when selecting individual files or
starting and monitoring an encode in a large filtered NAS inventory. Global
Encode All, Force All, and Clean All heading states now come from the in-memory
queue instead of making thousands of Tk table calls. Already known UNC paths
are no longer resolved against the NAS again for checkbox, ordering,
inspection, status, or progress updates. Routine HandBrake percentage updates
now update only the active row instead of refreshing every global control and
checking the backup path on every progress event. The inventory-to-inspection
handoff reuses canonical paths, and the Files counter reserves a stable width
so its temporary scanning message no longer shifts neighboring controls.

Version 5.1.9 fixes the long pause and Windows **Not Responding** state that
could occur immediately after Start in a large NAS library. Start now reuses
the completed directory inventory instead of walking the entire tree again and
repainting thousands of unchanged rows while the first file begins sampling.
With Active Directory Scanning enabled, later scans begin after the configured
interval and update only rows that actually changed. With it disabled, no
second directory pass is performed. Cleaner and Rename remain temporarily
unavailable only while the initial inventory is loading, and the displayed
folder total excludes the selected root folder itself.

Version 5.1.8 prevents displayed inventory rows from independently bypassing
the inventory/inspection barrier. The initial top-level folder estimate remains
visible throughout discovery instead of being overwritten by each 25-row list
batch. The permanent final line clearly distinguishes folders, all filesystem
files, and supported videos, then inspection begins with the exact supported-
video total and current filename.
The inventory has its own amber progress bar and Cancel Inventory control;
Cancel Inspection remains disabled until ffprobe work actually starts. Large
inventory batches no longer trigger repeated whole-table heading and missing-
file scans, and directory-entry metadata is reused instead of requesting a
second NAS file-stat operation for every discovered video.

Version 5.1.7 moves directory-inventory information to its own clearly labeled
row and leaves Active Directory Scanning as a simple ON/OFF status. The fast
base-directory folder count supplies an immediate estimate and an approximate
inventory progress bar while exact recursive folder, filesystem-file, and video
counts continue to grow. A hard synchronization barrier prevents media
inspection and directory inventory from overlapping, including repeat scans,
which avoids competing NAS activity and unstable inspection totals.
When Active Directory Scanning is OFF, its extra status text remains blank;
one-time discovery progress appears only in the dedicated inventory row.

Version 5.1.6 uses a two-stage large-library workflow. It first builds the
complete recursive inventory and reports top-level folders, folders scanned,
all filesystem files checked, and supported videos found. Only after that total
is fixed does optional media inspection begin, so its permanent **X of Y**
counter cannot exceed or regress below the discovered total. Inventory rows are
published in small acknowledged batches, successful rows say **Media inspected
— Ready**, full-table refresh work is throttled, repeat scans retain the prior
total until their replacement inventory completes, and closing cancels both
discovery and inspection promptly. Active Directory Scanning OFF still performs
one complete initial inventory and then remains idle.

Version 5.1.5 synchronizes large-directory discovery, list display, and media
inspection. Each 25-file discovery batch now reaches the visible list before
the next batch is released, preventing thousands of hidden UI updates from
building up. Inspection stays alive but sleeps while waiting for later batches,
and its permanent summary reports values such as **75 of 2,743 inspected** plus
succeeded and failed totals. The Files heading retains the discovered total.
Every selected/default directory receives one initial scan even when Active
Directory Scanning is off; that switch now controls repeat scanning only.

Version 5.1.4 makes large NAS directory discovery incremental and responsive.
Files are published to the Encoder list in small batches while traversal is
still underway, with a live discovered-file count and a clear completion
message. Media inspection can begin before every subfolder has been visited.
The interface yields between queued row-update groups so lists of thousands of
files do not monopolize the Windows event loop, and continued Active Directory
Scanning respects the configured rescan interval instead of immediately
walking the same large tree again.

Version 5.1.3 replaces the Encoder's redundant per-row Progress column with a
clickable **Backup** status column. **[ Present ]** verifies the backup and
opens the protected deletion confirmation; Deleted, Disabled, and Not found
states update without continuously polling NAS shares. Sampling, Encoding, and
Paused rows now use strong blue, green, and amber highlighting, and the Action
column shows matching live states plus **[ Play ]** for completed output. The
right-click menu adds a themed, selectable **File Details** window with Copy
All. Filter locking is explicitly labeled, the Encoder switches and scanning
status are more compact, and media-inspection progress has a shorter distinct
blue meter. Rename no longer uses a leading-dot temporary filename, preventing
some Samba/NAS systems from leaving renamed files hidden in Windows. Cleaner
adds independent Video, Audio, and Subtitle track-name removal controls. Main
page choices apply to one run and then revert to the saved Cleaner Settings
defaults; language and playback-related track properties remain protected.

Version 5.1.2 adds duration-adjusted **REVIEW SIZE** ratings for unusually
large H.265/HEVC files without automatically selecting or forcing them. The
Rating filter includes Review Size. The Rename tab adds **Spaces to dots**,
which can be combined with Find/Replace and other transformations, and places
**Rename Checked Files** in the more prominent button position. **Play Video**
now appears by itself at the top of the Encoder context menu. A comprehensive
local `USER_GUIDE.html` documents installation, inspection ratings, filtering,
sampling and projections, settings, audio, backups, Cleaner handoff, Rename,
statuses, and the 20-file NAS review workflow. The Encoder's **User Guide**
button opens it in the default browser, and the Windows build copies it beside
the executable. `USER_GUIDE.md` remains the editable documentation source.

Version 5.1.1 fixes the global **Encode All** and **Force All** controls when
media filters are active. Checking or clearing either heading now updates every
eligible queue row, including rows temporarily hidden by a codec, resolution,
or rating filter. This prevents a hidden previously checked file from starting
unexpectedly after the visible filtered rows have been cleared.

Version 5.1.0 adds an optional **Inspect files when added** inventory pass for
large batch workflows. A single background ffprobe worker fills compact Media
and Rating columns, shows file/count/progress/failed/ETA status, supports
cancellation, and caches results by path, size, and modification time. Encoder
rows can be filtered by codec, resolution, and H.264 waste rating, then sorted
by size, waste rating, or filename. The default remains off, so existing
discovery, sampling, and encoding behavior is unchanged; media details also
populate naturally as files reach their normal analysis step. Filters are
locked during encoding to keep completed and review rows stable. The visible
Clean column and Clean Selected button are replaced by right-click Cleaner
handoff, including a confirmed Send All Eligible command. The menu also adds
Inspect Selected, Select All Rows, and Clear Row Selection; Ctrl+A selects all
visible rows. Existing New files selected and Encode All controls support
unchecked, user-sized batches such as groups of 20, while Play Video and Delete
Backup File remain available for the review cycle.

Version 5.0.3 fixes progressively slower Encoder drag-and-drop when files are
added individually from a NAS. Idle directory scans no longer resend unchanged
rows through the interface, and global list controls are refreshed once per
event batch instead of once per file. Existing discovery, sampling, encoding,
and queue behavior is unchanged.

Version 5.0.2 adds **Remove Missing Files** beside **Clean Selected** on the
Encoder tab, removing only unavailable rows from the list without deleting
anything from disk. The Rename tab adds **Rename Selected File**, an explicit
**Set Default Folder...** control, and optional protection for `720p`, `1080p`,
and `2160p` during case conversion. Its right-click menu can now check,
uncheck, or remove every highlighted row. New **Remove Checked**, **Remove
Selected**, and **Remove All** buttons provide matching list-management
controls; none delete the underlying video files.

Version 5.0.1 keeps every displayed Encoder row synchronized with the file
system after renames, moves, or deletions. Waiting and finished rows—including
completed outputs—become **Missing** and non-interactive when their represented
file disappears, and recover their previous status and results if it returns.
Known paths continue being checked during a running session even when continuous
directory discovery is off. Encoder right-click actions now apply to the full
multi-selection for reanalysis, Force Encode, Encode selection, direct Cleaner
handoff, movement, Rename handoff, and list removal. Cleaner movement commands
also move selected rows as one stable block. Direct Cleaner handoff opens the
Cleaner tab immediately; unfinished files wait there for their encode output.

Version 5.0.0 adds an independent **Rename** tab with file, folder, recursive,
and drag-and-drop input. Encoder and Cleaner selections can be sent through
their right-click menus. The tab previews literal find/replace, case-sensitive
matching, prefix/suffix, beginning/end character removal, case conversion,
season/episode normalization, optional `1x02` conversion, and sequential
numbering. Checked files are validated as a complete batch before any changes;
filename swaps and case-only changes use safe temporary names, failed batches
roll back, and the most recent batch can be undone. Busy Encoder or Cleaner
files are identified and excluded only after confirmation. Successful renames
update Encoder queue state, completed outputs, Force/Encode/Clean selections,
pending Cleaner handoffs, and Cleaner rows without requiring a rescan.

Version 4.0.9 adds **Play Video** to the per-file right-click menus in both
the Encoder and Cleaner. Completed Encoder rows open their verified encoded
output, while safe unfinished rows open the original source. Playback is
disabled for a missing file and while that specific file is being sampled,
encoded, paused, cleaned, or remuxed. Videos open in the operating system's
default media player, so the application does not bundle a separate player.

Version 4.0.8 makes every manual **Reanalyze** pass use new timestamps while
keeping its samples distributed across the full video. Encoder rows now have a
right-click menu for reanalysis, Force/Encode/Clean selection, queue movement,
file-location and path tools, list removal, and guarded deletion of a completed
file's existing backup. Cleaner rows now have right-click actions for cleaning,
retrying, viewing results, queue movement, file-location and path tools, list
removal, and single or multi-file renaming. Batch rename supports literal
find-and-replace with a preview, blank replacement for removing a common
pattern, complete collision validation, and rollback. Cleaner blocks renaming
any file tracked by Encoder so queue state cannot be invalidated.

Version 4.0.7 adds Windows drag-and-drop to both queue lists. The Encoder
accepts supported video files and recursively scans dropped folders; Cleaner
accepts MKV files and recursively scans dropped folders. Dropped jobs are
deduplicated, displayed immediately, and wait for the appropriate Start button.
With no configured output directory, an externally dropped Encoder file writes
beside its source while preserving normal backup behavior. Stop All now ends an
idle directory-watching session without an incorrect active-encode confirmation;
real sampling or encoding remains protected. The Windows build bundles
`tkinterdnd2`.

Version 4.0.6 fixes a fresh-install startup crash in Dark mode where Missing-row
styling was applied before the Encoder file table had been created.

Version 4.0.5 prevents MKVToolNix inspection processes from inheriting invalid
Windows console input handles from Encoder activity. This fixes intermittent
`[WinError 50] The request is not supported` inspection failures and provides
a clearer error if `mkvmerge` itself cannot be started.

Version 4.0.4 marks unprocessed files that disappear from the scanned
directory as **Missing**. Their rows remain visible but gray and non-interactive
for the current session. If a file returns, it is checked for stability and
becomes active again; reopening the application starts with a fresh list. New
configurations default to **First English** audio and **Dark** appearance.
Closing the application automatically stops an idle directory watcher or
waiting queue, while active sampling, encoding, paused work, and Cleaner runs
remain protected.

Version 4.0.3 allows the Encoder and Cleaner tabs to be started and run
independently at the same time, matching the original separate applications.
Files sent through the Encoder's Clean controls remain coordinated: completed
files are sent immediately, while waiting or active encodes stay visible in
Cleaner until their verified MKV output is ready.

Version 4.0.2 makes Encoder cleaning requests visible immediately in the
Cleaner list. Requests waiting on an encode display the planned MKV filename,
the **Waiting for Encode** status, and the source filename. A verified output
changes to **Queued** before Cleaner claims it. Failed, skipped, or cancelled
encodes remain visible as **Not Cleaned** with the reason.

Version 4.0.1 shortens the clickable file-list headings to **Force All**,
**Encode All**, and **Clean All**, and moves the per-file Clean column to the far right beneath
the Clean Selected button. Encoder Settings now explains where
`HandBrakeCLI.exe` and `ffprobe.exe` come from and provides clearly labeled
official download links for HandBrakeCLI and FFmpeg.
If MKVToolNix is unavailable, the Cleaner tab displays a prominent warning and
its processing controls are disabled; Encoder cleaning checkboxes are also
unavailable, while all normal scanning, sampling, and encoding remain enabled.
The Force column is positioned immediately left of Encode All.
Force All, Encode All, and Clean All use compact fixed-width columns so the
remaining file information receives more horizontal space.
For non-MKV inputs selected for cleaning, Cleaner waits for a successful encode
and receives only the verified MKV output, even when its filename differs from
the source. The original and backup are never cleaned. Failed, skipped, or
cancelled encodes clear the pending cleaning request.
Completed or skipped MKV files selected from the Encoder page may be cleaned
while Encoder processes other files. Encoder and Cleaner may also be started
independently and run at the same time, matching the original separate apps.
Waiting, sampling, encoding, and paused files are not automatically handed from
Encoder to Cleaner before they are ready.
The Cleaner can clear the subtitle default-track flag when the primary audio
track is English or its language is untagged/unknown. This option is enabled by default, can be changed
for one run on the Cleaner page, and has a persistent default in Cleaner
Settings. With English audio, explicitly foreign forced subtitles have both
Default and Forced cleared, while English forced subtitles are left unchanged.
Files whose primary audio is explicitly tagged as a foreign
language retain their existing subtitle defaults. The fast path uses
`mkvpropedit` to update the MKV header
without remuxing.
Cleaning requests appear immediately in the Cleaner file list. A request that
depends on encoding displays the planned MKV filename with **Waiting for
Encode** and identifies its source file. It then changes to **Queued** when the
verified output is ready. If encoding fails, is skipped, or is cancelled, the
row remains visible as **Not Cleaned** with the reason.

Version 4.0.0 adds a per-file Clean checkbox, a Clean All heading, and a
Clean Selected button to the Encoder tab. Files still being encoded are held
until the verified output is ready, and Cleaner receives the completed MKV
rather than the original or backup. Required tools may be placed beside the application, selected in
Settings, installed normally, or available on PATH. Each Settings window shows
tool detection and official download links.

Version 3.0.14 keeps rows in their existing positions when
Force Encode is selected, while retaining forced retry priority internally.
The file list now displays the filename and parent path in separate File and
Path columns. The Force Encode heading is a clickable Force All control
with checked, unchecked, and mixed states. The GUI Settings window no longer
duplicates the global always-reencode option; per-file and Force-All controls
are available directly in the main file list. The legacy configuration value
remains supported by the command line.

Version 3.0.13 fixes a Windows startup crash caused by ordinary file-list
headings receiving an empty command option. Only the clickable Encode ALL
ON/OFF heading is now assigned a command.

Version 3.0.12 renames the per-file Always Encode heading to Force Encode and
adds an Encode checkbox to every file row. A persistent New files selected
toggle controls the default for newly discovered files. The clickable Encode
ALL ON/OFF column heading selects or excludes all eligible files and indicates
all-selected, none-selected, and mixed states. Excluded files remain visible
but are not sampled or encoded; active and completed files remain protected.

Version 3.0.11 separates directory discovery from processing. Active Directory
Scanning has a green ON / gray OFF switch and lists files from the default
input folder on opening without sampling or encoding. Start begins processing;
Stop All leaves directory watching enabled when the switch is ON. With it OFF,
Start performs one discovery pass. Play, stop, pause/resume, and red cancel
symbols accompany the control labels. Queue order and Force selections made
before Start are preserved. The filename stays prominent above live encoding
statistics, and forced jobs are placed below finished/active work and existing
priority requests, ahead of ordinary waiting files.

Version 3.0.10 allows a file already skipped by analysis to be checked for
Force Encode. It is queued without another sampling pass, moves below the
current encode with other priority work, and returns to its previous skipped
status and position if Force is unchecked before processing begins.
Version 3.0.9 adds drag handles for changing the processing order of waiting
files and a per-file Force option that skips sampling and the projected-savings
decision while retaining compatibility checks and completed-output verification.
Version 3.0.8 adds a First English audio-stream option, with a safe first-stream
fallback when no English language tag is present. It also prevents completed
no-backup outputs from being rediscovered and added to the active queue, even
when the output extension changes. Version 3.0.7 allows multiple files to be queued for reanalysis while another
file is encoding. Queued files move directly below the active encode, run in
the order selected, and return to their previous status and position if their
queued action is cancelled. Version 3.0.6 keeps the per-file Reanalyze action visible for skipped, failed,
and cancelled files even while Active Scanning remains enabled, and forces both
the completed row and large progress bar to 100.0% after a successful encode.
Version 3.0.5 prevents new HandBrakeCLI processes from inheriting an invalid
Windows input handle after pausing and resuming, so the remaining queue can
continue normally. Version 3.0.4 makes each Reanalyze action look like a compact button, updates
the main-screen Input folder immediately when its default is saved, restores
that default when the program starts, and identifies the filename before its
sample results in the bottom window. Version 3.0.3 adds a per-file Reanalyze action to the Files list. It reruns
sampling for only that unfinished or skipped file using the latest settings,
while completed encodes remain protected. Version 3.0.2 makes Start perform one complete scan when Active Scanning is
off, while leaving continuous folder watching disabled. Start now turns green
while a session is active, and Stop turns red and displays `Stopping...` as
soon as a stop request is confirmed. Version 3.0.1 added a main-screen Active
Scanning control and status indicator,
fixes dropdown colors in both themes, and reports every sample's timestamp,
size, projected savings, projected output, average, and total sampling time.
Version 3.0.0 introduced the MKV Encoder Q name and lets the CLI default to
`encode` when no command is supplied. Version 2.3.1 treated an existing backup
as evidence that a file was previously processed: the file is safely skipped
instead of reported as failed. Version 2.3
also added the current input file size to the queue and protected post-encode
verification from invalid Windows GUI console handles.

## Requirements

- Python 3.10 or newer when running from source
- `HandBrakeCLI.exe` for sample and final encodes
- `ffprobe.exe` for inspection and verification
- `mkvmerge.exe` and `mkvpropedit.exe` for Cleaner operations

Place the required programs beside `MKVEncoderQ.exe`, choose their locations
in the appropriate tab's Settings window, install them normally, or add them
to Windows `PATH`. Each Settings window reports whether its tools were found
and provides an official download link. The normal HandBrake desktop
application is separate; download the official command-line version if
`HandBrakeCLI.exe` is absent. Windows FFmpeg build packages include
`ffprobe.exe`. MKVToolNix supplies both Cleaner executables.

Source runs require `psutil` for the pause hotkey and `tkinterdnd2` for Windows
file/folder drops (`pip install -r requirements.txt`). `build.bat` bundles the
Python application and these dependencies into one `MKVEncoderQ.exe`;
HandBrakeCLI, ffprobe, and MKVToolNix remain external programs.

## Quick start

The first run creates the Encoder's `config.json`, the Cleaner's independent
`cleaner_config.json`, and their working folders beside the script or EXE.

```powershell
py MKVEncoderQ_CLI.py scan
py MKVEncoderQ_CLI.py
py MKVEncoderQ_CLI.py -i "D:\Videos" -o "E:\Optimized" --yes
py MKVEncoderQ_CLI.py -i "D:\Videos" --unattended
py MKVEncoderQ_CLI.py -i "D:\Videos" --no-backup
```

Running the CLI without a command defaults to `encode`. The explicit
`encode` command remains supported, and `scan` performs analysis without
encoding or changing files.

Backups are enabled by default. The original receives an appended `.bak`
suffix only after its temporary output passes verification. `--no-backup`
removes the original only after the same verification succeeds.

Double-click `MKVEncoderQ.exe` without arguments to open the GUI. Choose
an input directory and press **Start**. The upper queue shows every recursively
discovered file, its changing status, current file size, projected savings, projected output size,
and the encode/skip decision. Sampling results remain visible after testing.
The lower panel shows the active filename in larger text, encoder settings,
projection, audio handling, progress percentage, FPS, output size, elapsed time,
and ETA. The main meter displays its percentage in text sized to match the other
live statistics.

The GUI discovers files recursively in a separate background watcher, including
while another file is being encoded. New files appear as `Waiting to scan`;
readiness checks, probing, and benefit samples wait until the active encode
finishes. They then change to `Checking file...` and become `Waiting` only after
their size and modification time remain unchanged for the configured readiness
period. They are processed after files already
waiting. The session never processes the same path twice. **Stop All** cancels
the active HandBrake job through the protected cleanup path and ends the
watcher. **Cancel File** cancels only the active encode and continues the queue.
After Stop All finishes, Start can begin another session without closing the app.

The GUI **Pause** and **Resume** buttons send HandBrakeCLI its native `p` and
`r` controls through a dedicated hidden Windows console. This avoids the older
process-suspension method. A paused job is shown as paused only after HandBrake
acknowledges it. Cancellation sends native `q` first and uses process cleanup
only as an emergency fallback.

Live queue defaults in `config.json` are:

```json
"watch_input_directory": true,
"file_ready_seconds": 10.0,
"rescan_interval_seconds": 5.0
```

The **Settings** button opens a graphical, validated settings editor. It covers folders,
backups, video quality, sampling, audio, watching, helper-program paths, and appearance.
Choose `light` or `dark` on the Appearance tab; the theme changes immediately when saved
and remains selected after restarting. **Open JSON (advanced)** remains available. Other
saved changes apply the next time **Start** is pressed.
Existing v1.0 configuration files are migrated automatically.

## Command options

```text
-i, --input DIR          Override the configured input directory
-o, --output DIR         Write output elsewhere instead of the input directory
--config FILE            Use another JSON configuration file
--handbrake-cli PROGRAM  Use a specific HandBrakeCLI executable
--ffprobe PROGRAM        Use a specific ffprobe executable
--denoise MODE           Override denoise: off, light, medium, or strong
--crop-mode MODE         Override cropping: auto, conservative, or none
--force                  Include normally skipped files for testing
--audio-streams MODE     Select all, first, default, or first_english audio streams
--keep-metadata          Preserve common source metadata for this run
--include-review         Include REVIEW files in the approval list
--dry-run                Show commands without encoding or changing files
--no-backup              Remove originals after verification instead of backing up
--yes                    Test all files, then encode recommendations without prompts
--unattended             Test and immediately encode or skip each file without prompts
```

Use `--help`, `scan --help`, and `encode --help` for the authoritative help
text.

## Encoding behavior

- HandBrakeCLI performs both benefit samples and final encodes, so estimates
  use the same encoding engine as completed output.
- Output is H.265/HEVC 10-bit MKV by default, using the `fast` preset.
- RF defaults are 22.5 for 4K and 1080p, and 23 for 720p and SD. Values remain
  configurable in `quality_rf_by_resolution`.
- HandBrake automatic cropping and anamorphic `auto` are used by default,
  matching the GUI behavior. Cropping only removes detected borders and never
  permits enlargement beyond the source dimensions. Set `crop_mode` to `none`,
  or use `--crop-mode none`, to require the exact source storage dimensions.
  Source frame timing is retained and deinterlacing filters are disabled.
- Optional hqdn3d filtering supports `off`, `light`, `medium`, and `strong`.
- HDR dynamic metadata preservation is requested for HDR and Dolby Vision.
  Dolby Vision profile 5 remains blocked, and HDR/Dolby Vision outputs must
  pass ffprobe verification.
- `audio_passthrough_codecs` controls HandBrake's passthrough mask. Defaults
  pass AAC, AC-3, E-AC-3, TrueHD, DTS and DTS-HD. MP2, MP3, Opus, Vorbis,
  FLAC, ALAC and anything else are converted to AAC; output channel count is
  verified against the selected source track.
- `audio_stream_mode` can retain `all`, `first`, the first default audio, or
  `first_english`. If no English-tagged stream exists, `first_english` safely
  falls back to the first audio stream and reports a warning.
  track, falling back to the first track when no default flag exists.
- All embedded subtitle tracks are selected without burning and their count is
  verified. Chapters are retained for complete encodes.
- Common source metadata is disabled by default and enabled by
  `preserve_metadata` or `--keep-metadata`.
- Unsupported container data streams are omitted with a warning.
- Matroska attachments such as fonts and cover art are omitted. Each affected
  file displays a warning before encoding; embedded subtitle tracks remain.

## Benefit analysis

Each video is encoded at evenly distributed sample positions. Defaults are
three samples of 10 seconds each. The resulting sizes project the complete
output size. A recommendation requires the configured minimum savings, 10% by
default. Files below it display `SKIP`; sample failures display `ERROR` and do
not change the source.

`--unattended` tests a file, immediately encodes or skips it, then advances to
the next file. `--yes` tests the complete directory first and then encodes all
recommendations.

## Safety and progress

Every full encode goes to a uniquely named temporary MKV. MKV Encoder Q then
checks HEVC codec, bit depth, resolution, duration, HDR/Dolby Vision signaling,
audio streams, channel counts, and subtitle count. Only a verified result may
replace an original. Failed temporary files are retained for inspection.

The full encode display shows the active filename, percentage, current and
average FPS, growing output size, and ETA. In an interactive Windows console,
press `P` to pause HandBrakeCLI and `P` again to resume. A partial encode cannot
survive closing the console or restarting Windows.

HandBrakeCLI receives the console directly on Windows. Use its native lowercase
controls: `p` pauses, `r` resumes, and `q` cancels the active file. Video
Optimizer confirms pause/resume from HandBrake's output, allowing HandBrake to
manage its own encoder threads, buffers, progress and ETA state.

Press `Ctrl+C` to cancel the active HandBrake process and stop the complete
MKV Encoder Q session. Cancellation waits for HandBrake to exit and only
force-stops remaining processes when necessary. Originals remain untouched;
surviving partial temporary files are retained and reported.

Logs are written to `MKVEncoderQ.log` beside the application.
