import os
import asyncio
from pathlib import Path

from dotenv import load_dotenv
load_dotenv() 

from keepalive import start as start_keepalive
start_keepalive()

import discord
from discord.ext import commands

# Bot-wide intents
intents = discord.Intents.default()
intents.guilds = True
intents.reactions = True

# Use slash commands only; prefix only for fallback.
bot = commands.Bot(command_prefix="+", intents=intents, help_command=None)

COGS = ["cogs.karaoke", "cogs.settings"]

@bot.event
async def on_ready():
    # Sync slash commands
    try:
        await bot.tree.sync()
        print("✅ Slash commands synced.")
    except Exception as e:
        print("⚠️ Error syncing commands:", e)
    print(f"✅ Bot ready — Logged in as {bot.user} (ID {bot.user.id})")

async def main():
    # load cogs
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            print(f"Loaded cog: {cog}")
        except Exception as e:
            print(f"Failed to load cog {cog}: {e}")

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN environment variable not set.")
    await bot.start(token)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down...")
