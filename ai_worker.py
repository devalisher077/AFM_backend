from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args: Any, **kwargs: Any) -> None:
        return None

load_dotenv(override=True)

from local_llm import load_local_llm, local_model_name
from supabase_stream import build_ai_analysis_row, create_supabase_client, env_bool, env_int, env_str, item_key


KZ_BOOKMAKER_REFERENCE = [
    "Winline KZ", "Tennisi KZ", "Olimpbet KZ", "Parimatch", "1xBet",
    "Fonbet KZ", "Pin-Up KZ", "Ubet", "Betsson", "Бетсити",
]

KZ_LICENSED_CASINO_REFERENCE = {
    "CashVille": {"location": "Бурабай / Щучинск, район озера Щучье", "operator": "ТОО «СВ Менеджмент»"},
    "Bellagio": {"location": "Конаев, ул. Индустриальная, 2Б", "operator": "ТОО «Mega Club»"},
    "Bombay": {"location": "Конаев, ул. Индустриальная, 9/1", "operator": "ТОО «Капшагай Палас»"},
    "Astoria": {"location": "Конаев, ул. Индустриальная, 6/1", "operator": "ТОО «ASTORIA»"},
    "Makao": {"location": "Конаев, ул. Индустриальная, 2А", "operator": "ТОО «РК МАКАО»"},
    "Montana": {"location": "Конаев, ул. Индустриальная, 4", "operator": "ТОО «BESTAM CORPORATION»"},
}


def is_ai_eligible(item: Dict[str, Any], min_risk: int, min_kz: int) -> bool:
    risk = int(item.get("risk_score") or 0)
    kz = int(item.get("kz_score") or 0)
    global_high_risk_floor = max(70, min_risk)
    return risk >= min_risk and (min_kz <= 0 or kz >= min_kz or risk >= global_high_risk_floor)


def fetch_pending_items(client: Any, batch_size: int, pool_size: int, min_risk: int, min_kz: int) -> List[Dict[str, Any]]:
    media_resp = (
        client.table("media_items")
        .select("*")
        .gte("risk_score", min_risk)
        .order("risk_score", desc=True)
        .order("kz_score", desc=True)
        .limit(pool_size)
        .execute()
    )
    media_items = media_resp.data or []
    if not media_items:
        return []

    hashes = [str(x.get("url_hash") or item_key(x)) for x in media_items if x.get("url_hash") or x.get("url")]
    existing: set[str] = set()
    if hashes:
        analysis_resp = client.table("ai_analyses").select("url_hash").in_("url_hash", hashes).execute()
        existing = {str(x.get("url_hash")) for x in (analysis_resp.data or []) if x.get("url_hash")}

    pending = [
        item for item in media_items
        if str(item.get("url_hash") or item_key(item)) not in existing
        and is_ai_eligible(item, min_risk, min_kz)
    ]
    return pending[:batch_size]


def fetch_run_ranks(client: Any, items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    by_run: Dict[str, List[str]] = {}
    for item in items:
        run_id = str(item.get("last_seen_run_id") or "")
        url_hash = str(item.get("url_hash") or item_key(item))
        if run_id and url_hash:
            by_run.setdefault(run_id, []).append(url_hash)

    for run_id, hashes in by_run.items():
        try:
            resp = (
                client.table("run_items")
                .select("run_id,url_hash,rank")
                .eq("run_id", run_id)
                .in_("url_hash", hashes)
                .execute()
            )
            for row in resp.data or []:
                out[str(row.get("url_hash"))] = row
        except Exception:
            continue
    return out


def build_prompt(item: Dict[str, Any]) -> str:
    text_limit = max(500, env_int("LLM_TEXT_LIMIT", env_int("OPENAI_TEXT_LIMIT", 2500)))
    transcript_limit = max(0, env_int("LLM_TRANSCRIPT_LIMIT", env_int("OPENAI_TRANSCRIPT_LIMIT", 1200)))
    comments_limit = max(0, env_int("LLM_COMMENTS_LIMIT", env_int("OPENAI_COMMENTS_LIMIT", 800)))

    comments = item.get("comments") or []
    if not isinstance(comments, list):
        comments = []
    comments_text = "\n".join(["- " + str(x.get("text", "")) for x in comments[:10] if isinstance(x, dict)])

    return f"""
Ты — аналитик AI Media Watch для Казахстана.
Проанализируй candidate на признаки: финансовая пирамида, незаконное казино/ставки, scam investment, crypto referral, AI income bot, possible deepfake context.
Отличай прямую рекламу/воронку от новостей, фильмов и образовательного контента.
Важно: без прямых доказательств не утверждай окончательно "это финансовая пирамида"; формулируй как "признаки/паттерн, требуется проверка".

Верни ТОЛЬКО JSON:
{{
  "risk_score": 0-100,
  "kz_relevance_score": 0-100,
  "threat_type": "...",
  "is_false_positive": true/false,
  "confidence": 0-100,
  "key_signals": ["..."],
  "reasoning_short": "...",
  "recommended_action": "Ignore / Monitor / Review / Escalate"
}}

Candidate:
source_type: {item.get("source_type")}
platform: {item.get("platform")}
url: {item.get("url")}
title: {item.get("title")}
channel: {item.get("channel_name")}
published_at: {item.get("published_at")}
rule_based_risk: {item.get("risk_score")}
rule_based_kz: {item.get("kz_score")}
known_kz_bookmaker_brands_detected: {json.dumps(item.get("bookmaker_brands") or [], ensure_ascii=False)}
known_kz_bookmaker_reference_list: {json.dumps(KZ_BOOKMAKER_REFERENCE, ensure_ascii=False)}
licensed_kz_casinos_detected: {json.dumps((item.get("raw") or {}).get("licensed_casinos") or [], ensure_ascii=False)}
licensed_kz_casino_reference_list: {json.dumps(KZ_LICENSED_CASINO_REFERENCE, ensure_ascii=False)}
Important bookmaker rule: известные/лицензированные KZ bookmaker brands из reference_list не являются приоритетом этого мониторинга. Если кандидат — обычное промо этих брендов, ставь low risk / false positive / Monitor. Приоритет: финансовые пирамиды, scam investment, crypto referral, AI income bot, неизвестные казино/слоты, зеркала, фейки, affiliate funnel и бренды вне reference_list.
Important casino rule: лицензированные казино РК из licensed_kz_casino_reference_list не являются приоритетом мониторинга, если это обычное упоминание/официальное промо. Приоритет: неизвестные онлайн-казино, зеркала, фейки, affiliate funnel, нелицензированные домены и подозрительные бонусные воронки.
text:
{(item.get("snippet") or "")[:text_limit]}

transcript:
{(item.get("transcript") or "")[:transcript_limit]}

comments:
{comments_text[:comments_limit]}

links:
{json.dumps((item.get("links") or [])[:15], ensure_ascii=False)}
""".strip()


def analyze_item(llm_client: Any, item: Dict[str, Any], max_retries: int) -> Dict[str, Any]:
    prompt = build_prompt(item)
    last_error = ""
    for attempt in range(1, max_retries + 1):
        try:
            return llm_client.analyze_prompt(prompt)
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries:
                time.sleep(min(20, 2 ** attempt))
    return {"error": last_error}


def process_batch(client: Any, llm_client: Any, items: List[Dict[str, Any]], model: str, concurrency: int, max_retries: int) -> int:
    ranks = fetch_run_ranks(client, items)

    def one(item: Dict[str, Any]) -> Dict[str, Any]:
        url_hash = str(item.get("url_hash") or item_key(item))
        run_id = str(item.get("last_seen_run_id") or item.get("first_seen_run_id") or "")
        rank_row = ranks.get(url_hash) or {}
        rank = rank_row.get("rank")
        analysis = analyze_item(llm_client, item, max_retries)
        error = str(analysis.get("error") or "") if isinstance(analysis, dict) else ""
        if error:
            analysis = {}
        return build_ai_analysis_row(url_hash, run_id, rank, model, analysis, error)

    rows: List[Dict[str, Any]] = []
    if concurrency <= 1:
        for item in items:
            rows.append(one(item))
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(one, item) for item in items]
            for future in as_completed(futures):
                rows.append(future.result())

    if rows:
        batch_size = env_int("SUPABASE_BATCH_SIZE", 100)
        for i in range(0, len(rows), batch_size):
            client.table("ai_analyses").upsert(rows[i:i + batch_size], on_conflict="url_hash").execute()
    return len(rows)


def main() -> None:
    model = local_model_name()
    single_item = env_bool("AI_WORKER_SINGLE_ITEM", False)
    batch_size = 1 if single_item else env_int("AI_WORKER_BATCH_SIZE", env_int("LLM_MAX_ITEMS", env_int("OPENAI_MAX_ITEMS", 20)))
    pool_size = env_int("AI_WORKER_POOL_SIZE", max(batch_size * 5, 100))
    poll_seconds = env_int("AI_WORKER_POLL_SECONDS", 30)
    run_once = env_bool("AI_WORKER_ONCE", single_item)
    concurrency = max(1, env_int("LOCAL_LLM_CONCURRENCY", env_int("LLM_CONCURRENCY", 1)))
    max_retries = max(1, env_int("LOCAL_LLM_MAX_RETRIES", env_int("LLM_MAX_RETRIES", 2)))
    min_risk = max(0, env_int("LLM_MIN_RISK_SCORE", env_int("OPENAI_MIN_RISK_SCORE", 40)))
    min_kz = max(0, env_int("LLM_MIN_KZ_SCORE", env_int("OPENAI_MIN_KZ_SCORE", 30)))

    try:
        client = create_supabase_client()
        llm_client = load_local_llm()
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    print("🤖 AI Media Watch worker — local Llama")
    print(f"model={model} batch={batch_size} pool={pool_size} concurrency={concurrency}")
    print(f"filters: min_risk={min_risk} min_kz={min_kz} once={run_once} single_item={single_item}")

    while True:
        try:
            items = fetch_pending_items(client, batch_size, pool_size, min_risk, min_kz)
            if not items:
                print(f"no pending items; sleeping {poll_seconds}s")
                if run_once:
                    break
                time.sleep(poll_seconds)
                continue

            print(f"analyzing {len(items)} pending items")
            done = process_batch(client, llm_client, items, model, concurrency, max_retries)
            print(f"saved ai_analyses: {done}")
        except Exception as e:
            print(f"[WARN] worker loop failed: {e}")
            if run_once:
                raise
            time.sleep(poll_seconds)

        if run_once:
            break


if __name__ == "__main__":
    main()
