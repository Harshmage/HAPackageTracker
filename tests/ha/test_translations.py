"""strings.json, translations/en.json, services.yaml and icons.json stay in sync."""

import json
from pathlib import Path

from homeassistant.util.yaml import load_yaml

from custom_components.package_tracker import const
from custom_components.package_tracker.config_flow import DEFAULT_OPTIONS

ROOT = Path(__file__).parents[2] / "custom_components" / "package_tracker"


def _json(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_strings_equal_english_translation() -> None:
    assert _json("strings.json") == _json("translations/en.json")


def test_every_error_key_translated() -> None:
    exceptions = _json("strings.json")["exceptions"]
    keys = {value for name, value in vars(const).items() if name.startswith("ERR_")}
    assert keys == set(exceptions)
    assert all(exceptions[k]["message"] for k in keys)


def test_services_documented() -> None:
    services = load_yaml(str(ROOT / "services.yaml"))
    strings = _json("strings.json")["services"]
    icons = _json("icons.json")["services"]
    names = {value for name, value in vars(const).items() if name.startswith("SERVICE_")}
    assert set(services) == names == set(strings) == set(icons)
    for service, spec in services.items():
        assert strings[service]["name"] and strings[service]["description"]
        for field in spec.get("fields", {}):
            assert strings[service]["fields"][field]["name"], (service, field)
            assert strings[service]["fields"][field]["description"], (service, field)


def test_selector_options_translated() -> None:
    strings = _json("strings.json")
    services = load_yaml(str(ROOT / "services.yaml"))
    carrier_options = services["add_package"]["fields"]["carrier"]["selector"]["select"]["options"]
    assert set(carrier_options) == set(strings["selector"]["carrier"]["options"])
    assert set(strings["selector"]["notify_on"]["options"]) == set(const.NOTIFY_KINDS)
    assert set(strings["selector"]["fallback_tracker"]["options"]) == set(const.FALLBACK_TRACKERS)


def test_options_fields_translated() -> None:
    step = _json("strings.json")["options"]["step"]["init"]
    assert set(step["data"]) == set(DEFAULT_OPTIONS)
    assert set(step["data_description"]) == set(DEFAULT_OPTIONS)


def test_entity_names_and_icons() -> None:
    entity = _json("strings.json")["entity"]
    icons = _json("icons.json")["entity"]
    assert set(entity["sensor"]) == {"active", "overdue", "due_today", "next_delivery"}
    assert set(entity["calendar"]) == {"deliveries"}
    assert set(icons["sensor"]) == set(entity["sensor"])
    assert set(icons["calendar"]) == set(entity["calendar"])
