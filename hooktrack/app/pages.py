"""Public content pages: the comparison page, the alternatives page and the blog.

Each page is an HTML fragment in app/content/ with a JSON header on its first line:

    <!--meta {"title": "...", "description": "...", "date": "2026-10-20", "kind": "page|post", "slug": "..."} -->

The fragment is wrapped in static/content_page.html, which carries the canonical link, Open Graph tags and structured data
(Article or WebPage, BreadcrumbList and, when the page has <details> questions, FAQPage). Tokens like {{ht_from_price}} are
filled from the live plans so the numbers never drift from the pricing page.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Optional

from fastapi import Request

from . import seo

CONTENT = Path(__file__).parent / "content"
TEMPLATE = Path(__file__).parent.parent / "static" / "content_page.html"
META_RE = re.compile(r"^\s*<!--meta\s*(\{.*?\})\s*-->\s*", re.S)

# fixed pages: url path -> file name
PAGES = {
    "/compare": "compare.html",
    "/alternatives/postiz": "alternatives-postiz.html",
}


def _load(path: Path) -> Optional[tuple[dict, str]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = META_RE.match(raw)
    if not m:
        return None
    return json.loads(m.group(1)), raw[m.end():]


def posts() -> list[dict]:
    """Every blog post's metadata, newest first."""
    out = []
    d = CONTENT / "blog"
    if d.is_dir():
        for f in d.glob("*.html"):
            loaded = _load(f)
            if loaded:
                out.append(loaded[0])
    return sorted(out, key=lambda m: m.get("date", ""), reverse=True)


def _tokens(site: str) -> dict:
    low, trial = "$19", 7
    try:
        from . import billing
        data = billing.public_pricing()
        monthly = [p for p in data.get("plans", []) if p["interval"] == "month" and p["price_minor"] > 0]
        if monthly:
            low = f"${min(p['price_minor'] for p in monthly) / 100:g}"
        trial = data.get("trial_days") or trial
    except Exception:
        pass
    return {"{{site}}": site, "{{ht_from_price}}": low, "{{ht_trial_days}}": str(trial)}


def _crumbs(site: str, trail: list[tuple[str, str]]) -> dict:
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": n, "item": site + p} for i, (n, p) in enumerate(trail)]}


def _wrap(meta: dict, body: str, path: str, request: Request, trail: list[tuple[str, str]]) -> str:
    site = seo.site_url(request)
    for k, v in _tokens(site).items():
        body = body.replace(k, v)
    title, desc = meta["title"], meta["description"]
    graph = [_crumbs(site, trail)]
    if meta.get("kind") == "post":
        graph.append({"@context": "https://schema.org", "@type": "Article", "headline": title, "description": desc,
                      "datePublished": meta["date"], "dateModified": meta.get("updated", meta["date"]),
                      "mainEntityOfPage": site + path, "image": site + seo.OG_IMAGE,
                      "author": {"@type": "Organization", "name": "HookTrack"},
                      "publisher": {"@type": "Organization", "name": "HookTrack",
                                    "logo": {"@type": "ImageObject", "url": site + seo.OG_IMAGE}}})
    else:
        graph.append({"@context": "https://schema.org", "@type": "WebPage", "name": title, "description": desc,
                      "url": site + path, "dateModified": meta.get("updated", meta["date"])})
    faq = seo._faq_jsonld(body)
    if faq:
        graph.append(faq)
    ld = "\n".join('<script type="application/ld+json">' + json.dumps(g, ensure_ascii=False).replace("</", "<\\/") + "</script>"
                   for g in graph)
    crumbs = " &rsaquo; ".join(
        (f'<a href="{html.escape(p)}">{html.escape(n)}</a>' if i < len(trail) - 1 else f"<span>{html.escape(n)}</span>")
        for i, (n, p) in enumerate(trail))
    page = TEMPLATE.read_text(encoding="utf-8")
    subs = {"__TITLE__": html.escape(title), "__DESC__": html.escape(desc, quote=True), "__CANON__": site + path,
            "__SITE__": site, "__OGTYPE__": "article" if meta.get("kind") == "post" else "website",
            "__JSONLD__": ld, "__CRUMBS__": crumbs, "__BODY__": body,
            "__UPDATED__": html.escape(meta.get("updated", meta["date"]))}
    for k, v in subs.items():
        page = page.replace(k, v)
    return page


def render_page(path: str, request: Request) -> Optional[str]:
    fname = PAGES.get(path)
    loaded = _load(CONTENT / fname) if fname else None
    if not loaded:
        return None
    meta, body = loaded
    return _wrap(meta, body, path, request, [("Home", "/"), (meta.get("crumb", meta["title"]), path)])


def render_post(slug: str, request: Request) -> Optional[str]:
    if not re.fullmatch(r"[a-z0-9-]{1,80}", slug):
        return None
    loaded = _load(CONTENT / "blog" / f"{slug}.html")
    if not loaded:
        return None
    meta, body = loaded
    return _wrap(meta, body, f"/blog/{slug}", request,
                 [("Home", "/"), ("Blog", "/blog"), (meta.get("crumb", meta["title"]), f"/blog/{slug}")])


def render_blog_index(request: Request) -> str:
    cards = []
    for m in posts():
        cards.append(f'<article class="post-card"><h2><a href="/blog/{html.escape(m["slug"])}">{html.escape(m["title"])}</a></h2>'
                     f'<p class="meta">{html.escape(m["date"])} &middot; {html.escape(str(m.get("read", "5 min read")))}</p>'
                     f'<p>{html.escape(m["description"])}</p></article>')
    body = ('<h1>The HookTrack blog</h1><p class="lead">Plain-English guides for people running short-form video accounts: '
            'how to tell what worked, and what to make next.</p>' + "".join(cards))
    meta = {"title": "HookTrack Blog: Short-Form Video Analytics Guides", "date": "2026-10-20", "kind": "page",
            "description": "Plain-English guides on hooks, winners and running many TikTok, Instagram and YouTube Shorts accounts."}
    return _wrap(meta, body, "/blog", request, [("Home", "/"), ("Blog", "/blog")])


def sitemap_entries() -> list[tuple[str, str, str, str]]:
    """(path, lastmod, changefreq, priority) for every content page, for sitemap.xml."""
    out = []
    for path, fname in PAGES.items():
        loaded = _load(CONTENT / fname)
        if loaded:
            out.append((path, loaded[0].get("updated", loaded[0]["date"]), "monthly", "0.8"))
    ps = posts()
    if ps:
        out.append(("/blog", ps[0].get("updated", ps[0]["date"]), "weekly", "0.6"))
    for m in ps:
        out.append((f"/blog/{m['slug']}", m.get("updated", m["date"]), "monthly", "0.7"))
    return out
