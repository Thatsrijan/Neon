# At top of file: add imports
import aiohttp
import asyncio
import re
import os
import time
import json
import traceback
from discord import app_commands

# ---------- async helper: fetch lyrics via Genius API + page scraping ----------
async def fetch_lyrics_from_genius(query: str, timeout: float = 10.0, retries: int = 2):
    """
    Async fetch that:
      1. Calls Genius API search to get song path
      2. Fetches song page HTML and extracts lyrics containers
    Returns dict: {"title":..., "artist":..., "lyrics":...} or None on failure.
    Logs details via print for Render logs.
    """
    token = os.getenv("GENIUS_API_TOKEN")
    if not token:
        print("[lyrics][fetch] No GENIUS_API_TOKEN in env.")
        return None

    search_url = "https://api.genius.com/search"
    headers = {"Authorization": f"Bearer {token}"}
    attempt = 0
    last_exc = None

    while attempt <= retries:
        attempt += 1
        start = time.time()
        try:
            # 1) call Genius search API
            async with aiohttp.ClientSession() as session:
                # timeout for the whole request/response cycle
                try:
                    async with session.get(search_url, params={"q": query}, headers=headers, timeout=timeout) as resp:
                        text = await resp.text()
                        status = resp.status
                except asyncio.TimeoutError:
                    print(f"[lyrics][fetch] search request timed out (attempt {attempt}) for query={query!r}")
                    last_exc = "timeout"
                    await asyncio.sleep(0.5 * attempt)
                    continue

                print(f"[lyrics][fetch] search HTTP {status} (attempt {attempt}) for query={query!r}")
                if status != 200:
                    print(f"[lyrics][fetch] search non-200 status: {status}; body snippet: {text[:200]}")
                    last_exc = f"status_{status}"
                    await asyncio.sleep(0.5 * attempt)
                    continue

                try:
                    j = json.loads(text)
                except Exception as e:
                    print("[lyrics][fetch] Failed to parse JSON from search response:", e)
                    last_exc = e
                    await asyncio.sleep(0.5 * attempt)
                    continue

                # parse first hit
                hits = j.get("response", {}).get("hits", [])
                if not hits:
                    print(f"[lyrics][fetch] No hits for query {query!r}")
                    return None

                # get top result
                top = hits[0].get("result", {})
                song_path = top.get("path")
                title = top.get("title")
                artist = top.get("primary_artist", {}).get("name")
                if not song_path:
                    print("[lyrics][fetch] No path in top result; returning None")
                    return None

                song_url = "https://genius.com" + song_path
                print(f"[lyrics][fetch] Found song URL: {song_url} (title={title!r}, artist={artist!r})")

                # 2) fetch song page HTML
                try:
                    async with session.get(song_url, timeout=timeout) as page_resp:
                        page_status = page_resp.status
                        page_html = await page_resp.text()
                except asyncio.TimeoutError:
                    print(f"[lyrics][fetch] song page request timed out for {song_url}")
                    last_exc = "timeout_page"
                    await asyncio.sleep(0.5 * attempt)
                    continue

                if page_status != 200:
                    print(f"[lyrics][fetch] song page non-200 status {page_status} for {song_url}")
                    last_exc = f"page_status_{page_status}"
                    await asyncio.sleep(0.5 * attempt)
                    continue

                # Try extracting lyrics:
                # Newer Genius layout: multiple <div data-lyrics-container="true">...</div>
                lyrics_parts = re.findall(r'<div[^>]+data-lyrics-container="true"[^>]*>(.*?)</div>', page_html, flags=re.DOTALL | re.IGNORECASE)
                if not lyrics_parts:
                    # Fallback to old .lyrics container
                    m = re.search(r'<div class="lyrics">(.+?)</div>', page_html, flags=re.DOTALL | re.IGNORECASE)
                    if m:
                        lyrics_parts = [m.group(1)]

                if not lyrics_parts:
                    # As a last resort, try to extract from <div class="SongPage__lyrics"> blocks
                    lyrics_parts = re.findall(r'<div[^>]+class="SongPage__lyrics"[^>]*>(.*?)</div>', page_html, flags=re.DOTALL | re.IGNORECASE)

                if not lyrics_parts:
                    print("[lyrics][fetch] Could not find lyrics containers in page HTML. Returning snippet for debugging.")
                    snippet = page_html[:800]
                    print("[lyrics][fetch] Page snippet:", snippet)
                    return {"title": title or "Unknown", "artist": artist or "Unknown", "lyrics": ""}

                # Clean HTML tags from parts
                clean_parts = []
                for part in lyrics_parts:
                    # remove <br> and variants with newline placeholder
                    part = re.sub(r'<br\s*/?>', '\n', part, flags=re.IGNORECASE)
                    # remove remaining tags
                    part = re.sub(r'<.*?>', '', part, flags=re.DOTALL)
                    clean = part.strip()
                    if clean:
                        clean_parts.append(clean)

                lyrics_text = "\n\n".join(clean_parts).strip()
                elapsed = time.time() - start
                print(f"[lyrics][fetch] Successfully scraped lyrics (len={len(lyrics_text)} chars) in {elapsed:.2f}s")
                return {"title": title or "Unknown", "artist": artist or "Unknown", "lyrics": lyrics_text}

        except Exception as ex:
            print(f"[lyrics][fetch] Unexpected exception (attempt {attempt}) for query={query!r}: {ex}")
            traceback.print_exc()
            last_exc = ex
            await asyncio.sleep(0.5 * attempt)

    print(f"[lyrics][fetch] Failed all attempts for query={query!r}; last_exc={last_exc}")
    return None


# ---------- Replace your slash command handler with this safe handler ----------
@app_commands.command(name="lyrics", description="Fetch full lyrics for a song.")
@app_commands.describe(query="Song name or 'Artist - Title'")
async def lyrics_cmd(self, interaction: discord.Interaction, query: str):
    # Show the thinking UI so Discord doesn't mark the command as unresponded
    await interaction.response.defer(thinking=True)
    start_time = time.time()
    print(f"[lyrics] Handler started for query={query!r} by user={interaction.user} in {interaction.guild_id=}")

    try:
        result = await fetch_lyrics_from_genius(query, timeout=10.0, retries=2)
        elapsed = time.time() - start_time
        if not result:
            print(f"[lyrics] No result for {query!r} after {elapsed:.2f}s")
            await interaction.followup.send("❌ Could not fetch lyrics (timeout, network, or not found). Check logs.")
            return

        title = result.get("title", "Unknown")
        artist = result.get("artist", "Unknown")
        lyrics = result.get("lyrics", "")

        if not lyrics:
            await interaction.followup.send(f"ℹ️ Found **{title} - {artist}**, but no lyrics were scraped.")
            return

        # send header then chunk lyrics
        await interaction.followup.send(f"🎶 Lyrics for **{title} - {artist}** (fetched in {elapsed:.1f}s):")
        for i in range(0, len(lyrics), 1900):
            chunk = lyrics[i:i+1900]
            try:
                await interaction.followup.send(f"```{chunk}```")
                # small pause to avoid rate-limit bursts
                await asyncio.sleep(0.25)
            except Exception as e:
                print("[lyrics] Error sending chunk:", e)
                # continue trying next chunk
        print(f"[lyrics] Completed sending lyrics for {query!r} (total time {time.time()-start_time:.2f}s)")
    except Exception as e:
        print("[lyrics] Unexpected error in handler:", e)
        traceback.print_exc()
        try:
            await interaction.followup.send("⚠️ Unexpected error while fetching lyrics. Check bot logs.")
        except Exception:
            pass
