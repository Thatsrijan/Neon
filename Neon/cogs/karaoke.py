# --- Begin DIAGNOSTIC + FALLBACK lyrics code ---
import aiohttp
import asyncio
import socket
import re
import os
import time
import json
import traceback
from discord import app_commands

# Shared aiohttp session (reuse to reduce overhead)
_session: aiohttp.ClientSession | None = None
def get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session

async def safe_head(url: str, timeout: float = 5.0):
    """Return (status, text_snippet or None, error_or_None)"""
    try:
        sess = get_session()
        async with sess.head(url, timeout=timeout) as r:
            text = None
            try:
                text = (await r.text())[:400]
            except Exception:
                text = None
            return r.status, text, None
    except Exception as e:
        return None, None, repr(e)

async def safe_get(url: str, timeout: float = 8.0):
    try:
        sess = get_session()
        async with sess.get(url, timeout=timeout) as r:
            text = await r.text()
            return r.status, text, None
    except Exception as e:
        return None, None, repr(e)

# small helper to split "Artist - Title" when possible
def split_artist_title(query: str):
    if " - " in query:
        parts = query.split(" - ", 1)
        return parts[0].strip(), parts[1].strip()
    return None, query.strip()

# ---------- DIAGNOSTIC command ----------
@app_commands.command(name="lyricsdiag", description="Diagnose connectivity to Genius (for /lyrics issues).")
@app_commands.describe(query="Optional song name to include in diagnostics")
async def lyricsdiag(interaction: discord.Interaction, query: str = ""):
    await interaction.response.defer(thinking=True)
    start = time.time()
    results = {}
    # 1) DNS resolution check for api.genius.com and genius.com
    for host in ("api.genius.com", "genius.com"):
        try:
            addrs = socket.getaddrinfo(host, 443)
            results[f"dns_{host}"] = f"OK ({len(addrs)} addresses)"
            print(f"[lyricsdiag] DNS {host} -> {addrs[0][4]}")
        except Exception as e:
            results[f"dns_{host}"] = f"ERROR: {repr(e)}"
            print(f"[lyricsdiag] DNS error for {host}: {e}")

    # 2) HEAD to api.genius.com
    status, snippet, err = await safe_head("https://api.genius.com/", timeout=6)
    results["api_head_status"] = status
    results["api_head_err"] = err
    if snippet:
        results["api_head_snippet"] = snippet[:200]

    # 3) HEAD to genius.com
    status2, snippet2, err2 = await safe_head("https://genius.com/", timeout=6)
    results["page_head_status"] = status2
    results["page_head_err"] = err2
    if snippet2:
        results["page_head_snippet"] = snippet2[:200]

    # 4) optional: try small search via Genius API (unauthenticated short call) — won't work without token but we try to show connectivity
    token = os.getenv("GENIUS_API_TOKEN")
    if token:
        try:
            sess = get_session()
            async with sess.get("https://api.genius.com/search", params={"q": query or "Adele Hello"}, headers={"Authorization": f"Bearer {token}"}, timeout=8) as r:
                txt = await r.text()
                results["api_search_status"] = r.status
                results["api_search_snippet"] = txt[:300]
        except Exception as e:
            results["api_search_err"] = repr(e)
    else:
        results["api_search_err"] = "No GENIUS_API_TOKEN in env"

    elapsed = time.time() - start
    print("[lyricsdiag] Results:", json.dumps(results, default=str)[:2000])
    # present a short user-visible summary, and print full details to logs
    summary = []
    summary.append(f"DNS: api.genius.com -> {results.get('dns_api.genius.com')}")
    summary.append(f"api.genius.com HEAD -> {results.get('api_head_status')} (err={results.get('api_head_err')})")
    summary.append(f"genius.com HEAD -> {results.get('page_head_status')} (err={results.get('page_head_err')})")
    if "api_search_status" in results:
        summary.append(f"Genius API search -> {results.get('api_search_status')}")
    else:
        summary.append("Genius API search -> skipped (no token)")
    summary_text = "\n".join(summary)
    try:
        await interaction.followup.send(f"Diagnostics summary:\n```\n{summary_text}\n```")
    except Exception as e:
        print("[lyricsdiag] Failed followup send:", e)
    print("[lyricsdiag] Full details:", json.dumps(results, default=str))
    return

# ---------- FALLBACK lyrics fetcher using Genius + lyrics.ovh ----------
async def fetch_lyrics_from_genius_async(query: str, timeout: float = 8.0, retries: int = 1):
    """Try the Genius API + page scrape (async). Returns dict or None."""
    token = os.getenv("GENIUS_API_TOKEN")
    if not token:
        print("[lyricsfallback] No GENIUS_API_TOKEN set — skipping Genius path.")
        return None

    search_url = "https://api.genius.com/search"
    headers = {"Authorization": f"Bearer {token}"}
    attempt = 0
    while attempt <= retries:
        attempt += 1
        try:
            sess = get_session()
            async with sess.get(search_url, params={"q": query}, headers=headers, timeout=timeout) as r:
                status = r.status
                txt = await r.text()
            print(f"[lyricsfallback] search status={status} len={len(txt) if txt else 0}")
            if status != 200:
                print("[lyricsfallback] non-200 search status", status)
                await asyncio.sleep(0.3 * attempt)
                continue
            j = json.loads(txt)
            hits = j.get("response", {}).get("hits", [])
            if not hits:
                print("[lyricsfallback] no hits for query", query)
                return None
            top = hits[0].get("result", {})
            song_path = top.get("path")
            title = top.get("title")
            artist = top.get("primary_artist", {}).get("name")
            if not song_path:
                print("[lyricsfallback] no path in top hit")
                return None
            song_url = "https://genius.com" + song_path
            # fetch page
            sess = get_session()
            async with sess.get(song_url, timeout=timeout) as page_r:
                page_html = await page_r.text()
            # attempt to extract lyrics containers
            parts = re.findall(r'<div[^>]+data-lyrics-container="true"[^>]*>(.*?)</div>', page_html, flags=re.DOTALL | re.IGNORECASE)
            if not parts:
                m = re.search(r'<div class="lyrics">(.+?)</div>', page_html, flags=re.DOTALL | re.IGNORECASE)
                if m:
                    parts = [m.group(1)]
            if not parts:
                print("[lyricsfallback] no lyrics parts found; returning empty lyrics")
                return {"title": title or "Unknown", "artist": artist or "Unknown", "lyrics": ""}
            clean = []
            for p in parts:
                p = re.sub(r'<br\s*/?>', '\n', p, flags=re.IGNORECASE)
                p = re.sub(r'<.*?>', '', p, flags=re.DOTALL)
                p = p.strip()
                if p:
                    clean.append(p)
            lyrics_text = "\n\n".join(clean)
            print(f"[lyricsfallback] scraped len {len(lyrics_text)}")
            return {"title": title or "Unknown", "artist": artist or "Unknown", "lyrics": lyrics_text}
        except Exception as e:
            print("[lyricsfallback] attempt exception:", e)
            traceback.print_exc()
            await asyncio.sleep(0.3 * attempt)
    return None

async def fetch_lyrics_from_lyrics_ovh(query: str, timeout: float = 6.0):
    """
    Try the simple lyrics.ovh API as a fallback when Genius fails.
    Requires artist and title split (Artist - Title). Returns dict or None.
    """
    artist, title = split_artist_title(query)
    if not artist:
        print("[lyricsovh] Query does not contain 'Artist - Title' form; cannot use lyrics.ovh fallback.")
        return None
    api = f"https://api.lyrics.ovh/v1/{artist}/{title}"
    try:
        status, text, err = await safe_get(api, timeout=timeout)
        print("[lyricsovh] status", status, "err", err)
        if status != 200 or not text:
            return None
        j = json.loads(text)
        lyrics = j.get("lyrics", "")
        if not lyrics:
            return None
        return {"title": title, "artist": artist, "lyrics": lyrics}
    except Exception as e:
        print("[lyricsovh] exception", e)
        return None

# ---------- Updated /lyrics handler that uses the above ----------
@app_commands.command(name="lyrics", description="Fetch full lyrics for a song (tries Genius then fallback).")
@app_commands.describe(query="Song name or 'Artist - Title'")
async def lyrics_cmd(self, interaction: discord.Interaction, query: str):
    # always defer so we won't get stuck
    await interaction.response.defer(thinking=True)
    start = time.time()
    print(f"[lyricscmd] invoked by {interaction.user} query={query!r} guild={interaction.guild_id}")
    try:
        # try Genius async scraper
        res = await fetch_lyrics_from_genius_async(query, timeout=8.0, retries=1)
        used = "genius"
        if res is None:
            # attempt lyrics.ovh fallback
            res = await fetch_lyrics_from_lyrics_ovh(query, timeout=6.0)
            used = "lyrics.ovh" if res else "none"
        elapsed = time.time() - start
        if not res:
            print(f"[lyricscmd] no lyrics found via any provider (elapsed {elapsed:.2f}s)")
            await interaction.followup.send(f"❌ Could not fetch lyrics for **{query}**. (Tried Genius & lyrics.ovh) See logs.")
            return
        title = res.get("title", "Unknown")
        artist = res.get("artist", "Unknown")
        lyrics = res.get("lyrics", "")
        if not lyrics.strip():
            await interaction.followup.send(f"ℹ️ Found **{title} - {artist}** via {used}, but no lyrics text was scraped.")
            return
        # send header and chunked lyrics
        await interaction.followup.send(f"🎶 Lyrics for **{title} - {artist}** (via {used}, {elapsed:.1f}s):")
        for i in range(0, len(lyrics), 1900):
            chunk = lyrics[i:i+1900]
            try:
                await interaction.followup.send(f"```{chunk}```")
                await asyncio.sleep(0.25)
            except Exception as e:
                print("[lyricscmd] chunk send error:", e)
        print(f"[lyricscmd] completed send (provider={used}) total_time={time.time()-start:.2f}s")
    except Exception as e:
        print("[lyricscmd] unexpected handler error:", e)
        traceback.print_exc()
        try:
            await interaction.followup.send("⚠️ Unexpected error while fetching lyrics. Check logs.")
        except Exception:
            pass
# --- End DIAGNOSTIC + FALLBACK lyrics code ---


