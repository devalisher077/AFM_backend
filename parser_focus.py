#!/usr/bin/env python3


from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urljoin

try:
    import requests
except ImportError:
    print("[ERROR] requests не установлен. Установи: .venv/bin/python -m pip install requests python-dotenv beautifulsoup4 openai youtube-transcript-api")
    sys.exit(1)

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args: Any, **kwargs: Any) -> None:
        return None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

load_dotenv(override=True)

SERPAPI_URL = "https://serpapi.com/search.json"
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_WATCH_BASE = "https://www.youtube.com/watch?v="


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def split_pipe(raw: str) -> List[str]:
    return [x.strip() for x in raw.split("||") if x.strip()]


def mask_secret(value: str, left: int = 6, right: int = 4) -> str:
    if not value:
        return "<empty>"
    if len(value) <= left + right:
        return value[:2] + "..."
    return value[:left] + "..." + value[-right:]


KZ_PATTERNS = [
    r"\bказахстан\b", r"\bkazakhstan\b", r"\bқазақстан\b", r"\bkz\b",
    r"\bалматы\b", r"\bастана\b", r"\bнур-султан\b", r"\bшымкент\b",
    r"\bатырау\b", r"\bақтау\b", r"\bактау\b", r"\bактобе\b", r"\bақтөбе\b",
    r"\bкараганда\b", r"\bқарағанды\b", r"\bсемей\b", r"\bорал\b", r"\bуральск\b",
    r"\bтенге\b", r"\bтг\b", r"₸", r"\bkaspi\b", r"\bhalyk\b", r"\bforte\b", r"\.kz\b",
]

RISK_KEYWORDS: Dict[str, List[str]] = {
    "guaranteed_income": [
        "гарантированный доход", "без риска", "гарантия дохода", "доход каждый день",
        "пассивный доход", "ежедневный доход", "стабильный доход", "быстрый заработок",
        "легкий заработок", "лёгкий заработок", "доход без вложений", "заработок без вложений",
        "деньги работают на вас", "автоматический доход",
    ],
    "daily_return": [
        "% в день", "процентов в день", "0.8% в день", "1.5% в день", "2% в день", "3% в день",
        "доходность в день", "каждый день вывожу", "вывожу каждый день", "прибыль каждый день",
        "ежедневно вывожу", "вывод каждый день",
    ],
    "financial_pyramid": [
        "финансовая пирамида", "пирамида", "млм", "mlm", "уровней", "21 уровень",
        "структура", "реферальная программа", "реферальный доход", "реферальная ссылка",
        "пригласи друга", "приглашай друзей", "за каждого друга", "за 5 друзей", "команда",
    ],
    "marketplace_pyramid": [
        "маркетплейс", "пункт выдачи заказов", "пвз", "инвестиционный магазин", "виртуальный магазин",
        "купить магазин", "доход от магазина", "гарантированный доход от магазина",
    ],
    "crypto": [
        "usdt", "crypto", "крипта", "криптовалюта", "токен", "token", "bep", "bnb",
        "trust wallet", "metamask", "safepal", "bsc", "web3", "смарт-контракт", "binance",
    ],
    "gambling": [
        "казино", "casino", "ставки на спорт", "букмекер", "беттинг", "betting", "слоты", "слот",
        "book of ra", "фриспин", "free spin", "депозит бонус", "казино бонус", "промокод казино",
    ],
    "closed_funnel": [
        "закрытый клуб", "закрытая группа", "закрытый канал", "пиши в лс", "пиши в директ",
        "whatsapp", "ватсап", "telegram", "телеграм", "ссылка в комментариях", "ссылка в описании",
        "мест мало", "успей", "только сегодня", "осталось мест", "перейди по ссылке",
    ],
    "ai_income_bot": [
        "ai бот", "ии бот", "нейробот", "бот торгует", "искусственный интеллект зарабатывает",
        "нейросеть зарабатывает", "trading bot", "trade bot", "бот для заработка", "ai trading",
    ],
    "deepfake_context": [
        "дипфейк", "deepfake", "клон голоса", "ai voice", "голос ии", "нейроголос",
        "известный человек", "знаменитость", "президент", "основатель kaspi", "банкир рекомендует",
    ],
}

NEGATIVE_CONTEXT = [
    "финансовая грамотность", "дисклеймер", "не является индивидуальной инвестиционной рекомендацией",
    "новости", "телеканал", "разоблачение", "предупреждает", "предупредили", "как распознать",
    "как не попасть", "борьба с", "пресечена", "задержали", "суд", "приговор", "расследование",
    "депозит", "кфгд", "облигации", "дивиденды", "kase", "енпф", "налоги", "финансовый обзор",
    "комедия", "фильм", "сериал", "трейлер",
]

SHORT_LINK_DOMAINS = {
    "bit.ly", "goo.gl", "tinyurl.com", "t.co", "clck.ru", "cutt.ly", "taplink.cc", "taplink.kz",
    "linktr.ee", "wa.me", "api.whatsapp.com", "forms.gle", "docs.google.com",
}

LANDING_PAGE_DOMAINS = SHORT_LINK_DOMAINS | {
    "sites.google.com", "forms.gle", "docs.google.com", "google.com", "www.google.com",
    "beacons.ai", "bio.site", "msha.ke", "instabio.cc", "teletype.in", "telegra.ph",
}

SOCIAL_PUBLIC_PAGE_PLATFORMS = {
    "instagram_reel", "instagram", "tiktok", "tiktok_video",
}


KZ_BOOKMAKER_BRANDS: Dict[str, List[str]] = {
    "Winline KZ": ["winline", "winline kz", "winline.kz", "винлайн"],
    "Tennisi KZ": ["tennisi", "tennisi kz", "tennisi.kz", "тенниси"],
    "Olimpbet KZ": ["olimpbet", "olimpbet kz", "olimpbet.kz", "olimp bet", "олимпбет", "олимп"],
    "Parimatch": ["parimatch", "parimatch kz", "parimatch.kz", "pari match", "париматч", "пари матч"],
    "1xBet": ["1xbet", "1xbet kz", "1xbet.kz", "1x bet", "1 x bet", "1хбет", "1х bet", "ван икс бет"],
    "Fonbet KZ": ["fonbet", "fonbet kz", "fonbet.kz", "фонбет"],
    "Pin-Up KZ": ["pin-up", "pin up", "pinup", "pin-up kz", "pin-up.kz", "пин ап", "пинап"],
    "Ubet": ["ubet", "u bet", "ubet.kz", "юбет"],
    "Betsson": ["betsson", "betsson kz", "betsson.kz", "бетссон", "бетсон"],
    "Бетсити": ["бетсити", "betcity", "betcity kz", "betcity.kz", "bet city"],
}

KZ_LICENSED_CASINOS: Dict[str, Dict[str, Any]] = {
    "CashVille": {
        "aliases": ["cashville", "cash ville", "кэшвилл", "кэш вилл"],
        "location": "Бурабай / Щучинск, район озера Щучье",
        "operator": "ТОО «СВ Менеджмент»",
    },
    "Bellagio": {
        "aliases": ["bellagio", "белладжио", "беладжио"],
        "location": "Конаев, ул. Индустриальная, 2Б",
        "operator": "ТОО «Mega Club»",
    },
    "Bombay": {
        "aliases": ["bombay", "бомбей"],
        "location": "Конаев, ул. Индустриальная, 9/1",
        "operator": "ТОО «Капшагай Палас»",
    },
    "Astoria": {
        "aliases": ["astoria", "астория"],
        "location": "Конаев, ул. Индустриальная, 6/1",
        "operator": "ТОО «ASTORIA»",
    },
    "Makao": {
        "aliases": ["makao", "макао", "rk makao", "рк макао"],
        "location": "Конаев, ул. Индустриальная, 2А",
        "operator": "ТОО «РК МАКАО»",
    },
    "Montana": {
        "aliases": ["montana", "монтана"],
        "location": "Конаев, ул. Индустриальная, 4",
        "operator": "ТОО «BESTAM CORPORATION»",
    },
}

BOOKMAKER_PROMO_TERMS = [
    "промокод", "promo code", "promocode", "бонус", "депозит", "фрибет", "freebet",
    "ставка", "ставки", "коэффициент", "экспресс", "регистрация", "зеркало", "вывод",
    "букмекер", "беттинг", "betting", "казино", "слоты", "онлайн казино",
]


@dataclass
class Candidate:
    source_type: str
    platform: str
    source_api: str
    query: str
    title: str
    url: str
    snippet: str
    published_at: Optional[str]
    channel_name: str = ""
    channel_url: str = ""
    external_id: str = ""
    links: List[str] = None
    comments: List[Dict[str, Any]] = None
    transcript: str = ""
    raw: Dict[str, Any] = None
    kz_score: int = 0
    kz_signals: List[str] = None
    risk_score: int = 0
    risk_signals: List[str] = None
    bookmaker_brands: List[str] = None
    licensed_casinos: List[str] = None
    threat_type: str = "Low Risk / Unknown"
    status: str = "Low Risk / Unknown"
    openai_analysis: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.links is None:
            self.links = []
        if self.comments is None:
            self.comments = []
        if self.raw is None:
            self.raw = {}
        if self.kz_signals is None:
            self.kz_signals = []
        if self.risk_signals is None:
            self.risk_signals = []
        if self.bookmaker_brands is None:
            self.bookmaker_brands = []
        if self.licensed_casinos is None:
            self.licensed_casinos = []


def normalize_text(text: str) -> str:
    text = text or ""
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def detect_kz_bookmakers(text: str) -> List[str]:
    t = normalize_text(text)
    found: List[str] = []
    for brand, aliases in KZ_BOOKMAKER_BRANDS.items():
        for alias in aliases:

            if alias.lower() in t:
                found.append(brand)
                break
    return sorted(set(found))


def detect_kz_licensed_casinos(text: str) -> List[str]:
    t = normalize_text(text)
    found: List[str] = []
    for brand, info in KZ_LICENSED_CASINOS.items():
        for alias in info.get("aliases", []):
            if str(alias).lower() in t:
                found.append(brand)
                break
    return sorted(set(found))


def has_bookmaker_promo_context(text: str) -> bool:
    t = normalize_text(text)
    return any(term in t for term in BOOKMAKER_PROMO_TERMS)


def score_kz(text: str) -> Tuple[int, List[str]]:
    t = normalize_text(text)
    signals: List[str] = []
    for pattern in KZ_PATTERNS:
        if re.search(pattern, t, flags=re.IGNORECASE):
            signals.append(pattern.replace("\\b", "").replace("\\", ""))
    unique = sorted(set(signals))

    score = 0
    if any(s in unique for s in ["казахстан", "kazakhstan", "қазақстан"]):
        score += 40
    if any(s in unique for s in ["тенге", "тг", "₸"]):
        score += 30
    if any(s in unique for s in ["kaspi", "halyk", "forte"]):
        score += 30
    if any(city in unique for city in ["алматы", "астана", "шымкент", "атырау", "актау", "ақтау", "актобе", "ақтөбе", "караганда", "қарағанды", "семей", "орал", "уральск"]):
        score += 25
    if ".kz" in t:
        score += 30
    return min(score, 100), unique[:25]


def score_risk(text: str) -> Tuple[int, List[str], str]:
    t = normalize_text(text)
    matched_categories: List[str] = []
    matched_terms: List[str] = []

    weights = {
        "guaranteed_income": 25,
        "daily_return": 45,
        "financial_pyramid": 35,
        "marketplace_pyramid": 30,
        "crypto": 20,
        "gambling": 40,
        "closed_funnel": 25,
        "ai_income_bot": 30,
        "deepfake_context": 20,
    }

    score = 0
    for category, terms in RISK_KEYWORDS.items():
        hit = False
        for term in terms:
            if term.lower() in t:
                hit = True
                matched_terms.append(term)
        if hit:
            matched_categories.append(category)
            score += weights.get(category, 10)

    cats = set(matched_categories)
    if {"guaranteed_income", "financial_pyramid"}.issubset(cats):
        score += 20
    if {"daily_return", "closed_funnel"}.issubset(cats):
        score += 20
    if {"crypto", "financial_pyramid"}.issubset(cats):
        score += 15
    if {"ai_income_bot", "guaranteed_income"}.issubset(cats):
        score += 15
    if {"gambling", "closed_funnel"}.issubset(cats):
        score += 15
    if {"deepfake_context", "guaranteed_income"}.issubset(cats):
        score += 20

    negative_hits = [x for x in NEGATIVE_CONTEXT if x in t]
    direct_promo_cats = {"daily_return", "closed_funnel", "financial_pyramid", "marketplace_pyramid", "gambling", "ai_income_bot"}
    if negative_hits and not (cats & direct_promo_cats):
        score -= 35
        matched_terms.append("negative_context:" + ",".join(negative_hits[:3]))
    elif negative_hits and score < 70:
        score -= 15
        matched_terms.append("negative_context:" + ",".join(negative_hits[:3]))

    score = max(0, min(score, 100))

    if "gambling" in cats:
        threat_type = "Illegal Gambling"
    elif "marketplace_pyramid" in cats and ("guaranteed_income" in cats or "closed_funnel" in cats):
        threat_type = "Potential Marketplace Pyramid Pattern / Needs Review"
    elif "ai_income_bot" in cats and ("guaranteed_income" in cats or "crypto" in cats):
        threat_type = "Suspicious AI Income Bot"
    elif "crypto" in cats and ("financial_pyramid" in cats or "daily_return" in cats or "closed_funnel" in cats):
        threat_type = "Crypto Referral / Income Scheme"
    elif "financial_pyramid" in cats or "daily_return" in cats or "guaranteed_income" in cats:
        threat_type = "Potential Scam Investment / Pyramid Pattern / Needs Review"
    elif "deepfake_context" in cats:
        threat_type = "Possible Deepfake Context"
    else:
        threat_type = "Low Risk / Unknown"

    signals = sorted(set(matched_categories + matched_terms))
    return score, signals[:60], threat_type


def status_from_scores(kz_score: int, risk_score: int) -> str:
    if kz_score >= 50 and risk_score >= 70:
        return "KZ High Risk"
    if kz_score >= 50 and risk_score >= 40:
        return "KZ Review"
    if risk_score >= 70:
        return "Global High Risk / KZ Unknown"
    if risk_score >= 40:
        return "Global Review / KZ Unknown"
    if kz_score >= 50:
        return "KZ Source / Low Risk"
    return "Low Risk / Unknown"


def enrich_candidate_scores(c: Candidate) -> Candidate:
    combined = "\n".join([
        c.title or "",
        c.snippet or "",
        c.url or "",
        c.channel_name or "",
        c.transcript or "",
        "\n".join([str(x.get("text", "")) for x in c.comments[:30]]),
        "\n".join(c.links or []),
    ])
    c.kz_score, c.kz_signals = score_kz(combined)
    c.risk_score, c.risk_signals, c.threat_type = score_risk(combined)

    c.licensed_casinos = detect_kz_licensed_casinos(combined)
    if c.licensed_casinos:
        c.risk_signals.extend(["licensed_kz_casino:" + b for b in c.licensed_casinos])
        exclude_licensed = env_bool("EXCLUDE_LICENSED_KZ_CASINOS", True)
        if exclude_licensed:
            c.risk_signals.append("excluded_licensed_kz_casino")
            c.threat_type = "Licensed KZ Casino / Excluded"
            c.status = "Excluded / Licensed KZ Casino"
            c.risk_score = min(c.risk_score, env_int("LICENSED_KZ_CASINO_MAX_RISK", 10))
            c.kz_score = max(c.kz_score, 70)
            c.risk_signals = sorted(set(c.risk_signals))[:80]
            return c


    c.bookmaker_brands = detect_kz_bookmakers(combined)
    if c.bookmaker_brands:
        c.risk_signals.extend(["known_kz_bookmaker_brand:" + b for b in c.bookmaker_brands])
        exclude_known = env_bool("EXCLUDE_KNOWN_BOOKMAKERS", True)
        if exclude_known:


            c.risk_signals.append("excluded_known_kz_bookmaker")
            c.threat_type = "Known KZ Bookmaker / Excluded"
            c.status = "Excluded / Known KZ Bookmaker"
            c.risk_score = min(c.risk_score, env_int("KNOWN_BOOKMAKER_MAX_RISK", 10))
            c.kz_score = max(c.kz_score, 70)
            c.risk_signals = sorted(set(c.risk_signals))[:80]
            return c
        else:
            if has_bookmaker_promo_context(combined):
                c.risk_signals.append("bookmaker_promo_context")
                c.threat_type = "Known Bookmaker / Betting Promotion"
                c.risk_score = max(c.risk_score, 55)
                c.kz_score = max(c.kz_score, 70)
            else:
                c.threat_type = "Known Bookmaker Mention"
                c.risk_score = max(c.risk_score, 20)

    c.risk_signals = sorted(set(c.risk_signals))[:80]
    c.status = status_from_scores(c.kz_score, c.risk_score)
    return c


def classify_platform(url: str) -> str:
    u = url.lower()
    parsed = urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "")
    path = parsed.path.lower()
    if "t.me/s/" in u:
        return "telegram_public"
    if "t.me/" in u or "telegram.me/" in u:
        return "telegram_link"
    if "instagram.com/reel" in u or "instagram.com/reels" in u:
        return "instagram_reel"
    if "instagram.com/" in u:
        return "instagram"
    if domain in {"tiktok.com", "vm.tiktok.com", "vt.tiktok.com"} or domain.endswith(".tiktok.com"):
        if "/video/" in path or "/t/" in path:
            return "tiktok_video"
        return "tiktok"
    if "youtube.com/watch" in u or "youtu.be/" in u or "youtube.com/shorts" in u:
        return "youtube_link"
    if "wa.me/" in u or "api.whatsapp.com" in u:
        return "whatsapp_link"
    if domain == "sites.google.com" or domain.endswith(".sites.google.com"):
        return "google_site"
    if domain in {"forms.gle", "docs.google.com"}:
        return "google_form_or_doc"
    if domain in SHORT_LINK_DOMAINS:
        return "short_or_landing_link"
    return "web"


def extract_links(text: str) -> List[str]:
    if not text:
        return []
    pattern = r"https?://[^\s\)\]\}\"'<>]+"
    links = re.findall(pattern, text)
    return sorted(set([x.rstrip(".,;!") for x in links]))


def extract_telegram_usernames(text: str) -> List[str]:
    if not text:
        return []
    usernames = set()
    patterns = [
        r"https?://t\.me/s/([A-Za-z0-9_]{4,})",
        r"https?://t\.me/([A-Za-z0-9_]{4,})",
        r"@([A-Za-z0-9_]{4,})",
    ]
    for p in patterns:
        for m in re.findall(p, text):
            bad = {"share", "iv", "addstickers", "joinchat", "boost", "proxy", "c"}
            if m.lower() not in bad and not m.lower().endswith("bot"):
                usernames.add(m)
            elif m.lower().endswith("bot"):
                usernames.add(m)
    return sorted(usernames)


def normalize_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def is_social_public_page_candidate(c: Candidate) -> bool:
    return c.platform in SOCIAL_PUBLIC_PAGE_PLATFORMS


def is_landing_page_candidate(c: Candidate) -> bool:
    domain = normalize_domain(c.url)
    if c.platform in {"google_site", "google_form_or_doc", "short_or_landing_link"}:
        return True
    if domain in LANDING_PAGE_DOMAINS:
        return True
    return c.platform == "web" and bool(domain) and any(
        term in normalize_text(c.url)
        for term in ["income", "zarabot", "tabys", "bonus", "casino", "crypto", "usdt", "invest"]
    )


def extract_public_page_metadata(url: str, html: str, max_script_chars: int = 6000) -> Dict[str, Any]:
    if BeautifulSoup is None:
        return {"title": "", "description": "", "links": [], "script_text": ""}

    soup = BeautifulSoup(html, "html.parser")

    def meta_value(*selectors: str) -> str:
        for selector in selectors:
            el = soup.select_one(selector)
            if el:
                value = el.get("content") or el.get("value") or el.get_text(" ", strip=True)
                if value:
                    return str(value).strip()
        return ""

    title = meta_value('meta[property="og:title"]', 'meta[name="twitter:title"]')
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)

    description = meta_value(
        'meta[property="og:description"]',
        'meta[name="description"]',
        'meta[name="twitter:description"]',
    )

    links: List[str] = []
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        links.append(urljoin(url, href))

    script_chunks: List[str] = []
    for script in soup.select('script[type="application/ld+json"], script#__NEXT_DATA__, script'):
        text = script.get_text(" ", strip=True)
        if not text:
            continue
        if any(term in normalize_text(text) for terms in RISK_KEYWORDS.values() for term in terms):
            script_chunks.append(text[:1500])
        elif len(script_chunks) < 2 and script.get("type") == "application/ld+json":
            script_chunks.append(text[:1000])
        if sum(len(x) for x in script_chunks) >= max_script_chars:
            break

    visible_text = ""
    main = soup.select_one("main") or soup.body
    if main:
        visible_text = main.get_text(" ", strip=True)[:2500]

    return {
        "title": title[:500],
        "description": description[:3000],
        "visible_text": visible_text,
        "script_text": "\n".join(script_chunks)[:max_script_chars],
        "links": sorted(set(links))[:100],
    }


def parse_public_web_page(source: Candidate, delay: float, source_type: str) -> Optional[Candidate]:
    if BeautifulSoup is None:
        print("  [WARN] beautifulsoup4 не установлен — public page parse skipped")
        return None
    if not source.url.startswith(("http://", "https://")):
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AI-Media-Watch/1.0; +https://example.com/bot)",
        "Accept-Language": "ru-RU,ru;q=0.9,kk;q=0.8,en;q=0.7",
    }
    try:
        resp = requests.get(source.url, headers=headers, timeout=30, allow_redirects=True)
        if resp.status_code >= 400:
            print(f"  [WARN] public page {source.url[:90]}: HTTP {resp.status_code}")
            return None
    except requests.RequestException as e:
        print(f"  [WARN] public page {source.url[:90]}: {e}")
        return None

    final_url = resp.url or source.url
    meta = extract_public_page_metadata(final_url, resp.text)
    title = meta.get("title") or source.title
    text_parts = [
        meta.get("description", ""),
        meta.get("visible_text", ""),
        meta.get("script_text", ""),
    ]
    snippet = "\n".join([x for x in text_parts if x]).strip() or source.snippet
    links = sorted(set((source.links or []) + meta.get("links", []) + extract_links(snippet) + [final_url]))

    c = Candidate(
        source_type=source_type,
        platform=classify_platform(final_url),
        source_api="public_page_fetch",
        query=source.query,
        title=title,
        url=final_url,
        snippet=snippet,
        published_at=source.published_at,
        channel_name=source.channel_name,
        channel_url=source.channel_url,
        external_id=source.external_id,
        links=links,
        raw={
            "seed_url": source.url,
            "seed_source_type": source.source_type,
            "http_status": resp.status_code,
            "content_type": resp.headers.get("content-type", ""),
            "metadata": meta,
        },
    )
    if delay > 0:
        time.sleep(delay)
    return enrich_candidate_scores(c)


def serpapi_google_search(api_key: str, query: str, num: int, gl: str, hl: str, tbs: str = "") -> Dict[str, Any]:
    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": max(1, min(num, 20)),
        "google_domain": "google.kz" if gl.lower() == "kz" else "google.com",
        "gl": gl,
        "hl": hl,
        "no_cache": "true",
    }
    if tbs:
        params["tbs"] = tbs
    resp = requests.get(SERPAPI_URL, params=params, timeout=45)
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"SerpAPI non-JSON response HTTP {resp.status_code}: {resp.text[:500]}")
    if resp.status_code != 200 or data.get("error"):
        raise RuntimeError(f"SerpAPI error: {json.dumps(data, ensure_ascii=False)[:1200]}")
    return data


def candidates_from_serpapi_google(query: str, data: Dict[str, Any]) -> List[Candidate]:
    candidates: List[Candidate] = []

    for item in data.get("organic_results", []) or []:
        url = item.get("link", "") or ""
        title = item.get("title", "") or ""
        snippet = item.get("snippet", "") or ""
        platform = classify_platform(url)
        links = sorted(set([url] + extract_links(snippet)))
        c = Candidate(
            source_type="serpapi_google_organic",
            platform=platform,
            source_api="serpapi_google",
            query=query,
            title=title,
            url=url,
            snippet=snippet,
            published_at=item.get("date"),
            channel_name=item.get("displayed_link", "") or item.get("source", ""),
            links=links,
            raw=item,
        )
        candidates.append(enrich_candidate_scores(c))

    for item in data.get("ads_results", []) or []:
        url = item.get("link", "") or item.get("tracking_link", "") or ""
        title = item.get("title", "") or ""
        snippet = item.get("description", "") or item.get("snippet", "") or ""
        platform = classify_platform(url)
        links = sorted(set([url] + extract_links(snippet)))
        c = Candidate(
            source_type="serpapi_google_ad",
            platform=platform,
            source_api="serpapi_google_ads_results",
            query=query,
            title=title,
            url=url,
            snippet=snippet,
            published_at=None,
            channel_name=item.get("displayed_link", "") or item.get("source", ""),
            links=links,
            raw=item,
        )
        candidates.append(enrich_candidate_scores(c))


    for block_name in ["video_results", "inline_videos"]:
        for item in data.get(block_name, []) or []:
            url = item.get("link", "") or ""
            title = item.get("title", "") or ""
            snippet = item.get("snippet", "") or item.get("description", "") or ""
            platform = classify_platform(url)
            links = sorted(set([url] + extract_links(snippet)))
            c = Candidate(
                source_type=f"serpapi_google_{block_name}",
                platform=platform,
                source_api="serpapi_google",
                query=query,
                title=title,
                url=url,
                snippet=snippet,
                published_at=item.get("date"),
                channel_name=item.get("source", ""),
                links=links,
                raw=item,
            )
            candidates.append(enrich_candidate_scores(c))

    return candidates


def parse_telegram_public_channel(username: str, posts_limit: int, delay: float) -> List[Candidate]:
    if BeautifulSoup is None:
        print("  [WARN] beautifulsoup4 не установлен — Telegram public parse skipped")
        return []

    url = f"https://t.me/s/{username}"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AI-Media-Watch/1.0; +https://example.com/bot)",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"  [WARN] Telegram @{username}: HTTP {resp.status_code}")
            return []
    except requests.RequestException as e:
        print(f"  [WARN] Telegram @{username}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    messages = soup.select(".tgme_widget_message")
    candidates: List[Candidate] = []

    for msg in messages[-posts_limit:]:
        data_post = msg.get("data-post", "")
        message_url = f"https://t.me/{data_post}" if data_post else url
        text_el = msg.select_one(".tgme_widget_message_text")
        text = text_el.get_text(" ", strip=True) if text_el else ""
        date_el = msg.select_one("time")
        published_at = date_el.get("datetime") if date_el else None
        msg_links = [a.get("href") for a in msg.select("a") if a.get("href")]
        msg_links = sorted(set([x for x in msg_links if x]))

        c = Candidate(
            source_type="telegram_public_post",
            platform="telegram_post",
            source_api="telegram_public_preview",
            query=f"telegram:{username}",
            title=f"@{username}",
            url=message_url,
            snippet=text,
            published_at=published_at,
            channel_name=f"@{username}",
            channel_url=url,
            external_id=data_post,
            links=msg_links,
            raw={"data_post": data_post},
        )
        candidates.append(enrich_candidate_scores(c))

    if delay > 0:
        time.sleep(delay)
    return candidates


def youtube_get(endpoint: str, params: Dict[str, Any], api_key: str, retries: int = 2) -> Dict[str, Any]:
    url = f"{YOUTUBE_API_BASE}/{endpoint.lstrip('/')}"
    params = dict(params)
    params["key"] = api_key
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            try:
                last_error = resp.json()
            except Exception:
                last_error = {"status_code": resp.status_code, "text": resp.text[:500]}
            if resp.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        except requests.RequestException as e:
            last_error = {"request_exception": str(e)}
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            break
    raise RuntimeError(f"YouTube API error on {endpoint}: {json.dumps(last_error, ensure_ascii=False)[:1000]}")


def youtube_search(api_key: str, query: str, max_results: int, region_code: str, language: str, published_after: str) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max(1, min(max_results, 50)),
        "order": "relevance",
        "safeSearch": "none",
    }
    if region_code:
        params["regionCode"] = region_code
    if language:
        params["relevanceLanguage"] = language
    if published_after:
        params["publishedAfter"] = published_after
    return youtube_get("search", params, api_key).get("items", [])


def youtube_details(api_key: str, video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not video_ids:
        return out
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        data = youtube_get("videos", {
            "part": "snippet,statistics,contentDetails,status",
            "id": ",".join(chunk),
        }, api_key)
        for item in data.get("items", []):
            if item.get("id"):
                out[item["id"]] = item
    return out


def youtube_comments(api_key: str, video_id: str, max_comments: int) -> List[Dict[str, Any]]:
    if max_comments <= 0:
        return []
    comments: List[Dict[str, Any]] = []
    page_token = None
    while len(comments) < max_comments:
        params: Dict[str, Any] = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": min(100, max_comments - len(comments)),
            "textFormat": "plainText",
            "order": "relevance",
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            data = youtube_get("commentThreads", params, api_key, retries=1)
        except Exception as e:
            comments.append({"error": str(e)[:300]})
            break
        for item in data.get("items", []):
            top = item.get("snippet", {}).get("topLevelComment", {})
            sn = top.get("snippet", {})
            comments.append({
                "comment_id": top.get("id", ""),
                "author": sn.get("authorDisplayName", ""),
                "text": sn.get("textDisplay", "") or sn.get("textOriginal", ""),
                "published_at": sn.get("publishedAt"),
                "like_count": sn.get("likeCount"),
            })
            if len(comments) >= max_comments:
                break
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return comments


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


def get_transcript_data(video_id: str, enabled: bool) -> Tuple[str, List[Dict[str, Any]]]:
    if not enabled:
        return "", []
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return "", []
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["ru", "kk", "en"])
        segments: List[Dict[str, Any]] = []
        for x in transcript:
            text = str(x.get("text", "")).replace("\n", " ").strip()
            if not text:
                continue
            start = float(x.get("start") or 0)
            duration = float(x.get("duration") or 0)
            segments.append({
                "start_second": round(start, 2),
                "end_second": round(start + duration, 2),
                "duration_seconds": round(duration, 2),
                "timecode": format_timecode(start),
                "text": text,
            })
        text = " ".join([x["text"] for x in segments]).strip()
        return text, segments
    except Exception:
        return "", []


def build_youtube_candidate(
    query: str,
    detail: Dict[str, Any],
    comments: List[Dict[str, Any]],
    transcript: str,
    transcript_segments: Optional[List[Dict[str, Any]]] = None,
) -> Candidate:
    video_id = detail.get("id", "")
    snippet = detail.get("snippet", {})
    stats = detail.get("statistics", {})
    content_details = detail.get("contentDetails", {})
    duration_seconds = parse_youtube_duration(str(content_details.get("duration") or ""))
    title = snippet.get("title", "")
    description = snippet.get("description", "")
    channel_title = snippet.get("channelTitle", "")
    published_at = snippet.get("publishedAt")
    url = f"{YOUTUBE_WATCH_BASE}{video_id}"
    links = sorted(set(extract_links(description) + [url]))

    c = Candidate(
        source_type="youtube_api_video",
        platform="youtube_video",
        source_api="youtube_data_api",
        query=query,
        title=title,
        url=url,
        snippet=description,
        published_at=published_at,
        channel_name=channel_title,
        channel_url=f"https://www.youtube.com/channel/{snippet.get('channelId', '')}",
        external_id=video_id,
        links=links,
        comments=comments,
        transcript=transcript,
        raw={
            "video_id": video_id,
            "statistics": stats,
            "contentDetails": content_details,
            "duration_seconds": duration_seconds,
            "duration_label": format_timecode(duration_seconds),
            "transcript_segments": transcript_segments or [],
            "thumbnails": snippet.get("thumbnails", {}),
        },
    )
    return enrich_candidate_scores(c)


def openai_analyze_candidates(
    candidates: List[Candidate],
    output_json: str,
    model: str,
    max_items: int,
    batch_size: int = 100,
) -> List[Dict[str, Any]]:
    api_key = env_str("OPENAI_API_KEY")
    if not api_key:
        print("[WARN] OPENAI_API_KEY пустой — OpenAI analysis skipped")
        return []
    try:
        from openai import OpenAI
    except ImportError:
        print("[WARN] openai package not installed — OpenAI analysis skipped")
        return []

    client = OpenAI(api_key=api_key)

    min_risk_score = max(0, env_int("OPENAI_MIN_RISK_SCORE", 0))
    min_kz_score = max(0, env_int("OPENAI_MIN_KZ_SCORE", 0))
    global_high_risk_floor = max(70, min_risk_score)

    filtered_candidates = [
        c for c in candidates
        if c.risk_score >= min_risk_score
        and (min_kz_score <= 0 or c.kz_score >= min_kz_score or c.risk_score >= global_high_risk_floor)
    ]
    sorted_candidates = sorted(filtered_candidates, key=lambda c: (c.risk_score + c.kz_score * 0.5), reverse=True)
    if max_items and max_items > 0:
        selected = sorted_candidates[:max_items]
    else:
        selected = sorted_candidates

    batch_size = max(1, int(batch_size or 100))
    concurrency = max(1, env_int("OPENAI_CONCURRENCY", 1))
    max_retries = max(1, env_int("OPENAI_MAX_RETRIES", 3))
    text_limit = max(500, env_int("OPENAI_TEXT_LIMIT", 3500))
    transcript_limit = max(0, env_int("OPENAI_TRANSCRIPT_LIMIT", 2500))
    comments_limit = max(0, env_int("OPENAI_COMMENTS_LIMIT", 2000))

    print(
        f"  OpenAI filter: min_risk={min_risk_score} min_kz={min_kz_score} | "
        f"eligible={len(filtered_candidates)}/{len(candidates)}"
    )
    print(f"  OpenAI selected: {len(selected)} | concurrency={concurrency} | batch_size={batch_size}")

    def build_prompt(c: Candidate) -> str:
        comments_text = "\n".join(["- " + str(x.get("text", "")) for x in c.comments[:10]])
        video_timeline = build_video_timeline_context(c.raw or {})
        video_duration = (c.raw or {}).get("duration_label") or (c.raw or {}).get("duration_seconds") or ""
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
source_type: {c.source_type}
platform: {c.platform}
url: {c.url}
title: {c.title}
channel: {c.channel_name}
published_at: {c.published_at}
video_duration: {video_duration}
rule_based_risk: {c.risk_score}
rule_based_kz: {c.kz_score}
known_kz_bookmaker_brands_detected: {json.dumps(c.bookmaker_brands, ensure_ascii=False)}
known_kz_bookmaker_reference_list: {json.dumps(list(KZ_BOOKMAKER_BRANDS.keys()), ensure_ascii=False)}
licensed_kz_casinos_detected: {json.dumps(c.licensed_casinos, ensure_ascii=False)}
licensed_kz_casino_reference_list: {json.dumps({k: {"location": v.get("location"), "operator": v.get("operator")} for k, v in KZ_LICENSED_CASINOS.items()}, ensure_ascii=False)}
Important bookmaker rule: известные/лицензированные KZ bookmaker brands из reference_list не являются приоритетом этого мониторинга. Если кандидат — обычное промо этих брендов, ставь low risk / false positive / Monitor. Приоритет: финансовые пирамиды, scam investment, crypto referral, AI income bot, неизвестные казино/слоты, зеркала, фейки, affiliate funnel и бренды вне reference_list.
Important casino rule: лицензированные казино РК из licensed_kz_casino_reference_list не являются приоритетом мониторинга, если это обычное упоминание/официальное промо. Приоритет: неизвестные онлайн-казино, зеркала, фейки, affiliate funnel, нелицензированные домены и подозрительные бонусные воронки.
text:
{(c.snippet or '')[:text_limit]}

transcript:
{(c.transcript or '')[:transcript_limit]}

video_timeline_by_minute:
{video_timeline}

comments:
{comments_text[:comments_limit]}

links:
{json.dumps(c.links[:15], ensure_ascii=False)}
""".strip()

    def analyze_one(payload: Tuple[int, Candidate]) -> Dict[str, Any]:
        idx, c = payload
        prompt = build_prompt(c)
        last_error = ""
        for attempt in range(1, max_retries + 1):
            try:
                resp = client.responses.create(
                    model=model,
                    input=[{"role": "user", "content": prompt}],
                )
                text = getattr(resp, "output_text", "") or ""
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = {"raw_text": text[:4000]}
                return {
                    "rank": idx,
                    "url": c.url,
                    "title": c.title,
                    "platform": c.platform,
                    "source_type": c.source_type,
                    "rule_based_risk": c.risk_score,
                    "rule_based_kz": c.kz_score,
                    "openai_analysis": parsed,
                }
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries:
                    time.sleep(min(20, 2 ** attempt))
        return {
            "rank": idx,
            "url": c.url,
            "title": c.title,
            "platform": c.platform,
            "source_type": c.source_type,
            "rule_based_risk": c.risk_score,
            "rule_based_kz": c.kz_score,
            "error": last_error,
        }

    indexed_candidates = list(enumerate(selected, start=1))
    results: List[Dict[str, Any]] = []

    if concurrency <= 1:
        for payload in indexed_candidates:
            idx, c = payload
            print(f"  OpenAI {idx}/{len(selected)}: {c.platform} | risk={c.risk_score} kz={c.kz_score} | {c.title[:70]}")
            results.append(analyze_one(payload))
    else:
        done = 0
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_map = {executor.submit(analyze_one, payload): payload for payload in indexed_candidates}
            for future in as_completed(future_map):
                result = future.result()
                results.append(result)
                done += 1
                status = "ERR" if result.get("error") else "OK"
                print(f"  OpenAI done {done}/{len(selected)} [{status}] rank={result.get('rank')} | {str(result.get('title', ''))[:65]}")

    results = sorted(results, key=lambda x: int(x.get("rank", 0)))

    generated_at = datetime.now(timezone.utc).isoformat()
    output_path = Path(output_json)
    output_suffix = output_path.suffix or ".json"
    output_stem = output_path.stem
    output_dir = output_path.parent if str(output_path.parent) != "." else Path(".")

    if len(results) <= batch_size:
        payload = {
            "generated_at": generated_at,
            "model": model,
            "sent_items_to_openai": len(results),
            "openai_concurrency": concurrency,
            "batch_size": batch_size,
            "total_batches": 1,
            "items": results,
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"  OpenAI output: one file → {output_path}")
        return results

    part_files: List[str] = []
    total_batches = math.ceil(len(results) / batch_size)
    for batch_index in range(total_batches):
        start_i = batch_index * batch_size
        end_i = start_i + batch_size
        chunk = results[start_i:end_i]
        part_name = f"{output_stem}_part_{batch_index + 1:03d}{output_suffix}"
        part_path = output_dir / part_name
        part_payload = {
            "generated_at": generated_at,
            "model": model,
            "part": batch_index + 1,
            "total_batches": total_batches,
            "openai_concurrency": concurrency,
            "batch_size": batch_size,
            "range": {
                "start_rank": chunk[0]["rank"] if chunk else None,
                "end_rank": chunk[-1]["rank"] if chunk else None,
            },
            "sent_items_to_openai": len(chunk),
            "items": chunk,
        }
        with open(part_path, "w", encoding="utf-8") as f:
            json.dump(part_payload, f, ensure_ascii=False, indent=2)
        part_files.append(str(part_path))
        print(f"  OpenAI output part {batch_index + 1}/{total_batches}: {part_path} | items={len(chunk)}")

    index_payload = {
        "generated_at": generated_at,
        "model": model,
        "sent_items_to_openai": len(results),
        "openai_concurrency": concurrency,
        "batch_size": batch_size,
        "total_batches": total_batches,
        "mode": "chunked_openai_analysis_index",
        "files": part_files,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(index_payload, f, ensure_ascii=False, indent=2)
    print(f"  OpenAI output index: {output_path}")

    return results


def default_google_queries() -> List[str]:
    return [
        'site:t.me/s "Казахстан" "гарантированный доход"',
        'site:t.me/s "тенге" "пассивный доход"',
        'site:t.me/s "Kaspi" "заработок"',
        'site:t.me/s "USDT" "реферальный бонус"',
        'site:t.me/s "AI бот" "доход"',
        'site:instagram.com/reel "заработок Казахстан"',
        'site:instagram.com/reel "пассивный доход Казахстан"',
        'site:instagram.com/reel "доход каждый день" "Казахстан"',
        'site:instagram.com/reel "USDT" "заработок"',
        'site:instagram.com/reel "пиши в директ" "доход"',
        'site:tiktok.com "заработок Казахстан" "USDT"',
        'site:tiktok.com "тез табыс" "Қазақстан"',
        'site:tiktok.com "@*" "доход каждый день" "USDT"',
        'site:tiktok.com "пассивный доход" "Telegram"',
        'site:tiktok.com "пиши в лс" "заработок"',
        'site:youtube.com/watch "финансовая пирамида Казахстан 2026"',
        'site:t.me/s "гарантированный доход" "тенге"',
        'site:t.me/s "% в день" "Казахстан"',
        'site:t.me/s "пригласи друга" "доход" "Казахстан"',
        'site:t.me/s "закрытый клуб" "пассивный доход"',
        'site:youtube.com/watch "доход каждый день" "Telegram" "USDT"',
        'site:youtube.com/watch "финансовая свобода" "доход каждый день"',
        'site:youtube.com/watch "казино Казахстан бонус" -1xBet -Parimatch -Olimpbet -Fonbet -Winline -Tennisi -Pin-Up -Ubet -Betsson -Бетсити',
        'site:t.me/s "казино" "Казахстан" "бонус" -1xBet -Parimatch -Olimpbet -Fonbet -Winline -Tennisi -Pin-Up -Ubet -Betsson -Бетсити',
        'site:tiktok.com "казино" "Қазақстан" -1xBet -Parimatch -Olimpbet -Fonbet -Winline -Tennisi -Pin-Up -Ubet -Betsson -Бетсити',
        'site:instagram.com/reel "казино" "Казахстан" -1xBet -Parimatch -Olimpbet -Fonbet -Winline -Tennisi -Pin-Up -Ubet -Betsson -Бетсити',
        'site:sites.google.com "заработок" "Казахстан"',
        'site:sites.google.com "USDT" "доход"',
        'site:sites.google.com "казино" "бонус" -1xBet -Parimatch -Olimpbet -Fonbet -Winline -Tennisi -Pin-Up -Ubet -Betsson -Бетсити',
        'site:forms.gle "заработок" "Казахстан"',
    ]


def default_youtube_queries() -> List[str]:
    return [
        "пассивный доход Казахстан",
        "AI бот доход Казахстан",
        "USDT заработок Казахстан",
        "казино Казахстан бонус",
        "финансовая пирамида Казахстан 2026",
        "доход каждый день тенге",
        "гарантированный доход тенге",
        "доход каждый день Telegram USDT",
        "финансовая свобода деньги каждый день Казахстан",
        "казино Казахстан бонус слоты",
        "Book of Ra казино Казахстан бонус",
    ]


def dedupe_candidates(candidates: List[Candidate]) -> List[Candidate]:
    seen = set()
    out: List[Candidate] = []
    for c in candidates:
        key = c.url or c.external_id or f"{c.platform}:{c.title}:{c.snippet[:50]}"
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def main() -> None:
    serpapi_key = env_str("SERPAPI_KEY")
    youtube_key = env_str("YOUTUBE_API_KEY")
    openai_key = env_str("OPENAI_API_KEY")

    enable_serpapi_google = env_bool("ENABLE_SERPAPI_GOOGLE", True)
    enable_youtube = env_bool("ENABLE_YOUTUBE_API", True)
    enable_telegram = env_bool("ENABLE_TELEGRAM_PUBLIC_PARSE", True)
    enable_transcript = env_bool("ENABLE_TRANSCRIPT", True)
    enable_openai = env_bool("ENABLE_OPENAI_ANALYSIS", True)

    if enable_serpapi_google and not serpapi_key:
        print("[ERROR] ENABLE_SERPAPI_GOOGLE=true, но SERPAPI_KEY пустой")
        sys.exit(1)
    if enable_youtube and not youtube_key:
        print("[ERROR] ENABLE_YOUTUBE_API=true, но YOUTUBE_API_KEY пустой")
        sys.exit(1)

    google_queries = split_pipe(env_str("GOOGLE_WEB_QUERIES")) or default_google_queries()
    youtube_queries = split_pipe(env_str("YOUTUBE_QUERIES")) or default_youtube_queries()

    max_google_queries = env_int("MAX_GOOGLE_QUERIES", 30)
    google_results_per_query = env_int("GOOGLE_RESULTS_PER_QUERY", 5)
    max_youtube_results_per_query = env_int("MAX_YOUTUBE_RESULTS_PER_QUERY", 5)
    max_comments = env_int("MAX_COMMENTS_PER_VIDEO", 10)
    max_telegram_channels = env_int("MAX_TELEGRAM_CHANNELS_TO_PARSE", 20)
    posts_per_telegram = env_int("POSTS_PER_TELEGRAM_CHANNEL", 10)
    enable_social_page_parse = env_bool("ENABLE_SOCIAL_PUBLIC_PAGE_PARSE", True)
    enable_landing_page_parse = env_bool("ENABLE_LANDING_PAGE_PARSE", True)
    max_social_pages = env_int("MAX_SOCIAL_PUBLIC_PAGES_TO_PARSE", 30)
    max_landing_pages = env_int("MAX_LANDING_PAGES_TO_PARSE", 20)
    delay = env_float("REQUEST_DELAY_SECONDS", 0.7)

    google_gl = env_str("SERPAPI_GOOGLE_GL", "kz")
    google_hl = env_str("SERPAPI_GOOGLE_HL", "ru")
    google_tbs = env_str("SERPAPI_GOOGLE_TBS", "qdr:m3")
    youtube_region = env_str("YOUTUBE_REGION_CODE", "KZ")
    youtube_lang = env_str("YOUTUBE_RELEVANCE_LANGUAGE", "ru")
    published_after = env_str("PUBLISHED_AFTER", "2026-03-20T00:00:00Z")

    output_json = env_str("OUTPUT_JSON", "ai_media_watch_results.json")
    raw_output_json = env_str("RAW_OUTPUT_JSON", "ai_media_watch_raw.json")
    openai_output_json = env_str("OPENAI_OUTPUT_JSON", "openai_risk_analysis.json")
    openai_model = env_str("OPENAI_MODEL", "gpt-4.1-mini")
    openai_max_items = env_int("OPENAI_MAX_ITEMS", 20)
    openai_batch_size = env_int("OPENAI_BATCH_SIZE", 100)


    save_run_history = env_bool("SAVE_RUN_HISTORY", True)
    output_dir = env_str("OUTPUT_DIR", "runs")
    run_id = env_str("RUN_ID", "auto")
    run_dir: Optional[Path] = None
    if save_run_history:
        if not run_id or run_id.lower() == "auto":
            run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        run_dir = Path(output_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        output_json = str(run_dir / Path(output_json).name)
        raw_output_json = str(run_dir / Path(raw_output_json).name)
        openai_output_json = str(run_dir / Path(openai_output_json).name)
    else:
        run_id = "overwrite_mode"

    print("🚀 AI Media Watch — SerpAPI Google + public social/landing pages + YouTube API + Telegram + OpenAI")
    print(f"SerpAPI Google: {enable_serpapi_google} | Social page parse: {enable_social_page_parse} | Landing parse: {enable_landing_page_parse} | YouTube API: {enable_youtube} | Telegram parse: {enable_telegram} | OpenAI: {enable_openai}")
    print(f"SERPAPI_KEY: {mask_secret(serpapi_key)}")
    print(f"YOUTUBE_API_KEY: {mask_secret(youtube_key)}")
    print(f"OPENAI_API_KEY: {mask_secret(openai_key)}")
    if save_run_history:
        print(f"RUN_ID: {run_id}")
        print(f"RUN_DIR: {run_dir}")
    else:
        print("RUN_HISTORY: disabled — output files will be overwritten")

    stream_writer = None
    enable_stream_upload = env_bool("ENABLE_STREAM_UPLOAD", False)
    stream_paths = {
        "results_json_path": output_json,
        "raw_json_path": raw_output_json,
        "openai_json_path": openai_output_json,
    }
    if enable_stream_upload:
        try:
            from supabase_stream import SupabaseStreamWriter, now_iso as stream_now_iso

            stream_writer = SupabaseStreamWriter(run_id)
            stream_writer.upsert_scan_run({
                "generated_at": stream_now_iso(),
                "mode": "serpapi_google_social_landing_youtube_api_telegram_openai_streaming",
                "run_id": run_id,
                "run_dir": str(run_dir) if run_dir else None,
                "save_run_history": save_run_history,
                "settings": {
                    "streaming": True,
                    "enable_serpapi_google": enable_serpapi_google,
                    "enable_social_public_page_parse": enable_social_page_parse,
                    "enable_landing_page_parse": enable_landing_page_parse,
                    "enable_youtube_api": enable_youtube,
                    "enable_telegram_public_parse": enable_telegram,
                    "enable_openai_analysis": enable_openai,
                },
                "google_queries_used": google_queries[:max_google_queries],
                "youtube_queries_used": youtube_queries,
            }, stream_paths)
            print("Supabase streaming upload: enabled")
        except Exception as e:
            stream_writer = None
            print(f"[WARN] Supabase streaming upload disabled: {e}")
    else:
        print("Supabase streaming upload: disabled")

    def stream_candidates(label: str, candidates: List[Candidate]) -> None:
        if not stream_writer or not candidates:
            return
        try:
            uploaded = stream_writer.upsert_candidates([asdict(c) for c in candidates])
            print(f"  streamed to Supabase ({label}): {uploaded}")
        except Exception as e:
            print(f"  [WARN] Supabase stream failed ({label}): {e}")

    all_candidates: List[Candidate] = []
    raw_serpapi: List[Dict[str, Any]] = []
    raw_public_pages: List[Dict[str, Any]] = []
    raw_youtube_search_items: List[Dict[str, Any]] = []
    raw_youtube_details: Dict[str, Dict[str, Any]] = {}


    if enable_serpapi_google:
        for q in google_queries[:max_google_queries]:
            print(f"\n🔎 SerpAPI Google Search: {q}")
            try:
                data = serpapi_google_search(serpapi_key, q, google_results_per_query, google_gl, google_hl, google_tbs)
            except Exception as e:
                print(f"  [WARN] SerpAPI Google failed: {e}")
                continue
            raw_serpapi.append({"query": q, "data": data})
            candidates = candidates_from_serpapi_google(q, data)
            all_candidates.extend(candidates)
            print(f"  candidates: {len(candidates)}")
            stream_candidates("serpapi_google", candidates)
            if delay > 0:
                time.sleep(delay)


    if enable_social_page_parse:
        social_seeds = [c for c in all_candidates if is_social_public_page_candidate(c)]
        social_seeds = dedupe_candidates(social_seeds)[:max_social_pages]
        print(f"\n🎬 Social public pages to parse: {len(social_seeds)}")
        for seed in social_seeds:
            print(f"  Social page: {seed.platform} | {seed.url[:100]}")
            parsed = parse_public_web_page(seed, delay, "social_public_page")
            if parsed:
                all_candidates.append(parsed)
                raw_public_pages.append(parsed.raw)
                print(f"    parsed risk={parsed.risk_score} kz={parsed.kz_score} links={len(parsed.links)}")
                stream_candidates("social_public_page", [parsed])

    if enable_landing_page_parse:
        landing_seeds = [
            c for c in all_candidates
            if is_landing_page_candidate(c) and c.source_type != "social_public_page"
        ]
        landing_seeds = dedupe_candidates(landing_seeds)[:max_landing_pages]
        print(f"\n🌐 Landing/Google Sites pages to parse: {len(landing_seeds)}")
        for seed in landing_seeds:
            print(f"  Landing page: {seed.platform} | {seed.url[:100]}")
            parsed = parse_public_web_page(seed, delay, "landing_public_page")
            if parsed:
                all_candidates.append(parsed)
                raw_public_pages.append(parsed.raw)
                print(f"    parsed risk={parsed.risk_score} kz={parsed.kz_score} links={len(parsed.links)}")
                stream_candidates("landing_public_page", [parsed])


    if enable_telegram:
        text_blob = "\n".join([c.url + "\n" + c.snippet + "\n" + "\n".join(c.links) for c in all_candidates])
        usernames = extract_telegram_usernames(text_blob)[:max_telegram_channels]
        print(f"\n📡 Telegram channels to parse: {len(usernames)}")
        for username in usernames:
            print(f"  Telegram: @{username}")
            posts = parse_telegram_public_channel(username, posts_per_telegram, delay)
            all_candidates.extend(posts)
            print(f"    posts parsed: {len(posts)}")
            stream_candidates("telegram_public_post", posts)


    if enable_youtube:
        youtube_search_items: List[Dict[str, Any]] = []
        seen_vids = set()
        for q in youtube_queries:
            print(f"\n🎥 YouTube API Search: {q}")
            try:
                items = youtube_search(youtube_key, q, max_youtube_results_per_query, youtube_region, youtube_lang, published_after)
            except Exception as e:
                print(f"  [WARN] YouTube search failed: {e}")
                continue
            for it in items:
                vid = it.get("id", {}).get("videoId")
                if not vid or vid in seen_vids:
                    continue
                it["_query"] = q
                youtube_search_items.append(it)
                raw_youtube_search_items.append(it)
                seen_vids.add(vid)
            print(f"  unique videos so far: {len(seen_vids)}")
            if delay > 0:
                time.sleep(delay)

        video_ids = [it.get("id", {}).get("videoId") for it in youtube_search_items if it.get("id", {}).get("videoId")]
        raw_youtube_details = youtube_details(youtube_key, video_ids)

        for idx, it in enumerate(youtube_search_items, start=1):
            vid = it.get("id", {}).get("videoId")
            detail = raw_youtube_details.get(vid)
            if not detail:
                continue
            title = detail.get("snippet", {}).get("title", "")
            print(f"  [{idx}/{len(youtube_search_items)}] {vid}: {title[:80]}")
            comments = youtube_comments(youtube_key, vid, max_comments)
            transcript, transcript_segments = get_transcript_data(vid, enable_transcript)
            c = build_youtube_candidate(it.get("_query", ""), detail, comments, transcript, transcript_segments)
            all_candidates.append(c)
            print(f"    risk={c.risk_score} kz={c.kz_score} comments={len(comments)} transcript_len={len(transcript)}")
            stream_candidates("youtube_api_video", [c])
            if delay > 0:
                time.sleep(delay)


    all_candidates = dedupe_candidates(all_candidates)
    all_candidates_sorted = sorted(
        all_candidates,
        key=lambda c: (c.risk_score + c.kz_score * 0.55, c.published_at or ""),
        reverse=True,
    )

    platform_counts: Dict[str, int] = {}
    for c in all_candidates_sorted:
        platform_counts[c.platform] = platform_counts.get(c.platform, 0) + 1

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "serpapi_google_social_landing_youtube_api_telegram_openai",
        "run_id": run_id,
        "run_dir": str(run_dir) if run_dir else None,
        "save_run_history": save_run_history,
        "yandex_used": False,
        "google_search_provider": "serpapi_google",
        "youtube_provider": "youtube_data_api",
        "total_candidates": len(all_candidates_sorted),
        "kz_items": len([c for c in all_candidates_sorted if c.kz_score >= 50]),
        "high_risk_items": len([c for c in all_candidates_sorted if c.risk_score >= 70]),
        "kz_high_risk_items": len([c for c in all_candidates_sorted if c.kz_score >= 50 and c.risk_score >= 70]),
        "review_items": len([c for c in all_candidates_sorted if c.risk_score >= 40]),
        "platform_counts": platform_counts,
        "settings": {
            "enable_serpapi_google": enable_serpapi_google,
            "enable_social_public_page_parse": enable_social_page_parse,
            "enable_landing_page_parse": enable_landing_page_parse,
            "enable_youtube_api": enable_youtube,
            "enable_telegram_public_parse": enable_telegram,
            "enable_transcript": enable_transcript,
            "enable_openai_analysis": enable_openai,
            "max_google_queries": max_google_queries,
            "google_results_per_query": google_results_per_query,
            "max_social_public_pages_to_parse": max_social_pages,
            "max_landing_pages_to_parse": max_landing_pages,
            "max_youtube_results_per_query": max_youtube_results_per_query,
            "max_comments_per_video": max_comments,
            "serpapi_google_tbs": google_tbs,
            "published_after": published_after,
        },
        "google_queries_used": google_queries[:max_google_queries],
        "youtube_queries_used": youtube_queries,
    }

    if stream_writer:
        try:
            stream_writer.upsert_scan_run(summary, stream_paths)
            stream_writer.upsert_candidates([asdict(c) for c in all_candidates_sorted], rank_start=1)
            print(f"\n⬆️  Supabase final stream update: {len(all_candidates_sorted)} ranked candidates")
        except Exception as e:
            print(f"\n[WARN] Supabase final stream update failed: {e}")

    openai_results: List[Dict[str, Any]] = []
    if enable_openai:
        print(f"\n🤖 OpenAI analysis for top {openai_max_items if openai_max_items > 0 else 'ALL'} candidates | batch size={openai_batch_size}")
        openai_results = openai_analyze_candidates(all_candidates_sorted, openai_output_json, openai_model, openai_max_items, openai_batch_size)
        print(f"  saved: {openai_output_json} | analyzed: {len(openai_results)}")

    output = {
        "summary": summary,
        "items": [asdict(c) for c in all_candidates_sorted],
        "top_risk_candidates": [asdict(c) for c in all_candidates_sorted[:30]],
        "openai_analysis_file": openai_output_json if openai_results else None,
    }

    raw_output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "serpapi_google_results": raw_serpapi,
        "public_page_results": raw_public_pages,
        "youtube_search_items": raw_youtube_search_items,
        "youtube_video_details": raw_youtube_details,
    }

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    with open(raw_output_json, "w", encoding="utf-8") as f:
        json.dump(raw_output, f, ensure_ascii=False, indent=2)

    print("\n✅ Done")
    print(f"Saved: {output_json}")
    print(f"Saved raw: {raw_output_json}")
    if openai_results:
        print(f"Saved OpenAI analysis: {openai_output_json}")
    print("\nSummary:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
