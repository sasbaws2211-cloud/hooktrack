"""Refuse throwaway ("disposable") email addresses when an account is created.

Why: the free trial is per person, and a temporary mailbox is the cheapest way to get a fresh one.

Three layers, all checked by domain (and by parent domain, so `x.mailinator.com` is caught):
  1. a built-in list of well-known disposable providers (below; it can never be complete),
  2. extra domains the operator adds in the admin console,
  3. an allow list the operator uses to un-block a domain that is wrongly listed.
The whole check can be switched off in the admin console. Existing accounts are never touched.
"""
from __future__ import annotations

import re

from fastapi import HTTPException

from . import db

SETTING_ENABLED = "block_disposable_email"
SETTING_EXTRA = "disposable_domains_extra"
SETTING_ALLOW = "disposable_domains_allow"
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")

MESSAGE = "Please use a permanent email address. Temporary or disposable mailboxes can't be used to sign up."

BUILTIN = frozenset("""
10minutemail.com 10minutemail.net 10minutemail.co.uk 10minutemail.org 10minemail.com 20minutemail.com
1secmail.com 1secmail.net 1secmail.org 33mail.com 4warding.com 60minutemail.com
armyspy.com
binkmail.com bobmail.info bofthew.com boun.cr burnermail.io
cuvox.de
dayrep.com deadaddress.com discard.email discardmail.com discardmail.de disposableemailaddresses.com
dispostable.com dodgeit.com dont-reply.me dropmail.me dumpmail.de dumpyemail.com
e4ward.com easytrashmail.com einrot.com email-fake.com emailfake.com emailondeck.com emailsensei.com
emailtemporanea.com emailtemporanea.net emailtemporar.ro emailtemporario.com.br emltmp.com esiix.com
fakeinbox.com fakemail.net fakemailgenerator.com fleckens.hu filzmail.com flyspam.com fudgerub.com
generator.email getairmail.com getnada.com gishpuppy.com great-host.in grr.la guerrillamail.biz
guerrillamail.com guerrillamail.de guerrillamail.info guerrillamail.net guerrillamail.org
guerrillamailblock.com gustr.com
harakirimail.com hidemail.de hulapla.de
imails.info inbax.tk inboxalias.com inboxbear.com inboxkitten.com incognitomail.com incognitomail.net
incognitomail.org instantemailaddress.com iroid.com
jetable.com jetable.fr.nf jetable.net jetable.org jourrapide.com junk1.tk
kasmail.com kaspop.com klzlk.com kulturbetrieb.info kurzepost.de
laafd.com letthemeatspam.com lifebyfood.com link2mail.net litedrop.com lookugly.com lortemail.dk
luxusmail.org
mail-temporaire.fr mail.gw mail.tm mail1a.de mailbidon.com mailcatch.com maildrop.cc maildx.com
maileater.com mailexpire.com mailforspam.com mailfreeonline.com mailimate.com mailin8r.com mailinater.com
mailinator.com mailinator.net mailinator.org mailinator2.com mailme.lv mailmetrash.com mailmoat.com
mailnator.com mailnesia.com mailnull.com mailpoof.com mailsac.com mailscrap.com mailshell.com
mailtemp.info mailtothis.com mailzilla.com mailzilla.org meltmail.com messagebeamer.de mintemail.com
moakt.com moakt.cc moakt.ws mohmal.com mt2014.com mt2015.com mx0.wwwnew.eu mytemp.email mytempemail.com
mytrashmail.com
nada.email nepwk.com netzidiot.de no-spam.ws nobulk.com noclickemail.com nomail.xl.cx nospam.ze.tc
nospamfor.us nowmymail.com
objectmail.com obobbo.com oneoffemail.com onewaymail.com owlymail.com
pjjkp.com plexolan.de pokemail.net politikerclub.de poofy.org proxymail.eu putthisinyourspamdatabase.com
quickinbox.com
rcpt.at recode.me recursor.net regbypass.com rhyta.com rmqkr.net rppkn.com
safe-mail.net safetymail.info safetypost.de sandelf.de saynotospams.com selfdestructingmail.com
sendspamhere.com sharklasers.com shieldedmail.com shitmail.me shortmail.net sibmail.com skeefmail.com
slopsbox.com smashmail.de smellfear.com snakemail.com sneakemail.com sofort-mail.de spam4.me spamail.de
spamarrest.com spamavert.com spambob.com spambog.com spambog.de spambog.ru spambox.us spamcero.com
spamcon.org spamcorptastic.com spamcowboy.com spamex.com spamfree24.com spamfree24.de spamgourmet.com
spamherelots.com spamhereplease.com spamhole.com spamify.com spaml.com spaml.de spammotel.com
spamobox.com spamslicer.com spamspot.com spamthis.co.uk spamthisplease.com spamtrail.com speed.1s.fr
superrito.com supermailer.jp suremail.info
tafmail.com tagyourself.com teleworm.us temp-mail.io temp-mail.org temp-mail.ru tempail.com
tempe-mail.com tempemail.biz tempemail.co.za tempemail.com tempemail.net tempinbox.co.uk tempinbox.com
tempmail.com tempmail.de tempmail.dev tempmail.eu tempmail.it tempmail.net tempmail.plus tempmail2.com
tempmailaddress.com tempmailer.com tempmailer.de tempmailo.com tempomail.fr temporarily.de
temporaryemail.net temporaryemail.us temporaryforwarding.com temporaryinbox.com temporarymailaddress.com
tempr.email tempsky.com tempthe.net thankyou2010.com thc.st thelimestones.com thisisnotmyrealemail.com
throam.com throwam.com throwawayemailaddress.com throwawaymail.com tilien.com tmail.ws tmailinator.com
toiea.com trash-amil.com trash-mail.at trash-mail.com trash-mail.de trash2009.com trashdevil.com
trashemail.de trashmail.at trashmail.com trashmail.de trashmail.io trashmail.me trashmail.net
trashmail.org trashmail.ws trashmailer.com trashymail.com trbvm.com turual.com twinmail.de tyldd.com
uggsrock.com upliftnow.com uplipht.com veryrealemail.com vjuum.com vomoto.com
wegwerfemail.de wegwerfmail.de wegwerfmail.net wegwerfmail.org wh4f.org whyspam.me willhackforfood.biz
wwjmp.com
xagloo.com xemaps.com xents.com xjoi.com xojxe.com xoxy.net xyzfree.net
yepmail.net yogamaven.com yoggm.com yopmail.com yopmail.fr yopmail.net yuurok.com
zehnminutenmail.de zippymail.info zoemail.net zoemail.org
cool.fr.nf courriel.fr.nf moncourrier.fr.nf monemail.fr.nf monmail.fr.nf nomail.xl.cx mega.zik.dj
""".split())


def _settings() -> tuple[bool, set, set]:
    def get(key, default):
        row = db.one("SELECT value FROM platform_settings WHERE key=%s", (key,))
        return default if row is None else row["value"]
    enabled = bool(get(SETTING_ENABLED, True))
    extra = {str(d).strip().lower() for d in (get(SETTING_EXTRA, []) or [])}
    allow = {str(d).strip().lower() for d in (get(SETTING_ALLOW, []) or [])}
    return enabled, extra, allow


def _domain(email: str) -> str:
    return (email or "").strip().lower().rpartition("@")[2].strip(".")


def _parents(domain: str):
    """x.y.mailinator.com -> x.y.mailinator.com, y.mailinator.com, mailinator.com"""
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        yield ".".join(parts[i:])


def classify(email: str) -> dict:
    """{blocked: bool, domain, reason: 'builtin' | 'operator' | 'allowed' | 'off' | ''}, never raises."""
    enabled, extra, allow = _settings()
    dom = _domain(email)
    out = {"blocked": False, "domain": dom, "reason": ""}
    if not enabled:
        out["reason"] = "off"
        return out
    chain = list(_parents(dom))
    if any(d in allow for d in chain):
        out["reason"] = "allowed"
        return out
    if any(d in extra for d in chain):
        return {"blocked": True, "domain": dom, "reason": "operator"}
    if any(d in BUILTIN for d in chain):
        return {"blocked": True, "domain": dom, "reason": "builtin"}
    return out


def is_disposable(email: str) -> bool:
    return classify(email)["blocked"]


def require_permanent(email: str) -> None:
    """Raise 400 for a disposable address. Call where a NEW account or invitation is created."""
    if is_disposable(email):
        raise HTTPException(400, MESSAGE)


# ---- addresses that can never receive mail
# Every sign-up sends a confirmation email, so a bot that signs up with made-up addresses makes this server send mail that bounces, which
# damages the sending domain's reputation. These checks refuse an address that cannot receive mail BEFORE anything is sent. They only
# apply when this server can actually send email (mailer.configured()), so local development and the tests are not affected.
RESERVED_TLDS = frozenset("example test invalid localhost local internal localdomain home lan corp onion arpa".split())
RESERVED_DOMAINS = frozenset("example.com example.net example.org".split())
UNDELIVERABLE_MESSAGE = "That email address can't receive mail. Please use your real email address."
_dns_cache: dict = {}
_DNS_TTL = 6 * 3600


def is_reserved(domain: str) -> bool:
    """A domain reserved for documentation and testing (RFC 2606 / 6761): it can never receive real mail."""
    d = (domain or "").strip().lower().strip(".")
    return d.rpartition(".")[2] in RESERVED_TLDS or any(d == r or d.endswith("." + r) for r in RESERVED_DOMAINS)


def _lookup(domain: str):
    """True when the domain publishes somewhere to deliver mail, False when it definitely does not, None when we could not tell
    (a slow or failing DNS server must never block a real customer)."""
    import dns.resolver
    res = dns.resolver.Resolver()
    res.lifetime = res.timeout = 3.0
    try:
        for rr in res.resolve(domain, "MX"):
            return bool(str(rr.exchange).strip("."))         # a "null MX" (just ".") says the domain accepts no mail
    except dns.resolver.NXDOMAIN:
        return False
    except dns.resolver.NoAnswer:
        pass                                                 # no MX: mail falls back to the domain's own address record
    except Exception:
        return None
    for kind in ("A", "AAAA"):
        try:
            if list(res.resolve(domain, kind)):
                return True
        except dns.resolver.NXDOMAIN:
            return False
        except dns.resolver.NoAnswer:
            continue
        except Exception:
            return None
    return False


def can_receive_mail(domain: str):
    import time
    d = (domain or "").strip().lower().strip(".")
    hit = _dns_cache.get(d)
    if hit and time.monotonic() - hit[0] < _DNS_TTL:
        return hit[1]
    ok = _lookup(d)
    if ok is not None:                                       # only a definite answer is remembered
        _dns_cache[d] = (time.monotonic(), ok)
        if len(_dns_cache) > 5000:
            _dns_cache.clear()
    return ok


def require_deliverable(email: str) -> None:
    """Raise 400 when the address's domain is reserved or has no mail records. Does nothing when this server sends no email."""
    from . import mailer
    if not mailer.configured():
        return
    dom = _domain(email)
    if is_reserved(dom) or can_receive_mail(dom) is False:
        raise HTTPException(400, UNDELIVERABLE_MESSAGE)


def clean_domains(values) -> list[str]:
    out = []
    for v in values or []:
        d = str(v).strip().lower().lstrip("@").strip(".")
        if not d:
            continue
        if not DOMAIN_RE.match(d):
            raise HTTPException(400, f"'{v}' is not a domain name (use e.g. tempmail.example)")
        if d not in out:
            out.append(d)
    return out
