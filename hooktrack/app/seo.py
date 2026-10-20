"""Search-engine and social-share metadata for the public pages: canonical links, Open Graph, JSON-LD, robots.txt, sitemap.

The landing, terms and privacy pages are plain files with placeholders (__SITE__ and <!--JSONLD-->). They are filled in per
request so the canonical address and the structured data always match the site they are served from, and the prices in the
structured data come from the live plans instead of being typed twice.
"""
from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Request

STATIC = Path(__file__).parent.parent / "static"
OG_IMAGE = "/og.jpg"
DESCRIPTION = ("Track every TikTok, Instagram Reels and YouTube Shorts account in one dashboard. See which hooks beat each "
               "account's normal, then scale the winners. Free trial.")

# What a crawler should never fetch: the app itself, the API, private report links and uploaded files.
DISALLOW = ["/api/", "/app$", "/r/", "/uploads/", "/media/"]
PUBLIC_PAGES = [("/", "weekly", "1.0", "landing.html"), ("/terms", "yearly", "0.3", "terms.html"),
                ("/privacy", "yearly", "0.3", "privacy.html")]


def site_url(request: Optional[Request] = None) -> str:
    """The public address of this site, with no trailing slash. HOOKTRACK_PUBLIC_URL wins over the request's own host."""
    env = (os.environ.get("HOOKTRACK_PUBLIC_URL") or "").strip().rstrip("/")
    if env:
        return env
    return str(request.base_url).rstrip("/") if request is not None else ""


def _faq_jsonld(page: str) -> Optional[dict]:
    """A FAQPage built from the page's own <details> questions, so the markup can never drift from what people read."""
    items = []
    for q, a in re.findall(r"<details>\s*<summary>(.*?)</summary>\s*<p[^>]*>(.*?)</p>", page, re.S):
        clean = lambda t: html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", t))).strip()
        if clean(q) and clean(a):
            items.append({"@type": "Question", "name": clean(q),
                          "acceptedAnswer": {"@type": "Answer", "text": clean(a)}})
    return {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": items} if items else None


def _offers() -> Optional[dict]:
    try:
        from . import billing
        data = billing.public_pricing()
    except Exception:
        return None
    monthly = [p for p in data.get("plans", []) if p["interval"] == "month" and p["price_minor"] > 0]
    if not data.get("billing_enabled") or not monthly:
        return None
    prices = [p["price_minor"] / 100 for p in monthly]
    return {"@type": "AggregateOffer", "priceCurrency": monthly[0]["currency"], "lowPrice": f"{min(prices):.2f}",
            "highPrice": f"{max(prices):.2f}", "offerCount": len(monthly)}


def landing_jsonld(site: str, page: str) -> str:
    graph = [
        {"@context": "https://schema.org", "@type": "Organization", "name": "HookTrack", "url": site + "/",
         "logo": site + OG_IMAGE},
        {"@context": "https://schema.org", "@type": "WebSite", "name": "HookTrack", "url": site + "/"},
    ]
    app = {"@context": "https://schema.org", "@type": "SoftwareApplication", "name": "HookTrack",
           "applicationCategory": "BusinessApplication", "operatingSystem": "Web", "url": site + "/",
           "description": DESCRIPTION,
           "featureList": ["Multi-account TikTok, Instagram and YouTube tracking",
                           "Winner detection against each account's own normal",
                           "Scale, Tweak, Hold and Stop decisions", "Content calendar and scheduled publishing",
                           "Shareable client reports"]}
    offers = _offers()
    if offers:
        app["offers"] = offers
    graph.append(app)
    faq = _faq_jsonld(page)
    if faq:
        graph.append(faq)
    out = []
    for g in graph:
        # "</" inside JSON-LD would close the script block early
        out.append('<script type="application/ld+json">' + json.dumps(g, ensure_ascii=False).replace("</", "<\\/") + "</script>")
    return "\n".join(out)


def render(name: str, request: Request) -> str:
    """A public page with its placeholders filled in."""
    raw = (STATIC / name).read_text(encoding="utf-8")
    site = site_url(request)
    page = raw.replace("__SITE__", site)
    if "<!--JSONLD-->" in page:
        page = page.replace("<!--JSONLD-->", landing_jsonld(site, page) if name == "landing.html" else "")
    return page


def robots_txt(request: Request) -> str:
    lines = ["User-agent: *"] + [f"Disallow: {p}" for p in DISALLOW] + ["Allow: /", "", f"Sitemap: {site_url(request)}/sitemap.xml", ""]
    return "\n".join(lines)


def sitemap_xml(request: Request) -> str:
    site = site_url(request)
    rows = []
    for path, freq, prio, fname in PUBLIC_PAGES:
        try:
            mtime = datetime.fromtimestamp((STATIC / fname).stat().st_mtime, tz=timezone.utc).date().isoformat()
        except OSError:
            mtime = datetime.now(timezone.utc).date().isoformat()
        rows.append(f"  <url><loc>{html.escape(site + path)}</loc><lastmod>{mtime}</lastmod>"
                    f"<changefreq>{freq}</changefreq><priority>{prio}</priority></url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(rows) + "\n</urlset>\n")
