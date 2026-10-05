#!/usr/bin/env python3
import json, re, hashlib
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from html import unescape
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
import feedparser
from dateutil import parser as dateparser
from xml.etree.ElementTree import Element, SubElement, tostring

UA = "Art-Events-RSS/1.0 (+https://github.com/sniffingelmers/Art-Events)"
TIMEOUT = 20
MAX_ITEMS_PER_SOURCE = 40
DAYS_AHEAD = 180

DATE_PATTERNS = [
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\\s+\\d{1,2}(?:,\\s*\\d{4})?",
    r"\\b\\d{1,2}/\\d{1,2}/\\d{2,4}\\b",
    r"\\b\\d{4}-\\d{2}-\\d{2}\\b"
]

def clean(s):
    return re.sub(r"\\s+", " ", unescape(s or "")).strip()

def get(url):
    return requests.get(url, headers={"User-Agent":UA}, timeout=TIMEOUT)

def parse_date(text):
    text = clean(text)
    for p in DATE_PATTERNS:
        m = re.search(p, text, re.I)
        if m:
            try:
                d = dateparser.parse(m.group(0), fuzzy=True)
                if d:
                    if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
                    return d
            except Exception:
                pass
    return None

def from_feed(source, url, parsed):
    out=[]
    for e in parsed.entries[:MAX_ITEMS_PER_SOURCE]:
        title=clean(getattr(e,"title",""))
        link=getattr(e,"link",url)
        summary=clean(getattr(e,"summary",getattr(e,"description","")))
        raw_date=getattr(e,"published",getattr(e,"updated",""))
        d=None
        try: d=dateparser.parse(raw_date, fuzzy=True) if raw_date else None
        except Exception: d=None
        if d and d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
        out.append({"title":title or source,"source":source,"link":link,"description":summary[:1200],"date":d})
    return out

def html_events(source,url,html):
    soup=BeautifulSoup(html,"html.parser")
    # Prefer Schema.org Event JSON-LD, which is much more reliable than guessing from page text.
    out=[]
    for tag in soup.find_all("script",{"type":"application/ld+json"}):
        try:
            data=json.loads(tag.string or tag.get_text())
        except Exception: continue
        nodes=data if isinstance(data,list) else data.get("@graph",[]) if isinstance(data,dict) else []
        if isinstance(data,dict) and data.get("@type")=="Event": nodes=[data]
        for x in nodes:
            if not isinstance(x,dict) or "Event" not in str(x.get("@type","")): continue
            name=clean(x.get("name"))
            if not name: continue
            d=None
            for k in ("startDate","endDate"):
                if x.get(k):
                    try: d=dateparser.parse(str(x[k])); break
                    except Exception: pass
            loc=x.get("location")
            locs=""
            if isinstance(loc,dict):
                locs=clean(loc.get("name",""))
                addr=loc.get("address")
                if isinstance(addr,dict): locs=clean((locs+" "+clean(addr.get("streetAddress",""))+" "+clean(addr.get("addressLocality",""))))
                elif isinstance(addr,str): locs=clean(locs+" "+addr)
            desc=clean(x.get("description",""))
            link=x.get("url") or url
            out.append({"title":name,"source":source,"link":urljoin(url,link),"description":(desc+" "+locs).strip()[:1200],"date":d})
    if out: return out[:MAX_ITEMS_PER_SOURCE]

    # Fallback: inspect likely event/article cards. This intentionally errs on the side of fewer, cleaner items.
    for a in soup.select("article, .event, .events, [class*='event'], [class*='Event'], .card"):
        text=clean(a.get_text(" ",strip=True))
        if len(text)<20 or len(text)>1200: continue
        d=parse_date(text)
        if not d: continue
        h=a.find(["h1","h2","h3","h4","h5"])
        title=clean(h.get_text(" ",strip=True)) if h else ""
        if not title: title=clean(text[:180])
        href=""
        if a.find("a",href=True): href=urljoin(url,a.find("a",href=True)["href"])
        out.append({"title":title,"source":source,"link":href or url,"description":text[:1200],"date":d})
    return out[:MAX_ITEMS_PER_SOURCE]

def fetch_source(s):
    source,url=s["name"],s["url"]
    try:
        r=get(url); r.raise_for_status()
        ctype=r.headers.get("content-type","").lower()
        if "xml" in ctype or "rss" in ctype or "atom" in ctype or url.lower().endswith((".xml",".rss")):
            return from_feed(source,url,feedparser.parse(r.content))
        return html_events(source,url,r.text)
    except Exception as e:
        print(f"[WARN] {source}: {e}")
        return []

def main():
    with open("sources.json",encoding="utf-8") as f: cfg=json.load(f)
    now=datetime.now(timezone.utc)
    cutoff=now+timedelta(days=DAYS_AHEAD)
    items=[]
    for s in cfg["sources"]:
        items.extend(fetch_source(s))

    dedup={}
    for x in items:
        d=x.get("date")
        if not d: continue
        if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
        if d < now-timedelta(days=2) or d > cutoff: continue
        key=hashlib.sha1((clean(x["title"]).lower()+"|"+x["link"]).encode()).hexdigest()
        dedup[key]=x
    items=sorted(dedup.values(),key=lambda x:x["date"])[:300]

    rss=Element("rss",{"version":"2.0"})
    ch=SubElement(rss,"channel")
    SubElement(ch,"title").text="Bay Area Art & Events"
    SubElement(ch,"link").text="https://github.com/sniffingelmers/Art-Events"
    SubElement(ch,"description").text="Automatically collected Bay Area art, culture, gallery, music, film, and event listings."
    SubElement(ch,"lastBuildDate").text=format_datetime(now)
    for x in items:
        it=SubElement(ch,"item")
        SubElement(it,"title").text=clean(x["title"])
        SubElement(it,"link").text=x["link"]
        SubElement(it,"guid",{"isPermaLink":"false"}).text=hashlib.sha1((x["source"]+"|"+x["title"]+"|"+x["link"]).encode()).hexdigest()
        desc=clean(x["description"])
        SubElement(it,"description").text=f"{x['source']} — {desc}" if desc else x["source"]
        SubElement(it,"pubDate").text=format_datetime(x["date"])
        SubElement(it,"category").text=x["source"]
    xml=tostring(rss,encoding="utf-8",xml_declaration=True).decode()
    with open("feed.xml","w",encoding="utf-8") as f: f.write(xml)
    print(f"Wrote {len(items)} upcoming items.")

if __name__=="__main__":
    main()
