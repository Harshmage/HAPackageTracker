"""Format rules: which regex means which carrier, how sure we are, and how to accept it.

Each rule is matched against a *canonical* number (see classifier.canonicalize). The
confidence pair (checksum valid / checksum invalid) and the acceptance ``Policy`` are
the knobs that keep extraction from turning every 12-digit order id into a FedEx
package:

* ``STRONG``   - the format alone is distinctive (``1Z…``, ``TBA…``); accept on sight.
* ``CHECKSUM`` - plain digits; accept only with a valid check digit *and* a tracking
  keyword nearby.
* ``CONTEXT``  - weak/ambiguous shapes; accept only with a carrier keyword nearby.

Rules that need more than a regex (UPU S10, USPS handoffs, home-country alternatives,
FedEx 34-digit barcodes) are implemented in classifier.py using the tables here.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import re

from . import checksums


class Policy(StrEnum):
    """How much surrounding evidence extraction needs before accepting a match."""

    STRONG = "strong"
    CHECKSUM = "checksum"
    CONTEXT = "context"


@dataclass(frozen=True, slots=True)
class Rule:
    """A single regex rule mapping a canonical number shape to a carrier."""

    id: str
    pattern: re.Pattern[str]
    carrier: str
    service: str | None
    policy: Policy
    checksum: Callable[[str], bool] | None
    confidence: float  # when the checksum is valid, or when the format has no checksum
    confidence_invalid: float = 0.0  # when a checksum exists and fails


def _r(
    rule_id: str,
    pattern: str,
    carrier: str,
    service: str | None,
    policy: Policy,
    checksum: Callable[[str], bool] | None,
    confidence: float,
    confidence_invalid: float = 0.0,
) -> Rule:
    return Rule(
        rule_id,
        re.compile(pattern),
        carrier,
        service,
        policy,
        checksum,
        confidence,
        confidence_invalid,
    )


S = Policy.STRONG
C = Policy.CHECKSUM
X = Policy.CONTEXT

RULES: tuple[Rule, ...] = (
    # --- Prefixed formats -------------------------------------------------------------
    _r("ups_1z", r"^1Z[0-9A-Z]{16}$", "ups", None, S, checksums.ups_1z, 1.0, 0.7),
    _r("amazon_tba", r"^TB[ACM]\d{12}$", "amazon", None, S, None, 0.95),
    _r("lasership_1ls", r"^1LS7[12]\d{10}$", "ontrac", "LaserShip", S, None, 0.9),
    # Canonical form of the 17-char "…-1" label (classifier strips the trailing 1).
    _r("lasership_16", r"^1LS7[12]\d{2}01[1-4]\d{6}$", "ontrac", "LaserShip", S, None, 0.9),
    _r("lasership_1lscx", r"^1LSCX[0-9A-Z]{10}$", "ontrac", "LaserShip", S, None, 0.9),
    # Short LaserShip ids look like SKUs/part numbers, so they need a keyword.
    _r("lasership_l", r"^L[AIEHNX][1-3]\d{7}$", "ontrac", "LaserShip", X, None, 0.6),
    _r("ontrac_cd", r"^[CD]\d{14}$", "ontrac", None, C, checksums.ontrac, 0.9, 0.4),
    _r("dhl_ecommerce_gm", r"^GM[0-9A-Z]{14,22}$", "dhl", "eCommerce", S, None, 0.85),
    _r("dhl_jjd", r"^(?:JJD|JVGL)\d{9,20}$", "dhl", "Parcel", S, None, 0.85),
    _r("dhl_j", r"^J[A-Z]{2,3}\d{9,20}$", "dhl", "Parcel", X, None, 0.5),
    _r("ups_mi", r"^MI\d{6}[0-9A-Z]{1,22}$", "ups", "Mail Innovations", X, None, 0.5),
    _r("ups_t", r"^T\d{10}$", "ups", None, X, None, 0.4),
    # --- Numeric formats --------------------------------------------------------------
    _r(
        "usps_impb",
        r"^9[1-5](?:\d{20}|\d{24}|\d{28})$",
        "usps",
        "IMpb",
        C,
        checksums.usps_impb,
        0.95,
        0.6,
    ),
    _r("fedex_96", r"^96\d{20}$", "fedex", "Ground", C, checksums.fedex_96, 0.9, 0.5),
    _r(
        "deutsche_post_sscc",
        r"^00340434\d{12}$",
        "deutsche_post",
        "SSCC",
        C,
        checksums.sscc,
        0.9,
        0.5,
    ),
    _r("fedex_sscc", r"^00(?!340434)\d{18}$", "fedex", "SSCC", C, checksums.sscc, 0.6, 0.3),
    # 89-prefixed 20-digit strings are SIM ICCIDs far more often than USPS labels.
    _r("usps_20_iccid", r"^89\d{18}$", "usps", None, X, checksums.usps_impb, 0.3, 0.2),
    _r("usps_20", r"^(?!00|89)\d{20}$", "usps", None, C, checksums.usps_impb, 0.85, 0.4),
    _r("canada_post_16", r"^\d{16}$", "canada_post", None, X, None, 0.5),
    _r("fedex_15", r"^\d{15}$", "fedex", "Ground", C, checksums.fedex_15, 0.8, 0.4),
    # Kept low: a 12-digit mod-11 pass is 1-in-11 by chance, so a keyword bonus (+0.15)
    # must not lift it past the 0.8 "pre-select" threshold on its own.
    _r("fedex_12", r"^\d{12}$", "fedex", "Express", C, checksums.fedex_mod11, 0.6, 0.3),
    _r("dhl_express", r"^\d{10,11}$", "dhl", "Express", C, checksums.dhl_mod7, 0.7, 0.3),
)

# --- UPU S10 (``AA123456785US``) -----------------------------------------------------

S10_RE = re.compile(r"^([A-Z])([A-Z])(\d{9})([A-Z]{2})$")
S10_CONFIDENCE = 0.95
S10_CONFIDENCE_INVALID = 0.6
S10_UNTRACKED_CONFIDENCE = 0.4
S10_DESTINATION_PENALTY = 0.1
S10_SERVICE = {
    "E": "EMS",
    "R": "Registered",
    "C": "Parcel",
    "V": "Insured",
    "L": "Tracked",
    "U": "Untracked",
}

# --- USPS handoffs -------------------------------------------------------------------
# FedEx SmartPost/Ground Economy, UPS SurePost and DHL eCommerce hand parcels to USPS
# for the last mile using an IMpb label, so the shipper may know it by another carrier.
HANDOFF_CONFIDENCE = 0.3
HANDOFF_ALTERNATIVES: tuple[tuple[str, str], ...] = (
    ("fedex", "Ground Economy (USPS handoff)"),
    ("ups", "SurePost (USPS handoff)"),
    ("dhl", "eCommerce (USPS handoff)"),
)
FEDEX_IMPB_PREFIX = "92612"  # FedEx Ground Economy's IMpb prefix: much likelier FedEx.
FEDEX_IMPB_CONFIDENCE = 0.6

# --- 12-digit home-country alternatives ----------------------------------------------
HOME_12_CARRIERS = {"DE": "deutsche_post", "JP": "japan_post"}
HOME_12_PENALTY = 0.05

FEDEX_34_CONFIDENCE = 0.9
FEDEX_34_CONFIDENCE_INVALID = 0.6

CONTEXT_BONUS = 0.15
CONTEXT_CAP = 0.99

# UPS 1Z service code ([8:10]); only the well-documented ones, anything else is None.
UPS_SERVICES = {
    "01": "Next Day Air",
    "02": "2nd Day Air",
    "03": "Ground",
    "12": "3 Day Select",
    "13": "Next Day Air Saver",
    "14": "Next Day Air Early",
    "59": "2nd Day Air A.M.",
}

# Officially assigned ISO 3166-1 alpha-2 codes (249). An S10 suffix outside this set is
# not a real UPU origin, which rules out lots of "AB123456789CD"-shaped noise.
_ISO_ALPHA2_CODES = """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ
    BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
    CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
    DE DJ DK DM DO DZ
    EC EE EG EH ER ES ET
    FI FJ FK FM FO FR
    GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY
    HK HM HN HR HT HU
    ID IE IL IM IN IO IQ IR IS IT
    JE JM JO JP
    KE KG KH KI KM KN KP KR KW KY KZ
    LA LB LC LI LK LR LS LT LU LV LY
    MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
    NA NC NE NF NG NI NL NO NP NR NU NZ
    OM
    PA PE PF PG PH PK PL PM PN PR PS PT PW PY
    QA
    RE RO RS RU RW
    SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ
    TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ
    UA UG UM US UY UZ
    VA VC VE VG VI VN VU
    WF WS
    YE YT
    ZA ZM ZW
"""
ISO_ALPHA2: frozenset[str] = frozenset(_ISO_ALPHA2_CODES.split())
