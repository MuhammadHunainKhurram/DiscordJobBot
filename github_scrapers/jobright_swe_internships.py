import asyncio, os, re, sqlite3, textwrap, itertools, html
import discord, requests
from discord.ext import tasks
from dotenv import load_dotenv
load_dotenv()

# ──────────────────────────── SOURCES ──────────────────────────
REPOS = {
    # Jobright
    "swe" :     os.getenv("SWE"),
    "eng" :     os.getenv("ENG"),
    "data":     os.getenv("DATA"),
    "pm"  :     os.getenv("PM"),
    # Summer
    "sum26":    os.getenv("SUM26"),
    "sum25":    os.getenv("SUM25"),
    # Off-season
    "os26":     os.getenv("OS26"),
    "os25":     os.getenv("OS25"),
}

SOURCE_LABEL = {
    "swe":  "Jobright Software Engineering",
    "data": "Jobright Data Science",
    "pm":   "Jobright Product Management",
    "eng":  "Jobright Engineering",

    "sum25": "S-Internships",
    "os25":  "S-Internships Off-Season",

    "sum26": "O-Internships",
    "os26":  "O-Internships Off-Season",
}

RAW_FMT  = "https://raw.githubusercontent.com/{repo}"
HEADERS  = {"Accept": "application/vnd.github.v3.raw"}

ENG_KEYWORDS = re.compile(
    r"\b(ai|machine learning|cybersecurity|quant|quantum)\b", re.I
)

OFFSEASON_REPOS = {"os26", "os25"}


# ──────────────────────── PARSE UTILITIES ──────────────────────
TAG_RE = re.compile(r"<[^>]+>")

def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", ", ", text, flags=re.I)
    return TAG_RE.sub("", html.unescape(text)).strip()

LINK_RE = re.compile(r"https?://[^\)\"'\s]+")

def parse_readme(raw: str):
    lines = raw.splitlines()

    tbl_starts = [i for i, l in enumerate(lines) if l.lstrip().lower().startswith("| company")]
    rows, last_company = [], ""
    for idx in tbl_starts:

        for line in itertools.takewhile(lambda x: x.startswith("|"), lines[idx:]):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if len(cells) < 5 or cells[0].lower() == "company":
                continue
            company_raw, title_raw, loc, *_ = cells[:5]
            app_cell = cells[3] if len(cells) >= 4 else ""

            link_match = LINK_RE.search(app_cell) or LINK_RE.search(title_raw)
            if not link_match:
                continue
            link = link_match.group(0)

            company = (
                last_company if company_raw.startswith("↳")
                else re.sub(r"\*\*\[(.*?)\].*", r"\1", company_raw)
            )
            if not company_raw.startswith("↳"):
                last_company = company

            title = re.sub(r"\*\*\[(.*?)\].*", r"\1", title_raw)
            location = strip_html(loc)

            rows.append(
                dict(
                    id=link,
                    company=strip_html(company),
                    title=strip_html(title),
                    location=location,
                    link=link,
                )
            )
    return rows

def fetch_rows(repo_key: str):
    url  = f"https://raw.githubusercontent.com/{REPOS[repo_key]}"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    if resp.status_code != 200:
        print(f"[WARN] {repo_key} {resp.status_code} {url}")
        return []

    rows = parse_readme(resp.text)

    if repo_key in OFFSEASON_REPOS:
        for r in rows:
            r["company"] += " (Off-Season)"

    if repo_key == "eng":
        rows = [r for r in rows if ENG_KEYWORDS.search(r["title"]) or ENG_KEYWORDS.search(r["company"])]

    for r in rows:
        r["source"] = SOURCE_LABEL.get(repo_key, "Unknown List")
    return rows


# ───────────────────────── DEDUP STORAGE ───────────────────────
DB = "gh_seen.db"
conn = sqlite3.connect(DB)

# two UNIQUE keys:
#   • id  (URL)
#   • triple_key (company|title|location, lower-cased)
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS seen (
        id          TEXT    PRIMARY KEY,
        triple_key  TEXT    UNIQUE,
        ts          DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """
)
conn.commit()

def triple(row):
    return f"{row['company'].lower()}|{row['title'].lower()}|{row['location'].lower()}"

def is_new(row) -> bool:
    cur = conn.execute("SELECT 1 FROM seen WHERE id=? OR triple_key=?;", (row["id"], triple(row)))

    if cur.fetchone():
        return False
    
    conn.execute("INSERT OR IGNORE INTO seen (id, triple_key) VALUES (?, ?);", (row["id"], triple(row)))
    conn.commit()
    return True


# ───────────────────────── DISCORD BOT ─────────────────────────
load_dotenv()
TOKEN      = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("JOBRIGHT_CHANNEL_ID", "0"))

intents = discord.Intents.default()
bot = discord.Client(intents=intents)

@tasks.loop(minutes=2.0)
async def scrape_and_post():
    chan = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
    all_rows = list(
        itertools.chain.from_iterable(fetch_rows(k) for k in REPOS)
    )
    posted = 0
    for r in all_rows:
        if not is_new(r):
            continue
        msg = textwrap.dedent(
            f"""\
            ## {r['company']}
            --------------------------------------------------
            **Role:** {r['title']}
            **Location:** {r['location']}
            **Link:** **[Apply Here]({r['link']})**
            --------------------------------------------------
            """
        )
        # Add this to see where the link came from -->  **Source:** {r['source']}
        await chan.send(msg)
        posted += 1
    print(f"[GitHub scrape] posted {posted} new rows")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    if not scrape_and_post.is_running():
        scrape_and_post.start()

if __name__ == "__main__":
    if not (TOKEN and CHANNEL_ID):
        raise SystemExit("TOKEN or JOBRIGHT_CHANNEL_ID missing in .env")
    bot.run(TOKEN)
