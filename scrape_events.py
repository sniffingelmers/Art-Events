#!/usr/bin/env python3
import json, re, html
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from email.utils import format_datetime
from urllib.parse import urljoin
import requests
import feedparser
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

HEADERS = {"User-Agent": "Art-Events-RSS/1.0 (+https://github.com/sniffingelmers/Art-Events)"}
TIMEOUT = 25
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
HORIZON_DAYS = 180
MAX_ITEMS = 300

def clean(value):
    if not value:
        return ""
    return re.sub(r"\\s+", " ", BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)).strip()

def parse_date(value):
    if not value:
        return None
    try:
        dt = dateparser.parse(str(value), fuzzy=True)
        if not dt:
            return None
        if dt.tzinfo is None:
            # Event pages commonly omit the timezone; interpret naive
            # Bay Area dates in Pacific time rather than UTC.
            dt = dt.replace(tzinfo=LOCAL_TZ)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def item(title, link, dt, source, description=""):
    if not title or not link or not dt:
        return None
    return {
        "title": clean(title),
        "link": link,
        "date": dt,
        "source": source,
        "description": clean(description)[:1000],
    }

def from_feed(url, source):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        parsed = feedparser.parse(r.content)
        out = []
        for e in parsed.entries:
            dt = None
            for key in ("published_parsed", "updated_parsed"):
                if getattr(e, key, None):
                    dt = datetime(*getattr(e, key)[:6], tzinfo=timezone.utc)
                    break
            if not dt:
                dt = parse_date(e.get("published") or e.get("updated"))
            link = e.get("link")
            if link and dt:
                x = item(e.get("title"), link, dt, source, e.get("summary") or e.get("description"))
                if x: out.append(x)
        return out
    except Exception:
        return []

def from_schema(soup, page_url, source):
    out = []
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.string or node.get_text())
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        expanded = []
        for obj in stack:
            if isinstance(obj, dict) and isinstance(obj.get("@graph"), list):
                expanded.extend(obj["@graph"])
            else:
                expanded.append(obj)
        for obj in expanded:
            if not isinstance(obj, dict):
                continue
            types = obj.get("@type", [])
            if isinstance(types, str): types = [types]
            if not any(str(t).lower() in {"event", "socialevent", "exhibitionevent", "theaterevent", "music event".replace(" ","")} for t in types):
                continue
            dt = parse_date(obj.get("startDate"))
            if not dt:
                continue
            link = obj.get("url") or page_url
            if not link.startswith("http"): link = urljoin(page_url, link)
            loc = obj.get("location")
            loc_text = ""
            if isinstance(loc, dict):
                loc_text = loc.get("name","")
                addr = loc.get("address")
                if isinstance(addr, dict):
                    loc_text += " — " + ", ".join(str(addr.get(k)) for k in ("streetAddress","addressLocality","addressRegion") if addr.get(k))
            elif isinstance(loc, str):
                loc_text = loc
            desc = obj.get("description","")
            if loc_text: desc = f"{desc} Location: {loc_text}"
            x = item(obj.get("name"), link, dt, source, desc)
            if x: out.append(x)
    return out

def from_html(soup, page_url, source):
    out = []
    # Conservative fallback: only elements with an explicit date/time-ish attribute.
    selectors = ["time[datetime]", "[data-start-date]", "[data-date]", "[itemprop='startDate']"]
    seen = set()
    for sel in selectors:
        for el in soup.select(sel):
            raw = el.get("datetime") or el.get("data-start-date") or el.get("data-date") or el.get("content") or el.get_text(" ", strip=True)
            dt = parse_date(raw)
            if not dt: continue
            parent = el
            for _ in range(4):
                if parent.parent: parent = parent.parent
            a = parent.find("a", href=True) or el.find_parent("a", href=True)
            if not a: continue
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 3: continue
            link = urljoin(page_url, a["href"])
            key=(title,link)
            if key in seen: continue
            seen.add(key)
            x=item(title,link,dt,source,parent.get_text(" ",strip=True))
            if x: out.append(x)
    return out

def discover_feed_urls(response, page_url):
    """Find RSS/Atom feeds explicitly advertised by the page."""
    candidates = []
    soup = BeautifulSoup(response.text, "html.parser")

    for link in soup.find_all("link", href=True):
        rel = link.get("rel", [])
        rels = {str(x).lower() for x in rel} if isinstance(rel, list) else {str(rel).lower()}
        typ = (link.get("type") or "").lower().split(";")[0].strip()
        if "alternate" in rels and typ in {"application/rss+xml", "application/atom+xml"}:
            candidates.append(urljoin(page_url, link["href"]))

    # Some sites expose feed discovery through the HTTP Link header instead.
    link_header = response.headers.get("Link", "")
    for match in re.finditer(r"<([^>]+)> *;[^,]*rel=[\"\']?([^,\"\';]+)", link_header, re.I):
        href, rels = match.group(1), match.group(2)
        if "alternate" in {x.strip().lower() for x in rels.split()}:
            candidates.append(urljoin(page_url, href))

    # Keep order, remove duplicates.
    return list(dict.fromkeys(candidates))


def scrape(source):
    name, url = source["name"], source["url"]
    try:
        r=requests.get(url,headers=HEADERS,timeout=TIMEOUT)
        r.raise_for_status()

        # 1. If the supplied URL is itself a feed, parse it directly.
        parsed=feedparser.parse(r.content)
        if parsed.entries:
            return from_feed(url,name)

        soup=BeautifulSoup(r.text,"html.parser")

        # 2. Follow standards-based RSS/Atom autodiscovery links.
        #    This is the normal way a webpage advertises a feed without
        #    making the feed URL part of the human-facing page URL.
        for feed_url in discover_feed_urls(r, url):
            results=from_feed(feed_url,name)
            if results:
                return results

        # 3. Structured Event data embedded in the page.
        results=from_schema(soup,url,name)
        if results: return results

        # 4. Conservative HTML fallback.
        return from_html(soup,url,name)
    except Exception:
        return []

def main():
    with open("sources.json",encoding="utf-8") as f:
        sources=json.load(f)["sources"]
    now=datetime.now(timezone.utc)
    cutoff=now+timedelta(days=HORIZON_DAYS)
    all_items=[]
    for source in sources:
        all_items.extend(scrape(source))
    dedup={}
    for x in all_items:
        if now-timedelta(days=1) <= x["date"] <= cutoff:
            key=(x["title"].lower(),x["link"].rstrip("/"))
            if key not in dedup or x["date"] < dedup[key]["date"]:
                dedup[key]=x
    items=sorted(dedup.values(),key=lambda x:x["date"])[:MAX_ITEMS]
    rss=['<?xml version="1.0" encoding="UTF-8"?>','<rss version="2.0"><channel>',
         '<title>Bay Area Art &amp; Events</title>',
         '<link>https://github.com/sniffingelmers/Art-Events</link>',
         '<description>Upcoming Bay Area art, exhibitions, performances, screenings, and events.</description>',
         f'<lastBuildDate>{format_datetime(now)}</lastBuildDate>']
    for x in items:
        desc=html.escape(f'{x["source"]} — {x["description"]}', quote=False)
        rss += ['<item>',
                f'<title>{html.escape(x["title"])}</title>',
                f'<link>{html.escape(x["link"],quote=True)}</link>',
                f'<guid isPermaLink="true">{html.escape(x["link"],quote=True)}</guid>',
                f'<pubDate>{format_datetime(x["date"])}</pubDate>',
                f'<description>{desc}</description>',
                '</item>']
    rss.append('</channel></rss>')
    with open("feed.xml","w",encoding="utf-8") as f:
        f.write("\n".join(rss))
    print(f"Wrote {len(items)} events to feed.xml")

if __name__=="__main__":
    main()
