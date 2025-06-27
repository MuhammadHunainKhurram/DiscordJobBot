from __future__ import annotations
import os, re, html, itertools, sqlite3, textwrap, logging, platform, asyncio
from datetime import datetime
import requests, discord
import pandas as pd
from discord.ext import tasks, commands
from dotenv import load_dotenv
from jobspy import scrape_jobs
from sqlalchemy import create_engine, text
import os, sqlalchemy as sa



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
NG_CHANNEL_ID       = int(os.getenv("NG_CHANNEL_ID"))
TOKEN               = os.getenv("TOKEN")

# Quick feature toggles (optional in .env)
SCRAPE_GH_INTERN  = os.getenv("SCRAPE_GITHUB_INTERNSHIPS", "true").lower() == "true"
SCRAPE_GH_NG      = os.getenv("SCRAPE_GITHUB_NEWGRADS",     "true").lower() == "true"
SCRAPE_JOBSPY     = os.getenv("SCRAPE_JOBSPY",              "true").lower() == "true"
INTERVAL_MIN      = int(os.getenv("SCRAPE_MIN", 30))
RUN_ONCE          = os.getenv("RUN_ONCE", "false").lower() == "true"
RATE_LIMIT = 1.0


# ─── IMAGE PATHS ──────────────────────────────────────────────────
IMG_DIR            = "images"
INTERN_IMG_NAME    = "internship.png"
NEWGRAD_IMG_NAME   = "new-grad.png"
FULLTIME_IMG_NAME  = "full-time.png"



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
    "swe-ng"  : os.getenv("SWE_NG"),
    "eng-ng"  : os.getenv("ENG_NG"),
    "data-ng" : os.getenv("DATA_NG"),
    "pm-ng"   : os.getenv("PM_NG"),
}

REPOS = {k: v for k, v in REPOS.items() if v}

OFFSEASON_REPOS = {"os26", "os25"} 
NEWGRAD_REPOS   = {"swe-ng", "eng-ng", "data-ng", "pm-ng"}

SOURCE_LABEL = {
    "swe"       :   "J-SWE", 
    "eng"       :   "J-ENG",
    "data"      :   "J-DATA", 
    "pm"        :   "J-PM",
    "sum26"     :   "OH", 
    "sum25"     :   "SY",
    "os26"      :   "OH-Off-Season", 
    "os25"      :   "SY-Off-Season",
    "swe-ng"    :   "NG-SWE",
    "eng-ng"    :   "NG-ENG",
    "data-ng"   :   "NG-DATA",
    "pm-ng"     :   "NG-PM",
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
    "unpaid","senior","lead","manager","director","principal","vp", "staff",
    "sr.","sr","snr","ii","iii",
}

TECH_TERMS = re.compile(r"\b(software|engineer|developer|data|ai|machine learning|ml|product|cloud|devops|security|cyber|frontend|backend|full[- ]?stack|ios|android)\b", re.I)

SEARCH_TERMS_INTERN = [
    "software engineer intern",
    "software engineering intern",
    "software developer intern",
    "ai intern",
    "machine learning intern",
    "product management intern",
    "product manager intern",
    "project management intern",
    "data science intern",
]

SEARCH_TERMS_FT = [
    "software engineer",
    "software developer",
    "ai engineer",
    "data scientist",
    "product manager",
    "project manager",
]

INTN_WORDS = re.compile(r"\b(intern|internship|apprentice|co[- ]?op|coop|student|trainee)\b", re.I)



# ─────────────────────────────── DATABASE ──────────────────────────────
DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise SystemExit("DATABASE_URL missing in .env")

engine = sa.create_engine(DB_URL, pool_pre_ping=True)

with engine.begin() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS jobs (
            triple  TEXT PRIMARY KEY,               
            url     TEXT,
            source  TEXT,
            posted  TIMESTAMPTZ DEFAULT NOW()
        );
    """))

def triple(company: str, title: str, loc: str) -> str:
    return f"{company.lower()}|{title.lower()}|{loc.lower()}"

def has_been_posted(tp: str) -> bool:
    stmt = text("SELECT 1 FROM jobs WHERE triple = :tp;")
    with engine.connect() as conn:
        return conn.execute(stmt, {"tp": tp}).scalar() is not None

def remember(tp: str, url: str, src: str) -> None:
    stmt = text("""
        INSERT INTO jobs (triple, url, source)
        VALUES (:tp, :url, :src)
        ON CONFLICT (triple) DO NOTHING;
    """)
    with engine.begin() as conn:
        conn.execute(stmt, {"tp": tp, "url": url, "src": src})



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

def fetch_repo_rows(key:str,path:str)->list[dict]:
    url = f"https://raw.githubusercontent.com/{path}"
    r   = requests.get(url, headers={"Accept": "application/vnd.github.v3.raw"}, timeout=20)
    if r.status_code != 200:
        logging.warning(f"{key}: HTTP {r.status_code}")
        return []

    rows = parse_github_markdown(r.text)

    if key in OFFSEASON_REPOS:
        for r in rows: r["company"] += " (Off-Season)"

    if key in {"eng", "eng-ng"}:
        rows = [r for r in rows if AI_WORDS.search(r["title"]) or AI_WORDS.search(r["company"])]

    for r in rows: r["source"] = SOURCE_LABEL.get(key,"GitHub")
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
        -----------------------------------------------
        **Role:** {r['title']}
        **Location:** {r['location']}
        **Link:** **[Apply Here]({r['link']})**
        **Source:** {r['source']}
        -----------------------------------------------
        """
    )



# ─── EMBEDDED MESSAGES ────────────────────────────────────────
def build_embed(row:dict, category:str) -> tuple[discord.Embed, discord.File]:
    embed = discord.Embed(
        title=row["company"],
        colour=0x242429,
        timestamp=datetime.utcnow(),
    )

    embed.add_field(name="Role", value=row["title"], inline=False)
    embed.add_field(name="Location", value=row["location"] or "—", inline=False)
    embed.add_field(name="Link", value=f"[Apply Here]({row['link']})", inline=False)
    embed.set_footer(text=f"Source: {row['source']}")

    img_name = {"intern":   INTERN_IMG_NAME, "newgrad":  NEWGRAD_IMG_NAME, "fulltime": FULLTIME_IMG_NAME,}[category]
    file = discord.File(os.path.join(IMG_DIR, img_name), filename=img_name)
    embed.set_image(url=f"attachment://{img_name}")
    return embed, file



# ─────────────────────────────── DISCORD BOT ───────────────────────────
intents = discord.Intents.default()
bot     = commands.Bot(command_prefix=lambda _b, _m: [], intents=intents, help_command=None)

@tasks.loop(minutes=1)
async def status():
    await bot.change_presence(activity=discord.Game("hunting jobs 🔍"))

@tasks.loop(minutes=INTERVAL_MIN, count=1 if RUN_ONCE else None)
async def scrape():
    if SCRAPE_JOBSPY:
        await scrape_jobspy()
    if SCRAPE_GH_INTERN or SCRAPE_GH_NG:
        await scrape_github()
    logging.info("Cycle complete.")
    if RUN_ONCE:
        await bot.close()



# ───────── SCRAPE LINKEDIN / INDEED (JobSpy) ─────────
async def scrape_jobspy() -> None:
    chan_intern = bot.get_channel(INTERN_CHANNEL_ID) or await bot.fetch_channel(INTERN_CHANNEL_ID)
    chan_ft     = bot.get_channel(FT_CHANNEL_ID)     or await bot.fetch_channel(FT_CHANNEL_ID)

    total_int = total_ft = 0

    for term in SEARCH_TERMS_INTERN:
        df = scrape_jobs(
            site_name=      ["linkedin", "indeed"],
            search_term=    term,
            location=       "United States",
            results_wanted= 10,
            hours_old=      12,
        )
        total_int += await post_dataframe(df, "JS", chan_intern, term, expect_intern=True)

    for term in SEARCH_TERMS_FT:
        df = scrape_jobs(
            site_name=      ["linkedin", "indeed"],
            search_term=    term,
            location=       "United States",
            results_wanted= 10,
            hours_old=      12,
        )
        total_ft  += await post_dataframe(df, "JS", chan_ft,    term, expect_intern=False)

    logging.info(f"JobSpy cycle → {total_int} intern rows, {total_ft} full-time rows")


async def post_dataframe(df, source: str, channel: discord.TextChannel, term: str, expect_intern: bool) -> int:
    posted = 0
    for _, row in df.iterrows():

        if pd.isna(row["company"]) or pd.isna(row["title"]):
            continue

        if expect_intern and not is_intern_title(row["title"]):
            continue

        if not expect_intern and is_intern_title(row["title"]):
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

        category = "intern" if expect_intern else "fulltime"
        embed, file = build_embed(row_dict, category)
        await channel.send(file=file, embed=embed)
        await asyncio.sleep(RATE_LIMIT)
        remember(tp, row_dict["link"], source)
        posted += 1

    logging.info(f"{source} (‘{term}’) → posted {posted}")
    return posted


# ───────── SCRAPE GITHUB READMEs ─────────
async def scrape_github() -> None:
    chan_int = bot.get_channel(INTERN_CHANNEL_ID) or await bot.fetch_channel(INTERN_CHANNEL_ID)
    
    chan_ng  = bot.get_channel(NG_CHANNEL_ID) or await bot.fetch_channel(NG_CHANNEL_ID)

    loop = asyncio.get_running_loop()

    def _load_posted() -> set[str]:
        with engine.begin() as conn:
            return {row[0] for row in conn.execute(text("SELECT triple FROM jobs"))}

    posted: set[str] = await loop.run_in_executor(None, _load_posted)

    posted_int = posted_ng = 0

    for key, path in REPOS.items():
        is_ng_repo   = key in NEWGRAD_REPOS
        is_int_repo  = not is_ng_repo

        if is_ng_repo and not SCRAPE_GH_NG:
            continue
        if is_int_repo and not SCRAPE_GH_INTERN:
            continue

        category = "newgrad"    if is_ng_repo else "intern"
        channel  = chan_ng      if is_ng_repo else chan_int

        for row in fetch_repo_rows(key, path):
            if not passes_filters(row):
                continue

            tp = triple(row["company"], row["title"], row["location"] or "")
            if tp in posted:
                continue

            embed, file = build_embed(row, category)
            await channel.send(file=file, embed=embed)
            await asyncio.sleep(RATE_LIMIT)

            remember(tp, row["link"], row["source"])
            posted.add(tp)

            if is_ng_repo:
                posted_ng  += 1

            else:
                posted_int += 1

    logging.info(
        "GitHub → %d intern, %d NG posts (batch size: %d repos)",
        posted_int, posted_ng, len(REPOS),
    )



# ─────────────────────────────── BOT STARTUP ───────────────────────────
@bot.event
async def on_ready():
    logging.info(
        f"Logged in as {bot.user} • discord.py {discord.__version__}"
        f" • Python {platform.python_version()}"
    )
    status.start()
    scrape.start()


# ─────────────────────────────── RUN BOT ───────────────────────────────
if not TOKEN:
    raise SystemExit("TOKEN missing in .env")
bot.run(TOKEN)
