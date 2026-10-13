"""What a comment says about the video that earned it.

Views and likes tell you people watched. Comments like "where can I buy this?" or "when is the restock?" tell you
they wanted something, which is what a hook that SELLS looks like. This is deliberately rule-based: free,
deterministic and explainable (you can see which phrase matched), so credits go on fetching comments rather than
on classifying them.

kind  buy   wants the thing / asks the price / asks for a restock or drop / says they would pay   (strongest)
      ask   wants to know what it is, where it is from, how it is done, or for the details
      more  wants the next one (part 2, a series): demand for the format, not the product

Tuned against real comments (a consumer-brand account and entertainment accounts); the cases that taught it
something are pinned in tests/test_comments_velocity.py.
"""
import re

_FLAGS = re.I | re.U

# "I need/want" is only buying intent when it is not about doing something ("I need to sleep", "I need you to ...")
_WANT = r"(?:need|want|must have|gotta have|have to have|got to have|have to get)"
_NOT_AN_ACTION = r"(?! to\b| you\b| a (?:break|hug|nap|vacation)\b| sleep\b| help\b| more (?:sleep|time)\b| some\b)"

BUY = [
    r"\bwhere (can|do|could|would|should) (i|we|you|u) (buy|get|order|find|purchase|download)\b",
    r"\bwhere (to|do i|can i) (buy|get|order|find|purchase|download)\b",
    r"\b(take|shut up and take) my money\b",
    r"\bhow much\b",
    r"\bwhat('?s| is| are) the (price|cost)\b",
    r"\b(price|pricing|prices)\b",
    # wanting the thing itself
    r"\b(i|we) (really |so |badly |just |absolutely )?" + _WANT + r"\b" + _NOT_AN_ACTION + r"(?! be\b)(?! see\b)(?! know\b)",
    r"\b" + _WANT + r"(?: \w+){0,2} (this|it|these|those|that)\b",
    r"\bvery need\b|\bso need\b",
    # having bought / being about to
    r"\b(i )?(just |already )?(bought|ordered|purchased) (it|this|one|mine|these|them|two|some)\b",
    r"\b(i'?m|im|i am) (buying|getting|ordering)\b",
    r"\bi'?ll buy\b|\bi would buy\b|\bi'?d buy\b",
    # availability: restocks and drops are the clearest "I am ready to pay" signal a brand gets
    r"\b(sold out|restock|re-stock|back in stock|out of stock|restocking)\b",
    r"\b(drop|release|launch|come out|comes out|available)\b.{0,10}\b(again|soon|when|please|pls|plz)\b|"
    r"\b(drop|release|launch|restock)\b.{0,12}\b(again|soon)\b|\bdrop again\b",
    r"\bwhen (will|is|are|does|do|can|you gonna|u gonna|are you gonna|are we getting|you guys)\b.{0,50}"
    r"\b(drop|restock|release|launch|come out|be back|back|available|ship|ready)\b",
    r"\b(discount|promo code|coupon|code\b.{0,8}\bplease)\b",
    r"\bsign me up\b",
    r"\bis it worth( it| the money)?\b",
    r"\bdo you (ship|deliver)\b|\bship(s|ping)? to\b",
    r"\b(link|links)\b.{0,16}\b(please|pls|plz|bio|\?)",
    r"\b(please|pls|plz|drop|send|share|put|give)\b.{0,12}\b(the )?(link|links)\b",
    # es / pt / fr
    r"\bd[oó]nde (puedo )?(comprar|conseguir)\b|\bcu[aá]nto (cuesta|vale)\b|\bprecio\b",
    r"\bonde (comprar|encontro|consigo)\b|\bquanto (custa|[eé])\b|\bpre[cç]o\b",
    r"\bo[uù] (acheter|trouver)\b|\bcombien\b|\bprix\b",
]
ASK = [
    r"\bwhat('?s| is) (the |this |that )?(app|brand|product|website|site|name|tool|software|program)\b",
    r"\bwhich (app|brand|product|one|tool|website|camera|phone|colou?r|size)\b",
    r"\bwhat app\b",
    r"\bwhere (is|was|did|does) (this|that|it|you|u)\b.{0,12}\b(from|get|find|film|shot)\b",
    r"\bhow (do|did|can|could|to) (i|you|u|we)\b",
    r"\bhow (is|was) (this|it|that) (made|done|possible)\b",
    r"\b(tutorial|teach me|explain|walk(through)?)\b",
    r"\bwhat'?s (it|that|this) called\b",
    r"\b(dm|dmd|dm'd|inbox)\b",
    r"\bcan (you|u) (make|do|show|explain|share|tell|link|post)\b",
    r"\b(more|any) (info|details|information)\b",
    r"\b(what|which) (is|are) (it|they|this|that)\b\?",
    r"\b(give|share|drop|send|tell)\b.{0,12}\b(outfit|fit|name|details|info|brand|size|sizes|colou?r)\b",
    r"\b(inside leg|size chart|what size|which size|sizing)\b",
]
MORE = [
    r"\bpart (2|two|ii|3|three|iii)\b",
    r"\bfollow[- ]?up\b",
    r"\bmore (of this|like this|please|pls|plz|videos?|content)\b",
    r"\bnext (video|one|part)\b",
    r"\bmake (a )?(series|more|another)\b",
    r"\b(do|make) (a )?(part|series)\b",
    r"\bplease (do|make|post) more\b",
]

_COMPILED = [("buy", 3, [re.compile(p, _FLAGS) for p in BUY]),
             ("ask", 2, [re.compile(p, _FLAGS) for p in ASK]),
             ("more", 1, [re.compile(p, _FLAGS) for p in MORE])]


def classify(text: str) -> dict:
    """{'kind': 'buy'|'ask'|'more'|None, 'score': 0-3, 'matched': the phrase that fired}."""
    t = " ".join((text or "").split())[:600]
    if len(t) < 3:
        return {"kind": None, "score": 0, "matched": ""}
    for kind, score, patterns in _COMPILED:
        for rx in patterns:
            m = rx.search(t)
            if m:
                return {"kind": kind, "score": score, "matched": m.group(0)[:60]}
    return {"kind": None, "score": 0, "matched": ""}


def summarize(comments: list[dict]) -> dict:
    """Roll classified comments up to the numbers a post carries."""
    total = len(comments)
    counts = {"buy": 0, "ask": 0, "more": 0}
    for c in comments:
        if c.get("kind") in counts:
            counts[c["kind"]] += 1
    return {"fetched": total, "buy": counts["buy"], "ask": counts["ask"], "more": counts["more"],
            "intent": counts["buy"] + counts["ask"]}
