# Job-Scraper Bot

ACM Discord bot that aggregates fresh job listings from **LinkedIn, Indeed, Glassdoor** and posts them to category-specific channels.

Made by Muhammad Khurram

## Features
* Multi-board scraping with JobSpy
* 60-second scrape cycle, 10-second stagger between categories
* Filters: blacklist companies, title terms, quarantine mismatch (2024 vs 2025)
* SQLite deduplication per channel
* .env-driven channel IDs and bot token
* Colourful console logging

## Setup
```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
