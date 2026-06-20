#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args: Any, **kwargs: Any) -> None:
        return None

try:
    from supabase import Client, create_client
except ImportError:
    print("[ERROR] supabase package is not installed. Run: .venv/bin/python -m pip install supabase python-dotenv")
    sys.exit(1)

load_dotenv(override=True)


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env_str(name, str(default)))
    except Exception:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def item_key(item: Dict[str, Any]) -> str:
    url = (item.get("url") or "").strip()
    if url:
        return sha256_text(url.lower())
    fallback = "|".join([
        str(item.get("platform") or ""),
        str(item.get("source_type") or ""),
        str(item.get("external_id") or ""),
        str(item.get("title") or ""),
        str(item.get("snippet") or "")[:200],
    ])
    return sha256_text(fallback.lower())


def clean_timestamptz(value: Any) -> Optional[str]:
    if not value:
        return None
    if not isinstance(value, str):
        return None

    if "T" in value and (value.endswith("Z") or "+" in value or value.count(":") >= 2):
        return value
    return None


def chunks(rows: List[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def find_latest_run_dir(output_dir: str = "runs") -> Optional[Path]:
    root = Path(output_dir)
    if not root.exists():
        return None
    candidates: List[Path] = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        if (d / env_str("OUTPUT_JSON", "ai_media_watch_results.json")).exists():
            candidates.append(d)
    if not candidates:
        return None
    return max(candidates, key=lambda x: x.stat().st_mtime)


def resolve_run_paths() -> Tuple[str, Path, Path, Optional[Path], Optional[Path]]:
    run_dir_env = env_str("RUN_DIR")
    output_dir = env_str("OUTPUT_DIR", "runs")
    strict_latest = env_bool("STRICT_LATEST_RUN", False)
    latest_json = Path(env_str("RUNS_LATEST_JSON", f"{output_dir}/latest_run.json"))

    if run_dir_env:
        run_dir = Path(run_dir_env)
        run_id = run_dir.name
        results_path = run_dir / env_str("OUTPUT_JSON", "ai_media_watch_results.json")
        raw_path = run_dir / env_str("RAW_OUTPUT_JSON", "ai_media_watch_raw.json")
        openai_path = run_dir / env_str("OPENAI_OUTPUT_JSON", "openai_risk_analysis.json")
        if not openai_path.exists():
            openai_path = None
        return run_id, results_path, raw_path, openai_path, latest_json

    if latest_json.exists():
        latest = load_json(latest_json)
        run_id = str(latest.get("run_id") or Path(str(latest.get("run_dir", "unknown"))).name)
        results_path = Path(str(latest.get("results_json")))
        raw_path = Path(str(latest.get("raw_json"))) if latest.get("raw_json") else Path("")
        openai_path = Path(str(latest.get("openai_json"))) if latest.get("openai_json") else None
        return run_id, results_path, raw_path, openai_path, latest_json

    if strict_latest:
        print(f"[ERROR] STRICT_LATEST_RUN=true and latest pointer not found: {latest_json}")
        print("Parser did not create a new latest_run.json, so upload is stopped to avoid sending old data.")
        sys.exit(1)


    auto_run_dir = find_latest_run_dir(output_dir)
    if auto_run_dir:
        run_id = auto_run_dir.name
        print(f"[WARN] Latest pointer not found: {latest_json}")
        print(f"[AUTO] Using newest run folder instead: {auto_run_dir}")
        results_path = auto_run_dir / env_str("OUTPUT_JSON", "ai_media_watch_results.json")
        raw_path = auto_run_dir / env_str("RAW_OUTPUT_JSON", "ai_media_watch_raw.json")
        openai_path = auto_run_dir / env_str("OPENAI_OUTPUT_JSON", "openai_risk_analysis.json")
        if not openai_path.exists():
            openai_path = None

        latest_payload = {
            "run_id": run_id,
            "run_dir": str(auto_run_dir),
            "generated_at": now_iso(),
            "results_json": str(results_path),
            "raw_json": str(raw_path),
            "openai_json": str(openai_path) if openai_path else None,
        }
        latest_json.parent.mkdir(parents=True, exist_ok=True)
        with open(latest_json, "w", encoding="utf-8") as f:
            json.dump(latest_payload, f, ensure_ascii=False, indent=2)
        print(f"[AUTO] Re-created latest pointer: {latest_json}")
        return run_id, results_path, raw_path, openai_path, latest_json

    print(f"[ERROR] Latest run pointer not found: {latest_json}")
    print(f"[ERROR] No run folders found in: {output_dir}")
    print("Run parser first or set RUN_DIR=runs/<run_id>")
    sys.exit(1)


def load_openai_items(openai_path: Optional[Path]) -> Tuple[str, List[Dict[str, Any]]]:
    if not openai_path or not openai_path.exists():
        return "", []

    root = load_json(openai_path)
    model = str(root.get("model") or "")

    items: List[Dict[str, Any]] = []
    if isinstance(root.get("files"), list):
        for file_value in root["files"]:
            part_path = Path(str(file_value))
            if not part_path.exists():
                part_path = openai_path.parent / Path(str(file_value)).name
            if not part_path.exists():
                print(f"[WARN] OpenAI part file not found: {file_value}")
                continue
            part = load_json(part_path)
            if not model:
                model = str(part.get("model") or "")
            items.extend(part.get("items", []))
    else:
        items.extend(root.get("items", []))

    return model, items


def build_rows(run_id: str, results: Dict[str, Any], model: str, openai_items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    media_rows: List[Dict[str, Any]] = []
    run_item_rows: List[Dict[str, Any]] = []
    analysis_rows: List[Dict[str, Any]] = []


    for idx, item in enumerate(results.get("items", []), start=1):
        h = item_key(item)
        url = item.get("url")
        raw = item.get("raw") or {}
        if item.get("licensed_casinos"):
            raw = dict(raw)
            raw["licensed_casinos"] = item.get("licensed_casinos") or []
        media_rows.append({
            "url_hash": h,
            "url": url,
            "source_type": item.get("source_type"),
            "platform": item.get("platform"),
            "source_api": item.get("source_api"),
            "query": item.get("query"),
            "title": item.get("title"),
            "snippet": item.get("snippet"),
            "published_at": clean_timestamptz(item.get("published_at")),
            "channel_name": item.get("channel_name"),
            "channel_url": item.get("channel_url"),
            "external_id": item.get("external_id"),
            "links": item.get("links") or [],
            "comments": item.get("comments") or [],
            "transcript": item.get("transcript") or "",
            "raw": raw,
            "kz_score": item.get("kz_score") or 0,
            "kz_signals": item.get("kz_signals") or [],
            "risk_score": item.get("risk_score") or 0,
            "risk_signals": item.get("risk_signals") or [],
            "bookmaker_brands": item.get("bookmaker_brands") or [],
            "threat_type": item.get("threat_type"),
            "status": item.get("status"),
            "first_seen_run_id": run_id,
            "last_seen_run_id": run_id,
            "last_seen_at": now_iso(),
            "updated_at": now_iso(),
        })

        run_item_rows.append({
            "run_id": run_id,
            "url_hash": h,
            "rank": idx,
            "platform": item.get("platform"),
            "title": item.get("title"),
            "url": url,
            "rule_based_risk": item.get("risk_score") or 0,
            "rule_based_kz": item.get("kz_score") or 0,
            "threat_type": item.get("threat_type"),
            "status": item.get("status"),
        })

    for ai in openai_items:
        h = item_key(ai)
        analysis = ai.get("openai_analysis") or {}
        analysis_rows.append({
            "url_hash": h,
            "run_id": run_id,
            "rank": ai.get("rank"),
            "model": model,
            "risk_score": analysis.get("risk_score") if isinstance(analysis, dict) else None,
            "kz_relevance_score": analysis.get("kz_relevance_score") if isinstance(analysis, dict) else None,
            "threat_type": analysis.get("threat_type") if isinstance(analysis, dict) else None,
            "is_false_positive": analysis.get("is_false_positive") if isinstance(analysis, dict) else None,
            "confidence": analysis.get("confidence") if isinstance(analysis, dict) else None,
            "key_signals": analysis.get("key_signals") if isinstance(analysis, dict) else [],
            "reasoning_short": analysis.get("reasoning_short") if isinstance(analysis, dict) else None,
            "recommended_action": analysis.get("recommended_action") if isinstance(analysis, dict) else None,
            "raw_analysis": analysis if isinstance(analysis, dict) else {},
            "error": ai.get("error"),
            "updated_at": now_iso(),
        })

    return media_rows, run_item_rows, analysis_rows


def upsert_batches(client: Client, table: str, rows: List[Dict[str, Any]], on_conflict: str, batch_size: int) -> None:
    if not rows:
        print(f"  {table}: 0 rows")
        return
    total = 0
    for batch in chunks(rows, batch_size):
        client.table(table).upsert(batch, on_conflict=on_conflict).execute()
        total += len(batch)
        print(f"  {table}: uploaded {total}/{len(rows)}")


def main() -> None:
    supabase_url = env_str("SUPABASE_URL")
    supabase_key = env_str("SUPABASE_SERVICE_ROLE_KEY") or env_str("SUPABASE_KEY")
    batch_size = env_int("SUPABASE_BATCH_SIZE", 100)

    if not supabase_url or not supabase_key:
        print("[ERROR] Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")
        sys.exit(1)

    run_id, results_path, raw_path, openai_path, latest_path = resolve_run_paths()
    if not results_path.exists():
        print(f"[ERROR] Results JSON not found: {results_path}")
        sys.exit(1)

    print("🚀 Upload AI Media Watch run to Supabase")
    print(f"RUN_ID: {run_id}")
    print(f"results: {results_path}")
    print(f"raw: {raw_path if raw_path and raw_path.exists() else 'not found / skipped'}")
    print(f"openai: {openai_path if openai_path else 'not found / skipped'}")

    results = load_json(results_path)
    summary = results.get("summary", {})
    model, openai_items = load_openai_items(openai_path)

    client: Client = create_client(supabase_url, supabase_key)

    scan_run_row = {
        "run_id": run_id,
        "generated_at": summary.get("generated_at"),
        "mode": summary.get("mode"),
        "run_dir": summary.get("run_dir"),
        "save_run_history": summary.get("save_run_history", True),
        "total_candidates": summary.get("total_candidates") or 0,
        "kz_items": summary.get("kz_items") or 0,
        "high_risk_items": summary.get("high_risk_items") or 0,
        "kz_high_risk_items": summary.get("kz_high_risk_items") or 0,
        "review_items": summary.get("review_items") or 0,
        "platform_counts": summary.get("platform_counts") or {},
        "settings": summary.get("settings") or {},
        "google_queries_used": summary.get("google_queries_used") or [],
        "youtube_queries_used": summary.get("youtube_queries_used") or [],
        "results_json_path": str(results_path),
        "raw_json_path": str(raw_path) if raw_path else None,
        "openai_json_path": str(openai_path) if openai_path else None,
        "summary": summary,
        "updated_at": now_iso(),
    }

    print("\n⬆️  Upserting scan run")
    client.table("scan_runs").upsert(scan_run_row, on_conflict="run_id").execute()

    media_rows, run_item_rows, analysis_rows = build_rows(run_id, results, model, openai_items)

    print("\n⬆️  Upserting media items")
    upsert_batches(client, "media_items", media_rows, "url_hash", batch_size)

    print("\n⬆️  Upserting run items")
    upsert_batches(client, "run_items", run_item_rows, "run_id,url_hash", batch_size)

    print("\n⬆️  Upserting AI analyses")
    upsert_batches(client, "ai_analyses", analysis_rows, "url_hash", batch_size)

    print("\n✅ Done")
    print(f"scan_runs: 1")
    print(f"media_items: {len(media_rows)}")
    print(f"run_items: {len(run_item_rows)}")
    print(f"ai_analyses: {len(analysis_rows)}")


if __name__ == "__main__":
    main()
