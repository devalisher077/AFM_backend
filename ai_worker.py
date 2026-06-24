from __future__ import annotations

import json
import os
import re
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


def format_timecode(seconds: Any) -> str:
    try:
        total = max(0, int(float(seconds or 0)))
    except Exception:
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_youtube_duration(value: str) -> Optional[int]:
    if not value:
        return None
    match = re.fullmatch(
        r"PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
        value,
    )
    if not match:
        return None
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return hours * 3600 + minutes * 60 + seconds


def video_duration_label(raw: Dict[str, Any]) -> str:
    if not isinstance(raw, dict):
        return ""
    if raw.get("duration_label"):
        return str(raw.get("duration_label"))
    if raw.get("duration_seconds") is not None:
        return format_timecode(raw.get("duration_seconds"))
    content_details = raw.get("contentDetails") or {}
    if isinstance(content_details, dict):
        parsed = parse_youtube_duration(str(content_details.get("duration") or ""))
        if parsed is not None:
            return format_timecode(parsed)
    return ""


def build_video_timeline_context(raw: Dict[str, Any]) -> str:
    if not isinstance(raw, dict):
        return ""
    segments = raw.get("transcript_segments") or []
    if not isinstance(segments, list) or not segments:
        return ""

    max_minutes = max(1, env_int("OPENAI_VIDEO_TIMELINE_MINUTES", 20))
    max_chars_per_minute = max(120, env_int("OPENAI_VIDEO_TIMELINE_CHARS_PER_MINUTE", 600))
    buckets: Dict[int, List[str]] = {}
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        try:
            start = int(float(segment.get("start_second") or 0))
        except Exception:
            start = 0
        minute_start = (start // 60) * 60
        text = str(segment.get("text") or "").replace("\n", " ").strip()
        if text:
            buckets.setdefault(minute_start, []).append(text)

    lines: List[str] = []
    for minute_start in sorted(buckets.keys())[:max_minutes]:
        minute_end = minute_start + 60
        text = " ".join(buckets[minute_start]).strip()
        if len(text) > max_chars_per_minute:
            text = text[:max_chars_per_minute].rsplit(" ", 1)[0].strip() + "..."
        lines.append(f"{format_timecode(minute_start)}-{format_timecode(minute_end)} | 60s | {text}")
    return "\n".join(lines)


def load_openai_client() -> Any:
    api_key = env_str("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is empty")
    from openai import OpenAI

    return OpenAI(api_key=api_key)


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
    text_limit = max(500, env_int("OPENAI_TEXT_LIMIT", 2500))
    transcript_limit = max(0, env_int("OPENAI_TRANSCRIPT_LIMIT", 1200))
    comments_limit = max(0, env_int("OPENAI_COMMENTS_LIMIT", 800))

    comments = item.get("comments") or []
    if not isinstance(comments, list):
        comments = []
    comments_text = "\n".join(["- " + str(x.get("text", "")) for x in comments[:10] if isinstance(x, dict)])
    raw = item.get("raw") or {}
    video_timeline = build_video_timeline_context(raw if isinstance(raw, dict) else {})
    duration_label = video_duration_label(raw if isinstance(raw, dict) else {})

    return f"""
Ты — аналитик AI Media Watch для Казахстана.
Проанализируй candidate на признаки: финансовая пирамида, незаконное казино/ставки, scam investment, crypto referral, AI income bot, possible deepfake context.
Отличай прямую рекламу/воронку от новостей, фильмов и образовательного контента.
Важно: без прямых доказательств не утверждай окончательно "это финансовая пирамида"; формулируй как "признаки/паттерн, требуется проверка".
Если candidate — видео и дан video_timeline_by_minute, верни поминутные video_frames: каждый frame должен описывать, о чем этот отрезок, его time_range и duration_seconds.
Все текстовые значения в ответе пиши на русском языке: threat_type, key_signals, video_frames.summary, video_frames.risk_signals, reasoning_short и recommended_action.

Верни ТОЛЬКО JSON:
{{
  "risk_score": 0-100,
  "kz_relevance_score": 0-100,
  "threat_type": "...",
  "is_false_positive": true/false,
  "confidence": 0-100,
  "key_signals": ["..."],
  "video_frames": [
    {{"time_range": "00:00-01:00", "start_second": 0, "end_second": 60, "duration_seconds": 60, "summary": "...", "risk_signals": ["..."]}}
  ],
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
video_duration: {duration_label}
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

video_timeline_by_minute:
{video_timeline}

comments:
{comments_text[:comments_limit]}

links:
{json.dumps((item.get("links") or [])[:15], ensure_ascii=False)}
""".strip()


def analyze_item(openai_client: Any, item: Dict[str, Any], model: str, max_retries: int) -> Dict[str, Any]:
    prompt = build_prompt(item)
    last_error = ""
    for attempt in range(1, max_retries + 1):
        try:
            resp = openai_client.responses.create(
                model=model,
                input=[{"role": "user", "content": prompt}],
            )
            text = getattr(resp, "output_text", "") or ""
            try:
                return json.loads(text)
            except Exception:
                return {"raw_text": text[:4000]}
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries:
                time.sleep(min(20, 2 ** attempt))
    return {"error": last_error}


def process_batch(client: Any, openai_client: Any, items: List[Dict[str, Any]], model: str, concurrency: int, max_retries: int) -> int:
    ranks = fetch_run_ranks(client, items)

    def one(item: Dict[str, Any]) -> Dict[str, Any]:
        url_hash = str(item.get("url_hash") or item_key(item))
        run_id = str(item.get("last_seen_run_id") or item.get("first_seen_run_id") or "")
        rank_row = ranks.get(url_hash) or {}
        rank = rank_row.get("rank")
        analysis = analyze_item(openai_client, item, model, max_retries)
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
    model = env_str("OPENAI_MODEL", "gpt-4.1-mini")
    batch_size = env_int("AI_WORKER_BATCH_SIZE", env_int("OPENAI_MAX_ITEMS", 20))
    pool_size = env_int("AI_WORKER_POOL_SIZE", max(batch_size * 5, 100))
    poll_seconds = env_int("AI_WORKER_POLL_SECONDS", 30)
    run_once = env_bool("AI_WORKER_ONCE", False)
    concurrency = max(1, env_int("OPENAI_CONCURRENCY", 1))
    max_retries = max(1, env_int("OPENAI_MAX_RETRIES", 3))
    min_risk = max(0, env_int("OPENAI_MIN_RISK_SCORE", 40))
    min_kz = max(0, env_int("OPENAI_MIN_KZ_SCORE", 30))

    try:
        client = create_supabase_client()
        openai_client = load_openai_client()
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    print("🤖 AI Media Watch worker")
    print(f"model={model} batch={batch_size} pool={pool_size} concurrency={concurrency}")
    print(f"filters: min_risk={min_risk} min_kz={min_kz} once={run_once}")

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
            done = process_batch(client, openai_client, items, model, concurrency, max_retries)
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
