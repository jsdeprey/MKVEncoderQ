"""Ownership records for temporary encodes, including interrupted jobs."""
import json
import os
import re
from pathlib import Path

def record_path(output):
    return Path(str(output) + '.job.json')

def record(output, source, state, **extra):
    target = record_path(output)
    data = {'source': str(Path(source).resolve()), 'output': str(Path(output).resolve()),
            'pid': os.getpid(), 'state': state, **extra}
    try:
        target.write_text(json.dumps(data, indent=2), encoding='utf-8')
    except OSError as exc:
        from .tab_logging import write
        write('encoder', f'Could not save retained-output ownership record: {exc}')

def remove_record(output):
    try: record_path(output).unlink(missing_ok=True)
    except OSError: pass

def active_record(data):
    if data.get('state') != 'active': return False
    try:
        import psutil
        return psutil.pid_exists(int(data['pid']))
    except (ImportError, TypeError, ValueError, KeyError): return True

def _index_folder(folder: Path) -> tuple[dict, dict]:
    recorded = {}
    legacy = {}
    try:
        for candidate in folder.glob(".video-optimizer-*.mkv"):
            if not candidate.is_file():
                continue
            marker = record_path(candidate)
            try:
                data = json.loads(marker.read_text(encoding="utf-8"))
            except FileNotFoundError:
                match = re.fullmatch(r"\.video-optimizer-(.+)-[0-9a-f]{8}\.mkv", candidate.name)
                if match:
                    legacy.setdefault(match[1], []).append(candidate)
                continue
            except (OSError, ValueError):
                continue
            if (isinstance(data, dict) and data.get("state") in {"active", "retained"}
                    and isinstance(data.get("source"), str) and not active_record(data)):
                recorded.setdefault(Path(data["source"]), []).append(candidate)
    except OSError:
        pass
    return recorded, legacy


def discover(source, folders, folder_candidates=None):
    from .scanner import VIDEO_EXTENSIONS

    source = Path(source).resolve()
    indexes = {} if folder_candidates is None else folder_candidates
    found = set()
    for folder in set(map(Path, folders)):
        if folder not in indexes:
            indexes[folder] = _index_folder(folder)
        recorded, legacy = indexes[folder]
        found.update(recorded.get(source, ()))
        candidates = legacy.get(source.stem, ())
        if not candidates or folder.resolve() != source.parent:
            continue
        # Sidecar-free files need one unambiguous source beside the temp file.
        try:
            owners = [path for path in folder.iterdir()
                      if path.stem == source.stem and path.suffix.lower() in VIDEO_EXTENSIONS
                      and path.is_file()]
            if len(owners) != 1 or owners[0].resolve() != source:
                continue
            import psutil
            active_arguments = {argument for process in psutil.process_iter(["name", "cmdline"])
                                if "handbrake" in (process.info["name"] or "").lower()
                                for argument in process.info["cmdline"] or []}
        except Exception:
            continue
        found.update(candidate for candidate in candidates
                     if str(candidate) not in active_arguments)
    return sorted(found, key=lambda path: path.name)


def verification_record(source: Path, output: Path) -> dict | None:
    """Find proof that this exact retained file reached verification."""
    try:
        data = json.loads(record_path(output).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(data, dict) or data.get("state") != "retained"
            or data.get("source") != str(source.resolve())
            or data.get("output") != str(output.resolve())):
        return None
    if "verification_failed" in data:
        return data if data["verification_failed"] is True else None
    # Older builds recorded only the error. These messages were written
    # after HandBrake returned successfully and ffprobe read the output.
    error = data.get("error")
    if isinstance(error, str) and error.startswith("verification failed: "):
        return data
    return None
