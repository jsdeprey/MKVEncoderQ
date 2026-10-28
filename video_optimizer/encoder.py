from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from threading import Event, Thread

from .config import Config
from .models import MediaInfo, Stream
from .pause import PauseController
from .handbrake_console import CREATE_NEW_CONSOLE, hidden_console_startupinfo
from .probe import probe
from .subprocesses import helper_creation_flags


class EncodeCancelled(RuntimeError):
    """The user cancelled the current file without cancelling the whole run."""


class BackupAlreadyExists(FileExistsError):
    """The source was previously processed and its backup is still present."""

    def __init__(self, backup_path: Path) -> None:
        self.backup_path = backup_path
        super().__init__(f"backup already exists: {backup_path}")


class EncodeVerificationFailed(RuntimeError):
    """A complete encoded file was retained after safety verification failed."""

    def __init__(self, message: str, retained_output: Path) -> None:
        self.retained_output = retained_output
        super().__init__(message)

def _passthrough_set(config: Config) -> set[str]:
    # ffprobe reports both DTS and DTS-HD tracks as codec_name=dts.
    values = set(config.audio_passthrough_codecs)
    if "dtshd" in values:
        values.add("dts")
    return values


def selected_audio(media: MediaInfo, mode: str) -> tuple[Stream, ...]:
    audio = media.audios
    if mode == "all" or not audio:
        return audio
    if mode == "first":
        return audio[:1]
    if mode == "first_english":
        english = next((stream for stream in audio
                        if stream.language.lower() in {"eng", "en", "english"}), None)
        return (english or audio[0],)
    default = next((stream for stream in audio if stream.is_default), None)
    return (default or audio[0],)


_EMBEDDED_CAPTION_CODECS = {"eia_608", "eia_708"}


def expected_handbrake_subtitle_count(media: MediaInfo) -> int:
    """Count subtitles as HandBrake exposes embedded caption services.

    ffprobe can expose CC1-CC4 from one video stream as several EIA-608/708
    subtitle streams. HandBrake presents those services as one selectable
    closed-caption source, while ordinary container subtitle tracks remain
    individual tracks.
    """
    ordinary = sum(stream.codec_name not in _EMBEDDED_CAPTION_CODECS
                   for stream in media.subtitles)
    embedded = any(stream.codec_name in _EMBEDDED_CAPTION_CODECS
                   for stream in media.subtitles)
    return ordinary + int(embedded)


def _ordinary_subtitle_languages(media: MediaInfo) -> Counter[str]:
    return Counter(
        stream.language or "und" for stream in media.subtitles
        if stream.codec_name not in _EMBEDDED_CAPTION_CODECS)


@dataclass(frozen=True)
class EncodeOutcome:
    source: Path
    output: Path
    backup: Path | None
    bytes_before: int
    bytes_after: int


def _resolution_setting(width: int, height: int, settings: dict[str, float]) -> float:
    # Recognize letterbox-cropped sources by their full 16:9 canvas equivalent.
    # Examples: 3840x1600 -> 2160 class; 1920x800 -> 1080 class.
    effective_height = max(height, round(width * 9 / 16))
    choices = sorted(((int(key), value) for key, value in settings.items()), reverse=True)
    return next((value for boundary, value in choices if effective_height >= boundary),
                choices[-1][1])


def _handbrake_encoder(bit_depth: int) -> str:
    return "x265" if bit_depth == 8 else f"x265_{bit_depth}bit"


def _audio_ordinals(media: MediaInfo, mode: str) -> tuple[int, ...]:
    selected = selected_audio(media, mode)
    return tuple(media.audios.index(stream) + 1 for stream in selected)


def _audio_bitrates(media: MediaInfo, config: Config) -> str:
    values = []
    for audio in selected_audio(media, config.audio_stream_mode):
        bitrate = max(config.minimum_audio_bitrate,
                      config.audio_bitrate_per_channel * max(audio.channels, 2))
        values.append(str(round(bitrate / 1000)))
    return ",".join(values)


def video_options(media: MediaInfo, config: Config) -> list[str]:
    video = media.videos[0]
    rf = _resolution_setting(video.width, video.height, config.quality_rf_by_resolution)
    options = ["--encoder", _handbrake_encoder(config.video_bit_depth),
               "--encoder-preset", config.encoder_preset,
               "--quality", str(rf), "--vfr", "--crop-mode", config.crop_mode,
               "--maxWidth", str(video.width), "--maxHeight", str(video.height),
               "--auto-anamorphic", "--modulus", "2", "--inline-parameter-sets",
               "--no-comb-detect", "--no-decomb", "--no-deinterlace",
               "--no-detelecine"]
    denoise = config.hdr_denoise_mode if media.is_hdr else config.denoise_mode
    if denoise == "off":
        options.extend(["--no-hqdn3d", "--no-nlmeans"])
    else:
        options.extend([f"--hqdn3d={denoise}", "--no-nlmeans"])
    if media.is_hdr or media.is_dolby_vision:
        options.extend(["--hdr-dynamic-metadata", "all"])
    return options


def build_command(media: MediaInfo, temporary: Path, config: Config,
                  handbrake_cli: str = "HandBrakeCLI",
                  start: float | None = None,
                  duration: float | None = None,
                  include_subtitles: bool = True) -> list[str]:
    cmd = [handbrake_cli, "--input", str(media.path), "--output", str(temporary),
           "--format", "av_mkv", "--markers"]
    cmd.extend(video_options(media, config))
    ordinals = _audio_ordinals(media, config.audio_stream_mode)
    if ordinals:
        cmd.extend(["--audio", ",".join(map(str, ordinals)),
                    "--aencoder", ",".join("copy" for _ in ordinals),
                    "--audio-copy-mask", ",".join(config.audio_passthrough_codecs),
                    "--audio-fallback", "av_aac",
                    "--ab", _audio_bitrates(media, config)])
    else:
        cmd.extend(["--audio", "none"])
    if include_subtitles and media.subtitles:
        cmd.extend(["--all-subtitles", "--subtitle-burned=none"])
    else:
        cmd.extend(["--subtitle", "none"])
    cmd.append("--keep-metadata" if config.preserve_metadata else "--no-metadata")
    if start is not None:
        cmd.extend(["--start-at", f"seconds:{start:.3f}"])
    if duration is not None:
        cmd.extend(["--stop-at", f"seconds:{duration:.3f}", "--no-markers"])
    return cmd


def verify(source: MediaInfo, output: MediaInfo, config: Config) -> None:
    if not output.videos or output.videos[0].codec_name not in {"hevc", "h265"}:
        raise RuntimeError("verification failed: output video is not HEVC")
    before, after = source.videos[0], output.videos[0]
    if config.crop_mode == "none":
        if (before.width, before.height) != (after.width, after.height):
            raise RuntimeError("verification failed: output resolution changed with cropping disabled")
    elif (after.width <= 0 or after.height <= 0 or after.width > before.width
          or after.height > before.height):
        raise RuntimeError("verification failed: cropped output dimensions are invalid")
    expected_depth = config.video_bit_depth
    actual_depth = 12 if "12" in after.pixel_format else 10 if "10" in after.pixel_format else 8
    if actual_depth != expected_depth:
        raise RuntimeError(f"verification failed: expected {expected_depth}-bit video, got {actual_depth}-bit")
    if source.is_hdr and not output.is_hdr:
        raise RuntimeError("verification failed: HDR signaling was not preserved")
    if source.is_dolby_vision and not output.is_dolby_vision:
        raise RuntimeError("verification failed: Dolby Vision RPU metadata was not preserved")
    expected_audio = selected_audio(source, config.audio_stream_mode)
    if len(expected_audio) != len(output.audios):
        raise RuntimeError("verification failed: audio stream count changed")
    expected_subtitles = expected_handbrake_subtitle_count(source)
    if len(output.subtitles) < expected_subtitles:
        raise RuntimeError(
            "verification failed: subtitle stream count changed "
            f"(expected {expected_subtitles}, found {len(output.subtitles)})")
    required_languages = _ordinary_subtitle_languages(source)
    output_languages = Counter(stream.language or "und"
                               for stream in output.subtitles)
    missing_languages = required_languages - output_languages
    if missing_languages:
        detail = ", ".join(
            f"{language} ({count})" for language, count in
            sorted(missing_languages.items()))
        raise RuntimeError(
            "verification failed: required subtitle language track(s) missing: "
            + detail)
    if source.duration is not None and output.duration is not None:
        if abs(source.duration - output.duration) > config.duration_tolerance_seconds:
            raise RuntimeError("verification failed: duration changed beyond tolerance")
    for old, new in zip(expected_audio, output.audios):
        expected = (old.codec_name if old.codec_name in _passthrough_set(config)
                    else config.audio_fallback_codec)
        if new.codec_name != expected:
            raise RuntimeError(f"verification failed: expected audio {expected}, got {new.codec_name}")
        if old.channels and new.channels != old.channels:
            raise RuntimeError("verification failed: audio channel count changed")


def _format_progress(values: dict[str, str], duration: float, width: int = 30) -> str:
    try:
        elapsed = int(values.get("out_time_us", "0")) / 1_000_000
    except ValueError:
        elapsed = 0.0
    percent = min(100.0, max(0.0, elapsed / duration * 100)) if duration > 0 else 0.0
    if values.get("progress") == "end":
        percent = 100.0
    filled = int(width * percent / 100)
    bar = "#" * filled + "-" * (width - filled)
    try:
        size = int(values.get("total_size", "0")) / 1_048_576
    except ValueError:
        size = 0.0
    return (f"[{bar}] {percent:6.2f}%  frame={values.get('frame', '?')}  "
            f"fps={values.get('fps', '?')}  size={size:.1f}MiB  "
            f"time={values.get('out_time', '00:00:00')}  "
            f"bitrate={values.get('bitrate', '?')}  speed={values.get('speed', '?')}")


_HB_PROGRESS = re.compile(
    r"Encoding:.*?([0-9]+(?:\.[0-9]+)?)\s*%.*?\(([0-9.]+)\s*fps,\s*avg\s*([0-9.]+)\s*fps,\s*ETA\s*([^\)]+)\)",
    re.IGNORECASE,
)


def _run_handbrake_with_progress(command: list[str], duration: float, label: str,
                                 output: Path, cancel_event: Event | None = None,
                                 progress: Callable[[float, str], None] | None = None,
                                 pause_event: Event | None = None,
                                 pause_status: Callable[[bool], None] | None = None) -> None:
    gui_control = pause_event is not None
    creation_flags = (CREATE_NEW_CONSOLE if os.name == "nt" and gui_control
                      else helper_creation_flags(process_group=True))
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   text=True,
                                   encoding="utf-8", errors="replace", bufsize=1,
                                   creationflags=creation_flags,
                                   startupinfo=hidden_console_startupinfo() if gui_control else None)
    except OSError as exc:
        raise RuntimeError(f"Unable to start HandBrakeCLI: {exc}") from exc
    pause = PauseController(process, label)
    pause.native_console = bool(os.name == "nt" and gui_control)
    pause.start()
    if cancel_event is not None:
        def monitor_cancel() -> None:
            cancel_event.wait()
            if cancel_event.is_set() and process.poll() is None:
                pause.cancel()
        Thread(target=monitor_cancel, daemon=True).start()
    pause_monitor_stop = Event()
    if pause_event is not None:
        def monitor_pause() -> None:
            applied = False
            while not pause_monitor_stop.wait(0.1) and process.poll() is None:
                requested = pause_event.is_set()
                if requested and not applied:
                    try:
                        applied = pause.request_pause()
                    except OSError:
                        applied = False
                elif not requested and applied:
                    try:
                        pause.request_resume()
                    except OSError:
                        pass
                    applied = False
        Thread(target=monitor_pause, daemon=True).start()
    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.strip()
            match = _HB_PROGRESS.search(line)
            if match:
                was_paused = pause.pause_confirmed
                pause.observe_progress()
                if pause_status and was_paused and not pause.pause_confirmed:
                    pause_status(False)
                percent, fps, average, eta = match.groups()
                size = output.stat().st_size / 1_048_576 if output.exists() else 0.0
                if progress:
                    progress(float(percent),
                             f"{fps} fps · {size:.1f} MiB · ETA {eta}")
                if sys.stdout is not None:
                    sys.stdout.write(
                        f"\r[{('#' * int(float(percent) * 0.30)).ljust(30, '-')}] "
                        f"{float(percent):6.2f}%  fps={fps}  avg_fps={average}  "
                        f"size={size:.1f}MiB  ETA={eta}"
                    )
                    sys.stdout.flush()
            elif line:
                was_paused = pause.pause_confirmed
                pause.observe_output(line)
                if pause_status and pause.pause_confirmed != was_paused:
                    pause_status(pause.pause_confirmed)
                logging.info("HandBrakeCLI: %s", line)
        return_code = process.wait()
    except KeyboardInterrupt:
        pause.cancel()
        raise
    finally:
        pause_monitor_stop.set()
        pause.stop()
    if sys.stdout is not None:
        sys.stdout.write("\n")
        sys.stdout.flush()
    if pause.cancelled:
        raise EncodeCancelled("encode cancelled by user")
    if return_code:
        raise RuntimeError(f"HandBrakeCLI encoding failed with exit code {return_code}")


def encode(media: MediaInfo, root: Path, output_root: Path, config: Config, backup: bool,
           handbrake_cli: str = "HandBrakeCLI", ffprobe: str = "ffprobe",
           cancel_event: Event | None = None,
           progress: Callable[[float, str], None] | None = None,
           pause_event: Event | None = None,
           pause_status: Callable[[bool], None] | None = None) -> EncodeOutcome:
    source = media.path
    if media.is_dolby_vision and media.videos[0].dolby_vision_profile == 5:
        raise RuntimeError("Dolby Vision profile 5 cannot be safely re-encoded")
    relative = source.relative_to(root).with_suffix(".mkv")
    output = output_root / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".video-optimizer-{uuid.uuid4().hex}.mkv"
    backup_path = source.with_name(source.name + config.backup_suffix)
    if output != source and output.exists():
        raise FileExistsError(f"output already exists: {output}")
    if backup and backup_path.exists():
        raise BackupAlreadyExists(backup_path)
    logging.info("Encoding %s", source)
    try:
        command = build_command(media, temporary, config, handbrake_cli)
        _run_handbrake_with_progress(command, media.duration or 0.0,
                                     str(source), temporary, cancel_event, progress,
                                     pause_event, pause_status)
        encoded = probe(temporary, ffprobe)
        try:
            verify(media, encoded, config)
        except RuntimeError as exc:
            raise EncodeVerificationFailed(str(exc), temporary) from exc
        if backup:
            shutil.move(str(source), str(backup_path))
        os.replace(temporary, output)
        # Same-path output replaces the original directly. For a different
        # destination, keep the source until the verified output is committed.
        if not backup and output != source:
            source.unlink()
    except EncodeCancelled:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logging.exception("Could not remove cancelled temporary output: %s",
                              temporary)
        raise
    except BaseException:
        if temporary.exists():
            logging.error("Temporary output retained for inspection: %s", temporary)
            if sys.stderr is not None:
                print(f"Temporary output retained for inspection: {temporary}",
                      file=sys.stderr)
        raise
    return EncodeOutcome(source, output, backup_path if backup else None,
                         media.size, output.stat().st_size)
