import os
from jobspy import scrape_jobs
import discord
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("TEST_CHANNEL_ID"))

intents = discord.Intents.default()
bot = discord.Client(intents=intents)


@tasks.loop(minutes=1)
async def post_jobs():
    jobs = scrape_jobs(
        site_name=["linkedin"],
        search_term="software engineer intern",
        location="United States",
        results_wanted=20,
        hours_old=1
    )
    channel = await bot.fetch_channel(CHANNEL_ID)

    blacklist_companies = {
        "Team Remotely Inc",
        "HireMeFast LLC"
    }
    required_terms = {"software", "engineer", "intern"}

    for _, row in jobs.iterrows():
        if row["company"] in blacklist_companies:
            continue
        title_l = row["title"].lower()
        if not any(term in title_l for term in required_terms):
            continue

        message = (
            f"**Company:** {row['company']}\n"
            f"**Role:** {row['title']}\n"
            f"**Link:** {row['job_url']}\n"
            f"**Location:** {row['location']}"
        )
        await channel.send(message)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    post_jobs.start()


bot.run(TOKEN)
