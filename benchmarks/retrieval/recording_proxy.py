"""Local benchmark-only HTTP recorder. Never persist authorization headers."""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx


@contextmanager
def recording_proxy(
    upstream, directory, max_calls=0, *, disable_reasoning=False, sampling=None
):
    """Forward and record model traffic.

    sampling: optional request fields (e.g. {"temperature": 0, "seed": 1})
    applied to every generation request, so both agents decode identically.
    """
    directory.mkdir(parents=True, exist_ok=False)
    origin = urlsplit(upstream)
    target = f"{origin.scheme}://{origin.netloc}"
    lock = threading.Lock()
    counts = {"requests": 0, "model_calls": 0}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.forward()

        def do_POST(self):
            self.forward()

        def forward(self):
            started = time.perf_counter()
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            generation = urlsplit(self.path).path.endswith("/chat/completions")
            with lock:
                counts["requests"] += 1
                number = counts["requests"]
                blocked = bool(
                    generation and max_calls and counts["model_calls"] >= max_calls
                )
                if generation and not blocked:
                    counts["model_calls"] += 1
            prefix = directory / f"{number:04d}"
            if generation and (disable_reasoning or sampling):
                prefix.with_suffix(".original-request").write_bytes(body)
                payload = json.loads(body)
                if disable_reasoning:
                    payload["chat_template_kwargs"] = {
                        **payload.get("chat_template_kwargs", {}),
                        "enable_thinking": False,
                    }
                if sampling:
                    payload.update(sampling)
                body = json.dumps(payload).encode()
            prefix.with_suffix(".request").write_bytes(body)
            record = {
                "id": number,
                "method": self.command,
                "path": self.path,
                "generation": generation,
                "budget_blocked": blocked,
                "reasoning_explicitly_disabled": bool(generation and disable_reasoning),
                "sampling_override": sampling if generation and sampling else None,
            }
            prefix.with_suffix(".json").write_text(json.dumps(record) + "\n")
            # Headers are forwarded in memory, never logged. Drop hop-by-hop headers.
            headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower()
                not in {
                    "host",
                    "connection",
                    "content-length",
                    "transfer-encoding",
                    "accept-encoding",
                }
            }
            headers["Accept-Encoding"] = "identity"
            try:
                if blocked:
                    payload = (
                        b'{"error":{"message":"Benchmark model-call budget exhausted"}}'
                    )
                    prefix.with_suffix(".response").write_bytes(payload)
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    record["status"] = 429
                else:
                    with (
                        httpx.Client(timeout=60, trust_env=False) as client,
                        client.stream(
                            self.command,
                            target + self.path,
                            headers=headers,
                            content=body,
                        ) as response,
                    ):
                        record["status"] = response.status_code
                        record["content_type"] = response.headers.get(
                            "content-type", ""
                        )
                        self.send_response(response.status_code)
                        self.send_header("Content-Type", record["content_type"])
                        self.send_header("Connection", "close")
                        self.end_headers()
                        with prefix.with_suffix(".response").open("wb") as output:
                            for chunk in response.iter_bytes():
                                output.write(chunk)
                                self.wfile.write(chunk)
                                self.wfile.flush()
            except Exception as exc:  # noqa: BLE001 - preserve transport failures
                record["error_type"] = type(exc).__name__
                self.close_connection = True
            finally:
                record["elapsed_seconds"] = time.perf_counter() - started
                temporary = prefix.with_suffix(".tmp")
                temporary.write_text(json.dumps(record, indent=2) + "\n")
                temporary.replace(prefix.with_suffix(".json"))
                self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}{origin.path.rstrip('/')}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


UNREACHABLE = {"ConnectError", "ConnectTimeout"}


def transport_metrics(directory):
    """Count actual upstream attempts; tokens stay unknown if usage is absent."""
    rows = []
    for path in sorted(directory.glob("*.json")):
        record = json.loads(path.read_text())
        if not record["generation"] or record["budget_blocked"]:
            continue
        raw = (
            path.with_suffix(".response").read_text(errors="replace")
            if path.with_suffix(".response").exists()
            else ""
        )
        values = []
        if "text/event-stream" in record.get("content_type", ""):
            for line in raw.splitlines():
                if line.startswith("data:") and line[5:].strip() != "[DONE]":
                    try:
                        values.append(json.loads(line[5:]))
                    except ValueError:
                        pass
        else:
            try:
                values.append(json.loads(raw))
            except ValueError:
                pass
        usage = next(
            (
                v["usage"]
                for v in reversed(values)
                if isinstance(v, dict) and v.get("usage")
            ),
            None,
        )
        reasoning_chars = 0
        for value in values:
            for choice in value.get("choices", []) if isinstance(value, dict) else []:
                message = choice.get("delta", choice.get("message", {}))
                reasoning_chars += sum(
                    len(message.get(key) or "")
                    for key in ("reasoning", "reasoning_content")
                    if isinstance(message.get(key), str)
                )
        rows.append({**record, "usage": usage, "reasoning_chars": reasoning_chars})
    known = [
        r["usage"].get("total_tokens") for r in rows if isinstance(r["usage"], dict)
    ]
    complete = (
        bool(rows)
        and len(known) == len(rows)
        and all(isinstance(n, int) for n in known)
    )
    return {
        "model_calls": len(rows),
        "model_errors": sum(
            r.get("status", 0) >= 400 or "error_type" in r for r in rows
        ),
        # The server could not be reached at all: infrastructure, not the agent.
        "upstream_unreachable": sum(
            r.get("error_type") in UNREACHABLE and "status" not in r for r in rows
        ),
        "usage_complete": complete,
        "total_tokens": sum(known) if complete else None,
        "known_total_tokens": sum(n for n in known if isinstance(n, int)),
        "responses_with_reasoning": sum(r["reasoning_chars"] > 0 for r in rows),
        "reasoning_output_characters": sum(r["reasoning_chars"] for r in rows),
        "budget_rejections": sum(
            json.loads(p.read_text())["budget_blocked"]
            for p in directory.glob("*.json")
        ),
    }
