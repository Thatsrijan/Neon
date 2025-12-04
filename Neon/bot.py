# bot.py — Karaoke bot with DM support, dynamic RP, mention replies, and telemetry
import os
import asyncio
import itertools
import json
import time
import random
from pathlib import Path
from datetime import timedelta

from dotenv import load_dotenv
load_dotenv()

# start keepalive webserver early (your keepalive.py should start a small server)
from keepalive import start as start_keepalive
start_keepalive()

import discord
from discord.ext import commands

# Optional telemetry library
try:
    import psutil
except Exception:
    psutil = None

# -------------------------
# Intents & bot creation
# -------------------------
intents = discord.Intents.default()
intents.guilds = True
intents.reactions = True
intents.message_content = True  # required for prefix commands & reading message text
intents.dm_messages = True
intents.dm_reactions = True

bot = commands.Bot(command_prefix="+", intents=intents, help_command=None)

# cogs you load; keep as-is or modify
COGS = ["cogs.karaoke", "cogs.settings"]

# -------------------------
# Startup / sync guard
# -------------------------
SYNC_STATE = Path("sync_state.json")

async def maybe_sync_commands(bot, max_age_hours=24):
    try:
        if SYNC_STATE.exists():
            data = json.loads(SYNC_STATE.read_text())
            last = data.get("last_sync", 0)
            if time.time() - last < max_age_hours * 3600:
                print("Skipping global sync (recent).")
                return
        await bot.tree.sync()
        SYNC_STATE.write_text(json.dumps({"last_sync": time.time()}))
        print("Commands synced & timestamped.")
    except Exception as e:
        print("Sync error:", e)

# -------------------------
# Safe helpers
# -------------------------
async def safe_send_dm(user: discord.abc.Snowflake, content: str):
    try:
        await user.send(content)
        return True
    except discord.Forbidden:
        return False
    except Exception as e:
        print("Error sending DM:", e)
        return False

# -------------------------
# Telemetry & status system
# -------------------------
# RP timing and custom lines
MIN_ROTATE = 15
MAX_ROTATE = 25
STATUS_ROTATE_SECONDS = None  # unused; we use randomized sleep between MIN_ROTATE and MAX_ROTATE

# Playful custom RP lines
CUSTOM_RP_LINES = [
    "Playing with The Kidd's Heart",
    "Listening To The Kidd's Order",
    "Wanna Marry The Kidd",
    "Stealing The Kidd's Spotlight",
    "Dancing with The Kidd's Vibes",
    "Kidd's Personal DJ 🎧",
    "Whispering The Kidd's Secrets"
]

# Static helpful messages
STATUS_MESSAGES_STATIC = [
    "🎤 Karaoke ready — use +sing or /karaoke",
    "+lyrics | +sing",
    "📩 Active in DMs",
]

# record start time for uptime
bot._start_time = time.time()

def format_bytes_to_mb(n_bytes: int) -> float:
    return round(n_bytes / (1024 * 1024), 2)

async def sample_system_stats():
    stats = {
        "cpu_percent": None,
        "mem_percent": None,
        "proc_rss_mb": None,
        "proc_mem_percent": None,
    }
    if psutil is None:
        return stats
    try:
        # run blocking calls in thread to avoid blocking event loop
        cpu = await asyncio.to_thread(psutil.cpu_percent, 0.1)
        vm = await asyncio.to_thread(psutil.virtual_memory)
        proc = await asyncio.to_thread(psutil.Process)
        proc_mem = await asyncio.to_thread(lambda p: p.memory_info(), proc)
        proc_percent = await asyncio.to_thread(lambda p: p.memory_percent(), proc)

        stats["cpu_percent"] = round(cpu, 1)
        stats["mem_percent"] = round(vm.percent, 1)
        stats["proc_rss_mb"] = format_bytes_to_mb(proc_mem.rss)
        stats["proc_mem_percent"] = round(proc_percent, 1)
    except Exception as e:
        print("psutil sampling error:", e)
    return stats

def get_active_karaoke_sessions():
    """Detect active karaoke sessions by inspecting loaded cogs defensively."""
    try:
        total = 0
        for cog in bot.cogs.values():
            for attr in ("karaoke_state", "sessions", "active_sessions"):
                if hasattr(cog, attr):
                    container = getattr(cog, attr)
                    try:
                        total += len(container)
                    except Exception:
                        total += 1 if container else 0
        return total
    except Exception as e:
        print("Error detecting karaoke sessions:", e)
        return 0

def format_uptime(start_time: float) -> str:
    delta = timedelta(seconds=int(time.time() - start_time))
    days = delta.days
    hrs, rem = divmod(delta.seconds, 3600)
    mins, secs = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hrs}h"
    if hrs > 0:
        return f"{hrs}h {mins}m"
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"

async def build_status_messages():
    stats = await sample_system_stats()
    latency_ms = round(bot.latency * 1000) if bot.latency is not None else None
    uptime = format_uptime(bot._start_time)
    active_sessions = get_active_karaoke_sessions()

    msgs = []
    if latency_ms is not None:
        msgs.append(f"↯ Latency {latency_ms}ms | Uptime {uptime}")
    else:
        msgs.append(f"Uptime {uptime}")

    if stats.get("cpu_percent") is not None and stats.get("mem_percent") is not None:
        msgs.append(f"CPU {stats['cpu_percent']}% · RAM {stats['mem_percent']}%")
    elif stats.get("cpu_percent") is not None:
        msgs.append(f"CPU {stats['cpu_percent']}%")

    if stats.get("proc_rss_mb") is not None and stats.get("proc_mem_percent") is not None:
        msgs.append(f"Bot mem {stats['proc_rss_mb']}MB ({stats['proc_mem_percent']}%)")

    msgs.append(f"Karaoke sessions: {active_sessions}")

    # shuffle custom lines and pick up to 3 to sprinkle in
    shuffled_custom = CUSTOM_RP_LINES.copy()
    random.shuffle(shuffled_custom)
    msgs.extend(shuffled_custom[:3])

    msgs.extend(STATUS_MESSAGES_STATIC)

    # dedupe and trim
    deduped = []
    for m in msgs:
        if len(m) > 120:
            m = m[:117] + "..."
        if m not in deduped:
            deduped.append(m)
    return deduped

async def status_task():
    await bot.wait_until_ready()
    msgs = await build_status_messages()
    cycle = itertools.cycle(msgs)
    last_build = time.time()
    while not bot.is_closed():
        try:
            if time.time() - last_build > max(60, (MIN_ROTATE + MAX_ROTATE) * 3):
                msgs = await build_status_messages()
                cycle = itertools.cycle(msgs)
                last_build = time.time()

            status = next(cycle)
            activity = discord.Game(name=status)
            await bot.change_presence(status=discord.Status.online, activity=activity)
        except Exception as e:
            print("Error updating status:", e)

        sleep_time = random.randint(MIN_ROTATE, MAX_ROTATE)
        await asyncio.sleep(sleep_time)

# -------------------------
# Slash commands (public)
# -------------------------
@bot.tree.command(name="ping", description="Bot latency test")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000) if bot.latency is not None else "N/A"
    await interaction.response.send_message(f"Pong! `{latency}ms`")

@bot.tree.command(name="dmme", description="Ask the bot to DM you")
async def dmme(interaction: discord.Interaction):
    try:
        ok = await safe_send_dm(interaction.user, "👋 Hi! I’m now in your DMs. Use /help for commands.")
        if ok:
            await interaction.response.send_message("DM sent! 💌")
        else:
            await interaction.response.send_message("❌ Couldn't send DM — your privacy settings may block me.")
    except Exception as e:
        print("Error in dmme:", e)
        await interaction.response.send_message("❌ Error sending DM.")

# -------------------------
# Mention & message handlers
# -------------------------
# Loving messages pool (randomized for mentions/plain-name)
LOVING_MESSAGES = [
    "💖 Busy Loving The Kidd 💖",
    "💘 Busy Loving The Kidd 💘",
    "😍 Busy Loving The Kidd 😍",
    "💕 Busy Loving The Kidd — be right back 💕",
    "💝 Loving The Kidd, brb with more love 💝",
    "✨ Busy Loving The Kidd ✨",
]

DM_AUTOREPLY_MESSAGES = [
    "Hey! Thanks for DMing Neon — I’m here to help. Try `/ping` or `/lyrics`.",
    "Hello from Neon! I’ll reply if you use +sing or /karaoke in a server, or you can ask me here.",
    "Neon says hi! ❤️ Use /dmme in a server to add me to your DMs too."
]

def make_mention_embed(message_text: str):
    em = discord.Embed(title=message_text, description="Always here for The Kidd 💖", color=0xFF69B4)
    em.set_footer(text="• Neon — Karaoke Bot", icon_url=bot.user.display_avatar.url if bot.user else None)
    return em

@bot.event
async def on_message(message: discord.Message):
    # ignore other bots
    if message.author.bot:
        return

    # Normalize content for plain-name checks
    try:
        content_lower = message.content.lower()
    except Exception:
        content_lower = ""

    # 1) If message is a DM to the bot -> send auto-reply (cute onboarding)
    if message.guild is None:
        # Only send auto-reply for the first message in the DM (a simple heuristic:
        # reply if we haven't messaged the user in this DM recently).
        # To avoid spam, we won't blindly reply to every DM; use a small in-memory sentinel.
        # (This sentinel only lasts for the process lifetime.)
        if not hasattr(bot, "_dm_replied_cache"):
            bot._dm_replied_cache = set()

        cache_key = f"{message.author.id}"
        if cache_key not in bot._dm_replied_cache:
            bot._dm_replied_cache.add(cache_key)
            reply = random.choice(DM_AUTOREPLY_MESSAGES)
            try:
                await message.channel.send(reply)
            except Exception as e:
                print("Error sending DM autoreply:", e)

    # 2) If user mentions the bot directly (via @Neon), reply with embed
    if bot.user in message.mentions:
        # prepare randomized loving message
        text = random.choice(LOVING_MESSAGES)
        embed = make_mention_embed(text)
        try:
            await message.channel.send(embed=embed)
        except Exception as e:
            print("Error sending mention embed:", e)
            # fallback to plain text
            try:
                await message.channel.send(text)
            except Exception:
                pass

    # 3) If user types the bot's name (without mention), e.g., "neon" or "Neon"
    # Avoid triggering if they already mentioned the bot (handled above)
    elif bot.user and bot.user.mention not in message.content:
        # check for name as a separate token to reduce false positives
        bot_name = bot.user.name.lower() if bot.user else "neon"
        if bot_name in content_lower:
            # Randomized reply (plain text short)
            text = random.choice(LOVING_MESSAGES)
            try:
                # send only once per message
                await message.channel.send(text)
            except Exception as e:
                print("Error sending name-reply:", e)

    # Let commands (prefix commands) be processed
    await bot.process_commands(message)

# -------------------------
# Startup / cog loading
# -------------------------
async def main():
    # load cogs
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            print(f"Loaded cog: {cog}")
        except Exception as e:
            print(f"Failed to load cog {cog}: {e}")

    # start background status task
    try:
        bot.loop.create_task(status_task())
        print("Started dynamic status task.")
    except Exception as e:
        print("Failed to start status task:", e)

    # sync app commands (conditionally)
    try:
        await maybe_sync_commands(bot)
    except Exception as e:
        print("maybe_sync_commands error (continuing):", e)

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN environment variable not set.")
    await bot.start(token)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down...")
