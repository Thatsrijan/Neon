# Paste this into your karaoke cog file (replace existing lyrics/diag functions)
# Required imports (ensure these are present at top of the file)
import aiohttp
import asyncio
import socket
import re
import os
import time
import json
import traceback
import random

from discord import app_commands
from discord.ext import commands

# ---------- Shared aiohttp session ----------
_session: aiohttp.ClientSession | None = None
def get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session

async def safe_head(url: str, timeout: float = 5.0):
    try:
        sess = get_session()
        async with sess.head(url, timeout=timeout) as r:
            txt = None
            try:
                txt = (await r.text())[:400]
            except Exception:
                txt = None
            return r.status, txt, None
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

def split_artist_title(query: str):
    if " - " in query:
        parts = query.split(" - ", 1)
        return parts[0].strip(), parts[1].strip()
    return None, query.strip()

# ---------- Async Genius search + page scrape ----------
async def fetch_lyrics_from_genius_async(query: str, timeout: float = 8.0, retries: int = 1):
    token = os.getenv("GENIUS_API_TOKEN")
    if not token:
        print("[lyricsfetch] No GENIUS_API_TOKEN set — skipping Genius path.")
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
            print(f"[lyricsfetch] search status={status} (attempt {attempt}) for query={query!r}")
            if status != 200:
                print("[lyricsfetch] non-200 search status", status)
                await asyncio.sleep(0.3 * attempt)
                continue
            j = json.loads(txt)
            hits = j.get("response", {}).get("hits", [])
            if not hits:
                print("[lyricsfetch] no hits for query", query)
                return None
            top = hits[0].get("result", {})
            song_path = top.get("path")
            title = top.get("title")
            artist = top.get("primary_artist", {}).get("name")
            if not song_path:
                print("[lyricsfetch] no path in top hit")
                return None
            song_url = "https://genius.com" + song_path
            # fetch page
            async with sess.get(song_url, timeout=timeout) as page_r:
                page_html = await page_r.text()
                page_status = page_r.status
            if page_status != 200:
                print(f"[lyricsfetch] song page non-200 {page_status} for {song_url}")
                await asyncio.sleep(0.3 * attempt)
                continue

            # Try multiple extraction strategies
            parts = re.findall(r'<div[^>]+data-lyrics-container="true"[^>]*>(.*?)</div>', page_html, flags=re.DOTALL | re.IGNORECASE)
            if not parts:
                m = re.search(r'<div class="lyrics">(.+?)</div>', page_html, flags=re.DOTALL | re.IGNORECASE)
                if m:
                    parts = [m.group(1)]
            if not parts:
                parts = re.findall(r'<div[^>]+class="SongPage__lyrics"[^>]*>(.*?)</div>', page_html, flags=re.DOTALL | re.IGNORECASE)

            if not parts:
                print("[lyricsfetch] no lyrics parts found; dumping small snippet for debugging")
                print(page_html[:800])
                return {"title": title or "Unknown", "artist": artist or "Unknown", "lyrics": ""}

            clean = []
            for p in parts:
                p = re.sub(r'<br\s*/?>', '\n', p, flags=re.IGNORECASE)
                p = re.sub(r'<.*?>', '', p, flags=re.DOTALL)
                p = p.strip()
                if p:
                    clean.append(p)
            lyrics_text = "\n\n".join(clean)
            print(f"[lyricsfetch] scraped lyrics len={len(lyrics_text)} for {title!r}")
            return {"title": title or "Unknown", "artist": artist or "Unknown", "lyrics": lyrics_text}
        except Exception as e:
            print("[lyricsfetch] exception:", e)
            traceback.print_exc()
            await asyncio.sleep(0.3 * attempt)
    return None

# ---------- Fallback: lyrics.ovh ----------
async def fetch_lyrics_from_lyrics_ovh(query: str, timeout: float = 6.0):
    artist, title = split_artist_title(query)
    if not artist:
        print("[lyricsovh] Query not in 'Artist - Title' form; can't use lyrics.ovh")
        return None
    api = f"https://api.lyrics.ovh/v1/{artist}/{title}"
    try:
        status, text, err = await safe_get(api, timeout=timeout)
        print("[lyricsovh] status", status)
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

# ---------- The Cog methods to paste into your commands.Cog (replace old lyrics handlers) ----------
# If your karaoke cog class is named differently, put these inside that class.
# Example: class Karaoke(commands.Cog): ... paste methods below into that class.

# Prefix diagnostic command (immediate)
@commands.command(name="lyricsdiag")
async def lyricsdiag_prefix(self, ctx: commands.Context, *, query: str = ""):
    """Quick diagnostic for lyrics connectivity — run as +lyricsdiag [optional query]."""
    await ctx.trigger_typing()
    start = time.time()
    results = {}
    # DNS checks
    for host in ("api.genius.com", "genius.com"):
        try:
            addrs = socket.getaddrinfo(host, 443)
            results[f"dns_{host}"] = f"OK ({len(addrs)} addresses)"
            print(f"[lyricsdiag-pref] DNS {host} -> {addrs[0][4]}")
        except Exception as e:
            results[f"dns_{host}"] = f"ERROR: {repr(e)}"
            print(f"[lyricsdiag-pref] DNS error for {host}: {e}")

    # HEAD checks using aiohttp
    s1, snip1, err1 = await safe_head("https://api.genius.com/", timeout=6)
    results["api_head_status"] = s1
    results["api_head_err"] = err1
    if snip1:
        results["api_head_snippet"] = snip1[:200]

    s2, snip2, err2 = await safe_head("https://genius.com/", timeout=6)
    results["page_head_status"] = s2
    results["page_head_err"] = err2
    if snip2:
        results["page_head_snippet"] = snip2[:200]

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
    summary = []
    summary.append(f"DNS api.genius.com -> {results.get('dns_api.genius.com')}")
    summary.append(f"api.genius.com HEAD -> {results.get('api_head_status')} (err={results.get('api_head_err')})")
    summary.append(f"genius.com HEAD -> {results.get('page_head_status')} (err={results.get('page_head_err')})")
    if "api_search_status" in results:
        summary.append(f"Genius API search -> {results.get('api_search_status')}")
    else:
        summary.append("Genius API search -> skipped (no token)")
    summary_text = "\n".join(summary)
    await ctx.send(f"Diagnostics summary (took {elapsed:.1f}s):\n```\n{summary_text}\n```")
    print("[lyricsdiag-pref] Full details:", json.dumps(results, default=str))

# Prefix lyrics command (immediate)
@commands.command(name="lyrics")
async def lyrics_prefix(self, ctx: commands.Context, *, query: str):
    """Prefix lyrics command — uses async fetch + fallback. Usage: +lyrics Artist - Title"""
    await ctx.trigger_typing()
    start = time.time()
    print(f"[lyricscmd-prefix] invoked by {ctx.author} query={query!r} channel={getattr(ctx.channel,'id',None)}")
    try:
        res = await fetch_lyrics_from_genius_async(query, timeout=8.0, retries=1)
        used = "genius"
        if res is None:
            res = await fetch_lyrics_from_lyrics_ovh(query, timeout=6.0)
            used = "lyrics.ovh" if res else "none"
        elapsed = time.time() - start
        if not res:
            print(f"[lyricscmd-prefix] no lyrics found (elapsed {elapsed:.2f}s)")
            await ctx.send(f"❌ Could not fetch lyrics for **{query}**. (Tried Genius & lyrics.ovh). Check logs.")
            return
        title = res.get("title", "Unknown")
        artist = res.get("artist", "Unknown")
        lyrics = res.get("lyrics", "")
        if not lyrics.strip():
            await ctx.send(f"ℹ️ Found **{title} - {artist}** via {used}, but no lyrics text was scraped.")
            return
        await ctx.send(f"🎶 Lyrics for **{title} - {artist}** (via {used}, {elapsed:.1f}s):")
        for i in range(0, len(lyrics), 1900):
            chunk = lyrics[i:i+1900]
            try:
                await ctx.send(f"```{chunk}```")
                await asyncio.sleep(0.25)
            except Exception as e:
                print("[lyricscmd-prefix] chunk send error:", e)
        print(f"[lyricscmd-prefix] completed send (provider={used}) total_time={time.time()-start:.2f}s")
    except Exception as e:
        print("[lyricscmd-prefix] unexpected handler error:", e)
        traceback.print_exc()
        await ctx.send("⚠️ Unexpected error while fetching lyrics. Check logs.")

# Slash lyrics command (app command)
@app_commands.command(name="lyrics", description="Fetch full lyrics for a song.")
@app_commands.describe(query="Song name or 'Artist - Title'")
async def lyrics_slash(self, interaction: discord.Interaction, query: str):
    await interaction.response.defer(thinking=True)
    start = time.time()
    print(f"[lyricscmd-slash] invoked by {interaction.user} query={query!r} guild={interaction.guild_id}")
    try:
        res = await fetch_lyrics_from_genius_async(query, timeout=8.0, retries=1)
        used = "genius"
        if res is None:
            res = await fetch_lyrics_from_lyrics_ovh(query, timeout=6.0)
            used = "lyrics.ovh" if res else "none"
        elapsed = time.time() - start
        if not res:
            print(f"[lyricscmd-slash] no lyrics found (elapsed {elapsed:.2f}s)")
            await interaction.followup.send(f"❌ Could not fetch lyrics for **{query}**. (Tried Genius & lyrics.ovh). Check logs.")
            return
        title = res.get("title", "Unknown")
        artist = res.get("artist", "Unknown")
        lyrics = res.get("lyrics", "")
        if not lyrics.strip():
            await interaction.followup.send(f"ℹ️ Found **{title} - {artist}** via {used}, but no lyrics text was scraped.")
            return
        await interaction.followup.send(f"🎶 Lyrics for **{title} - {artist}** (via {used}, {elapsed:.1f}s):")
        for i in range(0, len(lyrics), 1900):
            chunk = lyrics[i:i+1900]
            try:
                await interaction.followup.send(f"```{chunk}```")
                await asyncio.sleep(0.25)
            except Exception as e:
                print("[lyricscmd-slash] chunk send error:", e)
        print(f"[lyricscmd-slash] completed send (provider={used}) total_time={time.time()-start:.2f}s")
    except Exception as e:
        print("[lyricscmd-slash] unexpected handler error:", e)
        traceback.print_exc()
        try:
            await interaction.followup.send("⚠️ Unexpected error while fetching lyrics. Check logs.")
        except Exception:
            pass

