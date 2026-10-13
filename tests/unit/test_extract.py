"""Extraction of tracking numbers from pasted text and email fixtures."""

from pathlib import Path

import pytest

from custom_components.package_tracker.carriers import ExtractResult, extract
from custom_components.package_tracker.carriers.extract import MAX_RESULTS, MAX_TEXT

FIXTURES = Path(__file__).parent.parent / "fixtures" / "emails"

# Words that satisfy every keyword requirement, so negatives below fail on their own
# filter rather than for lack of context.
KEYWORDS = "Your package tracking: FedEx UPS USPS DHL Canada Post shipment. "


def _read(name: str) -> str:
    # newline="" keeps the CRLF line endings of the .eml fixture intact.
    return (FIXTURES / name).read_text(encoding="utf-8", newline="")


def _numbers(text: str, **kwargs) -> list[str]:
    return [r.tracking_number for r in extract(text, **kwargs)]


# --- Fixtures ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("ups.txt", [("1Z999AA10123456784", "ups")]),
        ("fedex.html", [("477179081230", "fedex"), ("986578788855", "fedex")]),
        (
            "usps_informed.txt",
            [
                ("9400109699939938223564", "usps"),
                ("9205590164917312751089", "usps"),
                ("9101026837331000039521", "usps"),
            ],
        ),
        (
            "dhl_intl.txt",
            [("3318810025", "dhl"), ("JJD0099999999", "dhl"), ("RR123456785GB", "royal_mail")],
        ),
        ("amazon.html", []),
        ("quoted_printable.eml", [("1Z12345E6605272234", "ups")]),
        ("safelinks.html", [("1Z5R89390357567127", "ups")]),
        ("noise.txt", []),
        ("zero_width.txt", [("1Z999AA10123456784", "ups"), ("9400109699939938223564", "usps")]),
    ],
)
def test_fixture(fixture: str, expected: list[tuple[str, str]]) -> None:
    results = extract(_read(fixture))
    assert [(r.tracking_number, r.carrier) for r in results] == expected


def test_fixture_sources() -> None:
    (safelink,) = extract(_read("safelinks.html"))
    assert safelink.source == "link"
    (qp,) = extract(_read("quoted_printable.eml"))
    # The number is both the anchor text and in the link; the text comes first.
    assert qp.source == "text"
    assert "Tracking number" in qp.context


def test_fedex_html_ignores_head_style_script_and_comments() -> None:
    text = _read("fedex.html")
    assert "041441760228964" in text
    assert "041441760228964" not in _numbers(text)


def test_dhl_reference_number_is_rejected() -> None:
    # "Shipper reference: 73891051146" is a valid DHL waybill shape next to "DHL".
    assert "73891051146" not in _numbers(_read("dhl_intl.txt"))


# --- Negatives from the plan ---------------------------------------------------------

NEGATIVES = [
    "Call 1-800-555-1212",
    "Call (800) 555-1212 for help",
    "+1 331 881 0025",
    "Order #123456789012",
    "Order #477179081230",
    "112-1234567-1234567",
    "60601-1234",
    "10/12/2026 14:35",
    "2026-10-12 14:35:00",
    "Delivered 15.10.2026 at 09:30",
    "202610121430",
    "IMEI 490154203237518",
    "Gift card 6006 4912 3456 7890",
    "ICCID 89014103211118510720",
    "ID 123456789 IN",
    "https://click.example.com/ls/click?uid=9400109699939938223564&utm_source=email",
    "Customer ID 3318810025",
    "Invoice: 986578788855",
    "Reference Number 1: 3318810025",
    "Card ending in 1Z999AA10123456784",
    "Serial number: 9400109699939938223564",
    "SKU 9205590164917312751089",
    "P.O. 3318810025",
    "Ticket 986578788855",
]


@pytest.mark.parametrize("text", NEGATIVES)
def test_negative(text: str) -> None:
    assert extract(text) == []


@pytest.mark.parametrize("text", NEGATIVES)
def test_negative_even_with_keywords(text: str) -> None:
    assert extract(KEYWORDS + text) == []


@pytest.mark.parametrize(
    "text",
    [
        "tracking 123456789012",  # sequential digits, valid FedEx check digit
        "Canada Post 1234567890123456",
        "tracking 000000000000",
    ],
)
def test_repetitive_or_sequential_non_strong_rejected(text: str) -> None:
    assert extract(text) == []


def test_strong_prefix_survives_repetition_filter() -> None:
    assert _numbers("TBA000000000000") == ["TBA000000000000"]


# --- Acceptance policies -------------------------------------------------------------


@pytest.mark.parametrize(
    "number",
    [
        "1Z999AA10123456784",
        "1Z5R89390357567127",
        "TBA502887274000",
        "EA123456785US",
        "RR123456785GB",
        "GM2951173225174494",
        "JJD0099999999",
        "1LSCXVE005BUEFX",
    ],
)
def test_strong_needs_no_keyword(number: str) -> None:
    assert _numbers(f"Here it is: {number}.") == [number]


@pytest.mark.parametrize(
    "number",
    [
        "9400109699939938223564",
        "92748931507708513018050063",
        "03071790000523483741",
        "986578788855",
        "041441760228964",
        "9611020987654312345672",
        "3318810025",
        "73891051146",
        "C11031500001879",
    ],
)
def test_checksum_policy_needs_keyword(number: str) -> None:
    assert _numbers(f"See {number} for details") == []
    assert _numbers(f"Your tracking number is {number}") == [number]
    assert _numbers(f"Waybill: {number}") == [number]


@pytest.mark.parametrize(
    "keyword", ["TRACK", "Tracking", "WAYBILL", "AWB", "shipment", "Parcel", "package", "FedEx"]
)
def test_checksum_keywords(keyword: str) -> None:
    assert _numbers(f"{keyword} 986578788855") == ["986578788855"]


def test_keyword_must_be_word_bounded() -> None:
    assert _numbers("Backtracking 986578788855") == []
    assert _numbers("Trackers 986578788855") == []


def test_keyword_must_be_within_80_chars() -> None:
    assert _numbers("Tracking" + " " * 72 + "986578788855") == ["986578788855"]
    assert _numbers("Tracking" + " " * 90 + "986578788855") == []
    assert _numbers("986578788855" + " " * 90 + "tracking") == []


def test_amazon_is_not_a_checksum_keyword() -> None:
    assert _numbers("Amazon 986578788855") == []


def test_checksum_must_be_valid_in_text() -> None:
    assert _numbers("Tracking 996578788855") == []
    assert _numbers("UPS tracking 1Z999AA10123456780") == []
    assert _numbers("Tracking 3318810010") == []


def test_context_policy_needs_carrier_keyword() -> None:
    assert _numbers("Tracking LX17635036") == []
    assert _numbers("LaserShip tracking LX17635036") == ["LX17635036"]
    assert _numbers("Tracking 7023210039414604") == []
    assert _numbers("Canada Post: 7023210039414604") == ["7023210039414604"]


def test_fedex_34_canonicalized() -> None:
    assert _numbers("tracking 9622001900000000000000776632517510") == ["776632517510"]


def test_usps_420_routing_canonicalized() -> None:
    text = "Tracking: 4201028200009261290113185417468510"
    assert _numbers(text) == ["9261290113185417468510"]


# --- Grouped (printed) numbers -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tracking: 1Z 999A A101 2345 6784", "1Z999AA10123456784"),
        ("UPS: 1Z 999 AA1 01 2345 678 4 arriving 10/13", "1Z999AA10123456784"),
        ("Tracking #: 9400 1096 9993 9938 2235 64", "9400109699939938223564"),
        ("USPS 9400-1096-9993-9938-2235-64", "9400109699939938223564"),
        ("Tracking 9274 8931 5077 0851 3018 0500 63", "92748931507708513018050063"),
        ("FedEx tracking 9865 7878 8855", "986578788855"),
        ("Registered EA 123 456 785 US tracking", "EA123456785US"),
        ("Tracking number: 9400 1096 9993 9938 2235 64 on 10/13", "9400109699939938223564"),
    ],
)
def test_grouped(text: str, expected: str) -> None:
    assert _numbers(text) == [expected]


def test_grouped_needs_keyword_even_for_strong() -> None:
    assert _numbers("1Z 999A A101 2345 6784") == []
    assert _numbers("9400 1096 9993 9938 2235 64") == []


def test_grouped_two_numbers_in_one_run() -> None:
    text = "Tracking: 9400 1096 9993 9938 2235 64 or 9205 5901 6491 7312 7510 89"
    assert _numbers(text) == ["9400109699939938223564", "9205590164917312751089"]


def test_grouped_rejects_unknown_layouts_and_word_joins() -> None:
    # 3+ letter words end a run, so the keyword itself is never glued to the number.
    assert _numbers("Tracking 98 6578 788855 99") == []
    # Three 4-digit groups inside a longer digit run are not a FedEx number.
    assert _numbers("FedEx tracking 1234 9865 7878 8855") == []


def test_grouped_separators_must_be_single() -> None:
    assert _numbers("Tracking 9400  1096  9993  9938  2235  64") == []
    assert _numbers("Tracking 9400 - 1096 - 9993 - 9938 - 2235 - 64") == []


# --- Links ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.ups.com/track?loc=en_US&tracknum=1Z999AA10123456784", "1Z999AA10123456784"),
        (
            "https://wwwapps.ups.com/WebTracking/track?InquiryNumber1=1Z5R89390357567127",
            "1Z5R89390357567127",
        ),
        (
            "https://tools.usps.com/go/TrackConfirmAction?qtc_tLabels1=9205590164917312751089",
            "9205590164917312751089",
        ),
        ("https://www.fedex.com/fedextrack/?trknbr=477179081230", "477179081230"),
        (
            "https://www.dhl.com/global-en/home/tracking.html?tracking-id=3318810025&submit=1",
            "3318810025",
        ),
        ("https://track.amazon.com/tracking/TBA502887274000", "TBA502887274000"),
        (
            "https://www.google.com/url?q=https%3A%2F%2Ftools.usps.com%2Fgo%2FTrackConfirmAction"
            "%3FtLabels%3D9205590164917312751089&sa=D",
            "9205590164917312751089",
        ),
        (
            "https://shop.example.com/orders/track?trackingNumber=9400109699939938223564",
            "9400109699939938223564",
        ),
    ],
)
def test_links(url: str, expected: str) -> None:
    results = extract(f"Click here: {url} thanks")
    assert [r.tracking_number for r in results] == [expected]
    assert results[0].source == "link"


def test_link_with_several_numbers() -> None:
    url = "https://www.ups.com/track?tracknum=1Z999AA10123456784,1Z5R89390357567127"
    assert _numbers(url) == ["1Z999AA10123456784", "1Z5R89390357567127"]


def test_link_carrier_host_reads_only_allowed_params() -> None:
    assert _numbers("https://www.ups.com/track?loc=en_US&ref=1Z999AA10123456784") == []


def test_link_generic_host_needs_high_confidence() -> None:
    # FedEx 12 tops out at 0.6 without context: not enough on an unknown host.
    assert _numbers("https://shop.example.com/track?trackingNumber=986578788855") == []
    # Parameter names that don't look tracking-related are ignored entirely.
    assert _numbers("https://shop.example.com/x?uid=1Z999AA10123456784") == []


def test_link_unknown_format_on_carrier_host() -> None:
    (result,) = extract("https://www.ontrac.com/tracking/?number=ZZ12345678XY")
    assert (result.tracking_number, result.carrier, result.confidence) == (
        "ZZ12345678XY",
        "ontrac",
        0.6,
    )


def test_urls_are_removed_before_text_passes() -> None:
    text = "Tracking info https://example.com/p/1Z999AA10123456784/view"
    assert _numbers(text) == []


def test_html_href_collected() -> None:
    html = (
        '<html><body><p><a href="https://www.fedex.com/fedextrack/?trknbr=477179081230&amp;'
        'x=1">Track</a></p></body></html>'
    )
    assert _numbers(html) == ["477179081230"]


# --- Preprocessing -------------------------------------------------------------------


def test_quoted_printable_plain_text() -> None:
    text = "Tracking number: 1Z999AA1=\n0123456784 =3D done"
    assert _numbers(text) == ["1Z999AA10123456784"]


def test_plain_text_not_qp_decoded() -> None:
    # "=12" must not be turned into a control character when the text isn't QP.
    assert _numbers("https://www.ups.com/track?a=12&tracknum=1Z999AA10123456784") == [
        "1Z999AA10123456784"
    ]


def test_zero_width_and_soft_hyphen_removed() -> None:
    assert _numbers("1Z999AA1\u200b0123\u00ad456784\ufeff") == ["1Z999AA10123456784"]


def test_fullwidth_and_unicode_dashes() -> None:
    assert _numbers("Tracking: \uff11\uff3a999AA10123456784") == ["1Z999AA10123456784"]
    assert _numbers("Tracking 9400\u20131096\u20139993\u20139938\u20132235\u201364") == [
        "9400109699939938223564"
    ]


def test_html_entities_and_nbsp() -> None:
    html = (
        "<div>USPS&nbsp;tracking:&nbsp;9400&nbsp;1096&nbsp;9993&nbsp;9938&nbsp;2235&nbsp;64</div>"
    )
    assert _numbers(html) == ["9400109699939938223564"]


def test_lowercase_input() -> None:
    assert _numbers("tracking 1z999aa10123456784") == ["1Z999AA10123456784"]


# --- Dedupe, ordering, caps ----------------------------------------------------------


def test_dedupe_and_order_of_first_appearance() -> None:
    text = (
        "TBA502887274000 then 1Z999AA10123456784 and again TBA502887274000 "
        "https://www.ups.com/track?tracknum=1Z999AA10123456784"
    )
    assert _numbers(text) == ["TBA502887274000", "1Z999AA10123456784"]


def test_link_and_text_order_interleaved() -> None:
    text = "https://www.ups.com/track?tracknum=1Z5R89390357567127 then 1Z999AA10123456784"
    assert _numbers(text) == ["1Z5R89390357567127", "1Z999AA10123456784"]


def test_canonical_dedupe_across_forms() -> None:
    text = "Tracking 420221539101026837331000039521 aka 9101026837331000039521"
    assert _numbers(text) == ["9101026837331000039521"]


def test_substring_suppressed() -> None:
    # 3150770851 is a checksum-valid DHL waybill that is also a slice of the IMpb.
    text = "tracking 92748931507708513018050063, DHL waybill 3150770851"
    assert _numbers("DHL waybill 3150770851") == ["3150770851"]
    assert _numbers(text) == ["92748931507708513018050063"]


def test_result_cap() -> None:
    text = " ".join(f"TBA{100_000_000_000 + i}" for i in range(80))
    assert len(extract(text)) == MAX_RESULTS


def test_input_cap() -> None:
    assert _numbers("x" * MAX_TEXT + " 1Z999AA10123456784") == []
    assert _numbers("x " * 10 + "1Z999AA10123456784") == ["1Z999AA10123456784"]


@pytest.mark.parametrize("text", ["", None, 123, "   ", "<<<>>>", "=\n=3D", "<a href='x'>"])
def test_never_raises(text) -> None:
    assert extract(text) == []


def test_large_input_is_fast_enough() -> None:
    chunk = "Order 112-1234567-1234567 tracking 1Z999AA10123456784 call 1-800-555-1212\n"
    results = extract(chunk * (MAX_TEXT // len(chunk) + 1))
    assert _numbers(chunk) == ["1Z999AA10123456784"]
    assert [r.tracking_number for r in results] == ["1Z999AA10123456784"]


def test_context_snippet() -> None:
    text = "Hello. " * 30 + "Your tracking number is 1Z999AA10123456784. " + "Bye. " * 30
    (result,) = extract(text)
    assert len(result.context) <= 80
    assert "1Z999AA10123456784" in result.context
    assert "\n" not in result.context


def test_home_country_adds_destination_post() -> None:
    (result,) = extract("Tracking RR123456785GB", home_country="DE")
    assert result.carrier == "royal_mail"
    assert [c.carrier for c in result.candidates] == ["royal_mail", "deutsche_post"]


# --- as_dict -------------------------------------------------------------------------


def test_as_dict_untracked() -> None:
    (result,) = extract("Tracking: 1Z999AA10123456784")
    assert isinstance(result, ExtractResult)
    data = result.as_dict()
    assert data["tracking_number"] == "1Z999AA10123456784"
    assert data["carrier"] == "ups"
    assert data["carrier_name"] == "UPS"
    assert data["source"] == "text"
    assert data["checksum_valid"] is True
    assert data["confidence"] == 1.0
    assert data["already_tracked"] is False
    assert data["package_id"] is None
    assert data["tracking_url"] == "https://www.ups.com/track?loc=en_US&tracknum=1Z999AA10123456784"
    assert data["fallback_url"] is None
    assert data["candidates"][0]["carrier"] == "ups"
    assert data["candidates"][0]["policy"] == "strong"
    assert "Tracking" in data["context"]


def test_as_dict_tracked_and_fallback() -> None:
    (result,) = extract("Tracking: 1Z999AA10123456784")
    data = result.as_dict(tracked={"1Z999AA10123456784": "01JABC"}, fallback="17track")
    assert data["already_tracked"] is True
    assert data["package_id"] == "01JABC"
    assert data["fallback_url"] == "https://t.17track.net/en#nums=1Z999AA10123456784"
    other = result.as_dict(tracked={"TBA502887274000": "x"})
    assert other["already_tracked"] is False
    assert other["package_id"] is None


def test_as_dict_carrier_without_template() -> None:
    (result,) = extract("Registered LX123456785CN")
    data = result.as_dict(fallback="parcelsapp")
    assert data["carrier"] == "china_post"
    assert data["tracking_url"] is None
    assert data["copy_only"] is True
    assert data["fallback_url"] == "https://parcelsapp.com/en/tracking/LX123456785CN"
