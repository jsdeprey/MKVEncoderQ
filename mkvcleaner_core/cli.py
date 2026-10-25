from __future__ import annotations

import argparse
from pathlib import Path

from video_optimizer import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="MKVCleaner",
        description="Conservatively remove selected MKV metadata without re-encoding.",
    )
    parser.add_argument(
        "legacy_path",
        nargs="?",
        type=Path,
        help="Legacy positional input path. Prefer -i/--input.",
    )
    parser.add_argument(
        "-i",
        "--input",
        dest="input_path",
        type=Path,
        help="MKV file or directory to process. Overrides default_input_directory.",
    )
    parser.add_argument("-c", "--config", type=Path, help="Path to config.json.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect only; do not modify files.")
    parser.add_argument("-t", "--threads", type=int, help="Worker count from 1 to 8.")
    parser.add_argument("--recursive", action="store_true", help="Scan subdirectories.")
    parser.add_argument("--keep-backup", action="store_true", help="Keep a .bak copy.")
    parser.add_argument("--remove-chapters", action="store_true")
    parser.add_argument("--remove-attachments", action="store_true")
    parser.add_argument("--show-skipped", action="store_true")
    parser.add_argument("--mkvmerge", type=Path, help="Path to mkvmerge.exe.")
    parser.add_argument(
        "--inspection-mode",
        choices=("stream", "full"),
        help=(
            "Inspection behavior: stream inspects each file and queues it immediately "
            "(default); full inspects and lists every file before cleaning begins."
        ),
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Display the effective configuration and exit.",
    )
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def parse_args() -> argparse.Namespace:
    args = build_parser().parse_args()
    if args.input_path is not None and args.legacy_path is not None:
        raise SystemExit("Use either -i/--input or the legacy positional path, not both.")
    return args
