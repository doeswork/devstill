"""Small durable artifacts; proposal identity includes the complete patch payload."""

import hashlib
import json
import os
import re
import tempfile


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def run_directory(root, run_id):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Invalid minimal run ID")
    return root / run_id


def load(root, run_id):
    value = json.loads((run_directory(root, run_id) / "proposal.json").read_text())
    content = {k: v for k, v in value.items() if k != "digest"}
    if value.get("digest") != digest(content) or value.get("id") != run_id:
        raise ValueError("Proposal identity or digest changed")
    return value
