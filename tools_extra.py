"""Extra keyless tools for the personal agent (v2).

All network tools use free, no-key public APIs with short timeouts and
return graceful failure strings — they never raise.

  - get_datetime : local clock (UTC + Asia/Kolkata)
  - weather      : Open-Meteo geocoding + forecast (no key)
  - news         : Google News RSS (topic) or BBC RSS (no topic)
  - wikipedia    : Wikipedia REST summary API
  - calculate    : SAFE arithmetic via Python ast (no eval)
  - currency     : open.er-api.com keyless rates
"""
from __future__ import annotations

import ast
import math
import operator
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import requests

try:
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover - no tzdata on the box
    from datetime import timedelta

    IST = timezone(timedelta(hours=5, minutes=30))

UA = {"User-Agent": "rag-search-agent/2.0 (personal agent; contact: local)"}
TIMEOUT = 8


# ----------------------------------------------------------------------
# datetime
# ----------------------------------------------------------------------
def get_datetime() -> str:
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc.astimezone(IST)
    return (
        f"UTC: {now_utc.strftime('%A %Y-%m-%d %H:%M:%S')}\n"
        f"Asia/Kolkata: {now_ist.strftime('%A %Y-%m-%d %H:%M:%S')} IST"
    )


# ----------------------------------------------------------------------
# weather (Open-Meteo, no key)
# ----------------------------------------------------------------------
_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain",
    71: "slight snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "slight rain showers", 81: "rain showers", 82: "violent rain showers",
    85: "snow showers", 86: "snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail",
    99: "thunderstorm with hail",
}


def weather_data(city: str) -> dict[str, Any] | None:
    """Structured today's-weather dict for a city, or None on failure."""
    city = (city or "").strip()
    if not city:
        return None
    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en",
                    "format": "json"},
            headers=UA, timeout=TIMEOUT,
        )
        geo.raise_for_status()
        results = (geo.json() or {}).get("results") or []
        if not results:
            return None
        top = results[0]
        lat, lon = top["latitude"], top["longitude"]
        fc = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "daily": ("temperature_2m_max,temperature_2m_min,"
                          "weather_code,precipitation_probability_max"),
                "forecast_days": 1, "timezone": "auto",
            },
            headers=UA, timeout=TIMEOUT,
        )
        fc.raise_for_status()
        daily = (fc.json() or {}).get("daily") or {}
        code = (daily.get("weather_code") or [None])[0]
        rain = (daily.get("precipitation_probability_max") or [None])[0]
        return {
            "city": top.get("name") or city,
            "country": top.get("country") or "",
            "temp_max": (daily.get("temperature_2m_max") or [None])[0],
            "temp_min": (daily.get("temperature_2m_min") or [None])[0],
            "conditions": _WMO.get(code, f"code {code}"),
            "rain_chance": rain,
        }
    except Exception:
        return None


def weather(city: str) -> str:
    city = (city or "").strip()
    if not city:
        return "Please provide a city name for the weather lookup."
    data = weather_data(city)
    if not data:
        return f"Could not fetch weather for '{city}' right now."
    rain = data["rain_chance"]
    rain_txt = f"{rain:.0f}%" if isinstance(rain, (int, float)) else "unknown"
    return (
        f"Weather today in {data['city']}"
        f"{', ' + data['country'] if data['country'] else ''}: "
        f"{data['temp_min']}°C – {data['temp_max']}°C, "
        f"{data['conditions']}, chance of rain {rain_txt}."
    )


# ----------------------------------------------------------------------
# news (RSS, no key)
# ----------------------------------------------------------------------
def news_items(topic: str = "", limit: int = 6) -> list[dict[str, str]]:
    """Structured headlines [{title, link, source}]; [] on failure."""
    topic = (topic or "").strip()
    if topic:
        url = ("https://news.google.com/rss/search?q="
               + quote_plus(topic) + "&hl=en-IN&gl=IN&ceid=IN:en")
    else:
        url = "https://feeds.bbci.co.uk/news/rss.xml"
    try:
        resp = requests.get(url, headers=UA, timeout=TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception:
        return []
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        if not source and not topic:
            source = "BBC News"
        if title and link:
            items.append({"title": title, "link": link, "source": source})
        if len(items) >= limit:
            break
    return items


def news(topic: str = "", limit: int = 6) -> str:
    topic = (topic or "").strip()
    items = news_items(topic, limit)
    if not items:
        return "Could not fetch headlines right now, or none were found."
    lines = [f"Top headlines{' for ' + topic if topic else ''}:"]
    for i, it in enumerate(items, 1):
        src = f" ({it['source']})" if it["source"] else ""
        lines.append(f"{i}. {it['title']}{src}\n   {it['link']}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# wikipedia summary
# ----------------------------------------------------------------------
def wikipedia(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return "Please provide a topic for the Wikipedia lookup."
    try:
        resp = requests.get(
            "https://en.wikipedia.org/api/rest_v1/page/summary/"
            + quote_plus(query.replace(" ", "_")),
            headers=UA, timeout=TIMEOUT,
        )
        if resp.status_code == 404:
            return f"No Wikipedia article found for '{query}'."
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception as exc:
        return f"Could not fetch Wikipedia summary right now ({exc})."
    title = data.get("title") or query
    extract = (data.get("extract") or "").strip()
    page = ((data.get("content_urls") or {}).get("desktop") or {}).get("page", "")
    if not extract:
        return f"No summary available for '{query}'."
    return f"{title}: {extract[:1200]}\n{page}".strip()


# ----------------------------------------------------------------------
# calculate — SAFE arithmetic via ast (never eval)
# ----------------------------------------------------------------------
_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
    "log2": math.log2, "exp": math.exp, "sin": math.sin, "cos": math.cos,
    "tan": math.tan, "abs": abs, "round": round, "floor": math.floor,
    "ceil": math.ceil,
}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
_MAX_POW = 1000  # guard against 9**9**9 style blowups


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ValueError("only numeric constants are allowed")
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POW:
            raise ValueError("exponent too large")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ValueError("only these functions are allowed: "
                             + ", ".join(sorted(_FUNCS)))
        if node.keywords:
            raise ValueError("keyword arguments are not allowed")
        args = [_eval_node(a) for a in node.args]
        return _FUNCS[node.func.id](*args)
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    raise ValueError(f"expression element not allowed: {type(node).__name__}")


def calculate(expression: str) -> str:
    expr = (expression or "").strip()
    if not expr:
        return "Please provide an arithmetic expression."
    if len(expr) > 200:
        return "Expression too long (max 200 characters)."
    try:
        tree = ast.parse(expr, mode="eval")
        value = _eval_node(tree)
    except Exception as exc:
        return f"Could not calculate '{expr}': {exc}"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{expr} = {value}"


def calculate_value(expression: str) -> float:
    """Numeric result for tests; raises ValueError on bad input."""
    tree = ast.parse((expression or "").strip(), mode="eval")
    return _eval_node(tree)


# ----------------------------------------------------------------------
# currency (open.er-api.com, no key)
# ----------------------------------------------------------------------
def currency_data(from_ccy: str, to_ccy: str) -> dict[str, Any] | None:
    """Structured rate dict {from, to, rate, updated} or None."""
    src = (from_ccy or "").strip().upper()
    dst = (to_ccy or "").strip().upper()
    if len(src) != 3 or len(dst) != 3:
        return None
    try:
        resp = requests.get(f"https://open.er-api.com/v6/latest/{src}",
                            headers=UA, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception:
        return None
    rates = data.get("rates") or {}
    if data.get("result") != "success" or dst not in rates:
        return None
    return {"from": src, "to": dst, "rate": rates[dst],
            "updated": data.get("time_last_update_utc") or ""}


def currency(amount: Any, from_ccy: str, to_ccy: str) -> str:
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        return f"Invalid amount: {amount!r}"
    src = (from_ccy or "").strip().upper()
    dst = (to_ccy or "").strip().upper()
    if len(src) != 3 or len(dst) != 3:
        return "Currency codes must be 3 letters, e.g. USD, INR, EUR."
    try:
        resp = requests.get(f"https://open.er-api.com/v6/latest/{src}",
                            headers=UA, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception as exc:
        return f"Could not fetch exchange rates right now ({exc})."
    rates = data.get("rates") or {}
    if data.get("result") != "success" or dst not in rates:
        return f"No rate available for {src} → {dst}."
    rate = rates[dst]
    converted = amt * rate
    updated = data.get("time_last_update_utc") or "unknown date"
    return (f"{amt:g} {src} = {converted:,.2f} {dst} "
            f"(rate 1 {src} = {rate} {dst}, updated {updated})")


# ----------------------------------------------------------------------
# translate (MyMemory, no key) — source language defaults to English
# ----------------------------------------------------------------------
def translate_text(text: str, to_lang: str, from_lang: str = "en") -> str:
    text = (text or "").strip()
    to_lang = (to_lang or "").strip().lower()
    if not text:
        return "Please provide text to translate."
    if not to_lang:
        return "Please provide a target language code, e.g. ta, hi, es, fr."
    try:
        resp = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:450], "langpair": f"{from_lang}|{to_lang}"},
            headers=UA, timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception as exc:
        return f"Could not translate right now ({exc})."
    translated = ((data.get("responseData") or {}).get("translatedText")
                  or "").strip()
    if not translated or data.get("responseStatus") not in (200, "200", None):
        return f"Translation to '{to_lang}' failed or is unavailable."
    return f"{text}\n→ ({to_lang}) {translated}"


# ----------------------------------------------------------------------
# public holidays (Nager.Date, no key)
# ----------------------------------------------------------------------
def holidays_list(country: str = "IN", year: int | None = None,
                  limit: int = 5) -> str:
    country = (country or "IN").strip().upper()
    today = datetime.now(timezone.utc).date()
    year = year or today.year
    try:
        resp = requests.get(
            f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country}",
            headers=UA, timeout=TIMEOUT,
        )
        if resp.status_code == 404:
            return f"No holiday data for country '{country}'."
        resp.raise_for_status()
        if resp.status_code == 204 or not (resp.content or b"").strip():
            # Nager.Date returns 204 for countries it does not cover
            # (India included) — say so plainly instead of a parse error.
            return (f"Nager.Date has no public-holiday data for "
                    f"'{country}' (it does not cover this country).")
        rows = resp.json() or []
    except Exception as exc:
        return f"Could not fetch holidays right now ({exc})."
    upcoming = []
    for row in rows:
        d = (row.get("date") or "")
        try:
            hd = datetime.fromisoformat(d).date()
        except ValueError:
            continue
        if year == today.year and hd < today:
            continue
        upcoming.append((hd, row.get("name") or "", row.get("localName") or ""))
    upcoming.sort(key=lambda x: x[0])
    if not upcoming:
        return f"No upcoming holidays found for {country} in {year}."
    lines = [f"Next public holidays in {country} ({year}):"]
    for hd, name, local in upcoming[:limit]:
        extra = f" ({local})" if local and local != name else ""
        lines.append(f"- {hd.isoformat()}: {name}{extra}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# dictionary (Free Dictionary API, no key)
# ----------------------------------------------------------------------
def define_word(word: str) -> str:
    word = (word or "").strip()
    if not word:
        return "Please provide a word to define."
    try:
        resp = requests.get(
            "https://api.dictionaryapi.dev/api/v2/entries/en/"
            + quote_plus(word.lower()),
            headers=UA, timeout=TIMEOUT,
        )
        if resp.status_code == 404:
            return f"No dictionary entry found for '{word}'."
        resp.raise_for_status()
        entries = resp.json() or []
    except Exception as exc:
        return f"Could not fetch a definition right now ({exc})."
    if not entries:
        return f"No dictionary entry found for '{word}'."
    entry = entries[0]
    phonetic = entry.get("phonetic") or ""
    lines = [f"{entry.get('word') or word}"
             + (f" {phonetic}" if phonetic else "")]
    count = 0
    for meaning in entry.get("meanings") or []:
        pos = meaning.get("partOfSpeech") or ""
        for d in meaning.get("definitions") or []:
            definition = (d.get("definition") or "").strip()
            if not definition:
                continue
            lines.append(f"- ({pos}) {definition}")
            count += 1
            if count >= 3:
                break
        if count >= 3:
            break
    if count == 0:
        return f"No definitions found for '{word}'."
    return "\n".join(lines)
