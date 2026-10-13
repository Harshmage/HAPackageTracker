"""Carrier registry: display names, homepages, tracking-page URL templates and link hints.

There is no live tracking in this integration, so "tracking" means sending the user to
the right web page. Each carrier therefore carries a deep-link template (or ``None``
when the carrier has no stable public link, in which case the UI offers the homepage
plus copy-to-clipboard), and the hosts/query parameters used to pull a number back out
of a pasted carrier link. Templates rot; see docs/carriers.md for "last verified" dates.

Insertion order of ``CARRIERS`` is the registry order used as the final tie-breaker
when ranking candidates and for the UI's carrier list.
"""

from dataclasses import asdict, dataclass
import re
from urllib.parse import parse_qsl, quote, unquote, urlsplit


@dataclass(frozen=True, slots=True)
class Carrier:
    """Static description of a carrier (or postal operator)."""

    id: str
    name: str
    homepage: str
    url_template: str | None  # contains "{tn}"
    keywords: tuple[str, ...] = ()  # uppercase words for context ranking / extraction
    hosts: tuple[str, ...] = ()  # domain suffixes of the carrier's own tracking pages
    url_params: tuple[str, ...] = ()  # query params holding a number; "__path__" = last segment


PATH_PARAM = "__path__"

CARRIERS: dict[str, Carrier] = {
    c.id: c
    for c in (
        Carrier(
            "ups",
            "UPS",
            "https://www.ups.com",
            "https://www.ups.com/track?loc=en_US&tracknum={tn}",
            ("UPS",),
            ("ups.com",),
            ("tracknum", "InquiryNumber1", "tracknumbers", "trackNums"),
        ),
        Carrier(
            "usps",
            "USPS",
            "https://www.usps.com",
            "https://tools.usps.com/go/TrackConfirmAction?tLabels={tn}",
            ("USPS", "POSTAL SERVICE"),
            ("usps.com",),
            ("tLabels", "qtc_tLabels1"),
        ),
        Carrier(
            "fedex",
            "FedEx",
            "https://www.fedex.com",
            "https://www.fedex.com/fedextrack/?trknbr={tn}",
            ("FEDEX",),
            ("fedex.com",),
            ("trknbr", "tracknumbers"),
        ),
        Carrier(
            "dhl",
            "DHL",
            "https://www.dhl.com",
            "https://www.dhl.com/global-en/home/tracking.html?tracking-id={tn}&submit=1",
            ("DHL",),
            ("dhl.com",),
            ("tracking-id", "AWB"),
        ),
        Carrier(
            "amazon",
            "Amazon Logistics",
            "https://track.amazon.com",
            "https://track.amazon.com/tracking/{tn}",
            ("AMAZON",),
            ("track.amazon.com",),
            (PATH_PARAM,),
        ),
        Carrier(
            "ontrac",
            "OnTrac",
            "https://www.ontrac.com",
            "https://www.ontrac.com/tracking/?number={tn}",
            ("ONTRAC", "LASERSHIP"),
            ("ontrac.com", "lasership.com"),
            ("number", "tracking_number", "trackingNumber"),
        ),
        Carrier(
            "canada_post",
            "Canada Post",
            "https://www.canadapost-postescanada.ca",
            "https://www.canadapost-postescanada.ca/track-reperage/en#/search?searchFor={tn}",
            ("CANADA POST", "POSTES CANADA"),
            ("canadapost-postescanada.ca", "canadapost.ca"),
            ("searchFor",),
        ),
        Carrier(
            "royal_mail",
            "Royal Mail",
            "https://www.royalmail.com",
            "https://www.royalmail.com/track-your-item#/tracking-results/{tn}",
            ("ROYAL MAIL",),
            ("royalmail.com",),
            (PATH_PARAM,),
        ),
        Carrier(
            "australia_post",
            "Australia Post",
            "https://auspost.com.au",
            "https://auspost.com.au/mypost/track/#/details/{tn}",
            ("AUSPOST", "AUSTRALIA POST"),
            ("auspost.com.au",),
            (PATH_PARAM,),
        ),
        Carrier(
            "deutsche_post",
            "Deutsche Post",
            "https://www.deutschepost.de",
            "https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html?piececode={tn}",
            ("DEUTSCHE POST", "DHL PAKET"),
            ("dhl.de", "deutschepost.de"),
            ("piececode", "idc"),
        ),
        Carrier(
            "la_poste",
            "La Poste",
            "https://www.laposte.fr",
            "https://www.laposte.fr/outils/suivre-vos-envois?code={tn}",
            ("LA POSTE", "COLISSIMO"),
            ("laposte.fr",),
            ("code",),
        ),
        Carrier(
            "japan_post",
            "Japan Post",
            "https://www.post.japanpost.jp",
            "https://trackings.post.japanpost.jp/services/srv/search/direct"
            "?reqCodeNo1={tn}&locale=en",
            ("JAPAN POST",),
            ("japanpost.jp",),
            ("reqCodeNo1",),
        ),
        Carrier("china_post", "China Post", "https://www.chinapost.com.cn", None, ("CHINA POST",)),
        Carrier("india_post", "India Post", "https://www.indiapost.gov.in", None, ("INDIA POST",)),
        Carrier("postnl", "PostNL", "https://www.postnl.nl", None, ("POSTNL",)),
        Carrier("intl_post", "International Post", "https://www.upu.int", None),
        Carrier("unknown", "Unknown carrier", "https://www.17track.net/en", None),
    )
}

CARRIER_ORDER: dict[str, int] = {cid: i for i, cid in enumerate(CARRIERS)}

# UPU S10 suffix (origin country) -> postal operator. Other valid ISO codes -> intl_post.
S10_COUNTRY_CARRIER: dict[str, str] = {
    "US": "usps",
    "CA": "canada_post",
    "GB": "royal_mail",
    "AU": "australia_post",
    "DE": "deutsche_post",
    "FR": "la_poste",
    "JP": "japan_post",
    "CN": "china_post",
    "IN": "india_post",
    "NL": "postnl",
}

FALLBACK_TEMPLATES: dict[str, str] = {
    "17track": "https://t.17track.net/en#nums={tn}",
    "parcelsapp": "https://parcelsapp.com/en/tracking/{tn}",
}


def get_carrier(carrier_id: str) -> Carrier:
    """Return the registry entry, or the ``unknown`` entry for unrecognised ids."""
    return CARRIERS.get(carrier_id, CARRIERS["unknown"])


def carrier_name(carrier_id: str) -> str:
    """Human-readable carrier name (``"Unknown carrier"`` for unrecognised ids)."""
    return get_carrier(carrier_id).name


@dataclass(frozen=True, slots=True)
class Links:
    """Everything the UI needs to send the user to a tracking page."""

    tracking_url: str | None
    homepage_url: str
    fallback_url: str | None
    copy_only: bool

    def as_dict(self) -> dict:
        """JSON-safe representation."""
        return asdict(self)


def _fill(template: str, tracking_number: str) -> str:
    # quote(safe="") so a hostile "number" can never inject path segments or params.
    return template.replace("{tn}", quote(tracking_number, safe=""))


def _is_http_url(value: str | None) -> bool:
    return bool(value) and value.strip().lower().startswith(("http://", "https://"))


def build_links(
    carrier_id: str,
    tracking_number: str,
    *,
    fallback: str = "none",
    override: str | None = None,
) -> Links:
    """Build the deep link, homepage and universal-tracker fallback for a package.

    A per-package ``override`` (http/https only) always wins over the carrier template.
    The fallback link is provided whenever a fallback tracker is configured, even when a
    deep link exists, so the UI can offer it as a secondary option.
    """
    carrier = get_carrier(carrier_id)
    if _is_http_url(override):
        tracking_url: str | None = override.strip()  # type: ignore[union-attr]
    elif carrier.url_template and tracking_number:
        tracking_url = _fill(carrier.url_template, tracking_number)
    else:
        tracking_url = None
    fallback_template = FALLBACK_TEMPLATES.get(fallback)
    fallback_url = (
        _fill(fallback_template, tracking_number) if fallback_template and tracking_number else None
    )
    return Links(
        tracking_url=tracking_url,
        homepage_url=carrier.homepage,
        fallback_url=fallback_url,
        copy_only=tracking_url is None,
    )


# --- Link parsing --------------------------------------------------------------------

# On hosts we don't know, only parameters whose *name* looks like it carries a tracking
# number are considered (and the caller must then demand high confidence).
GENERIC_PARAM_RE = re.compile(r"track|trk|tn|label|awb|number|piece|shipment|inquiry", re.I)
_REDIRECT_PARAMS = ("url", "q", "u", "target", "redirect", "dest")


def looks_like_url(value: str) -> bool:
    """True for strings that should go through the link parser instead of normalize()."""
    v = value.strip().lower()
    return v.startswith(("http://", "https://", "www."))


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


def carrier_for_host(host: str) -> Carrier | None:
    """Return the carrier whose tracking pages live on ``host`` (suffix match)."""
    host = host.lower().rstrip(".")
    for carrier in CARRIERS.values():
        if carrier.hosts and _host_matches(host, carrier.hosts):
            return carrier
    return None


def _split(url: str):
    url = url.strip()
    if url.lower().startswith("www."):
        url = "https://" + url
    try:
        return urlsplit(url)
    except ValueError:
        return None


def _all_params(parts) -> list[tuple[str, str]]:
    params = parse_qsl(parts.query, keep_blank_values=False)
    # SPA routers (Canada Post, Royal Mail, 17track) keep the state in the fragment.
    frag = parts.fragment
    if "?" in frag:
        params += parse_qsl(frag.split("?", 1)[1], keep_blank_values=False)
    elif "=" in frag:
        params += parse_qsl(frag.lstrip("/"), keep_blank_values=False)
    return params


def _last_path_segment(parts) -> str | None:
    frag_path = parts.fragment.split("?", 1)[0]
    for path in (frag_path if frag_path.startswith("/") else "", parts.path):
        segments = [s for s in path.split("/") if s]
        if segments:
            return unquote(segments[-1])
    return None


def unwrap_url(url: str, *, max_depth: int = 2) -> str:
    """Peel redirect wrappers (Outlook safelinks ``url=``, Google ``q=``) off a link."""
    for _ in range(max_depth):
        parts = _split(url)
        if parts is None:
            return url
        inner = next(
            (
                v
                for k, v in parse_qsl(parts.query)
                if k.lower() in _REDIRECT_PARAMS and looks_like_url(unquote(v))
            ),
            None,
        )
        if inner is None:
            return url
        url = unquote(inner)
    return url


def numbers_from_url(url: str) -> tuple[Carrier | None, list[str]]:
    """Extract candidate tracking numbers from a (possibly wrapped) link.

    Returns ``(carrier, values)``: on a known carrier host only that carrier's allowed
    parameters (or last path segment) are read; on any other host ``carrier`` is
    ``None`` and values come from parameters whose names look tracking-related, which
    the caller must treat with suspicion. Never raises.
    """
    try:
        parts = _split(unwrap_url(url))
        if parts is None or not parts.hostname:
            return None, []
        carrier = carrier_for_host(parts.hostname)
        params = _all_params(parts)
        values: list[str] = []
        if carrier is not None:
            allowed = {p.lower() for p in carrier.url_params}
            values = [v for k, v in params if k.lower() in allowed]
            if PATH_PARAM in carrier.url_params:
                seg = _last_path_segment(parts)
                if seg:
                    values.append(seg)
        else:
            values = [v for k, v in params if GENERIC_PARAM_RE.search(k)]
        # A single parameter may carry several comma-separated numbers (UPS, FedEx).
        out: list[str] = []
        for value in values:
            out.extend(p for p in (s.strip() for s in value.split(",")) if p)
        return carrier, out
    except Exception:  # parsing untrusted text must never raise
        return None, []
