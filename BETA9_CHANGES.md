# 6.0 beta 9 — per-video audio selection

Right-click Encoder rows → Audio Streams. Videos inherit Settings unless explicitly overridden. Options: all, first English, first, default, or Choose audio streams. Specific-track dialog shows language, codec/profile, channels, title and default flag. Standard choices apply to selected rows; specific tracks require one video.

Overrides apply to sampling, final encoding and verification, including retained-output acceptance. They follow renames and appear in File Details and the activity log. Subtitle policy stays unchanged. Global settings are never changed.

Beta limitations: overrides last for this application session; set choices while the queue is stopped. Track selection requires ffprobe.

Manual checks:
- Set global First English; override one file to All and check both languages survive while another file keeps First English.
- Choose specific tracks and check output language/channel counts.
- Return to Use Settings default and confirm inheritance.
- Rename a video with an override; confirm choice follows it.
- Confirm subtitle retention, retained-output verification, and batch menu choices.
