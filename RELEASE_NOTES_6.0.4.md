# 6.0.4 — sample failure diagnostics

Sampling now logs its exact command, source, seek timestamp, duration, full combined HandBrake output and exit status. File Details includes a larger error excerpt and encoder.log path. Previously discarded messages cannot be recovered from older runs.

To diagnose Titanic: build this version, right-click Titanic and choose Reanalyze. Sampling writes only temporary samples, which are cleaned up. If a sample still fails, send encoder.log promptly. A shifted reanalysis may test a different timestamp, which is now recorded.

This update does not implement the pending subtitle warning-policy changes. Keep the Babylon retained outputs and original files.
