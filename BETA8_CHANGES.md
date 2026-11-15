# 6.0 beta 8

- Added Rename Checked / Reset Controls / Remove All between the existing removal and rename controls. The original Reset Rename Controls stays in place.
- Uses existing rename preview and confirmation, then resets controls and clears the list only after successful completion. Cancellation, failure, and excluded busy files leave controls and rows available for review. Remove All only clears list entries, never files. Undo remains available.
- Encoder log now has an Activity Log label and border matching the other tabs.

Manual checks: successful combined rename; cancel confirmation; destination conflict; busy file; unchecked rows clear only after success; Undo after clearing; Encoder log appearance in dark/light themes.
