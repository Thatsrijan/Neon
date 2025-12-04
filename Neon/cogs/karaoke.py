import asyncio
import os
from typing import Dict, Any, Optional
from pathlib import Path

import discord
from discord.ext import commands
from discord import app_commands
import lyricsgenius

# Short helper for settings manager import
from cogs.settings import SettingsManager, SETTINGS_FILE, DATA_DIR

# load or create settings manager
settings = SettingsManager(SETTINGS_FILE)

# Genius client (sync calls are run in executor)
GENIUS_TOKEN = os.getenv("GENIUS_API_TOKEN")
if not GENIUS_TOKEN:
    raise RuntimeError("GENIUS_API_TOKEN environment variable is not set.")

genius = lyricsgenius.Genius(GENIUS_TOKEN)
genius.skip_non_songs = True
genius.excluded_terms = ["(Remix)", "(Live)"]
genius.remove_section_headers = False

# In-memory karaoke state
# guild_id -> { "running": bool, "paused": bool, "task": asyncio.Task, "control_message_id": int, "lock": asyncio.Lock }
karaoke_state: Dict[int, Dict[str, Any]] = {}


async def fetch_song(query: str):
    # Run blocking search in thread
    return await asyncio.to_thread(genius.search_song, query)


async def run_karaoke(channel: discord.abc.Messageable, guild_id: int, title_display: str, lyrics: str, delay: float):
    lines = lyrics.split("\n")
    try:
        await channel.send(f"🎤 **Karaoke started:** {title_display}")

        for line in lines:
            state = karaoke_state.get(guild_id)
            if not state or not state.get("running"):
                await channel.send("⏹ Karaoke stopped.")
                return

            # pause loop
            while state.get("paused"):
                await asyncio.sleep(0.8)
                state = karaoke_state.get(guild_id)
                if not state or not state.get("running"):
                    await channel.send("⏹ Karaoke stopped.")
                    return

            if line.strip():
                await channel.send(line)
                await asyncio.sleep(delay)

        await channel.send("✅ Karaoke finished!")
    except asyncio.CancelledError:
        await channel.send("⏹ Karaoke cancelled.")
    except Exception as e:
        await channel.send(f"⚠️ Error during karaoke: {e}")
    finally:
        # cleanup
        karaoke_state.pop(guild_id, None)


class KaraokeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Small utility to create control message and add reactions
    async def _make_control_message(self, channel: discord.TextChannel):
        msg = await channel.send(
            "🎛️ Karaoke controls:\n▶ – resume/play\n⏸ – pause\n⏹ – stop"
        )
        for emoji in ("⏸", "▶", "⏹"):
            try:
                await msg.add_reaction(emoji)
            except Exception:
                pass
        return msg

    @app_commands.command(name="ping", description="Check if the bot is alive.")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message("🏓 Pong! I'm alive.", ephemeral=True)

    @app_commands.command(name="lyrics", description="Fetch full lyrics for a song.")
    @app_commands.describe(query="Song name or 'Artist - Title'")
    async def lyrics_cmd(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(thinking=True)
        song = await fetch_song(query)
        if not song:
            await interaction.followup.send("❌ Song not found or Genius API error.")
            return

        title_display = f"{song.title} - {song.artist}"
        lyrics = song.lyrics or "No lyrics found."
        chunks = [lyrics[i : i + 1900] for i in range(0, len(lyrics), 1900)]
        await interaction.followup.send(f"🎶 Lyrics for **{title_display}**:")
        for chunk in chunks:
            await interaction.followup.send(f"```{chunk}```")

    @app_commands.command(name="karaoke", description="Sing a song line by line (karaoke mode).")
    @app_commands.describe(query="Song name or 'Artist - Title'", delay="Delay in seconds between lines (overrides guild default)")
    async def karaoke(self, interaction: discord.Interaction, query: str, delay: Optional[float] = None):
        if not interaction.guild:
            await interaction.response.send_message("This command only works in servers.", ephemeral=True)
            return

        guild_id = interaction.guild_id
        # Use guild default if not provided
        if delay is None:
            delay = await settings.get_delay(guild_id, default=2.0)
        else:
            # bound checking
            if delay < 0.1 or delay > 10:
                await interaction.response.send_message("Delay must be between 0.1 and 10 seconds.", ephemeral=True)
                return

        # If karaoke already running, stop previous
        existing = karaoke_state.get(guild_id)
        if existing and existing.get("running"):
            existing["running"] = False
            t = existing.get("task")
            if t and not t.done():
                t.cancel()

        await interaction.response.defer(thinking=True)
        song = await fetch_song(query)
        if not song:
            await interaction.followup.send("❌ Song not found or Genius API error.")
            return

        title_display = f"{song.title} - {song.artist}"
        lyrics = song.lyrics or "No lyrics found."

        # Create control message and add reactions
        control_msg = await self._make_control_message(interaction.channel)

        # create task
        task = asyncio.create_task(run_karaoke(interaction.channel, guild_id, title_display, lyrics, delay))

        karaoke_state[guild_id] = {
            "running": True,
            "paused": False,
            "task": task,
            "control_message_id": control_msg.id,
            "lock": asyncio.Lock(),
        }

        await interaction.followup.send(f"🎤 Karaoke for **{title_display}** started (delay={delay}s). Use the control message reactions or slash commands to control.", ephemeral=False)

    # Slash commands to control (alternative to reactions)
    @app_commands.command(name="pausekaraoke", description="Pause the karaoke in this server.")
    async def pausekaraoke(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return

        state = karaoke_state.get(interaction.guild_id)
        if not state or not state.get("running"):
            await interaction.response.send_message("ℹ️ No karaoke is running right now.", ephemeral=True)
            return

        if state.get("paused"):
            await interaction.response.send_message("ℹ️ Karaoke is already paused.", ephemeral=True)
            return

        state["paused"] = True
        await interaction.response.send_message("⏸ Karaoke paused.", ephemeral=False)

    @app_commands.command(name="resumekaraoke", description="Resume the karaoke in this server.")
    async def resumekaraoke(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return

        state = karaoke_state.get(interaction.guild_id)
        if not state or not state.get("running"):
            await interaction.response.send_message("ℹ️ No karaoke is running right now.", ephemeral=True)
            return

        if not state.get("paused"):
            await interaction.response.send_message("ℹ️ Karaoke is not paused.", ephemeral=True)
            return

        state["paused"] = False
        await interaction.response.send_message("▶ Karaoke resumed.", ephemeral=False)

    @app_commands.command(name="stopkaraoke", description="Stop the karaoke in this server.")
    async def stopkaraoke(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return

        state = karaoke_state.get(interaction.guild_id)
        if not state or not state.get("running"):
            await interaction.response.send_message("ℹ️ No karaoke is running right now.", ephemeral=True)
            return

        state["running"] = False
        t = state.get("task")
        if t and not t.done():
            t.cancel()
        await interaction.response.send_message("🛑 Karaoke stopped.", ephemeral=False)

    # Reaction handling - global listener in this cog's on_reaction_add
    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if user.bot:
            return

        message = reaction.message
        guild = message.guild
        if not guild:
            return

        state = karaoke_state.get(guild.id)
        if not state:
            return

        if message.id != state.get("control_message_id"):
            return

        emoji = str(reaction.emoji)
        # remove user reaction for tidiness
        try:
            await message.remove_reaction(reaction.emoji, user)
        except Exception:
            pass

        if emoji == "⏸":
            if not state.get("paused"):
                state["paused"] = True
                await message.channel.send("⏸ Karaoke paused.")
        elif emoji == "▶":
            if state.get("paused"):
                state["paused"] = False
                await message.channel.send("▶ Karaoke resumed.")
        elif emoji == "⏹":
            state["running"] = False
            t = state.get("task")
            if t and not t.done():
                t.cancel()
            await message.channel.send("🛑 Karaoke stopped.")


async def setup(bot):
    await bot.add_cog(KaraokeCog(bot))
