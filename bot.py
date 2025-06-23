import logging
import os
import platform
import asyncio

from jobspy import scrape_jobs
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker

intents = discord.Intents.default()

Base = declarative_base()

# ── Database models ────────────────────────────────────────────
class FullTimeJob(Base):
    __tablename__ = "full_time_jobs"
    id = Column(Integer, primary_key=True)
    job_id = Column(String, unique=True)
    application_url = Column(String)
    job_title = Column(String)
    company_name = Column(String)
    company_url = Column(String)
    location = Column(String)

class InternJob(Base):
    __tablename__ = "intern_jobs"
    id = Column(Integer, primary_key=True)
    job_id = Column(String, unique=True)
    application_url = Column(String)
    job_title = Column(String)
    company_name = Column(String)
    company_url = Column(String)
    location = Column(String)

class NG2025Job(Base):
    __tablename__ = "ng_2025_jobs"
    id = Column(Integer, primary_key=True)
    job_id = Column(String, unique=True)
    application_url = Column(String)
    job_title = Column(String)
    company_name = Column(String)
    company_url = Column(String)
    location = Column(String)

class NG2024Job(Base):
    __tablename__ = "ng_2024_jobs"
    id = Column(Integer, primary_key=True)
    job_id = Column(String, unique=True)
    application_url = Column(String)
    job_title = Column(String)
    company_name = Column(String)
    company_url = Column(String)
    location = Column(String)

engine = create_engine("sqlite:///jobs.db", echo=False)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
session = Session()


# ── Logging (console-only, coloured) ────────────────────────────
class LoggingFormatter(logging.Formatter):
    colours = {
        logging.DEBUG: "\x1b[38m\x1b[1m",
        logging.INFO:  "\x1b[34m\x1b[1m",
        logging.WARNING:"\x1b[33m\x1b[1m",
        logging.ERROR:  "\x1b[31m",
        logging.CRITICAL:"\x1b[31m\x1b[1m"
    }
    reset = "\x1b[0m"
    def format(self, record):
        colour = self.colours[record.levelno]
        fmt = f"{colour}{{asctime}} {{levelname:<8}} {{name}}: {{message}}{self.reset}"
        formatter = logging.Formatter(fmt, "%Y-%m-%d %H:%M:%S", style="{")
        return formatter.format(record)

logger = logging.getLogger("discord_bot")
logger.setLevel(logging.INFO)
console = logging.StreamHandler()
console.setFormatter(LoggingFormatter())
logger.addHandler(console)

# ── Filters ─────────────────────────────────────────────────────
blacklist_companies = {
    "Team Remotely Inc","HireMeFast LLC","Get It Recruit - Information Technology",
    "Offered.ai","4 Staffing Corp","myGwork - LGBTQ+ Business Community",
    "Patterned Learning AI","Mindpal","Phoenix Recruiting","SkyRecruitment",
    "Phoenix Recruitment","Patterned Learning Career","SysMind","SysMind LLC",
    "Motion Recruitment"
}
bad_roles = {"unpaid","senior","lead","manager","director","principal","vp","sr.","snr","ii","iii"}
quarantined_2025_terms = {"2024","intern","internship"}
quarantined_2024_terms = {"2025","intern","internship"}

# ── Discord bot class ───────────────────────────────────────────
class DiscordBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=None, intents=intents, help_command=None)
        self.session = session

        self.ng_2024_terms = [
            "new grad software engineer",
            "recent graduate software engineer",
            "junior software engineer"
        ]
        self.ng_2025_terms = [
            "2025 software engineer",
            "new grad 2025 software engineer",
            "software engineer recent graduate 2025",
            "2025 Data Scientist",
            "2025 Data Analyst",
            "2025 Data Engineer"
        ]
        self.ng_2024_index = 0
        self.ng_2025_index = 0


    # ── presence every 2 min ───────────────────────────────────
    @tasks.loop(minutes=2.0)
    async def status_task(self):
        await self.change_presence(activity=discord.Game("hunting fresh jobs 🔍"))

    @status_task.before_loop
    async def wait_ready_status(self):
        await self.wait_until_ready()


    # ── scrape cycle every 2 min ───────────────────────────────
    @tasks.loop(minutes=2.0)
    async def job_posting_task(self):
        await self.full_time_task();  await asyncio.sleep(10)
        await self.ng_2025_task();    await asyncio.sleep(10)
        await self.ng_2024_task();    await asyncio.sleep(10)
        await self.intern_task()
        logger.info("Cycle complete – waiting 2 min for next scrape.")


    # ── Individual category tasks ──────────────────────────────
    async def full_time_task(self):
        chan = await self._chan("FT_CHANNEL_ID")
        jobs = await self.get_jobs(search_term="software engineer", results_wanted=20)
        await self._post_jobs(jobs, chan, FullTimeJob, set(), ["engineer","developer","software","data"])

    async def intern_task(self):
        chan = await self._chan("INTERN_CHANNEL_ID")
        jobs = await self.get_jobs(hours_old=10)
        await self._post_jobs(jobs, chan, InternJob, set(), ["intern","internship","co-op"])

    async def ng_2025_task(self):
        chan = await self._chan("NG_2025_CHANNEL_ID")
        term = self.ng_2025_terms[self.ng_2025_index]
        jobs = await self.get_jobs(search_term=term, hours_old=10)
        await self._post_jobs(jobs, chan, NG2025Job, quarantined_2025_terms, ["engineer","developer","software","data"])
        self.ng_2025_index = (self.ng_2025_index + 1) % len(self.ng_2025_terms)

    async def ng_2024_task(self):
        chan = await self._chan("NG_2024_CHANNEL_ID")
        term = self.ng_2024_terms[self.ng_2024_index]
        jobs = await self.get_jobs(search_term=term, hours_old=10)
        await self._post_jobs(jobs, chan, NG2024Job, quarantined_2024_terms, ["engineer","developer","software","data"])
        self.ng_2024_index = (self.ng_2024_index + 1) % len(self.ng_2024_terms)


    # ──fetch channel ──────────────────────────────────
    async def _chan(self, env_key):
        cid = int(os.getenv(env_key, "0"))
        if not cid:
            logger.error(f"{env_key} missing in .env");  return None
        return self.get_channel(cid) or await self.fetch_channel(cid)


    # ── post + store jobs ───────────────────────────────
    async def _post_jobs(self, jobs, chan, Model, quarantine, must_have):
        if chan is None:
            return
        posted = 0
        for _, row in jobs.iterrows():
            title_l = row["title"].lower()
            if (row["company"] in blacklist_companies or
                any(t in title_l for t in bad_roles) or
                any(t in title_l for t in quarantine) or
                not any(t in title_l for t in must_have) or
                self.session.query(Model).filter_by(job_id=row["id"]).first()):
                continue

            msg = (
                f"## **{row['company']}**\n"
                f"**Role:** {row['title']}\n"
                f"**Location:** {row['location']}\n"
                f"**Apply:** [Click here to apply]({row['job_url']})\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            )

            await chan.send(msg)
            rec = Model(
                job_id=row["id"],
                application_url=row["job_url"],
                job_title=row["title"],
                company_name=row["company"],
                company_url=row["company_url"],
                location=row["location"]
            )
            self.session.add(rec);  posted += 1
        self.session.commit()
        logger.info(f"Posted {posted} new listings to #{chan.name}")


    # ── single scrape helper ───────────────────────────────────
    async def get_jobs(self, sites=None, search_term="software engineer intern", location="United States", results_wanted=15, hours_old=12):
        if sites is None:
            sites = ["linkedin","indeed","glassdoor"]
        return scrape_jobs(
            site_name=sites,
            search_term=search_term,
            location=location,
            results_wanted=results_wanted,
            hours_old=hours_old,
        )


    # ── Discord lifecycle hooks ────────────────────────────────
    async def setup_hook(self):
        logger.info(f"Logged in as {self.user.name}")
        logger.info(f"discord.py {discord.__version__} • Python {platform.python_version()}")
        self.status_task.start();  self.job_posting_task.start()


# ── Run ────────────────────────────────────────────────────────
load_dotenv()
bot = DiscordBot()
bot.run(os.getenv("TOKEN"))
