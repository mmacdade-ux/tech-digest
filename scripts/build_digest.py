#!/usr/bin/env python3
"""
Daily tech-news digest — fetches RSS/Atom + Hacker News + Reddit, deduplicates
against seen.json, builds an email-safe newsletter, and sends it via Resend.

Runs on a GitHub Actions ubuntu runner (stdlib only, no pip installs) where
outbound egress is open. Email send needs env var RESEND_API_KEY; pass --dry-run
to build the archive page without sending.

  python3 scripts/build_digest.py            # build + send
  python3 scripts/build_digest.py --dry-run  # build only, no send
"""

import sys
import os
import re
import json
import time
import html as htmllib
import hashlib
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sources as S  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEEN_PATH = ROOT / "seen.json"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


# ---------- fetch + parse helpers ----------

def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _child(elem, name):
    for c in elem:
        if _local(c.tag) == name:
            return c
    return None


def _text(elem, name):
    c = _child(elem, name)
    return (c.text or "").strip() if c is not None and c.text else ""


def _link(elem):
    # RSS: <link>url</link>; Atom: <link href=".." rel="alternate"/>
    fallback = ""
    for c in elem:
        if _local(c.tag) != "link":
            continue
        href = c.get("href")
        if href:
            if c.get("rel", "alternate") in ("alternate", ""):
                return href
            fallback = fallback or href
        elif c.text and c.text.strip():
            return c.text.strip()
    return fallback


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def clean_text(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", "", s or "")
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def blurbify(s, n=200):
    s = clean_text(s)
    if len(s) > n:
        s = s[:n].rsplit(" ", 1)[0] + "…"
    return s


def parse_feed(xml_bytes, source):
    """Return normalized items: {title, link, source, date(datetime|None), blurb}."""
    out = []
    root = ET.fromstring(xml_bytes)
    for e in root.iter():
        if _local(e.tag) not in ("item", "entry"):
            continue
        title = clean_text(_text(e, "title"))
        link = _link(e)
        if not title or not link:
            continue
        desc = _text(e, "description") or _text(e, "summary") or _text(e, "content")
        date = parse_date(_text(e, "pubDate") or _text(e, "published") or _text(e, "updated"))
        out.append({"title": title, "link": link, "source": source,
                    "date": date, "blurb": blurbify(desc)})
    return out


def key_for(link):
    return "url:" + hashlib.sha1(link.encode("utf-8")).hexdigest()


def fresh(item, now, hours):
    d = item["date"]
    if d is None:
        return True  # keep undated items; dedup still prevents repeats
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    age = (now - d).total_seconds()
    return -86400 <= age <= hours * 3600  # allow slight clock skew into the future


def sort_key(item):
    d = item["date"]
    if d is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ---------- gather ----------

def gather(seen, now, failures):
    """Return list of (section_name, [items]) with dedup + freshness + caps applied.

    Mutates `seen` only for items actually included (so capped-out items can
    appear on a later run). Appends '<source>' to `failures` on fetch error.
    """
    sections = []

    def collect(feeds):
        items = []
        for name, url in feeds:
            try:
                items += parse_feed(http_get(url), name)
            except (urllib.error.URLError, ET.ParseError, ValueError, OSError) as ex:
                failures.append(f"{name} ({ex.__class__.__name__})")
        return items

    def finalize(name, items):
        picked, seen_run = [], set()
        items = [i for i in items if fresh(i, now, S.FRESHNESS_HOURS)]
        items.sort(key=sort_key, reverse=True)
        for it in items:
            k = key_for(it["link"])
            if k in seen or k in seen_run:
                continue
            seen_run.add(k)
            picked.append(it)
            if len(picked) >= S.SECTION_CAP:
                break
        for it in picked:
            seen[key_for(it["link"])] = {
                "title": it["title"], "source": it["source"],
                "first_seen": now.strftime("%Y-%m-%d"),
            }
        if picked:
            sections.append((name, picked))

    for section_name, feeds in S.RSS_SECTIONS:
        finalize(section_name, collect(feeds))

    # Community buzz: HN + Reddit (best-effort, throttled)
    community = []
    try:
        community += parse_feed(http_get(S.HN_FEED), "Hacker News")
    except (urllib.error.URLError, ET.ParseError, ValueError, OSError) as ex:
        failures.append(f"Hacker News ({ex.__class__.__name__})")
    time.sleep(2)  # be polite to Reddit to dodge 429
    for attempt in range(2):
        try:
            community += parse_feed(http_get(S.REDDIT_FEED), "Reddit")
            break
        except (urllib.error.URLError, ET.ParseError, ValueError, OSError) as ex:
            if attempt == 0:
                time.sleep(5)
                continue
            failures.append(f"Reddit ({ex.__class__.__name__})")
    finalize("Community buzz", community)

    return sections


# ---------- render ----------

def fmt_date(d):
    if d is None:
        return ""
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.strftime("%-d %b")


def esc(s):
    return htmllib.escape(s or "", quote=True)


def render_email(date, sections, failures, n):
    blocks = []
    for name, items in sections:
        rows = []
        for it in items:
            tag = " · ".join(x for x in [esc(it["source"]), fmt_date(it["date"])] if x)
            blurb = (f'<div class="muted" style="font-size:14px;color:#5a5a66;'
                     f'margin:2px 0 0">{esc(it["blurb"])}</div>') if it["blurb"] else ""
            rows.append(
                f'<div style="margin:0 0 16px">'
                f'<a href="{esc(it["link"])}" style="font-size:16px;font-weight:600;'
                f'color:#1a1a1f;text-decoration:none">{esc(it["title"])}</a>'
                f'{blurb}'
                f'<div class="muted" style="font-size:12px;color:#8a8a96;margin-top:3px">{tag}</div>'
                f'</div>')
        blocks.append(
            f'<h2 style="font-size:18px;margin:28px 0 12px;color:#1a1a1f">{esc(name)}</h2>'
            + "".join(rows))

    fail = (f'<p class="muted" style="font-size:12px;color:#8a8a96;margin-top:24px">'
            f'Sources unavailable today: {esc(", ".join(failures))}.</p>') if failures else ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Tech digest — {date}</title>
<style>
  body {{ margin:0; background:#f4f4f6; }}
  @media (prefers-color-scheme: dark) {{
    body, .wrap {{ background:#0d1117 !important; }}
    .card {{ background:#161b22 !important; border-color:#30363d !important; }}
    h1, h2, a.title {{ color:#e6edf3 !important; }}
    .muted {{ color:#9aa4b2 !important; }}
    .hr {{ border-color:#30363d !important; }}
  }}
</style>
</head>
<body style="margin:0;background:#f4f4f6;">
<div class="wrap" style="background:#f4f4f6;padding:24px 12px;">
  <div class="card" style="max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #d9d9e0;border-radius:12px;padding:28px 26px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.55;">
    <h1 style="font-size:22px;margin:0 0 4px;color:#1a1a1f;">Tech digest</h1>
    <div class="muted" style="font-size:13px;color:#8a8a96;margin-bottom:8px;">{date} · {n} stories</div>
    <hr class="hr" style="border:none;border-top:1px solid #e3e3e8;margin:8px 0 4px;">
    {"".join(blocks)}
    {fail}
    <hr class="hr" style="border:none;border-top:1px solid #e3e3e8;margin:24px 0 10px;">
    <div class="muted" style="font-size:12px;color:#8a8a96;">
      Built automatically from public RSS/Reddit/Hacker News feeds.
    </div>
  </div>
</div>
</body>
</html>"""


PAGE_TOKENS = """
:root{color-scheme:light dark;--bg:#f4f4f6;--surface:#fff;--text:#1a1a1f;--muted:#5a5a66;--border:#d9d9e0;--accent:#2f7fc1;}
[data-theme="dark"]{--bg:#0d1117;--surface:#161b22;--text:#e6edf3;--muted:#9aa4b2;--border:#30363d;--accent:#58a6ff;}
@media (prefers-color-scheme:dark){:root:not([data-theme]){--bg:#0d1117;--surface:#161b22;--text:#e6edf3;--muted:#9aa4b2;--border:#30363d;--accent:#58a6ff;}}
*{box-sizing:border-box;}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:680px;margin:0 auto;padding:32px 20px;background:var(--bg);color:var(--text);line-height:1.55;}
header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:4px;}
h1{font-size:1.5rem;margin:0 auto 0 0;}
h2{font-size:1.15rem;margin:28px 0 12px;}
.meta{color:var(--muted);font-size:.85rem;margin-bottom:16px;}
.item{margin:0 0 16px;}
.item a.title{font-size:1.02rem;font-weight:600;color:var(--text);text-decoration:none;}
.item a.title:hover{color:var(--accent);}
.blurb{color:var(--muted);font-size:.92rem;margin:2px 0 0;}
.tag{color:var(--muted);font-size:.78rem;margin-top:3px;opacity:.85;}
.fail{color:var(--muted);font-size:.8rem;margin-top:24px;}
#theme-toggle{cursor:pointer;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:999px;padding:.3rem .75rem;font-size:.82rem;font-weight:600;}
#theme-toggle:focus-visible{outline:3px solid var(--accent);outline-offset:2px;}
footer{margin-top:32px;padding-top:14px;border-top:1px solid var(--border);font-size:.82rem;color:var(--muted);}
"""


def render_page(date, sections, failures, n):
    blocks = []
    for name, items in sections:
        rows = []
        for it in items:
            tag = " · ".join(x for x in [esc(it["source"]), fmt_date(it["date"])] if x)
            blurb = f'<div class="blurb">{esc(it["blurb"])}</div>' if it["blurb"] else ""
            rows.append(
                f'<div class="item"><a class="title" href="{esc(it["link"])}">{esc(it["title"])}</a>'
                f'{blurb}<div class="tag">{tag}</div></div>')
        blocks.append(f"<h2>{esc(name)}</h2>" + "".join(rows))
    body = "".join(blocks) or '<p class="blurb">No new stories today.</p>'
    fail = f'<p class="fail">Sources unavailable today: {esc(", ".join(failures))}.</p>' if failures else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tech digest — {date} — {n} stories</title>
<script>(function(){{var s=localStorage.getItem("theme");var t=s||(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");document.documentElement.dataset.theme=t;}})();</script>
<style>{PAGE_TOKENS}</style>
</head>
<body>
<header>
<h1>Tech digest</h1>
<button id="theme-toggle" aria-label="Toggle dark mode">Dark mode</button>
</header>
<p class="meta">{date} · {n} stories</p>
{body}
{fail}
<footer>Built automatically from public RSS / Reddit / Hacker News feeds.</footer>
<script>
var b=document.getElementById("theme-toggle");function s(){{b.textContent=document.documentElement.dataset.theme==="dark"?"Light mode":"Dark mode";}}s();
b.addEventListener("click",function(){{var n=document.documentElement.dataset.theme==="dark"?"light":"dark";document.documentElement.dataset.theme=n;localStorage.setItem("theme",n);s();}});
</script>
</body>
</html>"""


# ---------- send ----------

def send_resend(subject, html):
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        raise SystemExit("RESEND_API_KEY not set — cannot send.")
    payload = json.dumps({
        "from": S.FROM_ADDR, "to": [S.RECIPIENT],
        "subject": subject, "html": html,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"Resend: HTTP {r.status} {r.read().decode()[:200]}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print(f"Resend send FAILED — HTTP {e.code}. Response:\n{body}", file=sys.stderr)
        raise SystemExit(1)


# ---------- main ----------

def main():
    dry_run = "--dry-run" in sys.argv
    now = datetime.now(timezone.utc)
    date = now.strftime("%Y-%m-%d")

    seen = {}
    if SEEN_PATH.exists():
        try:
            seen = json.loads(SEEN_PATH.read_text() or "{}")
        except json.JSONDecodeError:
            seen = {}

    failures = []
    sections = gather(seen, now, failures)
    n = sum(len(items) for _, items in sections)

    page = render_page(date, sections, failures, n)
    (ROOT / "latest.html").write_text(page)
    (ROOT / f"digest-{date}.html").write_text(page)
    SEEN_PATH.write_text(json.dumps(seen, indent=2, sort_keys=True))

    print(f"{date}: {n} new stories across {len(sections)} sections; "
          f"{len(seen)} tracked; failures={failures or 'none'}")

    if dry_run:
        (ROOT / "email-preview.html").write_text(render_email(date, sections, failures, n))
        print("DRY RUN — archive + email-preview.html written, email NOT sent.")
        return
    if n == 0:
        print("0 new stories — skipping email.")
        return
    send_resend(f"Tech digest — {date} — {n} stories", render_email(date, sections, failures, n))
    print("Email sent.")


if __name__ == "__main__":
    main()
