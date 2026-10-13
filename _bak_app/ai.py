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


def resolve(workspace_id: int) -> Config:
    """Workspace settings first, then the server-wide key for that provider, else unconfigured."""
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


SCORE_TOOL = {
    "name": "score_hook",
    "description": "Score a short-form video hook on the VSC rubric with confidence and full reasoning.",
    "input_schema": {
        "type": "object",
        "properties": {
            "v": {"type": "integer", "minimum": 0, "maximum": 10, "description": "Viral/visual pull: stops the scroll in the first second"},
            "s": {"type": "integer", "minimum": 0, "maximum": 10, "description": "Story/specificity: concrete, emotionally or curiosity-loaded"},
            "c": {"type": "integer", "minimum": 0, "maximum": 10, "description": "Conversion: makes viewers want the app/product (intent)"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1,
                           "description": "0-1 how sure you are given the evidence (lower if little performance data)"},
            "notes": {"type": "string", "description": "1-2 sentence summary + one concrete fix"},
            "reasoning": {"type": "string",
                          "description": "Full rationale: why V/S/C each, evidence from views/comments/history, risks"},
        },
        "required": ["v", "s", "c", "notes", "reasoning", "confidence"],
    },
}


def score_post(cfg, post: dict, account: dict | None, examples: list, thresholds: dict | None = None,
               blind: bool = False) -> dict:
    t = thresholds or {}
    scale, tweak = t.get('scale_vsc', 24), t.get('mutate_vsc', 21)
    per_axis = round(tweak / 3, 1)
    system = (
        "You are a blunt short-form content analyst. Score hooks on VSC, each /10: "
        "V = how hard the first second stops the scroll, S = specificity and emotional/curiosity charge, "
        "C = how likely viewers are to ask 'what's the app / where can I get this?'. "
        f"The score drives a decision: total {scale}+ of 30 = Scale (repeat it), {tweak}-{scale - 1} = Tweak (keep and "
        f"refine), below {tweak} = Stop (drop this angle). So a solid, competent hook that would perform around its "
        f"account's normal belongs around {per_axis} per axis (about {tweak} total); do not push ordinary hooks into "
        f"the Stop band, which is for hooks with a real weakness. 9+ on an axis is rare and must be earned. "
        "Use real performance of past posts as evidence. "
        "Always give confidence (0-1) and full reasoning, not only a short note. "
        "When 'Measured results' are given they are the PRIMARY evidence and outrank your opinion of the wording: "
        "a post at 2x or more of its account's normal, especially with saves, shares or comments asking to buy, "
        "has a hook that demonstrably worked, so V and S cannot be low; say what made it work and make any fix "
        "about repeating or sharpening it. A post far below normal despite good wording points at something other "
        "than the words (visual, timing, distribution): say so instead of blaming the hook. If it is not measured "
        "yet (too early, or too little history) judge the wording alone and keep confidence low."
    )
    ex = "\n".join(
        f"- hook: {e['hook']!r} | views {e['views']} | comments {e['comments']} | intent {e['intent']}"
        f"{' | ' + e['measured'] if e.get('measured') else ''}"
        for e in examples) or "(no history yet)"
    prompt = (
        f"Account niche: {(account or {}).get('niche', 'unknown')}\n"
        f"Post to score:\n- AI prompt/format: {post.get('ai_prompt', '')}\n- Hook (overlay 1): {post.get('hook', '')}\n"
        f"- Body (overlay 2): {post.get('body', '')}\n- Caption: {post.get('caption', '')}\n"
        + ("- Results: withheld. Judge the wording and the idea alone, as you would before posting.\n\n" if blind else
           f"- Views so far: {post.get('views', 0)}, comments: {post.get('comments', 0)}, intent comments: {post.get('intent')}\n"
           f"- Measured results: {post.get('measured') or 'not measured yet (too early, or this account has too little history for a baseline)'}\n\n")
        + f"Recent posts from this portfolio for calibration:\n{ex}"
    )
    out = _call_tool(cfg, system, prompt, SCORE_TOOL, max_tokens=2500)
    clamp = lambda x: max(0, min(10, int(x)))
    try:
        conf = float(out.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    return {
        "v": clamp(out["v"]), "s": clamp(out["s"]), "c": clamp(out["c"]),
        "notes": out.get("notes", "") or "",
        "reasoning": out.get("reasoning", "") or out.get("notes", "") or "",
        "confidence": conf,
    }


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
