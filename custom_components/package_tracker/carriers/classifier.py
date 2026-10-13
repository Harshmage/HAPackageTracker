"""Guess the carrier of a tracking number from its format, check digit and context.

Detection is by format only (no carrier APIs), so several carriers can share a shape.
``classify`` therefore returns *all* plausible carriers, ranked, and the UI lets the
user override. It is called on raw user input and on arbitrary email fragments, so it
must never raise.

Pipeline: normalize -> canonicalize -> regex rules (+ checksums) -> special alternatives
(S10 origin/destination, USPS handoffs, home-country 12-digit) -> per-carrier max ->
context keyword bonus -> rank.
"""

from dataclasses import dataclass
import re
import unicodedata

from . import checksums, rules
from .registry import (
    CARRIER_ORDER,
    CARRIERS,
    S10_COUNTRY_CARRIER,
    carrier_name,
    looks_like_url,
    numbers_from_url,
)
from .rules import Policy

__all__ = ["Candidate", "Policy", "canonicalize", "classify", "normalize"]

# Characters that are pure presentation inside a printed/pasted number.
_STRIP_CHARS = (
    " \t\r\n\f\v-._"  # whitespace and common separators
    "\u00a0\u202f\u2007"  # no-break spaces
    "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"  # unicode dashes/minus
    "\u200b\u200c\u200d\u2060\ufeff\u00ad"  # zero-width chars and soft hyphen
)
_STRIP_TABLE = str.maketrans("", "", _STRIP_CHARS)
_VALID_RE = re.compile(r"[0-9A-Z]{1,64}")
_MAX_RAW = 512

# A pasted URL's number is only trusted from an unknown host when this sure.
_GENERIC_URL_MIN_CONFIDENCE = 0.9


@dataclass(frozen=True, slots=True)
class Candidate:
    """One possible carrier for a tracking number."""

    carrier: str
    confidence: float
    checksum_valid: bool | None  # None = the format has no check digit
    service: str | None
    tracking_number: str  # canonical form
    rule_id: str
    policy: Policy

    def as_dict(self) -> dict:
        """JSON-safe representation for services, websocket and the card."""
        return {
            "carrier": self.carrier,
            "carrier_name": carrier_name(self.carrier),
            "confidence": self.confidence,
            "checksum_valid": self.checksum_valid,
            "service": self.service,
            "tracking_number": self.tracking_number,
            "rule_id": self.rule_id,
            "policy": str(self.policy),
        }


def normalize(raw: str) -> str:
    """Return the bare uppercase alphanumeric form of a tracking number, or ``""``.

    NFKC folds full-width digits and similar look-alikes; whitespace, ``-``/``.``/``_``,
    NBSP, unicode dashes and zero-width characters are removed because they only appear
    in printed or pasted numbers. Anything that still contains other characters is not
    a tracking number and yields ``""``.
    """
    if not isinstance(raw, str) or len(raw) > _MAX_RAW:
        return ""
    try:
        value = unicodedata.normalize("NFKC", raw.translate(_STRIP_TABLE))
        value = value.translate(_STRIP_TABLE).upper()
    except Exception:  # must never raise on user input
        return ""
    return value if _VALID_RE.fullmatch(value) else ""


_IMPB_RE = re.compile(r"9[1-5](?:\d{20}|\d{24}|\d{28})")
_LASERSHIP_17_RE = re.compile(r"1LS7[12]\d{2}01[1-4]\d{6}1")


def _canonicalize(tn: str) -> tuple[str, str | None]:
    """Return ``(canonical, transform_id)``; transform_id names the rewrite applied."""
    if tn.startswith("420") and tn.isdigit():
        # 420 + destination ZIP5 or ZIP9 routing prefix in front of an IMpb.
        splits = [tn[8:], tn[12:]]  # ZIP5, ZIP9
        shaped = [s for s in splits if _IMPB_RE.fullmatch(s)]
        valid = [s for s in shaped if checksums.usps_impb(s)]
        if valid:
            return valid[0], "usps_420"
        if shaped:
            return shaped[-1], "usps_420"  # neither checks out: prefer the ZIP9 split
    if len(tn) == 34 and tn.isdigit():
        # FedEx Ground 34-digit barcode: the trackable number is the last 12 digits.
        return tn[-12:], "fedex_34"
    if _LASERSHIP_17_RE.fullmatch(tn):
        # LaserShip prints "<number>-1"; normalize() removed the dash.
        return tn[:-1], "lasership_suffix"
    return tn, None


def canonicalize(tn: str) -> str:
    """Map a normalized number to the canonical form that carriers' sites accept."""
    try:
        return _canonicalize(tn)[0]
    except Exception:  # canonicalize is called on untrusted input
        return tn


def _apply_rules(tn: str) -> list[Candidate]:
    out: list[Candidate] = []
    for rule in rules.RULES:
        if not rule.pattern.fullmatch(tn):
            continue
        valid: bool | None = None
        confidence = rule.confidence
        if rule.checksum is not None:
            valid = rule.checksum(tn)
            if not valid:
                confidence = rule.confidence_invalid
        service = rule.service
        if rule.id == "ups_1z":
            service = rules.UPS_SERVICES.get(tn[8:10])
        out.append(Candidate(rule.carrier, confidence, valid, service, tn, rule.id, rule.policy))
    return out


def _s10(tn: str, home_post: str | None) -> list[Candidate]:
    match = rules.S10_RE.fullmatch(tn)
    if not match or match.group(4) not in rules.ISO_ALPHA2:
        return []
    first, suffix = match.group(1), match.group(4)
    origin = S10_COUNTRY_CARRIER.get(suffix, "intl_post")
    valid = checksums.s10(tn)
    service = f"{rules.S10_SERVICE.get(first, 'Postal')} (S10)"
    if first == "U":  # UPU: "U*" service indicators are untracked letter post.
        policy, confidence = Policy.CONTEXT, rules.S10_UNTRACKED_CONFIDENCE
    else:
        policy = Policy.STRONG
        confidence = rules.S10_CONFIDENCE if valid else rules.S10_CONFIDENCE_INVALID
    out = [Candidate(origin, confidence, valid, service, tn, "s10", policy)]
    if home_post and home_post != origin:
        # Inbound international items are tracked by the destination post as well.
        out.append(
            Candidate(
                home_post,
                confidence - rules.S10_DESTINATION_PENALTY,
                valid,
                service,
                tn,
                "s10_destination",
                policy,
            )
        )
    return out


def _alternatives(found: list[Candidate], home: str | None) -> list[Candidate]:
    out: list[Candidate] = []
    for cand in found:
        if cand.rule_id == "usps_impb":
            for carrier, service in rules.HANDOFF_ALTERNATIVES:
                confidence = rules.HANDOFF_CONFIDENCE
                if carrier == "fedex" and cand.tracking_number.startswith(rules.FEDEX_IMPB_PREFIX):
                    confidence = rules.FEDEX_IMPB_CONFIDENCE
                out.append(
                    Candidate(
                        carrier,
                        confidence,
                        cand.checksum_valid,
                        service,
                        cand.tracking_number,
                        "usps_handoff",
                        Policy.CONTEXT,
                    )
                )
        elif cand.rule_id == "fedex_12" and home in rules.HOME_12_CARRIERS:
            out.append(
                Candidate(
                    rules.HOME_12_CARRIERS[home],
                    cand.confidence - rules.HOME_12_PENALTY,
                    None,
                    None,
                    cand.tracking_number,
                    "home_12",
                    Policy.CONTEXT,
                )
            )
    return out


_KEYWORD_RES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (
        c.id,
        re.compile(r"(?<![A-Z0-9])(?:" + "|".join(map(re.escape, c.keywords)) + r")(?![A-Z0-9])"),
    )
    for c in CARRIERS.values()
    if c.keywords
)


def _keyword_carriers(context: str | None) -> set[str]:
    """Carriers whose name/keyword appears (word-bounded) in ``context``."""
    if not context or not isinstance(context, str):
        return set()
    text = unicodedata.normalize("NFKC", context).upper()
    return {cid for cid, pattern in _KEYWORD_RES if pattern.search(text)}


def _classify_normalized(
    tn: str,
    home_country: str | None,
    context: str | None,
    extra_keywords: frozenset[str] = frozenset(),
) -> list[Candidate]:
    if not tn:
        return []
    home = home_country.strip().upper() if isinstance(home_country, str) else None
    home_post = S10_COUNTRY_CARRIER.get(home) if home else None
    canonical, transform = _canonicalize(tn)

    if transform == "fedex_34":
        valid = checksums.fedex_mod11(canonical)
        confidence = rules.FEDEX_34_CONFIDENCE if valid else rules.FEDEX_34_CONFIDENCE_INVALID
        found = [
            Candidate("fedex", confidence, valid, "Ground", canonical, "fedex_34", Policy.CHECKSUM)
        ]
    else:
        found = _apply_rules(canonical) + _s10(canonical, home_post)
        found += _alternatives(found, home)
    if not found:
        return []

    keywords = _keyword_carriers(context) | extra_keywords

    def boosted(c: Candidate) -> Candidate:
        conf = c.confidence
        if c.carrier in keywords and conf < rules.CONTEXT_CAP:
            conf = min(rules.CONTEXT_CAP, conf + rules.CONTEXT_BONUS)
        conf = round(max(0.0, min(1.0, conf)), 2)
        return Candidate(
            c.carrier, conf, c.checksum_valid, c.service, c.tracking_number, c.rule_id, c.policy
        )

    def rank(c: Candidate) -> tuple:
        # Ties: valid checksum > keyword > home-country post > registry order.
        return (
            -c.confidence,
            c.checksum_valid is not True,
            c.carrier not in keywords,
            c.carrier != home_post and c.carrier != rules.HOME_12_CARRIERS.get(home or ""),
            CARRIER_ORDER.get(c.carrier, len(CARRIER_ORDER)),
        )

    best: dict[str, Candidate] = {}
    for cand in map(boosted, found):
        current = best.get(cand.carrier)
        if current is None or rank(cand) < rank(current):
            best[cand.carrier] = cand
    return sorted(best.values(), key=rank)


def classify(
    raw: str, *, home_country: str | None = None, context: str | None = None
) -> list[Candidate]:
    """Return every plausible carrier for ``raw``, best first (one entry per carrier).

    ``raw`` may be a pasted carrier tracking link; its number is pulled out with the link
    parser. ``home_country`` (ISO alpha-2) adds the local post as an alternative where
    relevant, and ``context`` (surrounding text) boosts carriers named in it. Returns
    ``[]`` when nothing matches; never raises.
    """
    try:
        if not isinstance(raw, str):
            return []
        if looks_like_url(raw):
            carrier, values = numbers_from_url(raw)
            hint = frozenset({carrier.id}) if carrier else frozenset()
            for value in values:
                found = _classify_normalized(normalize(value), home_country, context, hint)
                if not found:
                    continue
                if carrier is None and found[0].confidence < _GENERIC_URL_MIN_CONFIDENCE:
                    continue
                return found
            return []
        return _classify_normalized(normalize(raw), home_country, context)
    except Exception:  # classification of untrusted input must never raise
        return []
