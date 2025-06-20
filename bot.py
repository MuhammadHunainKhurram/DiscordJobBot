import os, discord
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TOKEN")

intents = discord.Intents.default()
bot = discord.Client(intents=intents)

@bot.event
async def on_ready():
    channel = await bot.fetch_channel(int(os.getenv("TEST_CHANNEL_ID")))
    await channel.send("👋 Bot online!")
    await bot.close()

bot.run(TOKEN)
