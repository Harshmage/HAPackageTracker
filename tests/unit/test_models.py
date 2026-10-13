"""Pure tests for the package model and status rules (no Home Assistant)."""

from datetime import UTC, date, datetime

import pytest

from custom_components.package_tracker.models import Package, Status, compute_status

TODAY = date(2026, 10, 12)


def make(**kwargs) -> Package:
    return Package(id="p1", tracking_number="1Z999AA10123456784", carrier="ups", **kwargs)


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (None, None, Status.NO_DATE),
        (date(2026, 10, 13), None, Status.IN_TRANSIT),
        (date(2026, 10, 12), None, Status.DUE_TODAY),
        (date(2026, 10, 11), None, Status.OVERDUE),
        # Delivery windows: due for every day of the window, overdue only after it.
        (date(2026, 10, 10), date(2026, 10, 12), Status.DUE_TODAY),
        (date(2026, 10, 12), date(2026, 10, 14), Status.DUE_TODAY),
        (date(2026, 10, 11), date(2026, 10, 13), Status.DUE_TODAY),
        (date(2026, 10, 9), date(2026, 10, 11), Status.OVERDUE),
        (date(2026, 10, 13), date(2026, 10, 15), Status.IN_TRANSIT),
    ],
)
def test_compute_status_dates(start, end, expected) -> None:
    pkg = make(expected_date=start, expected_date_end=end)
    assert compute_status(pkg, TODAY) is expected


def test_compute_status_precedence() -> None:
    now = datetime(2026, 10, 12, 15, tzinfo=UTC)
    overdue = make(expected_date=date(2026, 10, 1))
    assert compute_status(overdue, TODAY) is Status.OVERDUE
    overdue.delivered_at = now
    assert compute_status(overdue, TODAY) is Status.ARRIVED
    overdue.archived_at = now
    assert compute_status(overdue, TODAY) is Status.ARCHIVED
    # Archived without delivery is still archived.
    assert compute_status(make(archived_at=now), TODAY) is Status.ARCHIVED


def test_status_values() -> None:
    assert [s.value for s in Status] == [
        "no_date",
        "in_transit",
        "due_today",
        "overdue",
        "arrived",
        "archived",
    ]


def test_days_until() -> None:
    assert make().days_until(TODAY) is None
    assert make(expected_date=date(2026, 10, 14)).days_until(TODAY) == 2
    assert make(expected_date=date(2026, 10, 10)).days_until(TODAY) == -2
    window = make(expected_date=date(2026, 10, 10), expected_date_end=date(2026, 10, 13))
    assert window.days_until(TODAY) == 1


def test_round_trip() -> None:
    created = datetime(2026, 10, 1, 12, 30, tzinfo=UTC)
    pkg = make(
        description="Shoes",
        vendor="Shop",
        notes="Side door",
        expected_date=date(2026, 10, 13),
        expected_date_end=date(2026, 10, 15),
        tracking_url_override="https://example.com/t",
        created_at=created,
        updated_at=created,
        added_by="user1",
        delivered_at=created,
        last_status="arrived",
        notified={"due_today": "2026-10-13"},
    )
    data = pkg.to_dict()
    assert data["expected_date"] == "2026-10-13"
    assert data["created_at"] == "2026-10-01T12:30:00+00:00"
    assert Package.from_dict(data) == pkg


def test_from_dict_tolerant() -> None:
    pkg = Package.from_dict(
        {
            "id": "x",
            "tracking_number": "TBA502887274000",
            "future_field": {"nested": True},
            "expected_date": "not-a-date",
            "created_at": 12345,
            "carrier_source": "bogus",
            "notified": ["wrong", "type"],
        }
    )
    assert pkg is not None
    assert pkg.carrier == "unknown"
    assert pkg.carrier_source == "auto"
    assert pkg.expected_date is None
    assert pkg.notified == {}
    assert pkg.created_at.tzinfo is not None
    assert Package.from_dict({"id": "x"}) is None
    assert Package.from_dict({"tracking_number": "1"}) is None
    assert Package.from_dict("junk") is None  # type: ignore[arg-type]


def test_naive_datetime_assumed_utc() -> None:
    pkg = Package.from_dict(
        {"id": "x", "tracking_number": "1", "delivered_at": "2026-10-01T10:00:00"}
    )
    assert pkg is not None
    assert pkg.delivered_at == datetime(2026, 10, 1, 10, tzinfo=UTC)


def test_as_api_dict() -> None:
    pkg = make(expected_date=date(2026, 10, 11), description="Shoes")
    api = pkg.as_api_dict(TODAY, "17track")
    assert set(api) == {
        "id",
        "tracking_number",
        "carrier",
        "carrier_name",
        "carrier_source",
        "service",
        "description",
        "vendor",
        "notes",
        "expected_date",
        "expected_date_end",
        "tracking_url_override",
        "tracking_url",
        "homepage_url",
        "fallback_url",
        "copy_only",
        "status",
        "days_until",
        "created_at",
        "updated_at",
        "delivered_at",
        "archived_at",
    }
    assert api["status"] == "overdue"
    assert api["days_until"] == -1
    assert api["carrier_name"] == "UPS"
    assert api["tracking_url"].startswith("https://www.ups.com/")
    assert api["fallback_url"] == "https://t.17track.net/en#nums=1Z999AA10123456784"
    assert api["copy_only"] is False
    assert api["expected_date"] == "2026-10-11"


def test_api_dict_override_and_manual_service() -> None:
    pkg = make(tracking_url_override="https://shop.example/track", carrier_source="manual")
    api = pkg.as_api_dict(TODAY, "none")
    assert api["tracking_url"] == "https://shop.example/track"
    assert api["fallback_url"] is None
    assert api["service"] is None


def test_unknown_carrier_is_copy_only() -> None:
    pkg = Package(id="u", tracking_number="ABCDEFGH123", carrier="unknown")
    api = pkg.as_api_dict(TODAY, "none")
    assert api["tracking_url"] is None
    assert api["copy_only"] is True


def test_event_data() -> None:
    pkg = make(expected_date=date(2026, 10, 12), vendor="Shop")
    data = pkg.event_data(TODAY, "none")
    assert data == {
        "package_id": "p1",
        "tracking_number": "1Z999AA10123456784",
        "carrier": "ups",
        "carrier_name": "UPS",
        "description": None,
        "vendor": "Shop",
        "expected_date": "2026-10-12",
        "expected_date_end": None,
        "tracking_url": "https://www.ups.com/track?loc=en_US&tracknum=1Z999AA10123456784",
        "status": "due_today",
    }
