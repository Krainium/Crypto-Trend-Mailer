#!/usr/bin/env python3
"""
Crypto-Trend-Mailer — fetch trending / brand-new crypto and email it.
Single-file Python program. Stdlib only (no pip installs).
Run with no arguments for an interactive menu.
"""

from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import Any

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

APP_NAME = "Crypto-Trend-Mailer"
APP_VERSION = "1.0.0"
APP_TAGLINE = "trending + brand-new crypto delivered to your inbox"

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
SUBSCRIBERS_FILE = os.path.join(CONFIG_DIR, "subscribers.json")

COINGECKO_BASE     = "https://api.coingecko.com/api/v3"
DEXSCREENER_BASE   = "https://api.dexscreener.com"
KUCOIN_BASE        = "https://api.kucoin.com/api/v1"
CRYPTOCOMPARE_BASE = "https://min-api.cryptocompare.com/data"
COINMARKETCAP_BASE = "https://pro-api.coinmarketcap.com/v1"

BREVO_ENDPOINT  = "https://api.brevo.com/v3/smtp/email"
RESEND_ENDPOINT = "https://api.resend.com/emails"

USER_AGENT = f"{APP_NAME}/{APP_VERSION} (+https://github.com/Krainium)"
HTTP_TIMEOUT = 20  # seconds

# Each category maps to a (label, source-name) — source shown in the digest footer.
CATEGORIES = {
    "trending":   ("Trending right now (last 24h)",                 "CoinGecko"),
    "new24h":     ("Brand new (just launched in the last ~24h)",    "DexScreener"),
    "newweek":    ("Recently launched (popular in the last 1-2 wk)","DexScreener"),
    "gainers24h": ("Top 24h gainers (USDT pairs)",                  "KuCoin"),
    "topvolume":  ("Top by 24h volume",                             "CryptoCompare"),
    "topcap":     ("Top by market cap",                             "CoinMarketCap*"),
}

# Email providers — order matters for menu display.
PROVIDERS = ["gmail", "brevo", "resend", "smtp"]
PROVIDER_LABEL = {
    "gmail":  "Gmail SMTP (default — instant, 500/day)",
    "brevo":  "Brevo HTTPS API (300/day, requires account approval)",
    "resend": "Resend HTTPS API (3000/month free, instant signup)",
    "smtp":   "Custom SMTP (any host — Outlook, Yahoo, ProtonMail, SMTP2GO, etc.)",
}

# --------------------------------------------------------------------------- #
# ANSI color helpers (auto-disable on non-TTY / NO_COLOR / TERM=dumb)
# --------------------------------------------------------------------------- #

_NO_COLOR_FLAG = "--no-color" in sys.argv
if _NO_COLOR_FLAG:
    sys.argv.remove("--no-color")

def _color_supported() -> bool:
    if _NO_COLOR_FLAG:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    return sys.stdout.isatty()

USE_COLOR = _color_supported()

def _wrap(code: str) -> callable:
    def fn(s: str) -> str:
        return f"\x1b[{code}m{s}\x1b[0m" if USE_COLOR else s
    return fn

c_bold       = _wrap("1")
c_dim        = _wrap("2")
c_red        = _wrap("31")
c_green      = _wrap("32")
c_yellow     = _wrap("33")
c_blue       = _wrap("34")
c_magenta    = _wrap("35")
c_cyan       = _wrap("36")
c_bold_red   = _wrap("1;31")
c_bold_green = _wrap("1;32")
c_bold_cyan  = _wrap("1;36")
c_dim_cyan   = _wrap("2;36")

# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #

@dataclass
class Coin:
    name: str
    symbol: str
    price_usd: float | None = None
    change_24h: float | None = None
    market_cap: float | None = None
    volume_24h: float | None = None
    rank: int | None = None
    image_url: str | None = None
    link: str | None = None
    chain: str | None = None
    address: str | None = None
    age_label: str | None = None      # e.g. "12h ago", "5d ago"
    description: str | None = None

@dataclass
class Subscriber:
    email: str
    name: str = ""
    categories: list[str] = field(default_factory=lambda: ["trending"])
    per_category: int = 5

@dataclass
class Config:
    provider: str = "gmail"            # one of: gmail, brevo, resend, smtp
    sender_email: str = ""
    sender_name: str = APP_NAME
    default_per_category: int = 5
    # Gmail
    gmail_user: str = ""
    gmail_app_password: str = ""
    # Brevo HTTPS API
    brevo_api_key: str = ""
    # Resend HTTPS API
    resend_api_key: str = ""
    # Custom SMTP (host/port/user/password — works with Outlook, Yahoo, SMTP2GO, ProtonMail Bridge, etc.)
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_ssl: bool = True          # True = SMTPS (465), False = STARTTLS (typically 587)
    # CoinMarketCap (optional — only data source that needs a key)
    cmc_api_key: str = ""

# --------------------------------------------------------------------------- #
# JSON persistence
# --------------------------------------------------------------------------- #

def _env_overlay(cfg: Config) -> Config:
    """Apply env-var values on top of disk config (env wins). Secrets stay env-only."""
    # Gmail
    if os.environ.get("GMAIL_USER"):
        cfg.gmail_user = os.environ["GMAIL_USER"]
    if os.environ.get("GMAIL_APP_PASSWORD"):
        # Google sometimes shows the 16-char app password with spaces ("abcd efgh ijkl mnop").
        cfg.gmail_app_password = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
    # Brevo / Resend
    if os.environ.get("BREVO_API_KEY"):
        cfg.brevo_api_key = os.environ["BREVO_API_KEY"]
    if os.environ.get("RESEND_API_KEY"):
        cfg.resend_api_key = os.environ["RESEND_API_KEY"]
    # Custom SMTP
    if os.environ.get("SMTP_HOST"):     cfg.smtp_host     = os.environ["SMTP_HOST"]
    if os.environ.get("SMTP_PORT"):
        try: cfg.smtp_port = int(os.environ["SMTP_PORT"])
        except ValueError: pass
    if os.environ.get("SMTP_USER"):     cfg.smtp_user     = os.environ["SMTP_USER"]
    if os.environ.get("SMTP_PASSWORD"): cfg.smtp_password = os.environ["SMTP_PASSWORD"]
    if os.environ.get("SMTP_USE_SSL"):  cfg.smtp_use_ssl  = os.environ["SMTP_USE_SSL"].lower() in ("1","true","yes","on")
    # CoinMarketCap (data source key)
    if os.environ.get("CMC_API_KEY"):   cfg.cmc_api_key   = os.environ["CMC_API_KEY"]

    # Sensible default: if Gmail user is set but sender_email isn't, use it
    if cfg.provider == "gmail" and cfg.gmail_user and not cfg.sender_email:
        cfg.sender_email = cfg.gmail_user
    if cfg.provider == "smtp" and cfg.smtp_user and "@" in cfg.smtp_user and not cfg.sender_email:
        cfg.sender_email = cfg.smtp_user
    return cfg

def load_config() -> Config:
    if not os.path.exists(CONFIG_FILE):
        return _env_overlay(Config())
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        cfg = Config(**{k: data.get(k, getattr(Config(), k)) for k in Config.__dataclass_fields__})
        return _env_overlay(cfg)
    except Exception as e:
        print(c_yellow(f"warn: could not read config ({e}), using defaults"))
        return _env_overlay(Config())

def save_config(cfg: Config) -> None:
    # Never persist real secrets to disk — always blank them out.
    # Real secrets come from env vars at startup via _env_overlay() and stay in memory only.
    # Identifiers like gmail_user / smtp_user / sender_email are not secrets and are persisted.
    persisted = asdict(cfg)
    for field_name in (
        "gmail_app_password",
        "brevo_api_key",
        "resend_api_key",
        "smtp_password",
        "cmc_api_key",
    ):
        if field_name in persisted:
            persisted[field_name] = ""
    with open(CONFIG_FILE, "w") as f:
        json.dump(persisted, f, indent=2)

def load_subscribers() -> list[Subscriber]:
    if not os.path.exists(SUBSCRIBERS_FILE):
        return []
    try:
        with open(SUBSCRIBERS_FILE) as f:
            data = json.load(f)
        return [Subscriber(**s) for s in data]
    except Exception as e:
        print(c_yellow(f"warn: could not read subscribers ({e})"))
        return []

def save_subscribers(subs: list[Subscriber]) -> None:
    with open(SUBSCRIBERS_FILE, "w") as f:
        json.dump([asdict(s) for s in subs], f, indent=2)

# --------------------------------------------------------------------------- #
# HTTP helper
# --------------------------------------------------------------------------- #

def http_get_json(url: str, timeout: int = HTTP_TIMEOUT) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode("utf-8"))

# --------------------------------------------------------------------------- #
# Data sources
# --------------------------------------------------------------------------- #

def fetch_trending(limit: int = 7) -> list[Coin]:
    """CoinGecko /search/trending — top trending coins of the last 24h."""
    data = http_get_json(f"{COINGECKO_BASE}/search/trending")
    coins = []
    for entry in data.get("coins", [])[:limit]:
        item = entry.get("item", {})
        coin = Coin(
            name=item.get("name", "?"),
            symbol=(item.get("symbol") or "").upper(),
            rank=item.get("market_cap_rank"),
            image_url=item.get("large") or item.get("small") or item.get("thumb"),
            link=f"https://www.coingecko.com/en/coins/{item.get('id', '')}",
        )
        # Optional price data attached to trending payload
        d = item.get("data", {})
        if d:
            coin.price_usd = _to_float(d.get("price"))
            ch = d.get("price_change_percentage_24h", {}) or {}
            coin.change_24h = _to_float(ch.get("usd")) if isinstance(ch, dict) else _to_float(ch)
            coin.market_cap = _parse_currency(d.get("market_cap"))
            coin.volume_24h = _parse_currency(d.get("total_volume"))
        coins.append(coin)
    return coins

def fetch_new_24h(limit: int = 5) -> list[Coin]:
    """DexScreener latest token profiles — usually pages of just-listed tokens."""
    data = http_get_json(f"{DEXSCREENER_BASE}/token-profiles/latest/v1")
    if isinstance(data, dict):
        data = data.get("data") or data.get("profiles") or []
    coins = []
    for item in (data or [])[:limit]:
        chain = item.get("chainId") or item.get("chain") or "?"
        addr = item.get("tokenAddress") or item.get("address") or ""
        # Best-effort token name / symbol lookup
        name = item.get("description") or item.get("header") or addr[:10]
        symbol = ""
        # Strip URLs / newlines from descriptions
        if isinstance(name, str):
            name = re.sub(r"\s+", " ", name).strip()[:60]
        link = item.get("url") or (f"https://dexscreener.com/{chain}/{addr}" if addr else None)
        coin = Coin(
            name=name or "Unnamed token",
            symbol=symbol,
            chain=chain,
            address=addr,
            link=link,
            image_url=item.get("icon") or item.get("openGraph"),
            description=item.get("description"),
            age_label="just listed",
        )
        coins.append(coin)
    # If profiles are too sparse on detail, enrich top N with pair data
    enriched = []
    for c in coins:
        if c.address and c.chain:
            try:
                pairs = http_get_json(f"{DEXSCREENER_BASE}/latest/dex/tokens/{c.address}")
                pair_list = pairs.get("pairs") if isinstance(pairs, dict) else None
                if pair_list:
                    p = sorted(pair_list, key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0), reverse=True)[0]
                    base = p.get("baseToken", {})
                    c.name = base.get("name", c.name)
                    c.symbol = (base.get("symbol", "") or "").upper()
                    c.price_usd = _to_float(p.get("priceUsd"))
                    c.change_24h = _to_float((p.get("priceChange", {}) or {}).get("h24"))
                    c.volume_24h = _to_float((p.get("volume", {}) or {}).get("h24"))
                    c.market_cap = _to_float(p.get("fdv"))
                    created_ms = p.get("pairCreatedAt")
                    if isinstance(created_ms, (int, float)):
                        c.age_label = _humanize_age(created_ms / 1000.0)
            except Exception:
                pass
        enriched.append(c)
    return enriched

def fetch_top_gainers_24h(limit: int = 5) -> list[Coin]:
    """KuCoin public allTickers — top % gainers among USDT pairs (no key, no geo block)."""
    payload = http_get_json(f"{KUCOIN_BASE}/market/allTickers")
    data = (payload.get("data") or {}).get("ticker") or []
    if not isinstance(data, list):
        return []
    # KuCoin symbols look like "BTC-USDT". Keep only USDT spot pairs with real liquidity.
    # Filter common junk: leveraged tokens (3L/3S/2L/2S), reverse-listed wrappers.
    JUNK_SUFFIXES = ("3L-USDT", "3S-USDT", "2L-USDT", "2S-USDT", "5L-USDT", "5S-USDT")
    rows = [
        d for d in data
        if isinstance(d, dict)
        and isinstance(d.get("symbol"), str)
        and d["symbol"].endswith("-USDT")
        and not any(d["symbol"].endswith(suf) for suf in JUNK_SUFFIXES)
        and (_to_float(d.get("volValue")) or 0) > 1_000_000
    ]
    rows.sort(key=lambda d: _to_float(d.get("changeRate")) or 0, reverse=True)
    coins = []
    for d in rows[:limit]:
        sym = d["symbol"].split("-", 1)[0]
        change_pct = (_to_float(d.get("changeRate")) or 0) * 100.0
        coins.append(Coin(
            name=sym,
            symbol=sym,
            price_usd=_to_float(d.get("last")),
            change_24h=change_pct,
            volume_24h=_to_float(d.get("volValue")),
            link=f"https://www.kucoin.com/trade/{sym}-USDT",
        ))
    return coins

def fetch_top_volume(limit: int = 5) -> list[Coin]:
    """CryptoCompare /data/top/totalvolfull — top coins by 24h USD volume (no key)."""
    url = f"{CRYPTOCOMPARE_BASE}/top/totalvolfull?limit={max(limit, 5)}&tsym=USD"
    data = http_get_json(url)
    rows = (data or {}).get("Data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    coins = []
    for entry in rows[:limit]:
        info = entry.get("CoinInfo", {}) or {}
        raw  = ((entry.get("RAW") or {}).get("USD") or {})
        img_path = info.get("ImageUrl") or ""
        coins.append(Coin(
            name=info.get("FullName") or info.get("Name") or "?",
            symbol=(info.get("Name") or "").upper(),
            price_usd=_to_float(raw.get("PRICE")),
            change_24h=_to_float(raw.get("CHANGEPCT24HOUR")),
            market_cap=_to_float(raw.get("MKTCAP")),
            volume_24h=_to_float(raw.get("TOTALVOLUME24HTO")),
            image_url=f"https://www.cryptocompare.com{img_path}" if img_path else None,
            link=f"https://www.cryptocompare.com/coins/{(info.get('Name') or '').lower()}/overview",
        ))
    return coins

def fetch_top_marketcap(limit: int = 5, api_key: str = "") -> list[Coin]:
    """CoinMarketCap /listings/latest — top by market cap (REQUIRES api key)."""
    if not api_key:
        raise RuntimeError("CoinMarketCap requires an API key (set CMC_API_KEY env var or in Settings)")
    url = f"{COINMARKETCAP_BASE}/cryptocurrency/listings/latest?limit={limit}&convert=USD"
    req = urllib.request.Request(
        url,
        headers={
            "X-CMC_PRO_API_KEY": api_key,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=ctx) as r:
        payload = json.loads(r.read().decode("utf-8"))
    rows = payload.get("data") or []
    coins = []
    for d in rows[:limit]:
        usd = ((d.get("quote") or {}).get("USD") or {})
        cmc_id = d.get("id")
        coins.append(Coin(
            name=d.get("name", "?"),
            symbol=(d.get("symbol") or "").upper(),
            rank=d.get("cmc_rank"),
            price_usd=_to_float(usd.get("price")),
            change_24h=_to_float(usd.get("percent_change_24h")),
            market_cap=_to_float(usd.get("market_cap")),
            volume_24h=_to_float(usd.get("volume_24h")),
            image_url=f"https://s2.coinmarketcap.com/static/img/coins/64x64/{cmc_id}.png" if cmc_id else None,
            link=f"https://coinmarketcap.com/currencies/{(d.get('slug') or '')}/",
        ))
    return coins

def fetch_new_week(limit: int = 5) -> list[Coin]:
    """DexScreener latest token boosts — tokens getting attention recently (~last 1-2 weeks)."""
    data = http_get_json(f"{DEXSCREENER_BASE}/token-boosts/latest/v1")
    if isinstance(data, dict):
        data = data.get("data") or []
    seen = set()
    coins: list[Coin] = []
    for item in (data or []):
        addr = item.get("tokenAddress") or ""
        chain = item.get("chainId") or "?"
        key = (chain, addr)
        if not addr or key in seen:
            continue
        seen.add(key)
        link = item.get("url") or f"https://dexscreener.com/{chain}/{addr}"
        coin = Coin(
            name=addr[:10],
            symbol="",
            chain=chain,
            address=addr,
            link=link,
            image_url=item.get("icon"),
            description=item.get("description"),
        )
        # Enrich; include the coin if pair data is found, regardless of age.
        try:
            pairs = http_get_json(f"{DEXSCREENER_BASE}/latest/dex/tokens/{addr}")
            pair_list = pairs.get("pairs") if isinstance(pairs, dict) else None
            if not pair_list:
                continue
            p = sorted(pair_list, key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0), reverse=True)[0]
            base = p.get("baseToken", {})
            coin.name = base.get("name", coin.name)
            coin.symbol = (base.get("symbol", "") or "").upper()
            coin.price_usd = _to_float(p.get("priceUsd"))
            coin.change_24h = _to_float((p.get("priceChange", {}) or {}).get("h24"))
            coin.volume_24h = _to_float((p.get("volume", {}) or {}).get("h24"))
            coin.market_cap = _to_float(p.get("fdv"))
            created_ms = p.get("pairCreatedAt")
            if isinstance(created_ms, (int, float)):
                coin.age_label = _humanize_age(created_ms / 1000.0)
            coins.append(coin)
            if len(coins) >= limit:
                break
        except Exception:
            continue
    return coins

# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _parse_currency(v: Any) -> float | None:
    """Convert '$1,234,567' or '$3.4M' style strings to a float."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(",", "")
    mult = 1.0
    if s.endswith("K"): mult, s = 1e3,  s[:-1]
    elif s.endswith("M"): mult, s = 1e6,  s[:-1]
    elif s.endswith("B"): mult, s = 1e9,  s[:-1]
    elif s.endswith("T"): mult, s = 1e12, s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None

def _fmt_usd(v: float | None) -> str:
    if v is None:
        return "—"
    if v >= 1_000_000_000: return f"${v/1e9:,.2f}B"
    if v >= 1_000_000:     return f"${v/1e6:,.2f}M"
    if v >= 1_000:         return f"${v/1e3:,.2f}K"
    if v >= 1:             return f"${v:,.2f}"
    if v >= 0.01:          return f"${v:.4f}"
    return f"${v:.8f}".rstrip("0").rstrip(".")

def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"

def _humanize_age(epoch: float) -> str:
    delta = time.time() - epoch
    if delta < 0: return "just now"
    if delta < 3600: return f"{int(delta/60)}m ago"
    if delta < 86400: return f"{int(delta/3600)}h ago"
    return f"{int(delta/86400)}d ago"

# --------------------------------------------------------------------------- #
# HTML renderer
# --------------------------------------------------------------------------- #

def render_html(buckets: dict[str, list[Coin]], recipient_name: str = "") -> str:
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y · %H:%M UTC")
    greeting = "Hi"
    sections_html = []
    sources_used: list[str] = []
    for cat, coins in buckets.items():
        if not coins:
            continue
        label, source = CATEGORIES.get(cat, (cat, "?"))
        sections_html.append(_render_section(label, source, coins))
        if source not in sources_used:
            sources_used.append(source)
    sections = "\n".join(sections_html) if sections_html else _render_empty()
    sources_list = ", ".join(sources_used) if sources_used else "CoinGecko, DexScreener, KuCoin, CryptoCompare, CoinMarketCap"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{APP_NAME} digest</title>
</head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1a1f2e;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f9;padding:24px 0;">
    <tr><td align="center">
      <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="max-width:640px;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(15,23,42,0.06);">
        <!-- Header -->
        <tr><td style="background:linear-gradient(135deg,#0f172a 0%,#1e293b 50%,#0ea5e9 100%);padding:28px 32px;color:#ffffff;">
          <div style="font-size:13px;letter-spacing:1.5px;text-transform:uppercase;opacity:0.75;">{APP_NAME} · v{APP_VERSION}</div>
          <div style="font-size:26px;font-weight:700;margin-top:6px;">Your crypto digest</div>
          <div style="font-size:14px;opacity:0.8;margin-top:4px;">{APP_TAGLINE}</div>
          <div style="font-size:12px;opacity:0.6;margin-top:10px;">{now}</div>
        </td></tr>

        <!-- Greeting -->
        <tr><td style="padding:24px 32px 0 32px;font-size:15px;line-height:1.55;">
          <p style="margin:0 0 8px 0;">{greeting}</p>
          <p style="margin:0;color:#475569;">Here's what's moving in crypto right now, pulled live from {sources_list}.</p>
        </td></tr>

        <!-- Sections -->
        {sections}

        <!-- Footer -->
        <tr><td style="padding:20px 32px;background:#f8fafc;border-top:1px solid #e2e8f0;font-size:12px;color:#64748b;line-height:1.6;">
          Sent by <strong>{APP_NAME}</strong> v{APP_VERSION}. Data sources: {sources_list}.<br>
          Prices and data are informational only — not financial advice. Always do your own research.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

def _render_section(title: str, source: str, coins: list[Coin]) -> str:
    rows = "\n".join(_render_coin_row(i + 1, c) for i, c in enumerate(coins))
    src_badge = (
        f'<span style="background:#0ea5e9;color:#ffffff;font-size:10px;font-weight:600;'
        f'letter-spacing:0.5px;padding:3px 8px;border-radius:10px;margin-left:10px;'
        f'vertical-align:middle;text-transform:uppercase;">{_html_text(source)}</span>'
    )
    return f"""
        <tr><td style="padding:28px 32px 8px 32px;">
          <h2 style="margin:0 0 14px 0;font-size:17px;color:#0f172a;border-left:4px solid #0ea5e9;padding-left:10px;">{title}{src_badge}</h2>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:separate;border-spacing:0 8px;">
            {rows}
          </table>
        </td></tr>"""

def _render_coin_row(idx: int, c: Coin) -> str:
    img = (
        f'<img src="{_html_attr(c.image_url)}" alt="" width="36" height="36" '
        f'style="border-radius:50%;display:block;background:#e2e8f0;">'
        if c.image_url else
        f'<div style="width:36px;height:36px;border-radius:50%;background:#e2e8f0;color:#64748b;line-height:36px;text-align:center;font-weight:700;">{_html_text((c.symbol or c.name or "?")[:2])}</div>'
    )
    name_html = _html_text(c.name)
    sym_html = f' <span style="color:#64748b;font-weight:500;">({_html_text(c.symbol)})</span>' if c.symbol else ""
    rank_html = f'<span style="background:#e0f2fe;color:#0369a1;font-size:11px;padding:2px 6px;border-radius:4px;margin-left:6px;">#{c.rank}</span>' if c.rank else ""
    chain_html = f'<span style="background:#f1f5f9;color:#475569;font-size:11px;padding:2px 6px;border-radius:4px;margin-left:6px;">{_html_text(c.chain)}</span>' if c.chain else ""
    age_html = f'<span style="color:#64748b;font-size:11px;margin-left:6px;">{_html_text(c.age_label)}</span>' if c.age_label else ""

    chg = c.change_24h
    chg_color = "#16a34a" if (chg or 0) >= 0 else "#dc2626"
    chg_arrow = "▲" if (chg or 0) >= 0 else "▼"
    chg_html = f'<span style="color:{chg_color};font-weight:600;">{chg_arrow} {_fmt_pct(chg)}</span>' if chg is not None else '<span style="color:#94a3b8;">—</span>'

    stats = []
    if c.price_usd is not None: stats.append(f"<strong>{_fmt_usd(c.price_usd)}</strong>")
    if c.market_cap is not None: stats.append(f"MC {_fmt_usd(c.market_cap)}")
    if c.volume_24h is not None: stats.append(f"Vol {_fmt_usd(c.volume_24h)}")
    stats_html = " · ".join(stats) if stats else '<span style="color:#94a3b8;">no price data</span>'

    link = c.link or "#"

    return f"""
            <tr><td style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:14px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td width="48" valign="top" style="padding-right:12px;">{img}</td>
                  <td valign="top">
                    <div style="font-size:14px;color:#94a3b8;font-weight:600;">#{idx}</div>
                    <div style="font-size:15px;font-weight:600;color:#0f172a;margin-top:2px;">
                      <a href="{_html_attr(link)}" style="color:#0f172a;text-decoration:none;">{name_html}{sym_html}</a>{rank_html}{chain_html}{age_html}
                    </div>
                    <div style="font-size:13px;color:#475569;margin-top:6px;">
                      {stats_html} · 24h {chg_html}
                    </div>
                    <div style="font-size:12px;margin-top:8px;">
                      <a href="{_html_attr(link)}" style="color:#0ea5e9;text-decoration:none;font-weight:500;">View details →</a>
                    </div>
                  </td>
                </tr>
              </table>
            </td></tr>"""

def _render_empty() -> str:
    return """
        <tr><td style="padding:32px;text-align:center;color:#64748b;">
          No coins to show right now — try again in a few minutes.
        </td></tr>"""

def _html_text(s: str | None) -> str:
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def _html_attr(s: str | None) -> str:
    return _html_text(s).replace('"', "&quot;")

# --------------------------------------------------------------------------- #
# Brevo sender
# --------------------------------------------------------------------------- #

def send_email(cfg: Config, to_email: str, to_name: str, subject: str, html: str) -> tuple[bool, str]:
    """Top-level dispatcher — routes to the configured provider."""
    provider = (cfg.provider or "gmail").lower()
    if provider == "gmail":  return send_via_gmail(cfg, to_email, to_name, subject, html)
    if provider == "brevo":  return send_via_brevo(cfg, to_email, to_name, subject, html)
    if provider == "resend": return send_via_resend(cfg, to_email, to_name, subject, html)
    if provider == "smtp":   return send_via_smtp(cfg, to_email, to_name, subject, html)
    return False, f"unknown provider: {cfg.provider!r} (use one of: {', '.join(PROVIDERS)})"

def _build_mime(cfg: Config, sender_email: str, to_email: str, to_name: str, subject: str, html: str) -> MIMEMultipart:
    """Common MIME message construction shared by Gmail + custom SMTP paths."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = formataddr((cfg.sender_name or APP_NAME, sender_email))
    msg["To"]      = formataddr((to_name or to_email, to_email))
    msg["Date"]    = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="cryptomailer")
    plain = "Open this email in an HTML-capable client to see your crypto digest."
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html,  "html",  "utf-8"))
    return msg

def send_via_resend(cfg: Config, to_email: str, to_name: str, subject: str, html: str) -> tuple[bool, str]:
    if not cfg.resend_api_key:
        return False, "Resend not configured (Settings → set Resend API key, or set RESEND_API_KEY env)"
    sender_email = cfg.sender_email or "onboarding@resend.dev"  # Resend's sandbox sender works for testing
    payload = {
        "from": formataddr((cfg.sender_name or APP_NAME, sender_email)),
        "to": [to_email],
        "subject": subject,
        "html": html,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        RESEND_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {cfg.resend_api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            resp = json.loads(r.read().decode("utf-8") or "{}")
            return True, f"sent via Resend (id={resp.get('id', '?')})"
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8"))
            return False, f"Resend {e.code}: {err.get('message') or err.get('name') or err}"
        except Exception:
            return False, f"Resend HTTP {e.code}"
    except Exception as e:
        return False, f"Resend error: {e}"

def send_via_smtp(cfg: Config, to_email: str, to_name: str, subject: str, html: str) -> tuple[bool, str]:
    if not cfg.smtp_host or not cfg.smtp_user or not cfg.smtp_password:
        return False, "Custom SMTP not configured (Settings → set host, user, password)"
    sender_email = cfg.sender_email or cfg.smtp_user
    msg = _build_mime(cfg, sender_email, to_email, to_name, subject, html)
    try:
        ctx = ssl.create_default_context()
        if cfg.smtp_use_ssl:
            with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=ctx, timeout=HTTP_TIMEOUT) as s:
                s.login(cfg.smtp_user, cfg.smtp_password)
                s.sendmail(sender_email, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=HTTP_TIMEOUT) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.ehlo()
                s.login(cfg.smtp_user, cfg.smtp_password)
                s.sendmail(sender_email, [to_email], msg.as_string())
        mode = "SMTPS" if cfg.smtp_use_ssl else "STARTTLS"
        return True, f"sent via Custom SMTP {mode} ({cfg.smtp_host}:{cfg.smtp_port}, from {sender_email})"
    except smtplib.SMTPAuthenticationError as e:
        return False, f"SMTP auth failed: {e.smtp_code} {e.smtp_error.decode() if isinstance(e.smtp_error, bytes) else e.smtp_error}"
    except smtplib.SMTPException as e:
        return False, f"SMTP error: {e}"
    except Exception as e:
        return False, f"SMTP error: {e}"

def send_via_gmail(cfg: Config, to_email: str, to_name: str, subject: str, html: str) -> tuple[bool, str]:
    if not cfg.gmail_user or not cfg.gmail_app_password:
        return False, "Gmail not configured (Settings → set Gmail user + app password)"
    sender_email = cfg.sender_email or cfg.gmail_user
    sender_name = cfg.sender_name or APP_NAME

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((sender_name, sender_email))
    msg["To"] = formataddr((to_name or to_email, to_email))
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="cryptomailer")
    # Plain-text fallback for clients that block HTML
    plain = "Open this email in an HTML-capable client to see your crypto digest."
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=HTTP_TIMEOUT) as server:
            server.login(cfg.gmail_user, cfg.gmail_app_password)
            server.sendmail(cfg.gmail_user, [to_email], msg.as_string())
        return True, f"sent via Gmail SMTP (from {sender_email})"
    except smtplib.SMTPAuthenticationError as e:
        return False, f"Gmail auth failed: {e.smtp_code} {e.smtp_error.decode() if isinstance(e.smtp_error, bytes) else e.smtp_error}  — make sure GMAIL_APP_PASSWORD is a 16-char app password, not the regular Gmail password"
    except smtplib.SMTPException as e:
        return False, f"Gmail SMTP error: {e}"
    except Exception as e:
        return False, str(e)

def send_via_brevo(cfg: Config, to_email: str, to_name: str, subject: str, html: str) -> tuple[bool, str]:
    if not cfg.brevo_api_key:
        return False, "no Brevo API key configured (Settings → set key)"
    if not cfg.sender_email:
        return False, "no sender email configured (Settings → set sender)"
    payload = {
        "sender": {"name": cfg.sender_name or APP_NAME, "email": cfg.sender_email},
        "to": [{"email": to_email, "name": to_name or to_email}],
        "subject": subject,
        "htmlContent": html,
        "tags": [APP_NAME, f"v{APP_VERSION}"],
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BREVO_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "api-key": cfg.brevo_api_key,
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            resp = json.loads(r.read().decode("utf-8") or "{}")
            mid = resp.get("messageId", "(no id)")
            return True, f"queued (messageId={mid})"
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8"))
            msg = f"{e.code} {err.get('code','')} — {err.get('message','')}"
        except Exception:
            msg = f"HTTP {e.code}"
        return False, msg
    except Exception as e:
        return False, str(e)

# --------------------------------------------------------------------------- #
# Digest assembly
# --------------------------------------------------------------------------- #

def build_buckets(categories: list[str], per_category: int, cfg: Config | None = None) -> dict[str, list[Coin]]:
    """Fetch each requested category in turn, with colored progress in the CLI."""
    buckets: dict[str, list[Coin]] = {}
    for cat in categories:
        label, source = CATEGORIES.get(cat, (cat, "?"))
        print(c_dim(f"  • {c_cyan(source):<12s} ") + c_dim(f"→ {label} ..."), end=" ", flush=True)
        try:
            if   cat == "trending":   buckets[cat] = fetch_trending(limit=per_category)
            elif cat == "new24h":     buckets[cat] = fetch_new_24h(limit=per_category)
            elif cat == "newweek":    buckets[cat] = fetch_new_week(limit=per_category)
            elif cat == "gainers24h": buckets[cat] = fetch_top_gainers_24h(limit=per_category)
            elif cat == "topvolume":  buckets[cat] = fetch_top_volume(limit=per_category)
            elif cat == "topcap":
                key = (cfg.cmc_api_key if cfg else "") or os.environ.get("CMC_API_KEY", "")
                buckets[cat] = fetch_top_marketcap(limit=per_category, api_key=key)
            else:
                buckets[cat] = []
            print(c_green(f"✓ {len(buckets[cat])}"))
        except Exception as e:
            print(c_yellow(f"⚠ skipped ({e})"))
            buckets[cat] = []
    return buckets

def subject_for(buckets: dict[str, list[Coin]]) -> str:
    parts = []
    if buckets.get("trending"):
        parts.append(f"🔥 {buckets['trending'][0].name}")
    if buckets.get("gainers24h"):
        top = buckets["gainers24h"][0]
        if top.change_24h is not None:
            parts.append(f"📈 {top.symbol} {top.change_24h:+.1f}%")
        else:
            parts.append(f"📈 {top.symbol}")
    if buckets.get("topcap"):
        parts.append(f"#1 {buckets['topcap'][0].symbol}")
    if buckets.get("new24h"):
        parts.append(f"{len(buckets['new24h'])} new")
    if buckets.get("topvolume"):
        parts.append(f"top vol {buckets['topvolume'][0].symbol}")
    if buckets.get("newweek"):
        parts.append("recent gainers")
    tail = " · ".join(parts[:3]) if parts else "your crypto digest"  # cap at 3 for readability
    return f"{APP_NAME}: {tail}"

# --------------------------------------------------------------------------- #
# CLI prompts (with EOF safety)
# --------------------------------------------------------------------------- #

def prompt(msg: str) -> str:
    try:
        return input(msg).strip()
    except EOFError:
        print("\nEOF — bye.")
        sys.exit(0)
    except KeyboardInterrupt:
        print("\ninterrupted — bye.")
        sys.exit(0)

def prompt_int(msg: str, default: int, lo: int = 1, hi: int = 50) -> int:
    raw = prompt(f"{msg} [{default}]: ")
    if raw == "":
        return default
    try:
        v = int(raw)
        if v < lo: v = lo
        if v > hi: v = hi
        return v
    except ValueError:
        return default

def prompt_str(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    raw = prompt(f"{msg}{suffix}: ")
    return raw or default

def prompt_categories(default: list[str]) -> list[str]:
    print(c_bold_cyan("\n  Categories (data sources):"))
    keys = list(CATEGORIES.keys())
    for i, k in enumerate(keys, 1):
        mark = c_green("✓") if k in default else c_dim("·")
        label, source = CATEGORIES[k]
        print(f"   {mark} {c_cyan(str(i))}) {label}  {c_dim(f'[{source}]')}")
    print(c_dim("   * CoinMarketCap requires CMC_API_KEY env var (see Settings)."))
    raw = prompt(c_bold("  Enter numbers separated by commas, or blank to keep current: "))
    if not raw:
        return default
    chosen: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            n = int(part)
            if 1 <= n <= len(keys) and keys[n-1] not in chosen:
                chosen.append(keys[n-1])
    return chosen or default

# --------------------------------------------------------------------------- #
# Menu actions
# --------------------------------------------------------------------------- #

def banner() -> None:
    bar = "═" * 59
    print(c_bold_cyan(bar))
    print(c_bold_cyan(f"   {APP_NAME}  v{APP_VERSION}"))
    print(c_bold_cyan(f"   {APP_TAGLINE}"))
    print(c_bold_cyan(bar))

def _provider_creds_ok(cfg: Config) -> bool:
    p = (cfg.provider or "").lower()
    if p == "gmail":  return bool(cfg.gmail_user and cfg.gmail_app_password)
    if p == "brevo":  return bool(cfg.brevo_api_key and cfg.sender_email)
    if p == "resend": return bool(cfg.resend_api_key)
    if p == "smtp":   return bool(cfg.smtp_host and cfg.smtp_user and cfg.smtp_password)
    return False

def status_line(cfg: Config, subs: list[Subscriber]) -> None:
    creds_ok = _provider_creds_ok(cfg)
    creds_state = c_bold_green("configured") if creds_ok else c_bold_red("missing")
    sender = cfg.sender_email or cfg.gmail_user or cfg.smtp_user or c_dim("not set")
    cmc_state = c_green("yes") if cfg.cmc_api_key else c_dim("no")
    print(f"{c_dim_cyan('Subscribers')}: {c_bold(str(len(subs)))}  "
          f"{c_dim_cyan('Provider')}: {c_bold_cyan(cfg.provider)}  "
          f"{c_dim_cyan('Creds')}: {creds_state}  "
          f"{c_dim_cyan('Sender')}: {c_bold(sender)}  "
          f"{c_dim_cyan('CMC')}: {cmc_state}")
    print()

def menu_send_now(cfg: Config) -> None:
    print(c_bold("\nSend a digest right now (on-demand)"))
    to_email = prompt_str("Recipient email")
    if not to_email or "@" not in to_email:
        print(c_red("invalid email — aborting"))
        return
    to_name = prompt_str("Recipient name (optional)", "")
    cats = prompt_categories(list(CATEGORIES.keys()))
    n = prompt_int("How many coins per category", cfg.default_per_category, 1, 25)

    print(c_bold_cyan("\nFetching live data:"))
    buckets = build_buckets(cats, n, cfg)
    total = sum(len(v) for v in buckets.values())
    print(c_bold(f"\nGot {c_bold_green(str(total))} coins across {c_bold_cyan(str(len(cats)))} category(ies)."))
    if total == 0:
        print(c_yellow("nothing to send."))
        return

    html = render_html(buckets, recipient_name=to_name)
    subject = subject_for(buckets)

    print(c_dim(f"Sending via ") + c_bold_cyan(cfg.provider) + c_dim(f" to {to_email}..."))
    ok, info = send_email(cfg, to_email, to_name, subject, html)
    if ok:
        print(c_bold_green(f"✓ Sent: {info}"))
    else:
        print(c_bold_red(f"✗ FAIL: {info}"))

def menu_run_digest(cfg: Config, subs: list[Subscriber]) -> None:
    print(c_bold("\nRun the daily digest for all subscribers"))
    if not subs:
        print(c_yellow("no subscribers yet. Use option 2 to add some."))
        return
    print(c_dim(f"Will send to {len(subs)} subscriber(s)."))
    if prompt_str("Continue? (y/N)", "n").lower() not in ("y", "yes"):
        print("cancelled.")
        return
    sent = failed = 0
    for s in subs:
        print(c_bold_cyan(f"\n→ {s.email}") + c_dim(f"  ({', '.join(s.categories)}, {s.per_category} per category)"))
        buckets = build_buckets(s.categories, s.per_category, cfg)
        total = sum(len(v) for v in buckets.values())
        if total == 0:
            print(c_yellow("  no data — skipped"))
            continue
        html = render_html(buckets, recipient_name=s.name)
        subject = subject_for(buckets)
        ok, info = send_email(cfg, s.email, s.name, subject, html)
        if ok:
            print(c_bold_green(f"  ✓ sent ({info})"))
            sent += 1
        else:
            print(c_bold_red(f"  ✗ FAIL ({info})"))
            failed += 1
    print()
    print(c_bold("Digest complete."), c_green(f"sent={sent}"), c_red(f"failed={failed}"))

def menu_manage_subscribers(subs: list[Subscriber]) -> list[Subscriber]:
    while True:
        print(c_bold("\nManage subscribers"))
        if subs:
            for i, s in enumerate(subs, 1):
                cats = ",".join(s.categories)
                print(f"  {c_cyan(str(i))}) {s.email}  ({s.name or 'no name'})  [{cats}]  ×{s.per_category}")
        else:
            print(c_dim("  (no subscribers yet)"))
        print()
        print(f"  {c_bold_cyan('a')}) add new")
        print(f"  {c_bold_cyan('r')}) remove by number")
        print(f"  {c_bold_cyan('b')}) back to main menu")
        choice = prompt_str("Choose", "b").lower()
        if choice == "a":
            email = prompt_str("  Email")
            if not email or "@" not in email:
                print(c_red("  invalid email"))
                continue
            name = prompt_str("  Name (optional)", "")
            cats = prompt_categories(["trending", "new24h"])
            n = prompt_int("  Coins per category", 5, 1, 25)
            subs.append(Subscriber(email=email, name=name, categories=cats, per_category=n))
            save_subscribers(subs)
            print(c_green(f"  ✓ added {email}"))
        elif choice == "r":
            if not subs:
                print(c_yellow("  nothing to remove"))
                continue
            idx = prompt_int("  Number to remove", 0, 0, len(subs))
            if 1 <= idx <= len(subs):
                gone = subs.pop(idx - 1)
                save_subscribers(subs)
                print(c_green(f"  ✓ removed {gone.email}"))
        else:
            return subs

def menu_preview(cfg: Config) -> None:
    print(c_bold("\nPreview the next email (HTML to file, no send)"))
    cats = prompt_categories(list(CATEGORIES.keys()))
    n = prompt_int("Coins per category", cfg.default_per_category, 1, 25)
    print(c_bold_cyan("Fetching live data:"))
    buckets = build_buckets(cats, n, cfg)
    html = render_html(buckets, recipient_name="Preview")
    out = os.path.join(CONFIG_DIR, "preview.html")
    with open(out, "w") as f:
        f.write(html)
    total = sum(len(v) for v in buckets.values())
    print(c_bold_green(f"✓ wrote {out}  ({total} coins, {len(html):,} bytes)"))

def menu_test_connection(cfg: Config) -> None:
    print(c_bold(f"\nTest email connection ({cfg.provider})"))
    default_to = cfg.sender_email or cfg.gmail_user
    to = prompt_str("Send test email to", default_to)
    if not to or "@" not in to:
        print(c_red("invalid email"))
        return
    html = f"""<!doctype html><html><body style="font-family:sans-serif;padding:32px;">
        <h2 style="color:#0ea5e9;">{APP_NAME} — connection test ✅</h2>
        <p>If you can read this, your <strong>{cfg.provider}</strong> integration is working.</p>
        <p style="color:#64748b;font-size:12px;">Sent at {datetime.now(timezone.utc).isoformat()}</p>
        </body></html>"""
    ok, info = send_email(cfg, to, "", f"{APP_NAME} — test ping", html)
    if ok:
        print(c_bold_green(f"✓ {info}"))
    else:
        print(c_bold_red(f"✗ {info}"))

def menu_settings(cfg: Config) -> Config:
    print(c_bold_cyan("\nSettings"))
    print(c_dim(f"  Active provider  : ") + c_bold_cyan(cfg.provider))
    print(c_dim(f"  Sender name      : ") + c_bold(cfg.sender_name))
    print(c_dim(f"  Sender email     : ") + c_bold(cfg.sender_email or '(not set)'))
    print(c_dim(f"  Default per cat. : ") + c_bold(str(cfg.default_per_category)))
    print(c_dim("  ─── Email providers ───"))
    print(c_dim(f"    Gmail            : user={cfg.gmail_user or '(none)'}, pwd={'yes' if cfg.gmail_app_password else 'no'}"))
    print(c_dim(f"    Brevo            : key={'yes' if cfg.brevo_api_key else 'no'}"))
    print(c_dim(f"    Resend           : key={'yes' if cfg.resend_api_key else 'no'}"))
    print(c_dim(f"    Custom SMTP      : {cfg.smtp_host or '(none)'}:{cfg.smtp_port}, user={cfg.smtp_user or '(none)'}, pwd={'yes' if cfg.smtp_password else 'no'}, ssl={cfg.smtp_use_ssl}"))
    print(c_dim(f"  CoinMarketCap key  : ") + (c_green('yes') if cfg.cmc_api_key else c_yellow('no — set CMC_API_KEY to enable that bucket')))
    print()

    # Provider selection
    print(c_bold("Pick an email provider:"))
    for i, p in enumerate(PROVIDERS, 1):
        marker = c_green("✓") if p == cfg.provider else c_dim("·")
        print(f"   {marker} {c_cyan(str(i))}) {c_bold(p)} — {c_dim(PROVIDER_LABEL[p])}")
    pick = prompt_str("Provider [number or name]", cfg.provider).lower()
    if pick.isdigit() and 1 <= int(pick) <= len(PROVIDERS):
        cfg.provider = PROVIDERS[int(pick) - 1]
    elif pick in PROVIDERS:
        cfg.provider = pick

    cfg.sender_name = prompt_str("Sender name", cfg.sender_name)

    # Provider-specific creds
    if cfg.provider == "gmail":
        cfg.gmail_user = prompt_str("Gmail user (full address)", cfg.gmail_user)
        new_pwd = prompt_str("Gmail app password (16-char, blank to keep)", "")
        if new_pwd:
            cfg.gmail_app_password = new_pwd.replace(" ", "")
        cfg.sender_email = prompt_str("Sender email (defaults to Gmail user)", cfg.sender_email or cfg.gmail_user)
    elif cfg.provider == "brevo":
        cfg.sender_email = prompt_str("Sender email (must be a Brevo-verified sender)", cfg.sender_email)
        new_key = prompt_str("Brevo API key (paste to change, blank to keep)", "")
        if new_key:
            cfg.brevo_api_key = new_key
    elif cfg.provider == "resend":
        cfg.sender_email = prompt_str("Sender email (must be on a Resend-verified domain, or use onboarding@resend.dev for tests)", cfg.sender_email or "onboarding@resend.dev")
        new_key = prompt_str("Resend API key (paste to change, blank to keep)", "")
        if new_key:
            cfg.resend_api_key = new_key
    elif cfg.provider == "smtp":
        cfg.smtp_host = prompt_str("SMTP host (e.g. smtp.office365.com, smtp.mail.yahoo.com, mail.smtp2go.com)", cfg.smtp_host)
        cfg.smtp_port = prompt_int("SMTP port (465=SSL, 587=STARTTLS)", cfg.smtp_port or 465, 1, 65535)
        ssl_pick = prompt_str("Use SSL/TLS? (y/n) — y for port 465, n for port 587 with STARTTLS", "y" if cfg.smtp_use_ssl else "n").lower()
        cfg.smtp_use_ssl = ssl_pick.startswith("y")
        cfg.smtp_user = prompt_str("SMTP username (usually your full email)", cfg.smtp_user)
        new_pwd = prompt_str("SMTP password (paste to change, blank to keep)", "")
        if new_pwd:
            cfg.smtp_password = new_pwd
        cfg.sender_email = prompt_str("Sender email (defaults to SMTP user)", cfg.sender_email or cfg.smtp_user)

    # Optional CMC key (data-source key, applies to all providers)
    print(c_dim("\n  CoinMarketCap is the only data source that needs a key."))
    print(c_dim("  Get one free at https://coinmarketcap.com/api/  (333 calls/day on the free tier)"))
    new_cmc = prompt_str("CoinMarketCap API key (blank to keep / clear)", "")
    if new_cmc:
        cfg.cmc_api_key = new_cmc

    cfg.default_per_category = prompt_int("Default coins per category", cfg.default_per_category, 1, 25)
    save_config(cfg)
    print(c_bold_green("✓ saved. (secrets are kept in memory only — config.json holds no credentials)"))
    return cfg

# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #

def main_menu() -> None:
    cfg = load_config()
    subs = load_subscribers()
    banner()
    while True:
        print()
        status_line(cfg, subs)
        print(c_bold("What would you like to do?"))
        print(f"  {c_bold_cyan('1')}) Send a digest right now (on-demand)")
        print(f"  {c_bold_cyan('2')}) Manage subscribers (add / list / remove)")
        print(f"  {c_bold_cyan('3')}) Run the daily digest for all subscribers")
        print(f"  {c_bold_cyan('4')}) Preview the next email (HTML to file, no send)")
        print(f"  {c_bold_cyan('5')}) Test email connection (sends a small test ping)")
        print(f"  {c_bold_cyan('6')}) Settings")
        print(f"  {c_bold_cyan('0')}) Quit")
        choice = prompt_str("\nChoose [0-6]", "0")
        if choice == "1": menu_send_now(cfg)
        elif choice == "2": subs = menu_manage_subscribers(subs)
        elif choice == "3": menu_run_digest(cfg, subs)
        elif choice == "4": menu_preview(cfg)
        elif choice == "5": menu_test_connection(cfg)
        elif choice == "6": cfg = menu_settings(cfg)
        elif choice == "0":
            print(c_dim("bye."))
            return
        else:
            print(c_yellow(f"unknown choice: {choice!r}"))

if __name__ == "__main__":
    main_menu()
