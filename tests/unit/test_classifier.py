"""Classifier behaviour: normalization, canonical forms, per-rule results and ranking.

Vectors come from the plan's "Test vectors" section (sourced from carrier docs and
jkeen/tracking_number_data). Each asserts carrier, checksum validity and the canonical
number, because the canonical number is what gets stored and deduplicated.
"""

import json
from pathlib import Path
import random

import pytest

from custom_components.package_tracker.carriers import (
    CARRIERS,
    Candidate,
    Policy,
    canonicalize,
    classify,
    normalize,
)
from custom_components.package_tracker.carriers.checksums import mod10_31_check

# (raw, carrier, checksum_valid, canonical)
VALID = [
    ("1Z999AA10123456784", "ups", True, "1Z999AA10123456784"),
    ("1Z5R89390357567127", "ups", True, "1Z5R89390357567127"),
    ("1Z12345E6605272234", "ups", True, "1Z12345E6605272234"),
    ("9400109699939938223564", "usps", True, "9400109699939938223564"),
    ("9205590164917312751089", "usps", True, "9205590164917312751089"),
    ("9101026837331000039521", "usps", True, "9101026837331000039521"),
    ("92748931507708513018050063", "usps", True, "92748931507708513018050063"),
    ("03071790000523483741", "usps", True, "03071790000523483741"),
    ("71969010756003077385", "usps", True, "71969010756003077385"),
    ("420221539101026837331000039521", "usps", True, "9101026837331000039521"),
    ("4201028200009261290113185417468510", "usps", True, "9261290113185417468510"),
    ("986578788855", "fedex", True, "986578788855"),
    ("477179081230", "fedex", True, "477179081230"),
    ("041441760228964", "fedex", True, "041441760228964"),
    ("9611020987654312345672", "fedex", True, "9611020987654312345672"),
    ("9622001900000000000000776632517510", "fedex", True, "776632517510"),
    ("3318810025", "dhl", True, "3318810025"),
    ("73891051146", "dhl", True, "73891051146"),
    ("C11031500001879", "ontrac", True, "C11031500001879"),
    ("D10011354453707", "ontrac", True, "D10011354453707"),
    ("EA123456785US", "usps", True, "EA123456785US"),
    ("RR123456785GB", "royal_mail", True, "RR123456785GB"),
    ("CP123456785CA", "canada_post", True, "CP123456785CA"),
    ("RR123456785DE", "deutsche_post", True, "RR123456785DE"),
    ("LX123456785CN", "china_post", True, "LX123456785CN"),
]

INVALID = [
    ("1Z999AA10123456780", "ups", False, "1Z999AA10123456780"),
    ("1Z12345E0205271688", "ups", False, "1Z12345E0205271688"),
    ("996578788855", "fedex", False, "996578788855"),
    ("C11031500001878", "ontrac", False, "C11031500001878"),
    ("3318810010", "dhl", False, "3318810010"),
]

FORMAT_ONLY = [
    ("TBA502887274000", "amazon", None, "TBA502887274000"),
    ("TBA000000000000", "amazon", None, "TBA000000000000"),
    ("1LSCXVE005BUEFX", "ontrac", None, "1LSCXVE005BUEFX"),
    ("LX17635036", "ontrac", None, "LX17635036"),
    ("JJD0099999999", "dhl", None, "JJD0099999999"),
    ("GM2951173225174494", "dhl", None, "GM2951173225174494"),
]

# 20-digit USPS numbers have two acceptable check digits; mutation is tested separately.
_DUAL_CHECK = {"03071790000523483741", "71969010756003077385"}


@pytest.mark.parametrize(("raw", "carrier", "valid", "canonical"), VALID + INVALID + FORMAT_ONLY)
def test_vectors(raw: str, carrier: str, valid: bool | None, canonical: str) -> None:
    top = classify(raw)[0]
    assert top.carrier == carrier
    assert top.checksum_valid is valid
    assert top.tracking_number == canonical


@pytest.mark.parametrize(("raw", "carrier", "valid", "canonical"), INVALID)
def test_invalid_checksum_ranks_lower(raw: str, carrier: str, valid: bool, canonical: str) -> None:
    assert classify(raw)[0].confidence < 0.8


def test_ups_invalid_is_possible_typo_at_0_7() -> None:
    top = classify("1Z999AA10123456780")[0]
    assert (top.confidence, top.checksum_valid, top.policy) == (0.7, False, Policy.STRONG)
    assert classify("1Z999AA10123456784")[0].confidence == 1.0


@pytest.mark.parametrize(
    ("raw", "carrier", "valid", "canonical"),
    [v for v in VALID if v[0] not in _DUAL_CHECK],
)
def test_check_digit_mutation_not_valid(
    raw: str, carrier: str, valid: bool, canonical: str
) -> None:
    idx = 10 if len(raw) == 13 else len(raw) - 1  # S10 check digit sits before the suffix
    for digit in "0123456789":
        if digit == raw[idx]:
            continue
        mutated = raw[:idx] + digit + raw[idx + 1 :]
        same = [c for c in classify(mutated) if c.carrier == carrier]
        assert all(c.checksum_valid is not True for c in same), mutated


# --- normalize ----------------------------------------------------------------------


def _fullwidth(text: str) -> str:
    """Full-width forms (U+FF01..), as typed by CJK input methods."""
    return "".join(chr(ord(c) + 0xFEE0) for c in text)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1z999aa10123456784", "1Z999AA10123456784"),
        ("1Z 999 AA1 01 2345 678 4", "1Z999AA10123456784"),
        ("  1Z999AA1-0123-4567-84\n", "1Z999AA10123456784"),
        ("1Z999AA1.0123.456784", "1Z999AA10123456784"),
        ("1Z\u200b999\u200cAA1\u200d0123\u2060456\ufeff78\u00ad4", "1Z999AA10123456784"),
        ("9400\u00a01096\u202f9993\u20079938223564", "9400109699939938223564"),
        ("9400\u20101096\u20139993\u20149938\u2212223564", "9400109699939938223564"),
        (_fullwidth("9400109699939938223564"), "9400109699939938223564"),
        (_fullwidth("tba502887274000"), "TBA502887274000"),
        ("", ""),
        ("   ", ""),
        ("hello world!", ""),
        ("1Z999/AA10123456784", ""),
        ("\u00dcn\u00efc\u00f6d\u00e9", ""),
        ("9" * 100, ""),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_normalize_non_string_is_empty() -> None:
    assert normalize(None) == ""  # type: ignore[arg-type]
    assert normalize(12345) == ""  # type: ignore[arg-type]


# --- canonicalize -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tn", "expected"),
    [
        ("420221539101026837331000039521", "9101026837331000039521"),  # ZIP5 split
        ("4201028200009261290113185417468510", "9261290113185417468510"),  # ZIP9 split
        ("9622001900000000000000776632517510", "776632517510"),
        ("1LS71230112345671", "1LS7123011234567"),  # LaserShip "-1" suffix, dash removed
        ("1Z999AA10123456784", "1Z999AA10123456784"),
        ("42012345", "42012345"),
    ],
)
def test_canonicalize(tn: str, expected: str) -> None:
    assert canonicalize(tn) == expected
    assert canonicalize(expected) == expected  # idempotent: stored numbers stay stable


def test_420_routing_prefers_zip9_when_neither_checks_out() -> None:
    # ZIP5 remainder (26 digits, starts "00") has no IMpb shape; ZIP9 remainder does but
    # its check digit is wrong: the ZIP9 split still wins.
    raw = "4201028200009261290113185417468511"
    assert canonicalize(raw) == "9261290113185417468511"
    assert classify(raw)[0].checksum_valid is False


def test_lasership_dash_one_suffix() -> None:
    top = classify("1LS7123011234567-1")[0]
    assert (top.carrier, top.tracking_number, top.policy) == (
        "ontrac",
        "1LS7123011234567",
        Policy.STRONG,
    )
    assert classify("1LS712345678901")[0].carrier == "ontrac"


# --- URL input ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "carrier", "number"),
    [
        (
            "https://www.ups.com/track?loc=en_US&tracknum=1Z999AA10123456784",
            "ups",
            "1Z999AA10123456784",
        ),
        (
            "https://tools.usps.com/go/TrackConfirmAction?tLabels=9400109699939938223564",
            "usps",
            "9400109699939938223564",
        ),
        ("www.fedex.com/fedextrack/?trknbr=986578788855", "fedex", "986578788855"),
        ("https://track.amazon.com/tracking/TBA502887274000", "amazon", "TBA502887274000"),
        (
            "https://www.royalmail.com/track-your-item#/tracking-results/RR123456785GB",
            "royal_mail",
            "RR123456785GB",
        ),
        (
            "https://www.canadapost-postescanada.ca/track-reperage/en#/search?searchFor=CP123456785CA",
            "canada_post",
            "CP123456785CA",
        ),
        (
            "https://nam12.safelinks.protection.outlook.com/?url=https%3A%2F%2Ftools.usps.com"
            "%2Fgo%2FTrackConfirmAction%3FtLabels%3D9400109699939938223564&data=abc",
            "usps",
            "9400109699939938223564",
        ),
        (
            "https://shop.example.com/orders/1?tracking_number=1Z999AA10123456784",
            "ups",
            "1Z999AA10123456784",
        ),
    ],
)
def test_classify_url(url: str, carrier: str, number: str) -> None:
    top = classify(url)[0]
    assert (top.carrier, top.tracking_number) == (carrier, number)


def test_url_from_carrier_host_boosts_that_carrier() -> None:
    # A FedEx 12-digit number alone stays below 0.8; on fedex.com the host is evidence.
    assert classify("986578788855")[0].confidence == 0.6
    assert classify("https://www.fedex.com/fedextrack/?trknbr=986578788855")[0].confidence == 0.75


def test_url_unknown_host_requires_high_confidence() -> None:
    # Weak 12-digit numbers in an unknown host's tracking-ish param are ignored.
    assert classify("https://example.com/t?tracking=986578788855") == []
    # Parameters whose names don't look tracking-related are never read.
    assert classify("https://example.com/m?uid=9400109699939938223564") == []


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "hello",
        "not a tracking number!",
        "12345",
        "ABCDEFGHIJ",
        "https://",
        "https://www.ups.com/track",
        "http://[::1",
        "EA123456785XX",  # S10 shape, but XX is not an ISO 3166 code
        "\x00\x01\x02",
    ],
)
def test_garbage_returns_empty(raw: str) -> None:
    assert classify(raw) == []


def test_classify_non_string_returns_empty() -> None:
    assert classify(None) == []  # type: ignore[arg-type]


def test_classify_never_raises_on_random_input() -> None:
    rng = random.Random(1234)
    alphabet = "0123456789ABCXYZ1Z-. /:?=&#%\u200b\u00a0\u2013\u00c4\u00d6\u00fc\u6f22"
    for _ in range(2000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        result = classify(text, home_country=rng.choice([None, "US", "de", "", "ZZZ"]))
        assert isinstance(result, list)


# --- per-rule behaviour ---------------------------------------------------------------


def _sscc20(prefix: str) -> str:
    body = prefix + "0" * (19 - len(prefix))
    return body + str(mod10_31_check(body[2:]))


@pytest.mark.parametrize(
    ("raw", "carrier", "rule_id", "policy"),
    [
        ("1Z999AA10123456784", "ups", "ups_1z", Policy.STRONG),
        ("TBA502887274000", "amazon", "amazon_tba", Policy.STRONG),
        ("TBC502887274000", "amazon", "amazon_tba", Policy.STRONG),
        ("EA123456785US", "usps", "s10", Policy.STRONG),
        ("1LSCXVE005BUEFX", "ontrac", "lasership_1lscx", Policy.STRONG),
        ("LX17635036", "ontrac", "lasership_l", Policy.CONTEXT),
        ("C11031500001879", "ontrac", "ontrac_cd", Policy.CHECKSUM),
        ("GM2951173225174494", "dhl", "dhl_ecommerce_gm", Policy.STRONG),
        ("JJD0099999999", "dhl", "dhl_jjd", Policy.STRONG),
        ("JVX0099999999", "dhl", "dhl_j", Policy.CONTEXT),
        ("MI123456ABC123", "ups", "ups_mi", Policy.CONTEXT),
        ("T1234567890", "ups", "ups_t", Policy.CONTEXT),
        ("9400109699939938223564", "usps", "usps_impb", Policy.CHECKSUM),
        ("9611020987654312345672", "fedex", "fedex_96", Policy.CHECKSUM),
        (_sscc20("00340434"), "deutsche_post", "deutsche_post_sscc", Policy.CHECKSUM),
        (_sscc20("0012345"), "fedex", "fedex_sscc", Policy.CHECKSUM),
        ("03071790000523483741", "usps", "usps_20", Policy.CHECKSUM),
        ("89014103211118510720", "usps", "usps_20_iccid", Policy.CONTEXT),
        ("1234567890123456", "canada_post", "canada_post_16", Policy.CONTEXT),
        ("041441760228964", "fedex", "fedex_15", Policy.CHECKSUM),
        ("986578788855", "fedex", "fedex_12", Policy.CHECKSUM),
        ("9622001900000000000000776632517510", "fedex", "fedex_34", Policy.CHECKSUM),
        ("3318810025", "dhl", "dhl_express", Policy.CHECKSUM),
    ],
)
def test_rule_and_policy(raw: str, carrier: str, rule_id: str, policy: Policy) -> None:
    top = classify(raw)[0]
    assert (top.carrier, top.rule_id, top.policy) == (carrier, rule_id, policy)


def test_sscc_confidences() -> None:
    dp = classify(_sscc20("00340434"))[0]
    assert (dp.checksum_valid, dp.confidence) == (True, 0.9)
    fx = classify(_sscc20("0012345"))[0]
    assert (fx.carrier, fx.checksum_valid, fx.confidence) == ("fedex", True, 0.6)


def test_ups_service_from_code() -> None:
    assert classify("1Z999AA10123456784")[0].service == "Next Day Air"  # code 01


def test_s10_untracked_u_prefix_is_context_only() -> None:
    # "UA…" (U* service indicator) is untracked letter post.
    number = "UA123456785GB"
    top = classify(number)[0]
    assert (top.carrier, top.policy, top.service) == (
        "royal_mail",
        Policy.CONTEXT,
        "Untracked (S10)",
    )


def test_s10_other_iso_suffix_is_intl_post() -> None:
    top = classify("RR123456785SE")[0]
    assert (top.carrier, top.checksum_valid) == ("intl_post", True)


def test_s10_destination_alternative_from_home_country() -> None:
    result = classify("LX123456785CN", home_country="us")
    assert [c.carrier for c in result] == ["china_post", "usps"]
    assert result[1].rule_id == "s10_destination"
    assert result[1].confidence < result[0].confidence
    # Same origin and home: no duplicate.
    assert [c.carrier for c in classify("LX123456785CN", home_country="CN")] == ["china_post"]
    # Home country without a known post: no alternative.
    assert [c.carrier for c in classify("LX123456785CN", home_country="SE")] == ["china_post"]


def test_impb_handoff_alternatives() -> None:
    result = classify("9400109699939938223564")
    assert result[0].carrier == "usps"
    alts = {c.carrier: c for c in result[1:]}
    assert set(alts) == {"fedex", "ups", "dhl"}
    assert all(c.confidence == 0.3 and c.policy is Policy.CONTEXT for c in alts.values())
    assert alts["dhl"].service.startswith("eCommerce")
    # Ties fall back to registry order.
    assert [c.carrier for c in result[1:]] == ["ups", "fedex", "dhl"]


def test_impb_92612_prefix_is_likely_fedex() -> None:
    result = classify("9261290113185417468510")
    assert [c.carrier for c in result[:2]] == ["usps", "fedex"]
    assert result[1].confidence == 0.6


@pytest.mark.parametrize(("home", "alt"), [("DE", "deutsche_post"), ("jp", "japan_post")])
def test_12_digit_home_country_alternative(home: str, alt: str) -> None:
    result = classify("986578788855", home_country=home)
    assert [c.carrier for c in result] == ["fedex", alt]
    assert result[1].confidence == pytest.approx(result[0].confidence - 0.05)
    assert result[1].checksum_valid is None


def test_12_digit_no_alternative_for_other_home_countries() -> None:
    assert [c.carrier for c in classify("986578788855", home_country="US")] == ["fedex"]


# --- ranking and context ----------------------------------------------------------------


def test_context_keyword_boost() -> None:
    plain = classify("3318810025")[0]
    boosted = classify("3318810025", context="Your DHL Express waybill")[0]
    assert boosted.confidence == pytest.approx(plain.confidence + 0.15)


def test_context_keyword_must_be_word_bounded() -> None:
    assert classify("3318810025", context="DHLX")[0].confidence == 0.7
    assert classify("3318810025", context="ADHL")[0].confidence == 0.7


def test_context_bonus_capped() -> None:
    assert classify("EA123456785US", context="USPS")[0].confidence == 0.99
    # Already-certain matches are never reduced by the cap.
    assert classify("1Z999AA10123456784", context="UPS")[0].confidence == 1.0


def test_fedex_12_keyword_alone_cannot_pass_0_8() -> None:
    top = classify("986578788855", context="Shipped with FedEx, tracking number:")[0]
    assert top.carrier == "fedex"
    assert top.confidence < 0.8


def test_context_reorders_handoff_alternatives() -> None:
    result = classify("9400109699939938223564", context="shipped via UPS SurePost")
    assert result[0].carrier == "usps"
    assert result[1].carrier == "ups"
    assert result[1].confidence == 0.45


def test_one_candidate_per_carrier_sorted_best_first() -> None:
    for raw, *_ in VALID + INVALID + FORMAT_ONLY:
        result = classify(raw, home_country="DE")
        carriers = [c.carrier for c in result]
        assert len(carriers) == len(set(carriers))
        confidences = [c.confidence for c in result]
        assert confidences == sorted(confidences, reverse=True)
        assert all(c.carrier in CARRIERS for c in result)
        assert all(0.0 <= c.confidence <= 1.0 for c in result)
        assert all(round(c.confidence, 2) == c.confidence for c in result)


def test_candidate_as_dict_is_json_safe() -> None:
    cand = classify("1Z999AA10123456784")[0]
    assert isinstance(cand, Candidate)
    data = cand.as_dict()
    assert data == {
        "carrier": "ups",
        "carrier_name": "UPS",
        "confidence": 1.0,
        "checksum_valid": True,
        "service": "Next Day Air",
        "tracking_number": "1Z999AA10123456784",
        "rule_id": "ups_1z",
        "policy": "strong",
    }
    json.dumps(data)
    assert type(data["policy"]) is str


# --- packaging ------------------------------------------------------------------------


def test_carriers_package_has_no_homeassistant_imports() -> None:
    pkg = Path(__file__).parents[2] / "custom_components" / "package_tracker" / "carriers"
    files = sorted(pkg.glob("*.py"))
    assert files
    for path in files:
        source = path.read_text(encoding="utf-8")
        assert "homeassistant" not in source, path.name


def test_iso_alpha2_table() -> None:
    from custom_components.package_tracker.carriers.rules import ISO_ALPHA2

    assert len(ISO_ALPHA2) == 249
    assert {"US", "GB", "CN", "DE", "SS", "SX"} <= ISO_ALPHA2
    assert not {"XX", "UK", "EU", "ZZ"} & ISO_ALPHA2
