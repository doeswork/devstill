"""Headless adapters; this module does not import any UI code."""

import json
import sys

from devstill.runtime.artifacts import run_directory, write
from devstill.runtime.service import RuntimeSession, render


def perform(session, action, *, query=None, run_id=None, approved_digest=None):
    if action in {"review", "yolo"}:
        proposal = session.prepare(query or "")
        if action == "yolo" and proposal["status"] == "ready":
            result = session.apply(proposal["id"], proposal["digest"])
            return {**proposal, "status": result["status"]}
        return proposal
    proposal = session.load(run_id)
    if action == "show":
        state = json.loads(
            (run_directory(session.root, run_id) / "result.json").read_text()
        )
        return {**proposal, "status": state["status"]}
    if action == "apply":
        if not approved_digest:
            raise ValueError("Apply requires the reviewed proposal digest")
        return {**proposal, **session.apply(run_id, approved_digest)}
    if action == "discard":
        from devstill.runtime.workspace import writer_lock

        with writer_lock(session.root):
            path = run_directory(session.root, run_id) / "result.json"
            state = json.loads(path.read_text())
            if state["status"] not in {"ready", "failed", "cancelled"}:
                raise ValueError("Only unapplied proposals can be discarded")
            write(path, {**state, "status": "discarded"})
        return {**proposal, "status": "discarded"}
    raise ValueError("Unknown runtime action")


def command(args, config_path, config):
    from devstill.herdr import HerdrReporter

    reporter = HerdrReporter()
    try:
        return _command(args, config_path, config, reporter)
    finally:
        reporter.close()


def _command(args, config_path, config, reporter):
    with RuntimeSession(config_path, config) as session:
        if args.command == "session":
            # One request at a time, retaining encoder/storage but never chat history.
            reporter.report("idle")
            for line in sys.stdin:
                reporter.report("working")
                request = {}
                try:
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        raise TypeError("Expected a request object")
                    value = perform(
                        session,
                        request["action"],
                        query=request.get("query"),
                        run_id=request.get("run_id"),
                        approved_digest=request.get("digest"),
                    )
                    response = {"id": request.get("id"), "result": value}
                except (ValueError, TypeError, KeyError, OSError) as exc:
                    response = {
                        "id": request.get("id") if isinstance(request, dict) else None,
                        "error": str(exc),
                    }
                print(json.dumps(response), flush=True)
                ready = response.get("result", {}).get("status") == "ready"
                reporter.report("blocked" if ready else "idle", "Patch ready for review" if ready else None)
            return 0
        reporter.report("working")
        if args.command == "runs":
            result = perform(
                session, args.action, run_id=args.run_id, approved_digest=args.digest
            )
        else:
            result = perform(
                session, "yolo" if args.yolo else "review", query=args.query
            )
        print(json.dumps(result, indent=2) if args.json else render(result))
        return 0 if result["status"] in {"ready", "applied", "discarded"} else 1


def main():
    import argparse

    from devstill.config import find_config, load_config

    parser = argparse.ArgumentParser(description="Minimal source-grounded editor")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("edit", "task"):
        edit = sub.add_parser(name)
        edit.add_argument("query")
        edit.add_argument("--yolo", action="store_true")
        edit.add_argument("--json", action="store_true")
        edit.add_argument("--engine", choices=["minimal"], default="minimal")
    runs = sub.add_parser("runs")
    runs.add_argument("action", choices=["show", "apply", "discard"])
    runs.add_argument("run_id")
    runs.add_argument("--digest")
    runs.add_argument("--json", action="store_true")
    session = sub.add_parser("session")
    session.add_argument("--stdio", action="store_true", required=True)
    args = parser.parse_args()
    try:
        path = find_config()
        raise SystemExit(command(args, path, load_config(path)))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
