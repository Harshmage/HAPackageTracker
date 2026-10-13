"""Carrier registry and link building.

Links are the product here (there is no live tracking), so every template is pinned
to its exact URL and every number is checked to be URL-quoted, never spliced raw.
"""

from urllib.parse import quote, urlsplit

import pytest

from custom_components.package_tracker.carriers import (
    CARRIERS,
    Carrier,
    Links,
    build_links,
    carrier_name,
    get_carrier,
)
from custom_components.package_tracker.carriers.registry import (
    S10_COUNTRY_CARRIER,
    numbers_from_url,
    unwrap_url,
)

TEMPLATED = [cid for cid, c in CARRIERS.items() if c.url_template]
UNTEMPLATED = [cid for cid, c in CARRIERS.items() if not c.url_template]

EXPECTED_URLS = {
    "ups": "https://www.ups.com/track?loc=en_US&tracknum=TN123",
    "usps": "https://tools.usps.com/go/TrackConfirmAction?tLabels=TN123",
    "fedex": "https://www.fedex.com/fedextrack/?trknbr=TN123",
    "dhl": "https://www.dhl.com/global-en/home/tracking.html?tracking-id=TN123&submit=1",
    "amazon": "https://track.amazon.com/tracking/TN123",
    "ontrac": "https://www.ontrac.com/tracking/?number=TN123",
    "canada_post": "https://www.canadapost-postescanada.ca/track-reperage/en#/search?searchFor=TN123",
    "royal_mail": "https://www.royalmail.com/track-your-item#/tracking-results/TN123",
    "australia_post": "https://auspost.com.au/mypost/track/#/details/TN123",
    "deutsche_post": "https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html"
    "?piececode=TN123",
    "la_poste": "https://www.laposte.fr/outils/suivre-vos-envois?code=TN123",
    "japan_post": "https://trackings.post.japanpost.jp/services/srv/search/direct"
    "?reqCodeNo1=TN123&locale=en",
}


def test_registry_contents() -> None:
    assert "unknown" in CARRIERS
    assert "intl_post" in CARRIERS
    assert set(TEMPLATED) == set(EXPECTED_URLS)
    assert set(UNTEMPLATED) == {"china_post", "india_post", "postnl", "intl_post", "unknown"}
    assert list(CARRIERS)[-1] == "unknown"
    for cid, carrier in CARRIERS.items():
        assert isinstance(carrier, Carrier)
        assert carrier.id == cid
        assert carrier.homepage.startswith("https://")
        assert all(kw == kw.upper() for kw in carrier.keywords)
        if carrier.url_template:
            assert carrier.url_template.count("{tn}") == 1


def test_s10_map_targets_exist() -> None:
    assert set(S10_COUNTRY_CARRIER.values()) <= set(CARRIERS)


@pytest.mark.parametrize(("carrier", "expected"), sorted(EXPECTED_URLS.items()))
def test_exact_template(carrier: str, expected: str) -> None:
    assert build_links(carrier, "TN123").tracking_url == expected


@pytest.mark.parametrize("carrier", TEMPLATED)
@pytest.mark.parametrize("number", ["1Z999AA10123456784", "A B/C?D&E#F%G", "../x=y"])
def test_template_builds_https_url_with_quoted_number(carrier: str, number: str) -> None:
    links = build_links(carrier, number)
    assert links.tracking_url is not None
    assert links.tracking_url.startswith("https://")
    assert quote(number, safe="") in links.tracking_url
    assert links.copy_only is False
    template = CARRIERS[carrier].url_template
    # Nothing in the number may alter the URL's structure (host/path prefix).
    prefix = template.split("{tn}")[0]
    assert links.tracking_url.startswith(prefix)
    assert urlsplit(links.tracking_url).hostname == urlsplit(prefix).hostname


@pytest.mark.parametrize("carrier", UNTEMPLATED)
def test_untemplated_carriers_are_copy_only(carrier: str) -> None:
    links = build_links(carrier, "RR123456785SE")
    assert links.tracking_url is None
    assert links.copy_only is True
    assert links.homepage_url == CARRIERS[carrier].homepage


@pytest.mark.parametrize(
    ("fallback", "expected"),
    [
        ("none", None),
        ("17track", "https://t.17track.net/en#nums=RR123456785SE"),
        ("parcelsapp", "https://parcelsapp.com/en/tracking/RR123456785SE"),
        ("bogus", None),
    ],
)
def test_fallback_modes(fallback: str, expected: str | None) -> None:
    links = build_links("intl_post", "RR123456785SE", fallback=fallback)
    assert links.fallback_url == expected
    assert links.copy_only is True


def test_fallback_default_is_none_and_number_is_quoted() -> None:
    assert build_links("unknown", "X").fallback_url is None
    assert build_links("unknown", "A/B", fallback="17track").fallback_url == (
        "https://t.17track.net/en#nums=A%2FB"
    )


def test_fallback_provided_alongside_deep_link() -> None:
    links = build_links("ups", "1Z999AA10123456784", fallback="17track")
    assert links.tracking_url is not None
    assert links.fallback_url == "https://t.17track.net/en#nums=1Z999AA10123456784"


def test_override_wins() -> None:
    override = "https://example.com/my/tracking?id=42"
    links = build_links("ups", "1Z999AA10123456784", fallback="17track", override=override)
    assert links.tracking_url == override
    assert links.copy_only is False
    assert links.homepage_url == "https://www.ups.com"
    # Even a copy-only carrier gets a deep link from an override.
    assert build_links("unknown", "X", override=override).copy_only is False


@pytest.mark.parametrize("override", ["javascript:alert(1)", "ftp://x/y", "", "   ", None])
def test_non_http_override_is_ignored(override: str | None) -> None:
    links = build_links("ups", "1Z999AA10123456784", override=override)
    assert links.tracking_url == EXPECTED_URLS["ups"].replace("TN123", "1Z999AA10123456784")


def test_links_as_dict() -> None:
    links = build_links("china_post", "LX123456785CN", fallback="parcelsapp")
    assert isinstance(links, Links)
    assert links.as_dict() == {
        "tracking_url": None,
        "homepage_url": "https://www.chinapost.com.cn",
        "fallback_url": "https://parcelsapp.com/en/tracking/LX123456785CN",
        "copy_only": True,
    }


def test_get_carrier_and_name_fall_back_to_unknown() -> None:
    assert get_carrier("ups").name == "UPS"
    assert get_carrier("nope") is CARRIERS["unknown"]
    assert carrier_name("fedex") == "FedEx"
    assert carrier_name("nope") == CARRIERS["unknown"].name
    assert build_links("nope", "X").copy_only is True


# --- link parsing ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "carrier", "values"),
    [
        ("https://www.ups.com/track?tracknum=A,B&loc=en_US", "ups", ["A", "B"]),
        ("https://wwwapps.ups.com/WebTracking/track?InquiryNumber1=X1", "ups", ["X1"]),
        ("https://track.amazon.com/tracking/TBA1", "amazon", ["TBA1"]),
        ("https://auspost.com.au/mypost/track/#/details/AP1", "australia_post", ["AP1"]),
        ("https://www.dhl.de/de/x.html?piececode=P1", "deutsche_post", ["P1"]),
        ("https://www.dhl.com/x?tracking-id=D1&submit=1", "dhl", ["D1"]),
        ("https://www.ups.com/track?loc=en_US", "ups", []),
        ("https://evilups.com/track?tracknum=Z", None, ["Z"]),  # suffix match is label-wise
        ("https://example.com/x?trackingNo=T1&utm=Q", None, ["T1"]),
        ("not a url", None, []),
    ],
)
def test_numbers_from_url(url: str, carrier: str | None, values: list[str]) -> None:
    found_carrier, found = numbers_from_url(url)
    assert (found_carrier.id if found_carrier else None) == carrier
    assert found == values


def test_unwrap_redirects() -> None:
    inner = "https://www.fedex.com/fedextrack/?trknbr=986578788855"
    google = "https://www.google.com/url?q=" + quote(inner, safe="") + "&sa=D"
    assert unwrap_url(google) == inner
    double = "https://safelinks.example/?url=" + quote(google, safe="")
    assert unwrap_url(double) == inner
