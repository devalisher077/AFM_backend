#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args: Any, **kwargs: Any) -> None:
        return None

load_dotenv(override=True)

from ai_worker import analyze_item, fetch_pending_items
from local_llm import load_local_llm, local_model_name
from supabase_stream import create_supabase_client, env_int, env_str, item_key


def fetch_by_url_hash(client: Any, url_hash: str) -> Optional[Dict[str, Any]]:
    resp = (
        client.table("media_items")
        .select("*")
        .eq("url_hash", url_hash)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


def fetch_one_item(client: Any) -> Optional[Dict[str, Any]]:
    url_hash = env_str("CHECK_URL_HASH")
    if url_hash:
        return fetch_by_url_hash(client, url_hash)

    min_risk = max(0, env_int("CHECK_MIN_RISK_SCORE", env_int("LLM_MIN_RISK_SCORE", 40)))
    min_kz = max(0, env_int("CHECK_MIN_KZ_SCORE", env_int("LLM_MIN_KZ_SCORE", 30)))
    pool_size = max(1, env_int("CHECK_POOL_SIZE", 50))
    items: List[Dict[str, Any]] = fetch_pending_items(client, 1, pool_size, min_risk, min_kz)
    return items[0] if items else None


def main() -> None:
    try:
        client = create_supabase_client()
        item = fetch_one_item(client)
        if not item:
            print("No item found for local LLM check.")
            sys.exit(1)

        llm = load_local_llm()
        analysis = analyze_item(llm, item, max_retries=1)
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    payload = {
        "model": local_model_name(),
        "url_hash": str(item.get("url_hash") or item_key(item)),
        "title": item.get("title"),
        "url": item.get("url"),
        "platform": item.get("platform"),
        "rule_based_risk": item.get("risk_score"),
        "rule_based_kz": item.get("kz_score"),
        "analysis": analysis,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
