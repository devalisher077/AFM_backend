#!/usr/bin/env python3

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args: object, **kwargs: object) -> None:
        return None

load_dotenv(override=True)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def terminate_process(proc: subprocess.Popen[bytes], timeout: int = 20) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def main() -> None:
    parser_script = Path(os.getenv("PARSER_SCRIPT", "parser_focus.py").strip() or "parser_focus.py")
    worker_script = Path(os.getenv("AI_WORKER_SCRIPT", "ai_worker.py").strip() or "ai_worker.py")
    keep_worker = env_bool("AI_WORKER_KEEP_RUNNING", False)

    if not parser_script.exists():
        print(f"[ERROR] Parser script not found: {parser_script}")
        sys.exit(1)
    if not worker_script.exists():
        print(f"[ERROR] AI worker script not found: {worker_script}")
        sys.exit(1)

    env = os.environ.copy()
    env["ENABLE_STREAM_UPLOAD"] = "true"
    env["ENABLE_LLM_ANALYSIS"] = "false"
    env["ENABLE_OPENAI_ANALYSIS"] = "false"
    env.setdefault("AI_WORKER_ONCE", "false")

    print("🚀 Starting AI worker")
    worker = subprocess.Popen([sys.executable, str(worker_script)], env=env)
    time.sleep(2)
    if worker.poll() is not None:
        print(f"[ERROR] AI worker exited early with code {worker.returncode}")
        sys.exit(worker.returncode or 1)

    parser_code = 1
    try:
        print("\n🚀 Starting streaming parser")
        parser = subprocess.run([sys.executable, str(parser_script)], env=env)
        parser_code = parser.returncode
    except KeyboardInterrupt:
        print("\n[WARN] Interrupted by user")
        parser_code = 130
    finally:
        if keep_worker:
            print("AI worker left running because AI_WORKER_KEEP_RUNNING=true")
        else:
            print("Stopping AI worker")
            terminate_process(worker)

    if parser_code != 0:
        sys.exit(parser_code)
    print("\n✅ Streaming parser finished")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.default_int_handler)
    main()
