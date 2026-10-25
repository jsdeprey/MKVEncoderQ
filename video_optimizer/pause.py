from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading

from .handbrake_console import send_console_key

try:
    import psutil
except ImportError:
    psutil = None


class PauseController:
    """Track HandBrake's native console controls and cancel safely."""

    def __init__(self, process, label: str) -> None:
        self.process = process
        self.label = label
        self.paused = False
        self.cancelled = False
        self.pause_confirmed = False
        self.resume_pending = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.native_console = False

    def start(self) -> None:
        if sys.stdout is not None:
            print("HandBrake controls: p=pause, r=resume, q=cancel current file; "
                  "Ctrl+C=stop entire session.")

    @staticmethod
    def _process_tree(target) -> list[object]:
        try:
            children = target.children(recursive=True)
        except (AttributeError, psutil.Error):
            children = []
        return [*children, target]

    def _send_command(self, command: str) -> None:
        if self.native_console and send_console_key(self.process.pid, command.strip()):
            return
        if self.process.stdin is None:
            raise OSError("HandBrakeCLI input pipe is unavailable")
        self.process.stdin.write(command)
        self.process.stdin.flush()

    def observe_output(self, line: str) -> None:
        """Confirm state only from HandBrake's own output."""
        lowered = line.lower()
        if "paused by user command" in lowered and not self.pause_confirmed:
            self.paused = True
            self.pause_confirmed = True
            if sys.stdout is not None:
                print(f"\n[PAUSED]  {self.label}", flush=True)
                print("Press r to resume.", flush=True)
        if "resumed by user command" in lowered:
            self.paused = False
            self.pause_confirmed = False
            self.resume_pending = False
            if sys.stdout is not None:
                print(f"\n[RESUMED] {self.label}", flush=True)
        if "cancel" in lowered and "user" in lowered:
            self.cancelled = True

    def observe_progress(self) -> None:
        if self.pause_confirmed:
            self.paused = False
            self.pause_confirmed = False
            if sys.stdout is not None:
                print(f"\n[RESUMED] {self.label}", flush=True)

    def request_pause(self) -> bool:
        if self.process.poll() is not None:
            return False
        self._send_command("p")
        return True

    def request_resume(self) -> bool:
        if self.process.poll() is not None:
            return False
        self.resume_pending = True
        self._send_command("r")
        return True

    def cancel(self, grace_seconds: float = 10.0) -> None:
        """Interrupt the encoder, then force-stop its tree only if necessary."""
        if self.paused:
            try:
                self.request_resume()
            except OSError:
                pass
        with self._lock:
            if self.process.poll() is not None or self.cancelled:
                return
            self.cancelled = True
            targets = []
            if psutil is not None:
                try:
                    root = psutil.Process(self.process.pid)
                    targets = self._process_tree(root)
                except psutil.Error:
                    pass
            if sys.stdout is not None:
                print(f"\n[CANCELLING] {self.label}", flush=True)
            try:
                self._send_command("q")
                self.process.wait(timeout=grace_seconds)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    interrupt = (getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT)
                                 if os.name == "nt" else signal.SIGINT)
                    self.process.send_signal(interrupt)
                    self.process.wait(timeout=3.0)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    try:
                        self.process.terminate()
                        self.process.wait(timeout=5.0)
                    except (OSError, subprocess.TimeoutExpired):
                        self.process.kill()
            # The CLI can exit before a worker child does. Give the whole tree
            # a moment, then terminate and finally kill only surviving members.
            try:
                _, alive = psutil.wait_procs(targets, timeout=2.0) if psutil else ([], [])
            except (AttributeError, psutil.Error):
                alive = []
            for target in alive:
                try:
                    target.terminate()
                except psutil.Error:
                    pass
            try:
                _, alive = psutil.wait_procs(alive, timeout=5.0) if psutil else ([], [])
            except (AttributeError, psutil.Error):
                alive = []
            for target in alive:
                try:
                    target.kill()
                except psutil.Error:
                    pass
            if sys.stdout is not None:
                print(f"[CANCELLED]  {self.label}", flush=True)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self.paused = False
        if self._thread is not None:
            self._thread.join(timeout=0.5)
