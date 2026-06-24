#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args: Any, **kwargs: Any) -> None:
        return None

load_dotenv(override=True)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_newest_run_dir_after(output_dir: str, started_at: float) -> Optional[Path]:
    root = Path(output_dir)
    if not root.exists():
        return None

    results_name = os.getenv("OUTPUT_JSON", "ai_media_watch_results.json").strip() or "ai_media_watch_results.json"
    candidates = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        results_path = d / results_name
        if not results_path.exists():
            continue

        mtime = max(d.stat().st_mtime, results_path.stat().st_mtime)
        if mtime >= started_at - 5:
            candidates.append((mtime, d))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def create_latest_pointer_from_run_dir(run_dir: Path, latest_path: Path) -> None:
    output_json = os.getenv("OUTPUT_JSON", "ai_media_watch_results.json").strip() or "ai_media_watch_results.json"
    raw_output_json = os.getenv("RAW_OUTPUT_JSON", "ai_media_watch_raw.json").strip() or "ai_media_watch_raw.json"
    llm_output_json = (
        os.getenv("LLM_OUTPUT_JSON")
        or os.getenv("OPENAI_OUTPUT_JSON")
        or "llm_risk_analysis.json"
    ).strip() or "llm_risk_analysis.json"

    results_path = run_dir / output_json
    raw_path = run_dir / raw_output_json
    llm_path = run_dir / llm_output_json

    if not results_path.exists():
        raise FileNotFoundError(f"Cannot create latest pointer: results file not found: {results_path}")

    payload = {
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "generated_at": now_iso(),
        "results_json": str(results_path),
        "raw_json": str(raw_path) if raw_path.exists() else None,
        "openai_json": str(llm_path) if llm_path.exists() else None,
        "llm_json": str(llm_path) if llm_path.exists() else None,
        "created_by": "run_pipeline_safe_v2.py fallback",
    }

    latest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> None:
    parser_script = os.getenv("PARSER_SCRIPT", "parser_focus.py").strip()
    uploader_script = os.getenv("UPLOADER_SCRIPT", "upload_safe.py").strip()
    output_dir = os.getenv("OUTPUT_DIR", "runs").strip() or "runs"
    latest_path = Path(os.getenv("RUNS_LATEST_JSON", f"{output_dir}/latest_run.json").strip())

    delete_latest_before = env_bool("DELETE_LATEST_BEFORE_RUN", True)
    delete_latest_after = env_bool("DELETE_LATEST_AFTER_UPLOAD", True)
    skip_upload = env_bool("SKIP_UPLOAD", False)

    parser_path = Path(parser_script)
    uploader_path = Path(uploader_script)

    if not parser_path.exists():
        print(f"[ERROR] Parser script not found: {parser_path}")
        sys.exit(1)
    if not skip_upload and not uploader_path.exists():
        print(f"[ERROR] Uploader script not found: {uploader_path}")
        sys.exit(1)

    if delete_latest_before and latest_path.exists():
        latest_path.unlink()
        print(f"🧹 Deleted old latest pointer before run: {latest_path}")

    started_at = time.time()

    print("🚀 Step 1/2 — Parser")
    subprocess.run([sys.executable, str(parser_path)], check=True)

    if not latest_path.exists():
        print(f"[WARN] Parser finished but did not create latest pointer: {latest_path}")
        print("[AUTO] Trying to create latest pointer from a new run folder...")
        new_run_dir = find_newest_run_dir_after(output_dir, started_at)
        if not new_run_dir:
            print(f"[ERROR] No new run folder found in {output_dir} after parser start time")
            print("Upload stopped to avoid sending old JSON to Supabase.")
            sys.exit(1)
        create_latest_pointer_from_run_dir(new_run_dir, latest_path)
        print(f"[AUTO] Created latest pointer: {latest_path}")

    with open(latest_path, "r", encoding="utf-8") as f:
        latest = json.load(f)
    print(f"✅ Latest pointer ready: {latest_path}")
    print(f"   run_id: {latest.get('run_id')}")
    print(f"   run_dir: {latest.get('run_dir')}")
    print(f"   results_json: {latest.get('results_json')}")
    print(f"   openai_json: {latest.get('openai_json')}")

    if skip_upload:
        print("⏭️  SKIP_UPLOAD=true — Supabase upload skipped")
        return

    print("\n⬆️  Step 2/2 — Upload to Supabase")
    upload_env = os.environ.copy()
    upload_env["STRICT_LATEST_RUN"] = "true"
    upload_env["RUNS_LATEST_JSON"] = str(latest_path)
    subprocess.run([sys.executable, str(uploader_path)], check=True, env=upload_env)

    if delete_latest_after and latest_path.exists():
        latest_path.unlink()
        print(f"🧹 Deleted latest pointer after successful upload: {latest_path}")

    print("\n✅ Done: parser + upload. Old latest pointer cannot be reused.")


if __name__ == "__main__":
    main()
