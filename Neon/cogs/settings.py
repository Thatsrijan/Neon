import json
from pathlib import Path
import asyncio

from discord import app_commands
from discord.ext import commands

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = DATA_DIR / "settings.json"

class SettingsManager:
    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()
        if not self.path.exists():
            self._write_sync({})

    def _write_sync(self, data: dict):
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    async def read_all(self) -> dict:
        async with self.lock:
            if not self.path.exists():
                return {}
            # sync read is fine here
            with self.path.open("r", encoding="utf-8") as f:
                return json.load(f)

    async def write_all(self, data: dict):
        async with self.lock:
            self._write_sync(data)

    async def get_guild(self, guild_id: int) -> dict:
        data = await self.read_all()
        return data.get(str(guild_id), {})

    async def set_guild(self, guild_id: int, obj: dict):
        data = await self.read_all()
        data[str(guild_id)] = obj
        await self.write_all(data)

    async def set_delay(self, guild_id: int, delay: float):
        obj = await self.get_guild(guild_id)
        obj["default_delay"] = float(delay)
        await self.set_guild(guild_id, obj)

    async def get_delay(self, guild_id: int, default: float = 2.0) -> float:
        obj = await self.get_guild(guild_id)
        return float(obj.get("default_delay", default))


class SettingsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.settings = SettingsManager(SETTINGS_FILE)

    @app_commands.command(name="setdelay", description="Set default karaoke delay (seconds) for this guild.")
    @app_commands.describe(delay="Seconds between lines (e.g., 1.5). Range: 0.1 - 10")
    @app_commands.default_permissions(manage_guild=True)
    async def setdelay(self, interaction, delay: float):
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server (guild).", ephemeral=True)
            return

        if delay < 0.1 or delay > 10:
            await interaction.response.send_message("Delay must be between 0.1 and 10 seconds.", ephemeral=True)
            return

        await self.settings.set_delay(interaction.guild_id, delay)
        await interaction.response.send_message(f"✅ Default karaoke delay set to {delay} seconds for this server.", ephemeral=True)

    @app_commands.command(name="getdelay", description="Get the guild's default karaoke delay.")
    async def getdelay(self, interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server (guild).", ephemeral=True)
            return

        delay = await self.settings.get_delay(interaction.guild_id)
        await interaction.response.send_message(f"Current default karaoke delay for this server: {delay} seconds.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(SettingsCog(bot))
