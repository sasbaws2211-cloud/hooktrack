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
    "/tools": "tools.html",
}

TOOL_BOX = (
    '<div class="tool" id="tool" data-platform="__P__">'
    '<label for="views">Paste the views of your last videos, one per line</label>'
    '<p class="hint">Plain numbers work (12400, 12,400, 12.4k). To label a video, put a title first: Morning routine, 18400.</p>'
    '<textarea id="views" rows="10" spellcheck="false" placeholder="18400&#10;9600&#10;52300&#10;8700&#10;11200&#10;4100&#10;9900"></textarea>'
    '<div class="tool-row"><label class="order" for="order">First line is my <select id="order"><option value="newest">newest video</option>'
    '<option value="oldest">oldest video</option></select></label>'
    '<button type="button" class="btn primary" id="go">Check my videos</button>'
    '<button type="button" class="btn" id="sample">Use sample data</button>'
    '<button type="button" class="btn" id="clear">Clear</button></div>'
    '<div class="tool-out" id="out" aria-live="polite" hidden></div></div>'
    '<script src="/tool-analyzer.js" defer></script>')
BESTTIME_BOX = (
    '<div class="tool" id="besttime" data-platform="__P__">'
    '<label for="bt-data">Paste your videos\' publish date, time and views, one per line</label>'
    '<p class="hint">Format: 2026-10-04 18:30, 4200. Include the year, and use the time zone you plan your posting in. 20 or more videos gives a reliable answer.</p>'
    '<textarea id="bt-data" rows="10" spellcheck="false" placeholder="2026-10-02 18:30, 4200&#10;2026-10-04 10:00, 3100&#10;2026-10-07 12:15, 5200"></textarea>'
    '<div class="tool-row"><button type="button" class="btn primary" id="bt-go">Find my best time</button>'
    '<button type="button" class="btn" id="bt-sample">Use sample data</button>'
    '<button type="button" class="btn" id="bt-clear">Clear</button></div>'
    '<div class="tool-out" id="bt-out" aria-live="polite" hidden></div></div>'
    '<script src="/tool-besttime.js" defer></script>')
REPURPOSE_BOX = (
    '<div class="tool" id="repurpose" data-from="__F__" data-to="__T__">'
    '<div class="tool-row rp-pick"><label for="rp-from">Video is from</label><select id="rp-from"></select>'
    '<label for="rp-to">Posting it on</label><select id="rp-to"></select>'
    '<button type="button" class="btn" id="rp-reset">Start over</button></div>'
    '<div id="rp-list"></div><div id="rp-result" aria-live="polite"></div></div>'
    '<script src="/tool-repurpose.js" defer></script>')
SPECS_BOX = (
    '<div class="tool" id="specs">'
    '<p class="hint">Enter the length, the size, or both. Length like 45 or 1:30. Pick a common size or type your own.</p>'
    '<div class="tool-row sp-inputs"><label for="sp-dur">Length</label><input id="sp-dur" inputmode="text" placeholder="e.g. 45 or 1:30" autocomplete="off">'
    '<label for="sp-preset">Size</label><select id="sp-preset"><option value="">Pick a size (optional)</option>'
    '<option value="1080x1920">1080 \u00d7 1920 (9:16 vertical)</option><option value="1080x1350">1080 \u00d7 1350 (4:5)</option>'
    '<option value="1080x1080">1080 \u00d7 1080 (square)</option><option value="1920x1080">1920 \u00d7 1080 (horizontal)</option></select>'
    '<label for="sp-w">Width</label><input id="sp-w" type="number" min="1" inputmode="numeric" placeholder="1080">'
    '<label for="sp-h">Height</label><input id="sp-h" type="number" min="1" inputmode="numeric" placeholder="1920"></div>'
    '<div class="tool-row"><button type="button" class="btn primary" id="sp-go">Check my video</button>'
    '<button type="button" class="btn" id="sp-clear">Clear</button></div>'
    '<div class="tool-out" id="sp-out" aria-live="polite" hidden></div></div>'
    '<script src="/tool-specs.js" defer></script>')
TOOL_RE = re.compile(r"\{\{(tool|besttime|repurpose|specs):([A-Za-z -]+)\}\}")


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
    def _box(m):
        kind, arg = m.group(1), m.group(2)
        if kind == "specs":
            return SPECS_BOX
        if kind == "repurpose":
            f, _, t = arg.partition("-")
            return REPURPOSE_BOX.replace("__F__", f).replace("__T__", t)
        return (TOOL_BOX if kind == "tool" else BESTTIME_BOX).replace("__P__", arg)

    body = TOOL_RE.sub(_box, body)
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


def tool_slugs() -> list[str]:
    d = CONTENT / "tools"
    return sorted(f.stem for f in d.glob("*.html")) if d.is_dir() else []


def render_tool(slug: str, request: Request) -> Optional[str]:
    if not re.fullmatch(r"[a-z0-9-]{1,80}", slug):
        return None
    loaded = _load(CONTENT / "tools" / f"{slug}.html")
    if not loaded:
        return None
    meta, body = loaded
    return _wrap(meta, body, f"/tools/{slug}", request,
                 [("Home", "/"), ("Free tools", "/tools"), (meta.get("crumb", meta["title"]), f"/tools/{slug}")])


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
    for slug in tool_slugs():
        loaded = _load(CONTENT / "tools" / f"{slug}.html")
        if loaded:
            out.append((f"/tools/{slug}", loaded[0].get("updated", loaded[0]["date"]), "monthly", "0.8"))
    ps = posts()
    if ps:
        out.append(("/blog", ps[0].get("updated", ps[0]["date"]), "weekly", "0.6"))
    for m in ps:
        out.append((f"/blog/{m['slug']}", m.get("updated", m["date"]), "monthly", "0.7"))
    return out
