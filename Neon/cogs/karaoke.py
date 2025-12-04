# --- defensive lyrics fetch + robust /lyrics command ---
import asyncio
import time
import traceback

# Ensure genus client exists (if you create it elsewhere, skip this)
try:
    import lyricsgenius
except Exception:
    lyricsgenius = None

# If you create Genius client at cog init, prefer that. Example fallback:
def make_genius_client(token: str):
    if not token or lyricsgenius is None:
        return None
    try:
        g = lyricsgenius.Genius(token,
                                skip_non_songs=True,
                                excluded_terms=["(Remix)", "(Live)"],
                                timeout=10)  # optional param
        # tune attributes to be safe
        g.verbose = False
        g.remove_section_headers = True
        return g
    except Exception as e:
        print("Failed to create lyricsgenius client:", e)
        return None

# If cog has no self.genius, create one on first use
async def _ensure_genius(self):
    if getattr(self, "genius", None) is None:
        token = os.getenv("GENIUS_API_TOKEN")
        self.genius = make_genius_client(token)
    return getattr(self, "genius", None)

async def _fetch_song_with_timeout(self, query: str, timeout: float = 15.0, retries: int = 2):
    """
    Run genius.search_song in a thread with asyncio.wait_for and retries.
    Returns a song object or None on failure.
    Logs detailed info for debug.
    """
    genius = await _ensure_genius(self)
    if genius is None:
        print("[lyrics] Genius client not available (missing lyricsgenius or token).")
        return None

    attempt = 0
    last_exc = None
    while attempt <= retries:
        attempt += 1
        start_t = time.time()
        try:
            # Run blocking search in a thread
            coro = asyncio.to_thread(genius.search_song, query)
            song = await asyncio.wait_for(coro, timeout=timeout)
            elapsed = time.time() - start_t
            print(f"[lyrics] Genius.search_song success (attempt {attempt}) query={query!r} elapsed={elapsed:.2f}s")
            return song
        except asyncio.TimeoutError:
            print(f"[lyrics] Timeout (attempt {attempt}) for query={query!r} after {timeout}s")
            last_exc = asyncio.TimeoutError()
        except Exception as e:
            # log full traceback
            print(f"[lyrics] Exception (attempt {attempt}) for query={query!r}: {e}")
            traceback.print_exc()
            last_exc = e

        # small backoff before retry
        await asyncio.sleep(0.5 * attempt)

    print(f"[lyrics] All attempts failed for query={query!r}; last_exc={last_exc}")
    return None


# Slash command replacement
from discord import app_commands

@app_commands.command(name="lyrics", description="Fetch full lyrics for a song (safe timeout + retry).")
@app_commands.describe(query="Song name or 'Artist - Title'")
async def lyrics_cmd(self, interaction: discord.Interaction, query: str):
    # show "thinking" UI to prevent "unresponded" UI
    await interaction.response.defer(thinking=True)

    try:
        start = time.time()
        song = await _fetch_song_with_timeout(self, query, timeout=15.0, retries=2)
        elapsed = time.time() - start
        if not song:
            # clearly communicate cause & give diagnostics instructions
            await interaction.followup.send(
                "❌ Could not fetch lyrics (timeout/remote error). "
                "This may be a network/egress issue from the host or Genius rate-limiting.\n\n"
                "If this keeps happening, please run the `render shell` checks and paste logs.\n"
                f"(attempted for {elapsed:.1f}s)"
            )
            return

        # Got a song — prepare and send in chunks
        lyrics = getattr(song, "lyrics", None) or ""
        title_display = f"{getattr(song,'title','Unknown')} - {getattr(song,'artist','Unknown')}"
        if not lyrics.strip():
            await interaction.followup.send(f"ℹ️ Found song **{title_display}**, but no lyrics were returned.")
            return

        # Split into 1900-char chunks and send
        await interaction.followup.send(f"🎶 Lyrics for **{title_display}** (fetched in {elapsed:.1f}s):")
        for i in range(0, len(lyrics), 1900):
            chunk = lyrics[i:i+1900]
            # send code block for readability; smaller send intervals to dodge rate limits
            try:
                await interaction.followup.send(f"```{chunk}```")
                await asyncio.sleep(0.25)
            except Exception as e:
                print("[lyrics] Error sending chunk:", e)
                # continue trying next chunk
        return

    except Exception as e:
        print("[lyrics] Unexpected handler error:", e)
        traceback.print_exc()
        try:
            await interaction.followup.send("⚠️ Unexpected error while fetching lyrics. Check bot logs.")
        except Exception:
            pass
