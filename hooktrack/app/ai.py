"""LLM features, configured per workspace: Anthropic, OpenAI, or any OpenAI-compatible endpoint."""
import base64
import hashlib
import ipaddress
import json
import os
import re
import socket
import time
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlparse

import httpx

from . import crypto, db

PROVIDERS = {
    "anthropic": {
        "label": "Anthropic (Claude)", "needs_key": True, "needs_url": False,
        "default_model": os.environ.get("HOOKTRACK_MODEL", "claude-sonnet-5-5"),
        "suggestions": ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5", "claude-fable-5-1"],
        "env_key": "ANTHROPIC_API_KEY",
    },
    "openai": {
        "label": "OpenAI", "needs_key": True, "needs_url": False,
        "default_model": "gpt-4o", "suggestions": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"],
        "env_key": "OPENAI_API_KEY",
    },
    "compatible": {
        "label": "OpenAI-compatible (custom endpoint)", "needs_key": False, "needs_url": True,
        "default_model": "", "suggestions": [],
        "env_key": "",   # a server-wide key is never sent to a tenant-chosen URL
    },
}
DEFAULT_PROVIDER = "anthropic"
OPENAI_BASE = "https://api.openai.com/v1"
# kept for older imports/tests
DEFAULT_MODEL = PROVIDERS["anthropic"]["default_model"]
MODEL_SUGGESTIONS = PROVIDERS["anthropic"]["suggestions"]


class AIUnavailable(Exception):
    pass


@dataclass
class Config:
    api_key: str
    model: str
    source: str            # workspace | server | none
    provider: str = DEFAULT_PROVIDER
    base_url: str = ""

    @property
    def available(self) -> bool:
        if self.provider == "compatible":
            return bool(self.base_url and self.model)     # local servers often need no key
        return bool(self.api_key)


PLATFORM_KEY = "ai_platform"


def platform_config() -> dict:
    """{provider, model, base_url, key_hint, has_key} the platform admin saved, or {}."""
    from .sociavault import platform_get
    v = platform_get(PLATFORM_KEY)
    return dict(v) if isinstance(v, dict) else {}


def platform_status() -> dict:
    c = platform_config()
    provider = c.get("provider") if c.get("provider") in PROVIDERS else DEFAULT_PROVIDER
    spec = PROVIDERS[provider]
    cfg = _platform_resolved()
    return {"provider": provider, "model": c.get("model") or "", "effective_model": c.get("model") or spec["default_model"],
            "base_url": c.get("base_url") or "", "key_hint": c.get("key_hint") or "", "has_key": bool(c.get("key_enc")),
            "available": bool(cfg and cfg.available), "providers": {
                k: {"label": v["label"], "default_model": v["default_model"], "suggestions": v["suggestions"],
                    "needs_key": v["needs_key"], "needs_url": v["needs_url"]} for k, v in PROVIDERS.items()}}


def save_platform(provider: str, model: str = "", base_url: str = "", api_key: str = "", clear_key: bool = False) -> dict:
    from .sociavault import platform_set
    old = platform_config()
    provider = (provider or old.get("provider") or DEFAULT_PROVIDER).strip()
    if provider not in PROVIDERS:
        raise ValueError("Unknown provider")
    url = check_base_url(base_url) if provider == "compatible" else ""
    key = (api_key or "").strip()
    if key and (re.search(r"\s", key) or (provider != "compatible" and len(key) < 20)):
        raise ValueError("That does not look like a complete API key")
    model = (model or "").strip()
    if model and not re.fullmatch(r"[A-Za-z0-9._:/@-]{1,100}", model):
        raise ValueError("Model names may only contain letters, digits and . _ : / @ -")
    moved = provider != old.get("provider") or url != (old.get("base_url") or "")
    keep = old.get("key_enc") if not (clear_key or (moved and not key)) else ""
    hint = old.get("key_hint") if keep else ""
    if key:
        keep = crypto.encrypt(key)
        hint = key[:7] + "..." + key[-4:] if len(key) > 12 else "..." + key[-3:]
    platform_set(PLATFORM_KEY, {"provider": provider, "model": model, "base_url": url, "key_enc": keep or "",
                                "key_hint": hint or ""})
    return platform_status()


def _platform_resolved() -> "Config | None":
    c = platform_config()
    if not c:
        return None
    provider = c.get("provider") if c.get("provider") in PROVIDERS else DEFAULT_PROVIDER
    spec = PROVIDERS[provider]
    key = ""
    if c.get("key_enc"):
        try:
            key = crypto.decrypt(c["key_enc"]) or ""
        except Exception:
            key = ""
    base = c.get("base_url") or ""
    if not key:
        key = os.environ.get(spec["env_key"], "") if provider != "compatible" else ""
    cfg = Config(key, c.get("model") or spec["default_model"], "platform", provider, base)
    return cfg if cfg.available else None


def resolve(workspace_id: int) -> Config:
    """The platform's AI connection first; else (older setups) the workspace's own, then the server-wide key."""
    plat = _platform_resolved()
    if plat is not None:
        return plat
    row = db.one("SELECT api_key_enc, model, provider, base_url FROM workspace_ai WHERE workspace_id=%s",
                 (workspace_id,)) or {}
    provider = row.get("provider") or DEFAULT_PROVIDER
    if provider not in PROVIDERS:
        provider = DEFAULT_PROVIDER
    spec = PROVIDERS[provider]
    model = row.get("model") or spec["default_model"]
    base = row.get("base_url") or ""
    key = crypto.decrypt(row["api_key_enc"]) if row.get("api_key_enc") else ""
    if key or provider == "compatible":
        return Config(key, model, "workspace" if (key or base) else "none", provider, base)
    env = os.environ.get(spec["env_key"], "")
    return Config(env, model, "server" if env else "none", provider, base)


# ---------- outbound URL safety (tenants choose their own endpoint) ----------

def _resolve_host(host: str) -> list:
    return [i[4][0] for i in socket.getaddrinfo(host, None)]


def check_base_url(url: str) -> str:
    """Normalise a custom endpoint and refuse anything that points inside our network.

    Without this, any tenant could make the server call internal services (SSRF).
    Set HOOKTRACK_ALLOW_PRIVATE_AI_URLS=1 on a trusted single-tenant host to use e.g. a local Ollama.
    """
    url = (url or "").strip().rstrip("/")
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Enter a full URL starting with https:// (for example https://api.groq.com/openai/v1)")
    if u.username or u.password:
        raise ValueError("Don't put credentials in the URL; use the API key field")
    if os.environ.get("HOOKTRACK_ALLOW_PRIVATE_AI_URLS") != "1":
        try:
            addrs = _resolve_host(u.hostname)
        except OSError:
            raise ValueError(f"Can't resolve {u.hostname}")
        for a in addrs:
            ip = ipaddress.ip_address(a.split("%")[0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                    or ip.is_multicast or ip.is_unspecified):
                raise ValueError("That address is on a private network and is blocked. A server admin can allow it "
                                 "with HOOKTRACK_ALLOW_PRIVATE_AI_URLS=1")
    return url


# ---------- transport ----------

_clients: dict = {}


def _get_client(cfg: Config):
    k = hashlib.sha256(cfg.api_key.encode()).hexdigest()
    if k not in _clients:
        import anthropic
        if len(_clients) >= 32:
            _clients.clear()
        _clients[k] = anthropic.Anthropic(api_key=cfg.api_key)
    return _clients[k]


def _http_post(url, headers, body):
    # big reasoning models can take well over a minute for a long answer (e.g. a week plan)
    timeout = float(os.environ.get("HOOKTRACK_AI_TIMEOUT", "150") or 150)
    try:
        return httpx.post(url, headers=headers, json=body, timeout=timeout, follow_redirects=False)
    except httpx.TimeoutException:
        raise RuntimeError(f"the model did not answer within {timeout:.0f}s (raise HOOKTRACK_AI_TIMEOUT or pick a faster model)")


RETRY_STATUS = {429, 500, 502, 503, 504}   # the provider's hiccup, not a problem with our request
RETRY_DELAYS = (1.5, 4.0)                  # two retries, so three attempts in all


def _require(cfg: Config):
    if not cfg.available:
        raise AIUnavailable("AI isn't set up for this workspace. An admin can configure it under Settings → AI.")


def _openai_chat(cfg: Config, body: dict) -> dict:
    base = OPENAI_BASE
    if cfg.provider == "compatible":
        base = check_base_url(cfg.base_url)          # re-checked on every call, not just when saved
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    # newer OpenAI models reject max_tokens; most compatible servers only know max_tokens
    limit_key = "max_completion_tokens" if cfg.provider == "openai" else "max_tokens"
    body = {"model": cfg.model, limit_key: body.pop("max_tokens"), **body}
    r = _http_post(f"{base}/chat/completions", headers, body)
    for delay in RETRY_DELAYS:
        if r.status_code not in RETRY_STATUS:
            break
        try:                                # honour Retry-After when the provider sends one (capped)
            delay = min(float((getattr(r, "headers", None) or {}).get("retry-after", delay)), 10.0)
        except (TypeError, ValueError):
            pass
        time.sleep(delay)
        r = _http_post(f"{base}/chat/completions", headers, body)
    if r.status_code >= 400:
        try:
            detail = r.json().get("error", {})
            detail = detail.get("message") if isinstance(detail, dict) else detail
        except Exception:
            detail = r.text[:200]
        raise RuntimeError(f"HTTP {r.status_code}: {detail or 'request failed'}")
    return r.json()


def _to_openai_content(content):
    if isinstance(content, str):
        return content
    out = []
    for b in content:
        if b["type"] == "image":
            src = b["source"]
            out.append({"type": "image_url", "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}})
        else:
            out.append({"type": "text", "text": b["text"]})
    return out


def _json_from_text(text: str):
    """Fallback for servers that ignore forced tool calls and just answer in JSON."""
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def _call_tool(cfg, system, content, tool, max_tokens=4000):
    """Force a structured answer through a single tool call (works on every provider)."""
    _require(cfg)
    if cfg.provider == "anthropic":
        msg = _get_client(cfg).messages.create(
            model=cfg.model, max_tokens=max_tokens, system=system,
            tools=[tool], tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": content}],
        )
        for block in msg.content:
            if block.type == "tool_use":
                return block.input
        raise RuntimeError("Model returned no structured answer")

    def attempt(budget: int, nudge: str = ""):
        return _openai_chat(cfg, {
            "max_tokens": budget,
            "messages": [{"role": "system", "content": system + nudge},
                         {"role": "user", "content": _to_openai_content(content)}],
            "tools": [{"type": "function", "function": {
                "name": tool["name"], "description": tool.get("description", ""), "parameters": tool["input_schema"]}}],
            "tool_choice": {"type": "function", "function": {"name": tool["name"]}},
        })

    def extract(data):
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        for call in message.get("tool_calls") or []:
            try:
                return json.loads(call["function"]["arguments"]), choice.get("finish_reason")
            except (ValueError, KeyError):
                continue
        return _json_from_text(message.get("content") or ""), choice.get("finish_reason")

    # Reasoning models spend completion tokens thinking and sometimes run out before they call the tool, or answer in
    # prose. Retry once with a larger budget and an explicit instruction before giving up.
    result, finish = extract(attempt(max_tokens))
    if result is None:
        result, finish = extract(attempt(max_tokens * 2,
                                         "\n\nAnswer ONLY by calling the provided function. Keep any reasoning short."))
    if result is not None:
        return result
    why = " (the model ran out of tokens while thinking)" if finish == "length" else ""
    raise RuntimeError("Model returned no structured answer" + why + " (does this model support tool calling?)")


def _text(cfg, system, prompt, max_tokens=1500):
    _require(cfg)
    if cfg.provider == "anthropic":
        msg = _get_client(cfg).messages.create(
            model=cfg.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if b.type == "text").strip()
    def ask(budget):
        data = _openai_chat(cfg, {"max_tokens": budget, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": prompt}]})
        choice = (data.get("choices") or [{}])[0]
        # a thinking model that spends its whole budget reasoning returns content: null, not an empty string
        return ((choice.get("message") or {}).get("content") or "").strip(), choice.get("finish_reason")

    text, finish = ask(max_tokens)
    if not text and finish == "length":
        text, _ = ask(max_tokens * 3)               # it ran out of room while thinking: give it room once
    return text


def chat(cfg, system: str, messages: list, max_tokens: int = 700) -> str:
    """A multi-turn text conversation: messages are [{"role": "user"|"assistant", "content": str}], ending on the user."""
    _require(cfg)
    if cfg.provider == "anthropic":
        msg = _get_client(cfg).messages.create(model=cfg.model, max_tokens=max_tokens, system=system, messages=messages)
        return "".join(b.text for b in msg.content if b.type == "text").strip()

    def go(budget):
        data = _openai_chat(cfg, {"max_tokens": budget,
                                  "messages": [{"role": "system", "content": system}, *messages]})
        choice = (data.get("choices") or [{}])[0]
        return ((choice.get("message") or {}).get("content") or "").strip(), choice.get("finish_reason")

    text, finish = go(max_tokens)
    if not text and finish == "length":
        text, _ = go(max_tokens * 3)                     # a thinking model used its whole budget: give it room once
    return text


def suggest_niche(cfg, name: str, bio: str) -> str:
    """A one to three word niche label for an account ("fitness", "skincare tips"), or "" when it can't tell.

    The answer is asked for as `NICHE: <label>` and parsed out of the reply: a "thinking" model puts its reasoning
    in the reply too, so taking the whole reply (or its first line) would return the reasoning, not the label."""
    text = _text(cfg, "You label social media accounts by niche. Decide the account's niche from its name and bio. "
                      "Your final answer must be one line in exactly this form: NICHE: <label>, where <label> is one to "
                      "three lowercase words (examples: NICHE: fitness / NICHE: home cooking / NICHE: personal finance). "
                      "If there is no clue at all, answer: NICHE: unknown",
                 f"Account name: {name[:80]}\nBio: {bio[:300]}", max_tokens=600)
    found = re.findall(r"NICHE:\s*([^\n]+)", text or "", flags=re.I)
    if not found:
        return ""
    label = " ".join(re.sub(r"[^a-z0-9 &'-]", " ", found[-1].lower()).split())[:40]
    return "" if (not label or label == "unknown" or len(label.split()) > 4) else label


def test_connection(cfg: Config) -> str:
    """Cheapest possible round trip; returns the model's reply or raises."""
    reply = _text(cfg, "Reply with the single word OK.", "ping", max_tokens=64)
    # a connection that works but whose model thinks silently is still a working connection
    return reply or "(connected: the model answered but showed no text)"


# ---------- matching helpers (pure, unit-tested) ----------

def norm_handle(s: str) -> str:
    return re.sub(r"[^a-z0-9_.]", "", (s or "").lower().lstrip("@"))


def match_account(label: str, accounts: list):
    """Match '@account_1a', 'account_1a' or shorthand '1a' to an account row."""
    n = norm_handle(label)
    if not n:
        return None
    for a in accounts:
        if norm_handle(a["handle"]) == n:
            return a
    for tails in (
        [a for a in accounts if norm_handle(a["handle"]).endswith("_" + n)],   # '1a' -> account_1a
        [a for a in accounts if len(n) >= 2 and norm_handle(a["handle"]).endswith(n)],
    ):
        if len(tails) == 1:
            return tails[0]
        if tails:
            return None   # ambiguous: let the user fix it rather than guess
    return None


def parse_count(v):
    """'85k' -> 85000, '1.2M' -> 1200000, '1,177' -> 1177."""
    if isinstance(v, (int, float)):
        return int(v)
    m = re.fullmatch(r"\s*([\d.,]+)\s*([kKmM]?)\s*", str(v))
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    mult = {"": 1, "k": 1_000, "m": 1_000_000}[m.group(2).lower()]
    return int(round(num * mult))


# ---------- features ----------

VIEWS_TOOL = {
    "name": "record_views",
    "description": "Record per-account daily view counts read from the user's input.",
    "input_schema": {
        "type": "object",
        "properties": {
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "account": {"type": "string", "description": "Account handle or shorthand as written"},
                        "day": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                        "views": {"type": "integer"},
                        "note": {"type": "string", "description": "Anything uncertain about this entry"},
                    },
                    "required": ["account", "day", "views"],
                },
            },
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["entries"],
    },
}


def recent_calendar(today: date, days: int = 14) -> str:
    """'Fri 2026-10-09 (today), Thu 2026-10-08 (yesterday), ...' so a model never has to do weekday arithmetic."""
    out = []
    for i in range(days):
        d = today - timedelta(days=i)
        tag = " (today)" if i == 0 else " (yesterday)" if i == 1 else ""
        out.append(f"{d.strftime('%a')} {d.isoformat()}{tag}")
    return ", ".join(out)


def extract_views(cfg, text: str, images: list, accounts: list, today: date) -> dict:
    handles = ", ".join(a["handle"] for a in accounts) or "(none yet)"
    system = (
        "You read social-media view counts from screenshots or pasted text and record them. "
        f"Today is {today.isoformat()} ({today.strftime('%A')}). Known accounts: {handles}. "
        "Resolve relative days ('yesterday', 'Monday') to ISO dates, assuming the most recent such day "
        f"(today counts when it matches). Use this calendar instead of computing weekdays yourself:\n{recent_calendar(today)}\n"
        "Expand 85k -> 85000. If a screenshot shows one account, use its handle. "
        "Never invent numbers; skip anything unreadable and add a warning instead."
    )
    content = []
    for media_type, data in images:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": media_type, "data": base64.b64encode(data).decode()}})
    content.append({"type": "text", "text": text.strip() or "Extract the view counts from the image(s)."})
    out = _call_tool(cfg, system, content, VIEWS_TOOL)

    rows, unmatched = [], []
    for e in out.get("entries", []):
        acc = match_account(e.get("account", ""), accounts)
        n = parse_count(e.get("views"))
        try:
            day = date.fromisoformat(str(e.get("day", ""))[:10])
        except ValueError:
            day = None
        if acc is None or n is None or day is None:
            unmatched.append(e)
            continue
        rows.append({"account_id": acc["id"], "handle": acc["handle"], "day": day.isoformat(),
                     "views": n, "note": e.get("note", "")})
    return {"entries": rows, "unmatched": unmatched, "warnings": out.get("warnings", [])}


BLUEPRINT_TOOL = {
    "name": "describe_blueprint",
    "description": "Describe how a winning short-form video is built so it can be imitated.",
    "input_schema": {
        "type": "object",
        "properties": {
            "format": {"type": "string", "description": "e.g. talking head, voiceover over b-roll, skit, tutorial, listicle"},
            "tone": {"type": "string"},
            "structure": {"type": "string", "description": "1-3 sentences: how the video opens, builds and ends"},
            "keep": {"type": "array", "items": {"type": "string"},
                     "description": "The mechanics worth keeping (e.g. 'opens with a direct question', 'one concrete number')"},
            "vary": {"type": "array", "items": {"type": "string"},
                     "description": "What can safely change (topic, example, setting, product)"},
            "beat_roles": {"type": "array", "description": "The video beat by beat, in order, described by FUNCTION not words",
                           "items": {"type": "object", "properties": {
                               "role": {"type": "string", "description": "hook | setup | escalation | reveal | proof | payoff | call to action"},
                               "what_happens": {"type": "string", "description": "What this beat does, in your own words (never quote the transcript)"},
                               "approx_seconds": {"type": "number"}},
                               "required": ["role", "what_happens"]}},
        },
        "required": ["structure", "keep", "vary", "beat_roles"],
    },
}


def structure_blueprint(cfg, post: dict, transcript: str, spoken: str, perf: dict | None, notes: str) -> dict:
    """Read the winning video's words and say how it is built. Only what the transcript and notes support."""
    system = (
        "You study short-form videos that beat their own account's normal and explain how they are built, so a "
        "creator can make more like them. Describe mechanics, not praise. Use ONLY the transcript, caption, numbers "
        "and the owner's notes: do not invent visuals, sounds or on-screen text that are not stated. If something "
        "cannot be known, leave it out. For beat_roles describe what each beat DOES in your own words; never quote the "
        "transcript, which may be noisy auto-captions."
    )
    res = ""
    if perf:
        res = (f"Result: {perf.get('multiple')}x this account's normal; label {perf.get('label')}; "
               f"{perf.get('intent_per_1k')} saves+shares+comments per 1k views.\n")
    prompt = (
        f"{res}Duration: {post.get('duration_s')} s. Sound: {post.get('sound') or 'unknown'}.\n"
        f"Spoken in the first seconds: {spoken!r}\nFull transcript: {transcript[:4000]!r}\n"
        f"Caption: {(post.get('caption') or '')[:500]!r}\nOwner's note about what is on screen: {notes or '(none)'}"
    )
    out = _call_tool(cfg, system, prompt, BLUEPRINT_TOOL, max_tokens=1500)
    clean = lambda xs: [str(x)[:160] for x in (xs or [])][:8]
    roles = []
    for b in (out.get("beat_roles") or [])[:10]:
        if isinstance(b, dict) and (b.get("what_happens") or b.get("role")):
            roles.append({"role": str(b.get("role") or "")[:40], "what_happens": str(b.get("what_happens") or "")[:240],
                          "approx_seconds": b.get("approx_seconds") if isinstance(b.get("approx_seconds"), (int, float)) else None})
    return {"format": str(out.get("format") or "")[:80], "tone": str(out.get("tone") or "")[:80],
            "structure": str(out.get("structure") or "")[:600], "keep": clean(out.get("keep")),
            "vary": clean(out.get("vary")), "beat_roles": roles}


HOOKS_TOOL = {
    "name": "propose_hooks",
    "description": "Propose new post ideas.",
    "input_schema": {
        "type": "object",
        "properties": {"ideas": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "ai_prompt": {"type": "string", "description": "Format + delivery, e.g. 'Slideshow 5-pages, urgent tone'"},
                "hook": {"type": "string"},
                "body": {"type": "string"},
                "caption": {"type": "string", "description": "Caption with hashtags"},
            },
            "required": ["hook", "caption"],
        }}},
        "required": ["ideas"],
    },
}


def generate_hooks(cfg, account: dict, winners: list, losers: list, n: int, brief: str = "",
                   inspirations: list | None = None) -> list:
    system = (
        "You write short-form video hooks for a portfolio of accounts. Winners are posts that beat their own "
        "account's normal performance and drew saves, shares and comments, so study what made people act, not only watch. "
        "Study the winners' patterns, "
        "avoid the losers' patterns, and use competitor/inspiration examples as few-shot style references "
        "(do not copy them verbatim). Produce fresh variations. Keep hooks under 15 words."
    )
    fmt = lambda rows: "\n".join(f"- {r['hook']!r} ({r.get('note') or str(r['views']) + ' views'})" for r in rows) or "(none)"
    insp_lines = []
    for i in (inspirations or [])[:12]:
        bit = (i.get("hook_text") or i.get("title") or "").strip()
        if not bit:
            continue
        extra = []
        if i.get("platform"):
            extra.append(i["platform"])
        if i.get("notes"):
            extra.append(str(i["notes"])[:120])
        if i.get("source_url"):
            extra.append(i["source_url"][:80])
        tag = f" [{'; '.join(extra)}]" if extra else ""
        insp_lines.append(f"- {bit!r}{tag}")
    insp_block = ("\nInspiration / competitor hooks (style references only):\n" + "\n".join(insp_lines) + "\n") if insp_lines else ""
    prompt = (
        f"Account: {account['handle']} | niche: {account['niche']} | platform: {account['platform']} | "
        f"content hook style: {account['content_hook']}\nWinning hooks:\n{fmt(winners)}\nFlopped hooks:\n{fmt(losers)}\n"
        f"{insp_block}"
        f"{('Extra direction: ' + brief) if brief else ''}\nPropose {n} new posts."
    )
    out = _call_tool(cfg, system, prompt, HOOKS_TOOL)
    return out.get("ideas", [])[:n]


PLAN_TOOL = {
    "name": "plan_posts",
    "description": "Plan posts across accounts and days.",
    "input_schema": {
        "type": "object",
        "properties": {"posts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "day": {"type": "string", "description": "ISO date"},
                "ai_prompt": {"type": "string"},
                "hook": {"type": "string"},
                "body": {"type": "string"},
                "caption": {"type": "string"},
            },
            "required": ["account", "day", "hook"],
        }}},
        "required": ["posts"],
    },
}


def plan_week(cfg, start: date, accounts: list, winners: list, days: int = 7) -> list:
    system = (
        "You plan a week of short-form posts. Honor each account's posts-per-day. Reuse winning patterns "
        "with fresh angles; never repeat a hook verbatim. Skip accounts with 0 posts/day or status killed/paused."
    )
    acc = "\n".join(f"- {a['handle']} | niche {a['niche']} | {a['times_per_day']}/day | style {a['content_hook']}"
                    for a in accounts if a["status"] == "active" and a["times_per_day"] > 0)
    win = "\n".join(f"- {w['hook']!r} ({w.get('note') or str(w['views']) + ' views'}, {w['handle']})" for w in winners) or "(none yet)"
    prompt = (f"Plan {days} days starting {start.isoformat()}. Use exactly the account handles listed.\n"
              f"Accounts:\n{acc}\nBest hooks so far:\n{win}")
    out = _call_tool(cfg, system, prompt, PLAN_TOOL, max_tokens=8000)
    return out.get("posts", [])


def ask(cfg, question: str, snapshot: dict) -> str:
    system = (
        "You are the analyst inside a content-tracking app. Answer ONLY from the JSON data provided; "
        "if it cannot answer the question, say what is missing. Be concise, use concrete numbers and handles, "
        "and end with one recommended action when relevant."
    )
    return _text(cfg, system, f"DATA:\n{json.dumps(snapshot, default=str)}\n\nQUESTION: {question}")
