"""Optional, bounded, asynchronous Herdr lifecycle reporting."""

import os
import subprocess
import threading
import time


class HerdrReporter:
    def __init__(self, session_id=None, resume=()):
        self.binary = os.environ.get("HERDR_BIN_PATH")
        self.pane = os.environ.get("HERDR_PANE_ID")
        self.enabled = os.environ.get("HERDR_ENV") == "1" and bool(
            self.binary and self.pane and os.environ.get("HERDR_SOCKET_PATH")
        )
        self.session_id = session_id
        self.resume = tuple(resume)
        self._condition = threading.Condition()
        self._pending = None
        self._last = None
        self._closed = False
        self._seq = time.time_ns()
        self._thread = None
        if self.enabled:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def report(self, state, message=None):
        with self._condition:
            if self._closed or not self.enabled or self._last == (state, message):
                return
            self._last = (state, message)
            self._pending = (state, message)
            self._condition.notify()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending is not None)
                state, message = self._pending
                self._pending = None
            self._seq = max(time.time_ns(), self._seq + 1)
            release = state == "release"
            argv = [
                self.binary,
                "pane",
                "release-agent" if release else "report-agent",
                self.pane,
                "--source",
                "devstill",
                "--agent",
                "devstill",
                "--seq",
                str(self._seq),
            ]
            if not release:
                argv += ["--state", state]
                if message:
                    argv += ["--message", message]
                if self.session_id and self.resume:
                    argv += ["--agent-session-id", self.session_id, "--", *self.resume]
            try:
                subprocess.run(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=0.75,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
            if release:
                return

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._pending = ("release", None)
            self._condition.notify()
        if self._thread:
            self._thread.join(timeout=1.6)
