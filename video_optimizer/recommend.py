from __future__ import annotations

from .config import Config
from .models import Decision, MediaInfo, ScanResult

def _hdr_reason(media: MediaInfo) -> str | None:
    video = media.videos[0]
    transfer = video.color_transfer.lower()
    combined = " ".join((video.profile, *video.side_data)).lower()
    if video.dolby_vision_profile is not None or "dolby vision" in combined or "dovi" in combined:
        return "Dolby Vision detected; use protected high-bitrate 10-bit path"
    if transfer in {"smpte2084", "arib-std-b67"} or "hdr10+" in combined:
        return "HDR detected; use protected high-bitrate 10-bit path"
    return None


def recommend(media: MediaInfo, config: Config) -> ScanResult:
    """Compatibility preflight; benefit decisions now come from sample encodes."""
    if not media.videos:
        return ScanResult(media.path, Decision.SKIP, "No video stream", media)
    video = media.videos[0]
    hdr = _hdr_reason(media)
    if hdr:
        if media.is_dolby_vision and media.videos[0].dolby_vision_profile == 5:
            return ScanResult(media.path, Decision.SKIP,
                              "Dolby Vision profile 5 cannot be safely re-encoded by this version",
                              media)
        return ScanResult(media.path, Decision.REVIEW, hdr, media)
    return ScanResult(media.path, Decision.REVIEW,
                      "Sample encode required to estimate benefit", media)
