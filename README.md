# Discord Karaoke Bot (cog-based)

## Setup
1. Create a Discord bot in Developer Portal.
   - Enable **Applications Commands** (for slash commands).
   - Invite bot to your server with scopes: `bot` and `applications.commands`.
   - Required bot permissions: Send Messages, Add Reactions, Read Messages/View Channels, Use Slash Commands.

2. Regenerate your tokens if previously leaked. Never commit tokens to source control.

3. Set environment variables:
   - DISCORD_TOKEN — your Discord bot token
   - GENIUS_API_TOKEN — your Genius API token

Linux/macOS:
export DISCORD_TOKEN="..."
export GENIUS_API_TOKEN="..."

Windows (PowerShell):
setx DISCORD_TOKEN "..."
setx GENIUS_API_TOKEN "..."

4. Install dependencies:
pip install -r requirements.txt

5. Run:
python bot.py

## Commands
- /ping — bot health check
- /lyrics query:<song> — fetch full lyrics
- /karaoke query:<song> [delay:<seconds>] — start karaoke (uses guild default if delay omitted)
- /pausekaraoke, /resumekaraoke, /stopkaraoke — server-wide control via slash commands
- /setdelay delay:<seconds> — (manage_guild) set default delay for the guild
- /getdelay — see guild default delay

Control message reactions (per karaoke run):
⏸ pause, ▶ resume, ⏹ stop
