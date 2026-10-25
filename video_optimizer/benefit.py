from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

from .config import Config
from .encoder import build_command
from .models import Decision, MediaInfo, SampleResult, ScanResult
from .subprocesses import helper_creation_flags


def _sample_starts(duration: float, count: int, requested_length: float,
                   variant: int = 0) -> tuple[list[float], float]:
    """Spread samples across the video, shifting manual reanalysis positions."""
    length = min(requested_length, duration / count)
    bin_width = duration / count
    if variant <= 0:
        fraction = 0.5
    else:
        # A low-discrepancy step avoids quickly repeating timestamps while
        # preserving one sample in every evenly divided section of the video.
        fraction = (0.5 + variant * 0.3819660112501051) % 1.0
    usable_width = max(0.0, bin_width - length)
    starts = [bin_width * index + usable_width * fraction
              for index in range(count)]
    return starts, length


def _progressive_sample_starts(duration: float, count: int,
                               requested_length: float,
                               layout_count: int | None = None
                               ) -> tuple[list[float], float]:
    """Return a nested, space-filling sequence so later clicks add new areas."""
    layout_count = max(count, layout_count or count)
    length = min(requested_length, duration / layout_count)
    usable = max(0.0, duration - length)
    fractions = []
    index = 1
    while len(fractions) < count:
        value = index
        denominator = 1.0
        fraction = 0.0
        while value:
            value, bit = divmod(value, 2)
            denominator *= 2.0
            fraction += bit / denominator
        fractions.append(fraction)
        index += 1
    return [usable * fraction for fraction in fractions], length


def _sample_command(media: MediaInfo, output: Path, config: Config, handbrake_cli: str,
                    start: float, duration: float) -> list[str]:
    return build_command(media, output, config, handbrake_cli,
                         start=start, duration=duration,
                         include_subtitles=False)


def analyze_benefit(media: MediaInfo, config: Config, handbrake_cli: str,
                    sample_variant: int = 0,
                    prior_samples: tuple[SampleResult, ...] = (),
                    progressive_target: int | None = None) -> ScanResult:
    if not media.videos:
        return ScanResult(media.path, Decision.SKIP, "No video stream", media)
    if media.duration is None or media.duration <= 0:
        return ScanResult(media.path, Decision.ERROR,
                          "Duration unavailable; cannot estimate benefit", media)
    if media.is_dolby_vision and media.videos[0].dolby_vision_profile == 5:
        return ScanResult(media.path, Decision.SKIP,
                          "Dolby Vision profile 5 cannot be safely re-encoded", media)
    if progressive_target is not None:
        starts, sample_length = _progressive_sample_starts(
            media.duration, progressive_target, config.sample_duration_seconds,
            config.progressive_sample_max)
    else:
        starts, sample_length = _sample_starts(media.duration, config.sample_count,
                                               config.sample_duration_seconds,
                                               sample_variant)
    retained = tuple(prior_samples[:len(starts)])
    total_bytes = sum(sample.output_size for sample in retained)
    sample_results = [SampleResult(
        number=index + 1, total=len(starts), start_seconds=sample.start_seconds,
        output_size=sample.output_size, projected_size=sample.projected_size,
        projected_savings_percent=sample.projected_savings_percent,
    ) for index, sample in enumerate(retained)]
    sampling_started = time.monotonic()
    sample_root = Path(config.temp_directory)
    sample_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="video-optimizer-samples-",
                                     dir=sample_root) as directory:
        for index, start in enumerate(starts[len(retained):], start=len(retained)):
            output = Path(directory) / f"sample-{index}.mkv"
            completed = subprocess.run(
                _sample_command(media, output, config, handbrake_cli,
                                start, sample_length),
                check=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                creationflags=helper_creation_flags(),
            )
            if completed.returncode or not output.exists():
                lines = [line.strip() for line in (completed.stderr or "").splitlines()
                         if line.strip()]
                detail = " | ".join(lines[-3:]) if lines else "no error details returned"
                raise RuntimeError(
                    f"sample encode {index + 1} failed (exit {completed.returncode}): {detail}"
                )
            output_size = output.stat().st_size
            total_bytes += output_size
            sample_projected = int(output_size / sample_length * media.duration)
            sample_savings = ((media.size - sample_projected) / media.size * 100
                              if media.size else 0.0)
            sample_results.append(SampleResult(
                number=index + 1,
                total=len(starts),
                start_seconds=start,
                output_size=output_size,
                projected_size=sample_projected,
                projected_savings_percent=sample_savings,
            ))

    tested_seconds = sample_length * len(sample_results)
    sampling_elapsed = time.monotonic() - sampling_started
    projected = int(total_bytes / tested_seconds * media.duration)
    savings = (media.size - projected) / media.size * 100 if media.size else 0.0
    threshold = config.minimum_projected_savings_percent
    warnings = ((f"Omitting {len(media.data_streams)} unsupported data stream(s) from MKV output",)
                if media.data_streams else ())
    if media.attachments:
        warnings += (f"Omitting {len(media.attachments)} Matroska attachment(s) from output",)
    if (config.audio_stream_mode == "default" and media.audios
            and not any(audio.is_default for audio in media.audios)):
        warnings += ("No default audio stream is marked; keeping the first audio stream",)
    if (config.audio_stream_mode == "first_english" and media.audios
            and not any(audio.language.lower() in {"eng", "en", "english"}
                        for audio in media.audios)):
        warnings += ("No English audio stream is tagged; keeping the first audio stream",)
    if savings >= threshold:
        decision = Decision.RECOMMEND
        reason = (f"Projected savings {savings:.1f}% meets configured "
                  f"{threshold:.1f}% benefit")
    else:
        decision = Decision.SKIP
        reason = (f"Skipped: projected savings {savings:.1f}% does not meet configured "
                  f"{threshold:.1f}% benefit")
    return ScanResult(media.path, decision, reason, media, warnings=warnings,
                      projected_size=projected,
                      projected_savings_percent=savings,
                      samples_tested=len(starts),
                      sample_results=tuple(sample_results),
                      sampling_elapsed_seconds=sampling_elapsed)
