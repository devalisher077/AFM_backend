#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_int(name: str, default: int) -> int:
    raw = env_str(name, str(default))
    try:
        return int(raw)
    except Exception:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    if not value or not isinstance(value, str):
        return None
    if "T" in value and (value.endswith("Z") or "+" in value or value.count(":") >= 2):
        return value
    return None


def chunks(rows: List[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def create_supabase_client() -> Any:
    from supabase import create_client

    supabase_url = env_str("SUPABASE_URL")
    supabase_key = env_str("SUPABASE_SERVICE_ROLE_KEY") or env_str("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
    return create_client(supabase_url, supabase_key)


def build_media_row(run_id: str, item: Dict[str, Any]) -> Dict[str, Any]:
    h = item_key(item)
    ts = now_iso()
    raw = item.get("raw") or {}
    if item.get("licensed_casinos"):
        raw = dict(raw)
        raw["licensed_casinos"] = item.get("licensed_casinos") or []
    return {
        "url_hash": h,
        "url": item.get("url"),
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
        "first_seen_at": ts,
        "last_seen_at": ts,
        "updated_at": ts,
    }


def build_run_item_row(run_id: str, item: Dict[str, Any], rank: int) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "url_hash": item_key(item),
        "rank": rank,
        "platform": item.get("platform"),
        "title": item.get("title"),
        "url": item.get("url"),
        "rule_based_risk": item.get("risk_score") or 0,
        "rule_based_kz": item.get("kz_score") or 0,
        "threat_type": item.get("threat_type"),
        "status": item.get("status"),
        "created_at": now_iso(),
    }


def upsert_batches(client: Any, table: str, rows: List[Dict[str, Any]], on_conflict: str, batch_size: int) -> int:
    if not rows:
        return 0
    total = 0
    for batch in chunks(rows, max(1, batch_size)):
        client.table(table).upsert(batch, on_conflict=on_conflict).execute()
        total += len(batch)
    return total


class SupabaseStreamWriter:
    def __init__(self, run_id: str, batch_size: Optional[int] = None) -> None:
        self.run_id = run_id
        self.batch_size = batch_size or env_int("SUPABASE_STREAM_BATCH_SIZE", env_int("SUPABASE_BATCH_SIZE", 100))
        self.client = create_supabase_client()
        self.discovery_rank = 0
        self.enabled = True

    def upsert_scan_run(self, summary: Dict[str, Any], paths: Optional[Dict[str, Any]] = None) -> None:
        paths = paths or {}
        row = {
            "run_id": self.run_id,
            "generated_at": summary.get("generated_at") or now_iso(),
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
            "results_json_path": paths.get("results_json_path"),
            "raw_json_path": paths.get("raw_json_path"),
            "openai_json_path": paths.get("openai_json_path"),
            "summary": summary,
            "updated_at": now_iso(),
        }
        self.client.table("scan_runs").upsert(row, on_conflict="run_id").execute()

    def upsert_candidates(self, items: List[Dict[str, Any]], rank_start: Optional[int] = None) -> int:
        if not items:
            return 0
        media_rows = [build_media_row(self.run_id, item) for item in items]
        run_rows: List[Dict[str, Any]] = []
        for offset, item in enumerate(items, start=1):
            if rank_start is None:
                self.discovery_rank += 1
                rank = self.discovery_rank
            else:
                rank = rank_start + offset - 1
            run_rows.append(build_run_item_row(self.run_id, item, rank))

        upsert_batches(self.client, "media_items", media_rows, "url_hash", self.batch_size)
        upsert_batches(self.client, "run_items", run_rows, "run_id,url_hash", self.batch_size)
        return len(items)


def build_ai_analysis_row(url_hash: str, run_id: str, rank: Optional[int], model: str, analysis: Dict[str, Any], error: str = "") -> Dict[str, Any]:
    return {
        "url_hash": url_hash,
        "run_id": run_id,
        "rank": rank,
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
        "error": error or None,
        "analyzed_at": now_iso(),
        "updated_at": now_iso(),
    }
