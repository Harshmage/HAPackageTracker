"""Pull tracking numbers out of free text (pasted shipping emails, notes, IMAP bodies).

Emails are full of long numbers that are *not* tracking numbers: order ids, phone
numbers, ZIP+4 codes, IMEIs, gift cards, marketing click ids. A format match alone would
turn most of them into "FedEx packages", so every match must also clear an acceptance
policy (see rules.Policy) and a set of negative filters:

1. Preprocess: quoted-printable decode, HTML -> text (dropping ``<head>``/``<style>``/
   ``<script>`` and keeping link targets), unicode cleanup.
2. Link pass: carrier tracking links are the most reliable source, so URLs are parsed
   first (redirect wrappers unwrapped) and then *removed* from the text so that click
   ids inside them can never be read as numbers.
3. Text passes: contiguous tokens, then space/hyphen-grouped runs (printed layouts such
   as ``9400 1096 9993 9938 2235 64``), which always need a keyword nearby.
4. Acceptance per policy, negative filters on the raw span, dedupe, substring
   suppression, caps. Output is in order of first appearance.

Pure standard library and never raises on any input.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import html
from itertools import pairwise
import quopri
import re
import unicodedata
from urllib.parse import unquote

from .classifier import Candidate, Policy, classify, normalize
from .registry import CARRIERS, build_links, carrier_name, numbers_from_url

__all__ = ["ExtractResult", "extract"]

MAX_TEXT = 100_000
MAX_RESULTS = 50
_KEYWORD_RADIUS = 80
_SNIPPET_LEN = 80
_GENERIC_LINK_MIN_CONFIDENCE = 0.9
# A number read from a carrier's own tracking link whose format we don't recognise is
# still probably real; it is offered unchecked (below the card's 0.8 pre-select).
_UNKNOWN_LINK_CONFIDENCE = 0.6
_MIN_LEN, _MAX_LEN = 8, 40


@dataclass(frozen=True, slots=True)
class ExtractResult:
    """A tracking number found in text, with its ranked carrier candidates."""

    tracking_number: str
    carrier: str
    carrier_name: str
    service: str | None
    confidence: float
    checksum_valid: bool | None
    source: str  # "link" | "text"
    candidates: tuple[Candidate, ...]
    context: str  # <= 80-char snippet around the match

    def as_dict(self, *, tracked: Mapping[str, str] | None = None, fallback: str = "none") -> dict:
        """JSON-safe dict; ``tracked`` maps canonical number -> package id."""
        package_id = (tracked or {}).get(self.tracking_number)
        links = build_links(self.carrier, self.tracking_number, fallback=fallback)
        return {
            "tracking_number": self.tracking_number,
            "carrier": self.carrier,
            "carrier_name": self.carrier_name,
            "service": self.service,
            "confidence": self.confidence,
            "checksum_valid": self.checksum_valid,
            "source": self.source,
            "candidates": [c.as_dict() for c in self.candidates],
            "tracking_url": links.tracking_url,
            "homepage_url": links.homepage_url,
            "fallback_url": links.fallback_url,
            "copy_only": links.copy_only,
            "already_tracked": package_id is not None,
            "package_id": package_id,
            "context": self.context,
        }


# --- Preprocessing -------------------------------------------------------------------

# Only decode when the text really is quoted-printable: decoding plain text would turn
# innocent "?id=12AB" query strings into control characters.
_QP_HINT_RE = re.compile(r"=\r?\n|=3D|quoted-printable", re.IGNORECASE)
_HTML_HINT_RE = re.compile(
    r"<\s*(?:html|body|head|div|p|br|table|tr|td|span|a|img|style|font|center|meta)\b",
    re.IGNORECASE,
)
_ATTRS = r"(?:[^>\"']|\"[^\"]*\"|'[^']*')*"
_DROP_BLOCKS_RE = re.compile(
    r"<!--.*?-->|<(head|style|script)\b" + _ATTRS + r">.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_ANCHOR_RE = re.compile(r"<a\b(" + _ATTRS + r")>(.*?)</a\s*>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b(" + _ATTRS + r")/?>")
_HREF_RE = re.compile(r"\bhref\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", re.IGNORECASE)
_BLOCK_TAGS = frozenset(
    (
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "footer",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "ol", "p", "pre", "section",
        "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    )
)  # fmt: skip

_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")
_DASHES = str.maketrans(dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58", "-"))
_SPACES = str.maketrans(dict.fromkeys("\u00a0\u202f\u2007\u3000", " "))


def _decode_qp(text: str) -> str:
    if not _QP_HINT_RE.search(text):
        return text
    raw = text.replace("\r\n", "\n").encode("utf-8", "surrogatepass")
    return quopri.decodestring(raw).decode("utf-8", "replace")


def _href(attrs: str) -> str:
    match = _HREF_RE.search(attrs)
    if not match:
        return ""
    return next(g for g in match.groups() if g is not None).strip()


def _html_to_text(text: str) -> str:
    """Strip markup, keeping each link target right *after* its anchor text.

    Placing the URL after the anchor keeps a label such as "Tracking number:" close to
    a number that is itself the link text; the URL is cut out again before text passes.
    """
    text = _DROP_BLOCKS_RE.sub(" ", text)
    text = _ANCHOR_RE.sub(lambda m: f"{m.group(2)} {_href(m.group(1))} ", text)

    def tag(match: re.Match[str]) -> str:
        href = _href(match.group(3))
        if href:
            return f" {href} "
        return "\n" if match.group(2).lower() in _BLOCK_TAGS else " "

    text = _TAG_RE.sub(tag, text)
    return html.unescape(text)


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", _ZERO_WIDTH_RE.sub("", text))
    return text.translate(_DASHES).translate(_SPACES).replace("\r\n", "\n").replace("\r", "\n")


def _upper_same_length(text: str) -> str:
    # str.upper() can change length ("ß" -> "SS"); offsets must stay aligned.
    upper = text.upper()
    if len(upper) == len(text):
        return upper
    return "".join(u if len(u := c.upper()) == 1 else c for c in text)


# --- Keywords and negative filters ---------------------------------------------------


def _words(words) -> str:
    return "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in sorted(set(words), key=len)[::-1])


# Carrier names count as tracking keywords, except Amazon: it appears all over Amazon
# order mail next to order ids and has no checksum format of its own (TBA is STRONG).
_CARRIER_KEYWORDS = {cid: c.keywords for cid, c in CARRIERS.items() if c.keywords}
_TRACKING_WORDS = r"TRACK(?:ING)?|WAYBILL|AWB|SHIPMENTS?|PARCELS?|PACKAGES?"
_ACCEPT_KEYWORD_RE = re.compile(
    r"(?<![A-Z0-9])(?:"
    + _TRACKING_WORDS
    + "|"
    + _words(w for cid, ws in _CARRIER_KEYWORDS.items() if cid != "amazon" for w in ws)
    + r")(?![A-Z0-9])"
)
_CARRIER_KEYWORD_RES = {
    cid: re.compile(r"(?<![A-Z0-9])(?:" + _words(ws) + r")(?![A-Z0-9])")
    for cid, ws in _CARRIER_KEYWORDS.items()
}

_B, _E = r"(?<![A-Z0-9])", r"(?![A-Z0-9])"
_BLOCKED_RES = (
    # Phone numbers: NANP shapes with separators, and anything "+"-prefixed.
    re.compile(_B + r"(?:1[ .-]?)?(?:\(\d{3}\)\s?|\d{3}[ .-])\d{3}[ .-]\d{4}" + _E),
    re.compile(r"\+\d[\d .()-]{6,20}\d" + _E),
    # Amazon order ids (physical and digital).
    re.compile(_B + r"(?:\d{3}|D\d{2})-\d{7}-\d{7}" + _E),
    # ZIP+4.
    re.compile(_B + r"\d{5}-\d{4}" + _E),
    # Dates (numeric), times, and compact yyyymmddhhmm[ss] timestamps.
    re.compile(_B + r"(?:\d{4}[/.-]\d{1,2}[/.-]\d{1,2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})(?!\d)"),
    re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)"),
    re.compile(
        _B + r"20\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?:[01]\d|2[0-3])[0-5]\d"
        r"(?:[0-5]\d)?" + _E
    ),
)

# A label that says "this number is something else", directly before the number
# (optionally followed by "number"/"no"/"id"/"#"/":" etc.).
_PREFIX_RE = re.compile(
    _B + r"(?:ORDER|INVOICE|CONFIRMATION|ACCOUNT|REF(?:ERENCE)?|P\.?\s?O\.?|PHONE|TEL|FAX|SKU"
    r"|ITEM|IMEI|ICCID|SERIAL|S/N|MEID|GIFT\s+CARD|PIN|MEMBER|REWARDS|CUSTOMER|CASE"
    r"|TICKET|CARD\s+ENDING)"
    r"(?:\s*(?:NUMBER|NUM|NO|ID|CODE|IN|\d(?!\d)|#|:|\.|-))*\s*$"
)
_PREFIX_LOOKBEHIND = 40

_RUN_LIMIT = 8  # same/sequential digits in a row that mark a placeholder/test number


def _is_repetitive(number: str) -> bool:
    """True for placeholder-looking numbers: 8+ equal or consecutive digits in a row."""
    same = up = down = 1
    for prev, cur in pairwise(number):
        both = prev.isdigit() and cur.isdigit()
        same = same + 1 if prev == cur else 1
        up = up + 1 if both and int(cur) - int(prev) == 1 else 1
        down = down + 1 if both and int(prev) - int(cur) == 1 else 1
        if max(same, up, down) >= _RUN_LIMIT:
            return True
    return False


# --- Scanning ------------------------------------------------------------------------

_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'`{}|\\^\[\]]+", re.IGNORECASE)
_URL_TRAILING = ".,;:!?)]}'\""
_CONTIGUOUS_RE = re.compile(r"(?<![A-Z0-9])[A-Z0-9]{8,40}(?![A-Z0-9])")
_GROUP_RE = re.compile(r"[A-Z0-9]+")
# Letter-only groups that may *start* a printed number; any other all-letter word of 3+
# characters ends a grouped run, so "Tracking 1234 5678" never joins the word.
_KNOWN_LETTER_PREFIXES = frozenset({"TBA", "TBC", "TBM", "JJD", "JVGL"})
_DIGIT_LAYOUTS = (
    (4, 4, 4, 4, 4, 4, 4, 2),  # USPS IMpb 30
    (4, 4, 4, 4, 4, 4, 2),  # USPS IMpb 26
    (4, 4, 4, 4, 4, 2),  # USPS IMpb 22
    (4, 4, 4, 4, 4),  # USPS 20
    (4, 4, 4),  # FedEx 12
)


@dataclass(slots=True)
class _Hit:
    pos: int
    order: int
    result: ExtractResult


class _Scanner:
    def __init__(self, text: str, upper: str, home_country: str | None) -> None:
        self.text = text
        self.upper = upper
        self.home_country = home_country
        self.blocked = [m.span() for r in _BLOCKED_RES for m in r.finditer(upper)]
        self.hits: list[_Hit] = []

    # -- helpers --
    def window(self, start: int, end: int) -> str:
        u = self.upper
        return f"{u[max(0, start - _KEYWORD_RADIUS) : start]} {u[end : end + _KEYWORD_RADIUS]}"

    def snippet(self, start: int, end: int) -> str:
        pad = max(0, (_SNIPPET_LEN - (end - start)) // 2)
        return " ".join(self.text[max(0, start - pad) : end + pad].split())[:_SNIPPET_LEN]

    def add(
        self, start: int, end: int, cands: list[Candidate], source: str, context: str = ""
    ) -> None:
        top = cands[0]
        result = ExtractResult(
            tracking_number=top.tracking_number,
            carrier=top.carrier,
            carrier_name=carrier_name(top.carrier),
            service=top.service,
            confidence=top.confidence,
            checksum_valid=top.checksum_valid,
            source=source,
            candidates=tuple(cands),
            context=self.snippet(start, end) or context[:_SNIPPET_LEN],
        )
        self.hits.append(_Hit(start, len(self.hits), result))

    # -- link pass --
    def link(self, url: str, pos: int) -> None:
        carrier, values = numbers_from_url(url)
        for value in values:
            for _ in range(2):  # values may arrive percent-encoded once more
                if "%" not in value:
                    break
                value = unquote(value)
            tn = normalize(value)
            if not (_MIN_LEN <= len(tn) <= _MAX_LEN) or not any(c.isdigit() for c in tn):
                continue
            if carrier is not None:
                hint = " ".join(carrier.keywords)  # the host names the carrier
                cands = classify(tn, home_country=self.home_country, context=hint)
                if not cands:
                    if sum(c.isdigit() for c in tn) < 4:
                        continue
                    cands = [
                        Candidate(
                            carrier.id,
                            _UNKNOWN_LINK_CONFIDENCE,
                            None,
                            None,
                            tn,
                            "link",
                            Policy.CONTEXT,
                        )
                    ]
            else:
                cands = classify(tn, home_country=self.home_country)
                if not cands:
                    continue
                top = cands[0]
                if top.confidence < _GENERIC_LINK_MIN_CONFIDENCE or top.checksum_valid is False:
                    continue
                if top.policy is not Policy.STRONG and _is_repetitive(top.tracking_number):
                    continue
            self.add(pos, pos, cands, "link", url)

    # -- text passes --
    def evaluate(self, start: int, end: int, *, grouped: bool) -> bool:
        """Classify ``upper[start:end]`` and record it if it clears every filter."""
        raw = self.upper[start:end]
        tn = normalize(raw)
        if not tn or not any(c.isdigit() for c in tn):
            return False
        if any(bs < end and start < be for bs, be in self.blocked):
            return False
        if _PREFIX_RE.search(self.upper[max(0, start - _PREFIX_LOOKBEHIND) : start]):
            return False
        window = self.window(start, end)
        cands = classify(tn, home_country=self.home_country, context=window)
        if not cands:
            return False
        top = cands[0]
        # Machine-generated mail does not contain typos; a failed check digit is noise.
        if top.checksum_valid is False:
            return False
        if top.policy is not Policy.STRONG and _is_repetitive(top.tracking_number):
            return False
        has_keyword = bool(_ACCEPT_KEYWORD_RE.search(window))
        if grouped and not (has_keyword or _CARRIER_KEYWORD_RES["amazon"].search(window)):
            return False
        if top.policy is Policy.CHECKSUM and not (top.checksum_valid and has_keyword):
            return False
        if top.policy is Policy.CONTEXT:
            pattern = _CARRIER_KEYWORD_RES.get(top.carrier)
            if pattern is None or not pattern.search(window):
                return False
        self.add(start, end, cands, "text")
        return True

    def contiguous(self) -> None:
        for match in _CONTIGUOUS_RE.finditer(self.upper):
            self.evaluate(*match.span(), grouped=False)

    def grouped(self) -> None:
        for run in self._runs():
            if len(run) < 2:
                continue
            start, end = run[0][0], run[-1][1]
            if _MIN_LEN <= sum(len(g) for _, _, g in run) <= _MAX_LEN and self.evaluate(
                start, end, grouped=True
            ):
                continue
            self._layouts(run)

    def _runs(self) -> list[list[tuple[int, int, str]]]:
        runs: list[list[tuple[int, int, str]]] = []
        current: list[tuple[int, int, str]] = []
        for match in _GROUP_RE.finditer(self.upper):
            group = match.group()
            if group.isalpha() and len(group) >= 3 and group not in _KNOWN_LETTER_PREFIXES:
                if current:
                    runs.append(current)
                current = []
                continue
            if current and self.upper[current[-1][1] : match.start()] not in (" ", "-"):
                runs.append(current)
                current = []
            current.append((match.start(), match.end(), group))
        if current:
            runs.append(current)
        return runs

    def _layouts(self, run: list[tuple[int, int, str]]) -> None:
        i = 0
        while i < len(run):
            n = self._layout_at(run, i)
            if n and self.evaluate(run[i][0], run[i + n - 1][1], grouped=True):
                i += n
            else:
                i += 1

    @staticmethod
    def _layout_at(run: list[tuple[int, int, str]], i: int) -> int:
        """Number of groups from ``i`` that form a known print layout (0 if none)."""
        groups = [g for _, _, g in run]
        if groups[i].startswith("1Z"):
            total, j = 0, i
            while j < len(groups) and total < 18:
                total += len(groups[j])
                j += 1
            return j - i if total == 18 and j - i > 1 else 0
        # UPU S10 printed as "EA 123 456 785 US".
        part = groups[i : i + 5]
        if (
            [len(g) for g in part] == [2, 3, 3, 3, 2]
            and part[0].isalpha()
            and part[4].isalpha()
            and all(g.isdigit() for g in part[1:4])
        ):
            return 5
        for layout in _DIGIT_LAYOUTS:
            part = groups[i : i + len(layout)]
            if [len(g) for g in part] != list(layout) or not all(g.isdigit() for g in part):
                continue
            if layout == (4, 4, 4):
                # Three 4-digit groups inside a longer digit run are part of something
                # bigger (a card number, a longer label), not a FedEx number.
                before = groups[i - 1] if i > 0 else ""
                after = groups[i + 3] if i + 3 < len(groups) else ""
                if before.isdigit() or after.isdigit():
                    continue
            return len(layout)
        return 0

    # -- output --
    def results(self) -> list[ExtractResult]:
        """Dedupe by canonical number, drop numbers contained in longer ones, order."""

        def rank(hit: _Hit) -> tuple:
            return (-hit.result.confidence, hit.pos, hit.order)

        best: dict[str, _Hit] = {}
        first: dict[str, tuple[int, int]] = {}
        for hit in self.hits:
            tn = hit.result.tracking_number
            first[tn] = min(first.get(tn, (hit.pos, hit.order)), (hit.pos, hit.order))
            if tn not in best or rank(hit) < rank(best[tn]):
                best[tn] = hit
        kept = [tn for tn in best if not any(tn != other and tn in other for other in best)]
        kept.sort(key=first.__getitem__)
        return [best[tn].result for tn in kept[:MAX_RESULTS]]


def _strip_urls(text: str) -> tuple[str, list[tuple[str, int]]]:
    """Return ``text`` with URLs cut out (each replaced by a newline) and the URLs found.

    Each URL is paired with its offset in the returned text so link results can be
    ordered among text results.
    """
    parts: list[str] = []
    urls: list[tuple[str, int]] = []
    last = length = 0
    for match in _URL_RE.finditer(text):
        url = match.group().rstrip(_URL_TRAILING)
        chunk = text[last : match.start()]
        parts.append(chunk)
        length += len(chunk)
        urls.append((url, length))
        parts.append("\n")
        length += 1
        last = match.start() + len(url)
    parts.append(text[last:])
    return "".join(parts), urls


def extract(text: str, *, home_country: str | None = None) -> list[ExtractResult]:
    """Return tracking numbers found in ``text``, in order of first appearance.

    Input beyond 100 000 characters is ignored and at most 50 results are returned,
    deduplicated by canonical number. Never raises.
    """
    try:
        if not isinstance(text, str) or not text:
            return []
        text = _decode_qp(text[:MAX_TEXT])
        if _HTML_HINT_RE.search(text):
            text = _html_to_text(text)
        stripped, urls = _strip_urls(_normalize_text(text))
        scanner = _Scanner(stripped, _upper_same_length(stripped), home_country)
        for url, pos in urls:
            scanner.link(url, pos)
        scanner.contiguous()
        scanner.grouped()
        return scanner.results()
    except Exception:  # extraction runs on arbitrary pasted text and must never raise
        return []
