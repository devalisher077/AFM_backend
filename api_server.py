#!/usr/bin/env python3
"""Local HTTP API for launching the AI Media Watch parser from the frontend."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8787

_lock = threading.Lock()
_process: Optional[subprocess.Popen[str]] = None
_last_start: Optional[str] = None


def load_env_file(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            env[key] = value
    return env


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    cors_origin = os.getenv("CORS_ORIGIN", "*")
    handler.send_response(status)
    if status != 204:
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", cors_origin)
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    if status != 204:
        handler.wfile.write(body)


def process_status() -> Dict[str, Any]:
    global _process
    if _process is None:
        return {"running": False, "returncode": None, "last_start": _last_start}
    return {
        "running": _process.poll() is None,
        "returncode": _process.poll(),
        "pid": _process.pid,
        "last_start": _last_start,
    }


def start_parser() -> Dict[str, Any]:
    global _last_start, _process
    with _lock:
        if _process is not None and _process.poll() is None:
            return {"started": False, **process_status()}

        env = os.environ.copy()
        env.update(load_env_file(ENV_PATH))
        env["ENABLE_STREAM_UPLOAD"] = "true"
        env["ENABLE_OPENAI_ANALYSIS"] = env.get("ENABLE_OPENAI_ANALYSIS", "false")

        script = ROOT / env.get("PIPELINE_SCRIPT", "run_streaming_pipeline.py")
        if not script.exists():
            raise FileNotFoundError(f"Pipeline script not found: {script}")

        _process = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(ROOT),
            env=env,
            text=True,
        )
        _last_start = datetime.now(timezone.utc).isoformat()
        return {"started": True, **process_status()}


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "AFMBackendAPI/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[api] {self.address_string()} - {fmt % args}")

    def do_OPTIONS(self) -> None:
        json_response(self, 204, {})

    def do_GET(self) -> None:
        if self.path in {"/", "/health"}:
            json_response(self, 200, {"ok": True, **process_status()})
            return
        if self.path == "/parse/status":
            json_response(self, 200, process_status())
            return
        json_response(self, 404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/parse/start":
            json_response(self, 404, {"error": "Not found"})
            return
        try:
            result = start_parser()
            status = 202 if result.get("started") else 409
            json_response(self, status, result)
        except Exception as exc:
            json_response(self, 500, {"error": str(exc)})


def main() -> None:
    env = load_env_file(ENV_PATH)
    for key, value in env.items():
        os.environ.setdefault(key, value)
    host = os.getenv("HOST") or env.get("HOST") or DEFAULT_HOST
    port = int(os.getenv("PORT") or env.get("PORT") or DEFAULT_PORT)
    server = ThreadingHTTPServer((host, port), ApiHandler)
    print(f"AFM backend API listening on http://{host}:{port}")
    print("POST /parse/start launches the streaming pipeline")
    server.serve_forever()


if __name__ == "__main__":
    main()
