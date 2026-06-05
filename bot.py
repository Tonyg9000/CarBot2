"""
Classic Car Alert Bot
Monitors Craigslist RSS, eBay Motors, AutoTrader, and Cars.com
for classic cars (1900-2005) and sends email alerts.
"""

import os
import re
import time
import hashlib
import logging
import smtplib
import sqlite3
import requests
import feedparser
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "seen_listings.db")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL_MINUTES", "15"))

CLASSIC_YEAR_MIN = 1900
CLASSIC_YEAR_MAX = 2005

# Realistic browser headers that won't get blocked
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}

# ── Database ──────────────────────────────────────────────────────────────────

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            id TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
            source TEXT,
            price TEXT,
            year TEXT,
            seen_at TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    con.commit()
    con.close()

def already_seen(listing_id):
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT 1 FROM seen WHERE id=?", (listing_id,)).fetchone()
    con.close()
    return row is not None

def mark_seen(listing):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT OR IGNORE INTO seen(id,title,url,source,price,year,seen_at) VALUES(?,?,?,?,?,?,?)",
        (listing["id"], listing["title"], listing["url"], listing["source"],
         listing.get("price", ""), listing.get("year", ""), datetime.utcnow().isoformat())
    )
    con.commit()
    con.close()

def get_recent_listings(limit=50):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT title,url,source,price,year,seen_at FROM seen ORDER BY seen_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    con.close()
    return [{"title": r[0], "url": r[1], "source": r[2], "price": r[3], "year": r[4], "seen_at": r[5]} for r in rows]

def get_config(key, default=None):
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    con.close()
    return row[0] if row else default

def set_config(key, value):
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT OR REPLACE INTO config(key,value) VALUES(?,?)", (key, value))
    con.commit()
    con.close()

# ── Year extraction ───────────────────────────────────────────────────────────

def extract_year(text):
    matches = re.findall(r'\b(19\d{2}|200[0-5])\b', text)
    for m in matches:
        y = int(m)
        if CLASSIC_YEAR_MIN <= y <= CLASSIC_YEAR_MAX:
            return y
    return None

def is_classic(title, description=""):
    year = extract_year(f"{title} {description}")
    if year:
        return True, str(year)
    return False, ""

# ── Craigslist ────────────────────────────────────────────────────────────────

CRAIGSLIST_CITIES = [
    "newyork", "losangeles", "chicago", "houston", "phoenix",
    "philadelphia", "sfbay", "seattle", "miami", "atlanta",
    "boston", "denver", "dallas", "sandiego", "portland",
]

def scrape_craigslist():
    results = []
    session = requests.Session()
    session.headers.update(HEADERS)

    for city in CRAIGSLIST_CITIES:
        url = (
            f"https://{city}.craigslist.org/search/cta"
            f"?format=rss&auto_year_min={CLASSIC_YEAR_MIN}&auto_year_max={CLASSIC_YEAR_MAX}"
            f"&sort=date"
        )
        try:
            resp = session.get(url, timeout=15)
            log.info(f"Craigslist {city}: HTTP {resp.status_code}, {len(resp.content)} bytes")
            if resp.status_code != 200:
                log.warning(f"Craigslist {city} returned {resp.status_code}")
                continue

            feed = feedparser.parse(resp.content)
            entries = feed.entries
            log.info(f"Craigslist {city}: {len(entries)} feed entries")

            for entry in entries:
                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = entry.get("summary", "")
                classic, year = is_classic(title, summary)
                if not classic:
                    # Still add it — Craigslist year filter should mean all are classic
                    year = ""
                price_match = re.search(r'\$[\d,]+', title + " " + summary)
                price = price_match.group(0) if price_match else "N/A"
                listing_id = hashlib.md5(link.encode()).hexdigest()
                clean_summary = BeautifulSoup(summary, "html.parser").get_text()[:200]
                results.append({
                    "id": listing_id,
                    "title": title,
                    "url": link,
                    "source": f"Craigslist ({city})",
                    "price": price,
                    "year": year,
                    "description": clean_summary,
                })
        except Exception as e:
            log.warning(f"Craigslist {city} error: {e}")
        time.sleep(0.5)

    log.info(f"Craigslist total: {len(results)} listings")
    return results

# ── eBay Motors (RSS — much more reliable than scraping) ─────────────────────

def scrape_ebay():
    results = []
    # eBay has a public RSS feed for searches — far more reliable than HTML scraping
    url = (
        "https://www.ebay.com/sch/i.html"
        "?_nkw=classic+car+1900+2005&_sacat=6001&_sop=10&_rss=1"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        log.info(f"eBay RSS: HTTP {resp.status_code}, {len(resp.content)} bytes")
        feed = feedparser.parse(resp.content)
        log.info(f"eBay RSS: {len(feed.entries)} entries")

        for entry in feed.entries:
            title = entry.get("title", "")
            link = entry.get("link", "").split("?")[0]
            price_match = re.search(r'\$[\d,]+\.?\d*', entry.get("summary", "") + title)
            price = price_match.group(0) if price_match else "N/A"
            classic, year = is_classic(title)
            if not classic:
                year = extract_year(entry.get("summary", ""))
                year = str(year) if year else ""
            listing_id = hashlib.md5(link.encode()).hexdigest()
            results.append({
                "id": listing_id,
                "title": title,
                "url": link,
                "source": "eBay Motors",
                "price": price,
                "year": year,
                "description": entry.get("summary", "")[:200],
            })
    except Exception as e:
        log.warning(f"eBay error: {e}")

    # Fallback: also try direct HTML scrape
    if not results:
        results = _scrape_ebay_html()

    log.info(f"eBay total: {len(results)} listings")
    return results

def _scrape_ebay_html():
    results = []
    url = (
        "https://www.ebay.com/sch/i.html?_nkw=classic+vintage+car&_sacat=6001"
        "&_sop=10&LH_ItemCondition=3&_pgn=1"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        log.info(f"eBay HTML fallback: HTTP {resp.status_code}")
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select(".s-item__wrapper") or soup.select(".s-item")
        log.info(f"eBay HTML: found {len(items)} item elements")
        for item in items[:25]:
            title_el = item.select_one(".s-item__title")
            link_el = item.select_one("a.s-item__link") or item.select_one(".s-item__link")
            price_el = item.select_one(".s-item__price")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if title.lower() == "shop on ebay":
                continue
            link = (link_el.get("href", "") if link_el else "").split("?")[0]
            price = price_el.get_text(strip=True) if price_el else "N/A"
            classic, year = is_classic(title)
            if not classic:
                continue
            listing_id = hashlib.md5((title + link).encode()).hexdigest()
            results.append({
                "id": listing_id,
                "title": title,
                "url": link or "https://ebay.com",
                "source": "eBay Motors",
                "price": price,
                "year": year,
                "description": title,
            })
    except Exception as e:
        log.warning(f"eBay HTML fallback error: {e}")
    return results

# ── AutoTrader ────────────────────────────────────────────────────────────────

def scrape_autotrader():
    results = []
    url = (
        f"https://www.autotrader.com/cars-for-sale/all-cars"
        f"?startYear={CLASSIC_YEAR_MIN}&endYear={CLASSIC_YEAR_MAX}"
        f"&sortBy=datelistedDESC&numRecords=25"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        log.info(f"AutoTrader: HTTP {resp.status_code}, {len(resp.content)} bytes")
        soup = BeautifulSoup(resp.text, "html.parser")

        # Try multiple possible selectors
        cards = (
            soup.select("[data-cmp='inventoryListing']") or
            soup.select(".listing-container") or
            soup.select("[class*='inventory-listing']") or
            soup.select("div[id*='listing']")
        )
        log.info(f"AutoTrader: found {len(cards)} listing cards")

        # If no cards, try JSON embedded in page
        if not cards:
            scripts = soup.find_all("script", type="application/ld+json")
            for script in scripts:
                try:
                    import json
                    data = json.loads(script.string or "")
                    items = data if isinstance(data, list) else data.get("itemListElement", [])
                    for item in items[:20]:
                        name = item.get("name", "") or item.get("item", {}).get("name", "")
                        url_item = item.get("url", "") or item.get("item", {}).get("url", "")
                        price = str(item.get("offers", {}).get("price", "N/A"))
                        classic, year = is_classic(name)
                        if not classic or not name:
                            continue
                        lid = hashlib.md5((name + url_item).encode()).hexdigest()
                        results.append({
                            "id": lid, "title": name, "url": url_item,
                            "source": "AutoTrader", "price": f"${price}", "year": year, "description": name,
                        })
                except Exception:
                    pass

        for card in cards[:20]:
            title_el = card.select_one("h2") or card.select_one("h3") or card.select_one(".title")
            link_el = card.select_one("a")
            price_el = card.select_one("[class*='price']") or card.select_one("[data-cmp='pricingBlock']")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = link_el.get("href", "") if link_el else ""
            link = f"https://www.autotrader.com{href}" if href.startswith("/") else href
            price = price_el.get_text(strip=True) if price_el else "N/A"
            classic, year = is_classic(title)
            if not classic:
                continue
            lid = hashlib.md5((title + link).encode()).hexdigest()
            results.append({
                "id": lid, "title": title, "url": link or "https://autotrader.com",
                "source": "AutoTrader", "price": price, "year": year, "description": title,
            })
    except Exception as e:
        log.warning(f"AutoTrader error: {e}")

    log.info(f"AutoTrader total: {len(results)} listings")
    return results

# ── Cars.com ──────────────────────────────────────────────────────────────────

def scrape_carsdotcom():
    results = []
    url = (
        f"https://www.cars.com/shopping/results/"
        f"?maximum_distance=all&sort=listed_at_desc"
        f"&stock_type=all&year_max={CLASSIC_YEAR_MAX}&year_min={CLASSIC_YEAR_MIN}&zip=90210"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
        log.info(f"Cars.com: HTTP {resp.status_code}, {len(resp.content)} bytes")
        soup = BeautifulSoup(resp.text, "html.parser")

        cards = (
            soup.select("div.vehicle-card") or
            soup.select("[data-qa='vehicle-card']") or
            soup.select("div[class*='vehicle-card']") or
            soup.select("article")
        )
        log.info(f"Cars.com: found {len(cards)} cards")

        for card in cards[:20]:
            title_el = (
                card.select_one("h2") or
                card.select_one("[class*='title']") or
                card.select_one(".vehicle-card-main-specs h2")
            )
            link_el = card.select_one("a[href*='/vehicledetail/']") or card.select_one("a")
            price_el = card.select_one(".primary-price") or card.select_one("[class*='price']")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = link_el.get("href", "") if link_el else ""
            link = f"https://www.cars.com{href}" if href.startswith("/") else href
            price = price_el.get_text(strip=True) if price_el else "N/A"
            classic, year = is_classic(title)
            if not classic:
                continue
            lid = hashlib.md5((title + link).encode()).hexdigest()
            results.append({
                "id": lid, "title": title, "url": link or "https://cars.com",
                "source": "Cars.com", "price": price, "year": year, "description": title,
            })
    except Exception as e:
        log.warning(f"Cars.com error: {e}")

    log.info(f"Cars.com total: {len(results)} listings")
    return results

# ── Email ─────────────────────────────────────────────────────────────────────

def send_email_alert(listings, recipient):
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")

    if not smtp_user or not smtp_pass:
        log.warning("SMTP credentials not set — skipping email.")
        return

    subject = f"🚗 {len(listings)} New Classic Car Listing{'s' if len(listings) > 1 else ''} Found!"
    html_items = ""
    for l in listings:
        html_items += f"""
        <div style="border:1px solid #d4a843;border-radius:8px;padding:16px;margin-bottom:16px;background:#1a1a1a;">
          <div style="font-size:11px;color:#d4a843;text-transform:uppercase;letter-spacing:2px;">{l['source']} &nbsp;·&nbsp; {l.get('year','')}</div>
          <div style="font-size:18px;font-weight:bold;color:#f0e6c8;margin:8px 0;">{l['title']}</div>
          <div style="font-size:22px;color:#d4a843;font-weight:bold;">{l['price']}</div>
          <a href="{l['url']}" style="display:inline-block;margin-top:12px;padding:8px 20px;background:#d4a843;color:#0d0d0d;text-decoration:none;border-radius:4px;font-weight:bold;font-size:13px;">View Listing →</a>
        </div>"""

    html = f"""
    <html><body style="background:#0d0d0d;font-family:Georgia,serif;padding:32px;color:#f0e6c8;">
      <div style="max-width:600px;margin:auto;">
        <div style="text-align:center;margin-bottom:32px;">
          <div style="font-size:36px;">🏎</div>
          <h1 style="color:#d4a843;font-size:28px;margin:8px 0;">Classic Car Alert</h1>
          <p style="color:#888;font-size:14px;">New listings · {datetime.utcnow().strftime('%b %d, %Y %H:%M UTC')}</p>
        </div>
        {html_items}
        <p style="color:#555;font-size:12px;text-align:center;margin-top:32px;">
          Listings sourced from Craigslist, eBay Motors, AutoTrader, and Cars.com.
        </p>
      </div>
    </body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = recipient
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, recipient, msg.as_string())
        log.info(f"Email sent to {recipient} with {len(listings)} listings.")
    except Exception as e:
        log.error(f"Email send failed: {e}")

# ── Main check ────────────────────────────────────────────────────────────────

def run_check():
    recipient = get_config("alert_email") or os.environ.get("ALERT_EMAIL", "")
    if not recipient:
        log.warning("No alert email configured. Skipping check.")
        return

    log.info("=== Starting classic car check ===")
    all_listings = []
    all_listings += scrape_craigslist()
    all_listings += scrape_ebay()
    all_listings += scrape_autotrader()
    all_listings += scrape_carsdotcom()

    log.info(f"Total scraped across all sources: {len(all_listings)}")

    new_listings = []
    for listing in all_listings:
        if not already_seen(listing["id"]):
            mark_seen(listing)
            new_listings.append(listing)

    log.info(f"New (unseen) listings: {len(new_listings)}")

    if new_listings:
        send_email_alert(new_listings, recipient)

# ── Scheduler ─────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler()

def start_scheduler():
    if scheduler.running:
        log.info("Scheduler already running — skipping start.")
        return
    init_db()
    scheduler.add_job(run_check, "interval", minutes=CHECK_INTERVAL, id="car_check", replace_existing=True)
    scheduler.start()
    log.info(f"Scheduler started — checking every {CHECK_INTERVAL} minutes.")
