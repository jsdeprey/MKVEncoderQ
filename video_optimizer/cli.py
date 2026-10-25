from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import application_directory, default_config_path, load_config
from .encoder import BackupAlreadyExists, EncodeCancelled, build_command, encode
from .models import Decision, ScanResult
from .scanner import discover, scan, scan_file
from .tools import resolve_program


class _DefaultEncodeParser(argparse.ArgumentParser):
    """Treat omitted subcommands as ``encode`` while preserving root help."""

    def parse_args(self, args=None, namespace=None):
        values = list(sys.argv[1:] if args is None else args)
        if (not values or
                values[0] not in {"scan", "encode", "-h", "--help", "--version"}):
            values.insert(0, "encode")
        return super().parse_args(values, namespace)


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-i", "--input", type=Path, metavar="DIRECTORY",
                        help="input directory (default: input_directory in config.json)")
    common.add_argument("-o", "--output", type=Path, metavar="DIRECTORY",
                        help="output directory (default: input directory)")
    common.add_argument("--config", type=Path, metavar="FILE",
                        help="configuration file (default: config.json beside the program)")
    common.add_argument("--handbrake-cli", metavar="PROGRAM",
                        help="HandBrakeCLI executable or path (overrides config.json)")
    common.add_argument("--ffprobe", metavar="PROGRAM",
                        help="ffprobe executable or path (overrides config.json)")
    root = _DefaultEncodeParser(
        prog="MKVEncoderQ-CLI",
        description=("Scan video libraries and safely re-encode approved files to HEVC MKV. "
                     "If no command is given, encode is used."))
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command")
    commands.add_parser("scan", parents=[common], help="analyze files without changes",
                        description="Recursively analyze the input directory without changing files.")
    enc = commands.add_parser("encode", parents=[common], help="scan and encode approved files")
    enc.add_argument("--no-backup", action="store_true",
                     help="delete each original only after its output passes verification")
    enc.add_argument("--yes", action="store_true",
                     help="approve every recommended file after the complete scan")
    enc.add_argument("--unattended", action="store_true",
                     help="test and immediately process each file without prompting")
    enc.add_argument("--include-review", action="store_true",
                     help="also offer files marked REVIEW for approval")
    enc.add_argument("--dry-run", action="store_true",
                     help="show planned HandBrakeCLI commands without changing files")
    enc.add_argument("--denoise", choices=("off", "light", "medium", "strong"),
                     help="override the configured denoise mode for this run")
    enc.add_argument("--crop-mode", choices=("auto", "conservative", "none"),
                     help="override HandBrake cropping for this run")
    enc.add_argument("--force", action="store_true",
                     help="also re-encode files normally marked SKIP (testing use)")
    enc.add_argument("--audio-streams", choices=("all", "first", "default", "first_english"),
                     help="audio selection mode (overrides config.json)")
    enc.add_argument("--keep-metadata", action="store_true",
                     help="preserve source tags and metadata for this run")
    return root


def _show(result: ScanResult, root: Path) -> None:
    media = result.media
    details = ""
    if media and media.videos:
        v = media.videos[0]
        rate = v.bit_rate or media.bit_rate
        details = f" | {v.codec_name} {v.width}x{v.height}"
        if rate:
            details += f" {rate / 1_000_000:.1f} Mbps"
    print(f"[{result.decision.value:9}] {result.path.relative_to(root)}{details}")
    print(f"            {result.reason}")
    for sample in result.sample_results:
        print(f"            Sample {sample.number}/{sample.total} — "
              f"{_clock_text(sample.start_seconds)} — "
              f"{_size_text(sample.output_size)} sample — "
              f"{sample.projected_savings_percent:.1f}% projected savings — "
              f"{_size_text(sample.projected_size)} projected output")
    if result.projected_size is not None:
        elapsed = (f" — total sampling time {_duration_text(result.sampling_elapsed_seconds)}"
                   if result.sampling_elapsed_seconds is not None else "")
        print(f"            Average used for projection — "
              f"{result.projected_savings_percent:.1f}% savings — "
              f"{_size_text(result.projected_size)} projected output{elapsed}")
    for warning in result.warnings:
        print(f"            WARNING: {warning}")


def _size_text(value: int) -> str:
    if value >= 1_073_741_824:
        return f"{value / 1_073_741_824:.2f} GiB"
    return f"{value / 1_048_576:.1f} MiB"


def _clock_text(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _duration_text(seconds: float | None) -> str:
    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def _confirm(result: ScanResult, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    while True:
        answer = input(f"Encode {result.path.name}? [y/N/q] ").strip().lower()
        if answer in {"", "n", "no"}:
            return False
        if answer in {"y", "yes"}:
            return True
        if answer in {"q", "quit"}:
            raise KeyboardInterrupt


def _heading(action: str, path: Path, root: Path) -> None:
    print("\n" + "=" * 78)
    print(f"{action}: {path.relative_to(root)}")
    print("=" * 78)


def _process(item: ScanResult, root: Path, output_root: Path, config, backup: bool,
             handbrake_cli: str, ffprobe: str, dry_run: bool) -> bool:
    assert item.media is not None
    _heading("ENCODING", item.path, root)
    if dry_run:
        temp = item.path.parent / ".video-optimizer-TEMP.mkv"
        command = build_command(item.media, temp, config, handbrake_cli)
        print("DRY RUN:", " ".join(f'"{x}"' if " " in x else x for x in command))
        return True
    try:
        outcome = encode(item.media, root, output_root, config, backup,
                         handbrake_cli, ffprobe)
        saved = outcome.bytes_before - outcome.bytes_after
        print(f"Completed: {outcome.output} ({saved / 1_048_576:.1f} MiB saved)")
        return True
    except EncodeCancelled:
        print(f"CANCELLED: {item.path}")
        print("The original was not changed. Continuing with the next file.")
        return True
    except BackupAlreadyExists as exc:
        logging.info("Skipped %s because backup already exists: %s",
                     item.path, exc.backup_path)
        print(f"SKIPPED: {item.path}: backup already exists: {exc.backup_path}")
        return True
    except Exception as exc:
        logging.exception("Failed: %s", item.path)
        print(f"FAILED: {item.path}: {exc}", file=sys.stderr)
        return False


def _unattended(root: Path, output_root: Path, config, args, handbrake_cli: str,
                ffprobe: str, excluded: Path | None) -> int:
    backup = config.backup_originals and not args.no_backup
    force = args.force or config.always_reencode
    print("Unattended streaming mode: test each file, then immediately encode or skip it.")
    print(f"Backup mode: {'enabled' if backup else 'DISABLED'}")
    counts = {decision: 0 for decision in Decision}
    failures = 0
    # Freeze discovery before writing outputs into a directory being scanned.
    paths = list(discover(root, "_video_optimizer_backups", excluded))
    try:
        for path in paths:
            _heading("TESTING", path, root)
            item = scan_file(path, config, ffprobe, handbrake_cli)
            counts[item.decision] += 1
            _show(item, root)
            eligible = ((force and item.decision != Decision.ERROR
                         and item.media is not None and item.media.videos)
                        or item.decision == Decision.RECOMMEND
                        or (args.include_review and item.decision == Decision.REVIEW))
            if not eligible:
                continue
            if not _process(item, root, output_root, config, backup, handbrake_cli,
                            ffprobe, args.dry_run):
                failures += 1
    except KeyboardInterrupt:
        print("\nStopped by user; current originals remain protected.")
        return 130
    print("\nSummary: " + ", ".join(f"{d.value}={counts[d]}" for d in Decision))
    return 1 if failures or counts[Decision.ERROR] else 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config_path = args.config.resolve() if args.config else default_config_path()
    first_run = not config_path.exists()
    try:
        config = load_config(args.config)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if first_run:
        print(f"Created first-run configuration: {config_path}")
    base = config_path.parent if args.config else application_directory()
    def setting_path(cli_value: Path | None, configured: str) -> Path:
        value = cli_value if cli_value is not None else Path(configured)
        return (value if value.is_absolute() else base / value).resolve()
    root = setting_path(args.input, config.input_directory)
    configured_output = config.output_directory.strip()
    output_root = (setting_path(args.output, configured_output)
                   if args.output is not None or configured_output else root)
    temp_root = setting_path(None, config.temp_directory)
    if args.input is None:
        root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        print(f"error: input is not a directory: {root}", file=sys.stderr)
        return 2
    if args.command == "encode":
        output_root.mkdir(parents=True, exist_ok=True)
    try:
        temp_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"error: cannot create temporary directory {temp_root}: {exc}", file=sys.stderr)
        return 2
    from dataclasses import replace
    config = replace(config, temp_directory=str(temp_root))
    if getattr(args, "denoise", None):
        from dataclasses import replace
        config = replace(config, denoise_mode=args.denoise,
                         hdr_denoise_mode=args.denoise)
    if getattr(args, "crop_mode", None):
        from dataclasses import replace
        config = replace(config, crop_mode=args.crop_mode)
    if getattr(args, "audio_streams", None):
        from dataclasses import replace
        config = replace(config, audio_stream_mode=args.audio_streams)
    if getattr(args, "keep_metadata", False):
        from dataclasses import replace
        config = replace(config, preserve_metadata=True)
    handbrake_cli = resolve_program(args.handbrake_cli or config.handbrake_cli)
    ffprobe = resolve_program(args.ffprobe or config.ffprobe)
    if ffprobe is None:
        print(f"error: ffprobe not found: {args.ffprobe or config.ffprobe}", file=sys.stderr)
        return 2
    if handbrake_cli is None:
        print(f"error: HandBrakeCLI not found: {args.handbrake_cli or config.handbrake_cli}",
              file=sys.stderr)
        return 2
    logging.basicConfig(filename=base / "MKVEncoderQ.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    excluded = output_root if output_root != root and output_root.is_relative_to(root) else None
    if args.command == "encode" and args.unattended:
        return _unattended(root, output_root, config, args, handbrake_cli,
                           ffprobe, excluded)
    print(f"Scanning and sample-testing {root} ...")
    results = scan(root, config, ffprobe, handbrake_cli, excluded_root=excluded,
                   progress=lambda path: _heading("TESTING", path, root))
    for item in results:
        _show(item, root)
    counts = {decision: sum(r.decision == decision for r in results) for decision in Decision}
    print("\nSummary: " + ", ".join(f"{d.value}={counts[d]}" for d in Decision))
    if args.command == "scan":
        return 1 if counts[Decision.ERROR] else 0

    force = args.force or config.always_reencode
    eligible = [r for r in results if (force and r.decision != Decision.ERROR
                                       and r.media is not None and r.media.videos) or
                r.decision == Decision.RECOMMEND or
                (args.include_review and r.decision == Decision.REVIEW)]
    if not eligible:
        print("No eligible files.")
        return 1 if counts[Decision.ERROR] else 0
    backup = config.backup_originals and not args.no_backup
    print(f"\nBackup mode: {'enabled' if backup else 'DISABLED'}")
    failures = 0
    try:
        for item in eligible:
            if not _confirm(item, args.yes):
                continue
            assert item.media is not None
            if not _process(item, root, output_root, config, backup,
                            handbrake_cli, ffprobe, args.dry_run):
                failures += 1
    except KeyboardInterrupt:
        print("\nStopped by user; current originals remain protected.")
        return 130
    return 1 if failures or counts[Decision.ERROR] else 0
