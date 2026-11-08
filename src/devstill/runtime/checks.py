"""Bounded checks with process-group cancellation. Workspace isolation is not a sandbox."""

import os
import signal
import subprocess
import sys
import tempfile
import time

from devstill.runtime.editor import validate_checks


def run_checks(repo, commands, deadline, checkpoint=lambda: None, python=None):
    validate_checks(commands)
    results = []
    env = dict(
        os.environ,
        PYTHONPATH=str(repo / "src") + os.pathsep + str(repo),
        # Same-size edits within one timestamp tick must not reuse stale bytecode.
        PYTHONDONTWRITEBYTECODE="1",
    )
    for command in commands:
        checkpoint()
        argv = [python or sys.executable, *command[1:]]
        end = min(deadline, time.perf_counter() + 30)
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                argv,
                cwd=repo,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                while process.poll() is None:
                    checkpoint()
                    if time.perf_counter() >= end:
                        raise TimeoutError("Check deadline exhausted")
                    time.sleep(0.05)
            finally:
                # Also remove descendants left behind by a completed check.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            output.seek(0, os.SEEK_END)
            output.seek(max(0, output.tell() - 10000))
            results.append(
                {
                    "command": argv,
                    "returncode": process.returncode,
                    "output": output.read().decode(errors="replace"),
                }
            )
    return results
