# 🎯 Job-Scraper Bot & Talent-Signal Generator
*A 400-line Python bot that turns the scattered world of tech-job postings into a curated, duplicate-free feed for Discord.*

> **What a recruiter sees:**  
> • A real-time radar for internship & early-career talent  
> • Zero manual sourcing – the bot collects, filters, and broadcasts roles every 15 minutes  
> • A single database of “who-posted-what-when” you can re-use for analytics or outreach

Built end-to-end by **Muhammad Khurram** (Python • web-scraping • automation • data quality).

---

## 💡 Why this project matters

<table>
<tr>
<td width="50%">

**Recruiting pain-point**  
Job leads sit on half a dozen boards & GitHub lists.  
Repeats, expired links, off-topic roles, and spam make screening a chore.

</td>
<td>

**Bot solution**  
*Job-Scraper* fetches posts from mainstream boards (LinkedIn, Indeed) **and** curated GitHub lists, rejects non-tech roles, deduplicates them, and drops a polished card into the right Discord channel – all without human touch.

</td></tr></table>

---

## ✨ What the bot does

| ✓ | Capability |
|---|-------------|
| 🚀 **15-min heartbeat** | Candidates see fresh roles **same morning they go live**, driving engagement & referrals. |
| 🎯 **Smart categorisation** | “Intern / Co-op / Apprentice / Trainee” → *Internships* channel; everything else → *Full-Time Tech* channel. |
| 🧹 **Quality filters** | Blocks shady staffing firms, senior-only titles, and non-tech disciplines (e.g., civil engineering). |
| 🗂 **Single source-of-truth DB** | SQLite table tracks every company × title × location ever sent – no duplicate posts, perfect for follow-up campaigns. |
| 🔒 **Secrets via .env** | Repo is safe to open-source; tokens & repo URLs stay private. |
| 🌈 **Colour-coded logs** | One look tells you what was scraped, skipped, or posted. |

*(Under the hood: asyncio, thread-pool offloading, regex classification, and a handful of clean SQL queries.)*

---

## ⚡ One-command set-up

```bash
# 1 – clone & enter virtual-env
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
git clone https://github.com/your-org/job-scraper-bot.git
cd job-scraper-bot

# 2 – install everything (tiny footprint: discord.py, jobspy, requests …)
pip install -r requirements.txt

# 3 – drop in your secrets & channel IDs
cp .env.example .env  # then open .env and paste your Discord token

# 4 – go live
python bot.py
