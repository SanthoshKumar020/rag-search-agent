"""Mood companion media suggestions (v3) — "friend mode".

suggest_media(mood) returns songs + shows/movies for how the user
feels, using only keyless APIs:

  - Songs : iTunes Search API (no key) — track page + 30s preview URL.
  - Shows : TVMaze search API (no key).
  - Movies: grounded web search links (this repo's search pipeline).

Moods map to curated search terms. Tamil songs are included by
default for the user's main moods (sad / stressed / romantic /
relaxing); pass language="en" to skip Tamil, "ta" for Tamil only.
Everything degrades gracefully: an API miss falls back to web-search
links, never an exception.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus

import requests

UA = {"User-Agent": "rag-search-agent/3.0 (personal agent; contact: local)"}
TIMEOUT = 8

# mood -> song search terms + TVMaze genres + a movie-list web query.
# iTunes matches artist names far better than genre phrases, so the
# Tamil terms are the composers/singers known for that mood.
MOODS: dict[str, dict[str, Any]] = {
    "sad": {
        "ta_songs": ["Ilaiyaraaja melody", "Sid Sriram melody"],
        "en_songs": ["Adele ballad", "sad acoustic songs"],
        "genres": ["Comedy"],
        "movies_q": "best feel-good uplifting movies to watch when sad",
    },
    "stressed": {
        "ta_songs": ["A. R. Rahman melody", "Ilaiyaraaja"],
        "en_songs": ["calm acoustic chill"],
        "genres": ["Comedy", "Drama"],
        "movies_q": "best relaxing comfort movies to unwind",
    },
    "relaxed": {
        "ta_songs": ["Ilaiyaraaja instrumental", "A. R. Rahman"],
        "en_songs": ["chill lofi"],
        "genres": ["Drama"],
        "movies_q": "best slow feel-good movies",
    },
    "happy": {
        "ta_songs": ["Anirudh Ravichander", "Yuvan Shankar Raja"],
        "en_songs": ["feel good pop hits"],
        "genres": ["Comedy"],
        "movies_q": "best comedy movies to watch when happy",
    },
    "romantic": {
        "ta_songs": ["A. R. Rahman love", "Harris Jayaraj"],
        "en_songs": ["romantic love songs"],
        "genres": ["Romance", "Drama"],
        "movies_q": "best romantic movies of all time",
    },
    "energetic": {
        "ta_songs": ["Anirudh Ravichander mass", "D. Imman"],
        "en_songs": ["workout pump up"],
        "genres": ["Action", "Thriller"],
        "movies_q": "best high-energy action movies",
    },
    "bored": {
        "ta_songs": ["Ilaiyaraaja hits", "S. P. Balasubrahmanyam"],
        "en_songs": ["greatest hits pop"],
        "genres": ["Mystery", "Thriller"],
        "movies_q": "best gripping thriller movies you cannot pause",
    },
    "lonely": {
        "ta_songs": ["Ilaiyaraaja", "K. J. Yesudas melody"],
        "en_songs": ["comfort acoustic songs"],
        "genres": ["Drama", "Comedy"],
        "movies_q": "best heartwarming movies about friendship",
    },
}

_ALIASES = {
    "stress": "stressed", "anxious": "stressed", "tired": "relaxed",
    "calm": "relaxed", "chill": "relaxed", "love": "romantic",
    "energy": "energetic", "workout": "energetic", "down": "sad",
    "unhappy": "sad", "depressed": "sad", "alone": "lonely",
    "excited": "happy", "joyful": "happy",
}


def normalize_mood(mood: str) -> str:
    m = (mood or "").strip().lower()
    if m in MOODS:
        return m
    if m in _ALIASES:
        return _ALIASES[m]
    for key in MOODS:
        if key in m or m in key:
            return key
    for alias, key in _ALIASES.items():
        if alias in m:
            return key
    return "happy"  # gentle default: upbeat picks


# ----------------------------------------------------------------------
# keyless APIs
# ----------------------------------------------------------------------
def _itunes_songs(term: str, limit: int = 4) -> list[dict[str, Any]]:
    try:
        resp = requests.get(
            "https://itunes.apple.com/search",
            params={"term": term, "media": "music", "entity": "song",
                    "limit": limit, "country": "IN"},
            headers=UA, timeout=TIMEOUT,
        )
        resp.raise_for_status()
        rows = (resp.json() or {}).get("results") or []
    except Exception:
        return []
    out = []
    for r in rows:
        name = (r.get("trackName") or "").strip()
        if not name:
            continue
        out.append({
            "title": name,
            "artist": (r.get("artistName") or "").strip(),
            "album": (r.get("collectionName") or "").strip(),
            "url": r.get("trackViewUrl") or "",
            "preview": r.get("previewUrl") or "",
        })
    return out


_SHOWS_CACHE: list[dict[str, Any]] | None = None


def _tvmaze_catalogue() -> list[dict[str, Any]]:
    """First pages of the TVMaze show catalogue (cached per process).

    TVMaze's /search matches titles, not moods — so for mood picks we
    fetch the catalogue once and filter by genre + rating locally.
    """
    global _SHOWS_CACHE
    if _SHOWS_CACHE is not None:
        return _SHOWS_CACHE
    shows: list[dict[str, Any]] = []
    for page in (0, 1):
        try:
            resp = requests.get(
                "https://api.tvmaze.com/shows",
                params={"page": page}, headers=UA, timeout=TIMEOUT,
            )
            resp.raise_for_status()
            shows.extend(resp.json() or [])
        except Exception:
            break
    _SHOWS_CACHE = shows
    return shows


def _tvmaze_shows(genres: list[str], limit: int = 3) -> list[dict[str, Any]]:
    wanted = {g.lower() for g in genres}
    matches = []
    for show in _tvmaze_catalogue():
        show_genres = {g.lower() for g in (show.get("genres") or [])}
        if not (wanted & show_genres):
            continue
        rating = ((show.get("rating") or {}).get("average")) or 0
        matches.append((rating, show))
    matches.sort(key=lambda x: x[0], reverse=True)
    out = []
    for rating, show in matches[:limit]:
        summary = re.sub(r"<[^>]+>", "", show.get("summary") or "")
        out.append({
            "title": (show.get("name") or "").strip(),
            "url": show.get("url") or "",
            "summary": " ".join(summary.split())[:220],
            "rating": rating or None,
            "genres": show.get("genres") or [],
        })
    return [s for s in out if s["title"]]


def _movie_links(query: str, limit: int = 3) -> list[dict[str, str]]:
    try:
        from search import web_search

        results = web_search(query, max_results=limit)
        return [{"title": r["title"], "url": r["url"]} for r in results]
    except Exception:
        return []


# ----------------------------------------------------------------------
# public API
# ----------------------------------------------------------------------
def suggest_media_data(mood: str, language: str = "both") -> dict[str, Any]:
    """Structured suggestions {mood, songs, shows, movies, note}."""
    key = normalize_mood(mood)
    cfg = MOODS[key]
    language = (language or "both").strip().lower()

    terms: list[str] = []
    if language in ("both", "ta"):
        terms += cfg["ta_songs"]
    if language in ("both", "en"):
        terms += cfg["en_songs"]

    songs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for term in terms:
        for song in _itunes_songs(term, limit=3):
            dedupe = (song["title"] + song["artist"]).lower()
            if dedupe in seen:
                continue
            seen.add(dedupe)
            songs.append(song)
        if len(songs) >= 6:
            break

    shows = _tvmaze_shows(cfg["genres"], limit=3)

    movies = _movie_links(cfg["movies_q"], limit=3)

    note = ""
    if not songs and not shows and not movies:
        note = ("Media APIs returned nothing right now — try again in a "
                "moment, or ask me to web-search playlists for your mood.")
    return {"mood": key, "songs": songs[:6], "shows": shows,
            "movies": movies, "note": note}


def suggest_media(mood: str, language: str = "both") -> str:
    """Text form for the agent tool loop."""
    data = suggest_media_data(mood, language)
    lines = [f"Suggestions for when you feel {data['mood']}:"]
    if data["songs"]:
        lines.append("\n🎵 Songs:")
        for s in data["songs"]:
            lines.append(f"- {s['title']} — {s['artist']}"
                         + (f"\n  {s['url']}" if s["url"] else ""))
    if data["shows"]:
        lines.append("\n📺 Shows:")
        for s in data["shows"]:
            rating = f" (★{s['rating']})" if s.get("rating") else ""
            lines.append(f"- {s['title']}{rating}: {s['summary']}"
                         + (f"\n  {s['url']}" if s["url"] else ""))
    if data["movies"]:
        lines.append("\n🎬 Movie lists to browse:")
        for m in data["movies"]:
            lines.append(f"- {m['title']}\n  {m['url']}")
    if data["note"]:
        lines.append("\n" + data["note"])
    return "\n".join(lines)
