import discord
from discord.ext import commands
import asyncio
import atexit
import os
import uuid
import json
import sys
import tempfile
import msvcrt
import redis.asyncio as redis  # WICHTIG: Die asynchrone Version!

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

LOCK_PATH = os.path.join(tempfile.gettempdir(), "ai_bot_my_bot.lock")
_lock_handle = open(LOCK_PATH, "a+")

try:
    msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
except OSError:
    print("Bot läuft bereits in einer anderen Instanz.")
    sys.exit(0)


def _release_lock():
    try:
        _lock_handle.seek(0)
        msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        _lock_handle.close()


atexit.register(_release_lock)

# Verbinde dich mit Redis (Passe Host/Passwort an, falls nötig)
redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True, protocol=2)  # protocol=2 für Kompatibilität mit älteren Redis-Versionen

@bot.event
async def on_ready():
    print(f'{bot.user} has logged in!')

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.content.startswith(bot.command_prefix):
        await bot.process_commands(message)
        return

    is_dm = message.guild is None
    is_mentioned = bot.user.mentioned_in(message)

    if is_mentioned or is_dm:
        prompt = message.clean_content.replace(f'@{bot.user.name}', '').strip()
        
        if not prompt:
            await message.channel.send("Wie kann ich dir helfen?")
            return

        request_id = str(uuid.uuid4())
        response_key = f"llm:response:{request_id}"

        try:
            async with message.channel.typing():
                # HIER NUR NOCH DIE NACKTE DM + CHANNEL_ID REINPACKEN
                job = json.dumps({
                    "request_id": request_id, 
                    "channel_id": message.channel.id,  # <-- Wichtig für den Loader!
                    "prompt": prompt                   # <-- Nur das aktuelle Wort des Users
                })
                await redis_client.rpush("llm:requests", job)

                full_response = ""
                while True:
                    result = await redis_client.blpop(response_key, timeout=60)
                    if not result:
                        full_response = "❌ Das Modell hat zu lange gebraucht (Timeout)."
                        break
                    
                    _, raw_data = result
                    data = json.loads(raw_data)
                    
                    if data.get("error"):
                        full_response = f"❌ Fehler vom Modell: {data['error']}"
                        break
                    if data.get("done"):
                        break
                    if "token" in data:
                        full_response += data["token"]

                if not full_response.strip():
                    full_response = "Das Modell hat eine leere Antwort zurückgegeben."

                if len(full_response) > 2000:
                    full_response = full_response[:1996] + "..."

                await message.channel.send(full_response)

        except Exception as e:
            print(f"[FEHLER] Unerwarteter Fehler: {e}")
            await message.channel.send("Ups, bei der Kommunikation mit dem Gehirn ist etwas schiefgelaufen.")

    await bot.process_commands(message)
# Starte den Bot (HIER DEINEN TOKEN EINTRAGEN)
@bot.command(name="reset")
async def reset_history(ctx):
    history_key = f"llm:history:{ctx.channel.id}"
    # Löscht den gesamten Verlaufs-Key aus Redis
    await redis_client.delete(history_key)
    await ctx.send("🧹 *Gedächtnis für diesen Kanal wurde gelöscht!*")


bot.run('MTUxNzYyODE2NDg4NzgxMDE1MA.GmHGG-.Bygqdjs2XEHLzXo8izzpUubqJ561c-ojb6Kzhg')
