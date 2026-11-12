"""Private, atomic snapshots of a TUI session; never replay work on restore."""

import json
import re
import uuid

from devstill.config import resolve_index_path
from devstill.runtime.artifacts import write


class UiSession:
    def __init__(self, config_path, config, resume=None):
        self.id = resume or uuid.uuid4().hex
        if not re.fullmatch(r"[0-9a-f]{32}", self.id):
            raise ValueError("Invalid Devstill session ID")
        self.path = (
            resolve_index_path(config_path, config).parent
            / "sessions"
            / (self.id + ".json")
        )
        self.config_path = str(config_path.resolve())
        self.data = {}
        if resume:
            self.data = json.loads(self.path.read_text())
            if (
                self.data.get("version") != 1
                or self.data.get("config") != self.config_path
            ):
                raise ValueError(
                    "Session belongs to another configuration or has an unsupported version"
                )
        self._saved = None

    def save(self, data):
        value = dict(data, version=1, config=self.config_path)
        if value != self._saved:
            write(self.path, value)
            self._saved = json.loads(json.dumps(value))
