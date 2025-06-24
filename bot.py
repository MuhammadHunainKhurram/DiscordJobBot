from __future__ import annotations
import os, re, html, itertools, sqlite3, textwrap, logging, platform, asyncio
from datetime import datetime
import requests, discord
import pandas as pd
from discord.ext import tasks, commands
from dotenv import load_dotenv
from jobspy import scrape_jobs
import sqlalchemy as sa



# ────────────────────────────── ENV & LOGGING ──────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Discord channel IDs and bot token
FT_CHANNEL_ID       = int(os.getenv("FT_CHANNEL_ID"))
INTERN_CHANNEL_ID   = int(os.getenv("INTERN_CHANNEL_ID"))
TOKEN               = os.getenv("TOKEN")

# Quick feature toggles (optional in .env)
SCRAPE_GITHUB   = os.getenv("SCRAPE_GITHUB", "true").lower() == "true"
SCRAPE_JOBSPY   = os.getenv("SCRAPE_JOBSPY", "true").lower() == "true"
INTERVAL_MIN    = int(os.getenv("SCRAPE_MIN", 15)) 



# ─────────────────────────────── REPO LIST ─────────────────────────────
# Each key = human label, value = raw.githubusercontent path (stored in .env)
REPOS = {
    "swe"  : os.getenv("SWE"),
    "eng"  : os.getenv("ENG"),
    "data" : os.getenv("DATA"),
    "pm"   : os.getenv("PM"),
    "sum26": os.getenv("SUM26"),
    "sum25": os.getenv("SUM25"),
    "os26" : os.getenv("OS26"),
    "os25" : os.getenv("OS25"),
}

# Remove any None entries so you can comment a repo out by deleting its env var
REPOS = {k: v for k, v in REPOS.items() if v}

OFFSEASON_REPOS = {"os26", "os25"} 
SOURCE_LABEL = {
    "swe":"Jobright SWE", "eng":"Jobright ENG",
    "data":"Jobright Data", "pm":"Jobright PM",
    "sum26":"Ouckah", "sum25":"Simplify",
    "os26":"Ouckah Off-Season", "os25":"Simplify Off-Season",
}


# ─────────────────────────────── FILTERS ───────────────────────────────
BLACKLIST_COMPANIES = {
    "Team Remotely Inc","HireMeFast LLC","Get It Recruit - Information Technology",
    "Offered.ai","4 Staffing Corp","myGwork - LGBTQ+ Business Community",
    "Patterned Learning AI","Mindpal","Phoenix Recruiting","SkyRecruitment",
    "Phoenix Recruitment","Patterned Learning Career","SysMind","SysMind LLC",
    "Motion Recruitment","Lensa",
}

BAD_ROLES = {
    "unpaid","senior","lead","manager","director","principal","vp",
    "sr.","sr","snr","ii","iii",
}


# New: allow-list of *tech* keywords – rows must contain one of these
TECH_TERMS = re.compile(
    r"\b(software|engineer|developer|data|ai|machine learning|ml|product|cloud|devops|security|cyber|frontend|backend|full[- ]?stack|ios|android)\b",
    re.I,
)


# JobSpy search terms (intern & full-time)
SEARCH_TERMS_INTERN = [
    "software engineer intern","software engineering intern","software developer intern",
    "software development intern","ai intern","machine learning intern",
    "machine learning engineering intern","product management intern",
    "product manager intern","project management intern","data science intern",
    "data analyst intern","data engineering intern",
]


SEARCH_TERMS_FT = [
    "software engineer","software engineering","software developer",
    "ai engineer","data scientist","product manager","project manager",
]


# Regex to classify internship titles
INTN_WORDS = re.compile(r"\b(intern|internship|apprentice|co[- ]?op|coop|student|trainee)\b", re.I)


# ─────────────────────────────── DATABASE ──────────────────────────────
# One PostGreSQL table to store every job we've already posted (for deduping)
DB_URL = os.getenv("DATABASE_URL")
engine = sa.create_engine(DB_URL, pool_pre_ping=True)
conn   = engine.raw_connection()



conn.execute(
    """CREATE TABLE IF NOT EXISTS jobs (
           triple   TEXT PRIMARY KEY,           -- company|title|location (lower)
           url      TEXT,
           source   TEXT,
           posted   DATETIME DEFAULT CURRENT_TIMESTAMP
       );"""
)

def triple(company: str, title: str, loc: str) -> str:
    return f"{company.lower()}|{title.lower()}|{loc.lower()}"

def has_been_posted(tp: str) -> bool:
    return conn.execute("SELECT 1 FROM jobs WHERE triple=?;", (tp,)).fetchone() is not None

def remember(tp: str, url: str, src: str) -> None:
    conn.execute("INSERT OR IGNORE INTO jobs (triple,url,source) VALUES (?,?,?);", (tp, url, src))
    conn.commit()


# ─────────────────────────────── HELPERS ───────────────────────────────
TAG_RE   = re.compile(r"<[^>]+>")
LINK_RE  = re.compile(r"https?://[^\)\"'\s]+")
AI_WORDS = re.compile(r"\b(ai|machine learning|cybersecurity|quant|quantum)\b", re.I)


def _strip(markup: str) -> str:
    """Remove HTML tags & entities from GitHub cells."""
    markup = markup.replace("<br>", ", ").replace("<br/>", ", ")
    return TAG_RE.sub("", html.unescape(markup)).strip()


def parse_github_markdown(raw: str) -> list[dict]:
    """
    Extract rows (company, title, location, link) from a README table.
    Works for Jobright, Simplify, Ouckah.
    """
    lines = raw.splitlines()
    starts = [i for i, l in enumerate(lines) if l.lstrip().lower().startswith("| company")]
    rows, last_co = [], ""
    for s in starts:
        for line in itertools.takewhile(lambda l: l.startswith("|"), lines[s:]):
            parts = [c.strip() for c in line.split("|")[1:-1]]
            if len(parts) < 4 or parts[0].lower() == "company":
                continue
            c_raw, t_raw, loc, app, *_ = parts
            link_match = LINK_RE.search(app) or LINK_RE.search(t_raw)
            if not link_match:
                continue
            link = link_match.group(0)
            company = last_co if c_raw.startswith("↳") else re.sub(r"\*\*\[(.*?)\].*", r"\1", c_raw)
            if not c_raw.startswith("↳"):
                last_co = company
            title = re.sub(r"\*\*\[(.*?)\].*", r"\1", t_raw)
            rows.append(
                dict(company=_strip(company), title=_strip(title), location=_strip(loc), link=link)
            )
    return rows


def fetch_repo_rows(key: str, path: str) -> list[dict]:
    """Download a README and return all valid internship rows."""
    url = f"https://raw.githubusercontent.com/{path}"
    r   = requests.get(url, headers={"Accept": "application/vnd.github.v3.raw"}, timeout=20)
    if r.status_code != 200:
        logging.warning(f"{key}: HTTP {r.status_code}")
        return []

    rows = parse_github_markdown(r.text)
    # Add “(Off-Season)” label where needed
    if key in OFFSEASON_REPOS:
        for r in rows:
            r["company"] += " (Off-Season)"
    # Extra keyword filter for the general engineering repo
    if key == "eng":
        rows = [r for r in rows if AI_WORDS.search(r["title"]) or AI_WORDS.search(r["company"])]
    # Attach a human-readable source label
    for r in rows:
        r["source"] = SOURCE_LABEL.get(key, "GitHub")
    return rows


def classification(title: str) -> str:
    """Return 'intern' or 'ft' based on title keywords."""
    return "intern" if INTN_WORDS.search(title) else "ft"

def is_intern_title(title: str) -> bool:
    INTN_WORDS = re.compile(
        r"\b(intern|internship|apprentice|co[- ]?op|coop|student|trainee)\b",
        re.I,
    )
    return bool(INTN_WORDS.search(title or ""))


def passes_filters(row: dict) -> bool:
    """Apply blacklist, bad-role, and tech-keywords filters."""
    if row["company"] in BLACKLIST_COMPANIES:
        return False
    if any(bad in row["title"].lower() for bad in BAD_ROLES):
        return False
    if not TECH_TERMS.search(row["title"]) and not TECH_TERMS.search(row["company"]):
        return False
    return True


def discord_message(r: dict) -> str:
    return textwrap.dedent(
        f"""\
        ## {r['company']}
        --------------------------------------------------
        **Role:** {r['title']}
        **Location:** {r['location']}
        **Link:** **[Apply Here]({r['link']})**
        **Source:** {r['source']}
        --------------------------------------------------
        """
    )



# ─────────────────────────────── DISCORD BOT ───────────────────────────
intents = discord.Intents.default()
bot = commands.Bot(command_prefix=None, intents=intents, help_command=None)

@tasks.loop(minutes=1)
async def status():
    await bot.change_presence(activity=discord.Game("hunting jobs 🔍"))

@tasks.loop(minutes=INTERVAL_MIN)
async def scrape():
    if SCRAPE_JOBSPY:
        await scrape_jobspy()
    if SCRAPE_GITHUB:
        await scrape_github()
    logging.info("Cycle complete.")


# ───────── SCRAPE LINKEDIN / INDEED (JobSpy) ─────────
async def scrape_jobspy() -> None:
    """Run JobSpy searches for internship and full-time terms."""
    chan_intern = bot.get_channel(INTERN_CHANNEL_ID) or await bot.fetch_channel(INTERN_CHANNEL_ID)
    chan_ft     = bot.get_channel(FT_CHANNEL_ID)     or await bot.fetch_channel(FT_CHANNEL_ID)

    total_int = total_ft = 0

    # Internship searches
    for term in SEARCH_TERMS_INTERN:
        df = scrape_jobs(
            site_name=      ["linkedin", "indeed"],
            search_term=    term,
            location=       "United States",
            results_wanted= 30,
            hours_old=      12,
        )
        total_int += await post_dataframe(df, "JobSpy", chan_intern, term, expect_intern=True)

    # Full-time searches
    for term in SEARCH_TERMS_FT:
        df = scrape_jobs(
            site_name=      ["linkedin", "indeed"],
            search_term=    term,
            location=       "United States",
            results_wanted= 30,
            hours_old=      12,
        )
        total_ft  += await post_dataframe(df, "JobSpy", chan_ft,    term, expect_intern=False)

    logging.info(f"JobSpy cycle → {total_int} intern rows, {total_ft} full-time rows")


async def post_dataframe(df, source: str, channel: discord.TextChannel, term: str, expect_intern: bool) -> int:
    """Send each new, filtered job row to Discord and persist in DB."""
    posted = 0
    for _, row in df.iterrows():

        # --- Skip malformed rows -----------------------------------
        if pd.isna(row["company"]) or pd.isna(row["title"]):
            continue

        # --- Internship keyword guard ------------------------------
        if expect_intern and not is_intern_title(row["title"]):
            continue
        if not expect_intern and is_intern_title(row["title"]):
            # safety: keep stray interns out of the FT channel
            continue

        row_dict = {
            "company":  row["company"],
            "title":    row["title"],
            "location": row["location"] if not pd.isna(row["location"]) else "",
            "link":     row["job_url"],
            "source":   source,
        }

        if not passes_filters(row_dict):
            continue

        tp = triple(row_dict["company"], row_dict["title"], row_dict["location"])
        if has_been_posted(tp):
            continue

        await channel.send(discord_message(row_dict))
        remember(tp, row_dict["link"], source)
        posted += 1

    logging.info(f"{source} (‘{term}’) → posted {posted}")
    return posted


# ───────── SCRAPE GITHUB READMEs ─────────
async def scrape_github():
    """Parse every configured repo and post internships (GitHub lists are all internships)."""
    chan = bot.get_channel(INTERN_CHANNEL_ID) or await bot.fetch_channel(INTERN_CHANNEL_ID)
    posted = 0
    for k, path in REPOS.items():
        for r in fetch_repo_rows(k, path):
            if not passes_filters(r):
                continue
            tp = triple(r["company"], r["title"], r["location"])
            if has_been_posted(tp):
                continue
            await chan.send(discord_message(r))
            remember(tp, r["link"], r["source"])
            posted += 1
    logging.info(f"GitHub: posted {posted}")


# ─────────────────────────────── BOT STARTUP ───────────────────────────
@bot.event
async def on_ready():
    logging.info(
        f"Logged in as {bot.user} • discord.py {discord.__version__} "
        f"• Python {platform.python_version()}"
    )
    status.start()
    scrape.start()


# ─────────────────────────────── RUN BOT ───────────────────────────────
if not TOKEN:
    raise SystemExit("TOKEN missing in .env")
bot.run(TOKEN)
