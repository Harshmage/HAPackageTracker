"""Config flow (confirm-only, single instance) and options flow."""

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.package_tracker.config_flow import DEFAULT_OPTIONS
from custom_components.package_tracker.const import DOMAIN


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Package Tracker"
    entry = result["result"]
    assert entry.options == DEFAULT_OPTIONS
    assert entry.state is ConfigEntryState.LOADED


async def test_single_instance(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow_saves_and_reloads(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    async_mock_service(hass, "notify", "mobile_app_phone")
    async_mock_service(hass, "notify", "send_message")
    old_manager = setup_integration.runtime_data

    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    schema = result["data_schema"].schema
    services_selector = next(v for k, v in schema.items() if k == "notify_services")
    options = services_selector.config["options"]
    assert "mobile_app_phone" in options
    assert "send_message" not in options
    assert services_selector.config["custom_value"] is True

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "notify_time": "07:15:00",
            "notify_on": ["due_today", "arrived"],
            "notify_entities": [],
            "notify_services": ["mobile_app_phone"],
            "persistent_notification": False,
            "remind_overdue_daily": True,
            "archive_after_days": 3.0,
            "purge_after_days": 90.0,
            "fallback_tracker": "parcelsapp",
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_integration.options["archive_after_days"] == 3
    assert isinstance(setup_integration.options["archive_after_days"], int)
    assert setup_integration.options["purge_after_days"] == 90
    assert setup_integration.options["fallback_tracker"] == "parcelsapp"

    # OptionsFlowWithReload reloaded the entry: a fresh manager sees the new options.
    assert setup_integration.state is ConfigEntryState.LOADED
    manager = setup_integration.runtime_data
    assert manager is not old_manager
    assert manager.fallback == "parcelsapp"
    assert manager.archive_after_days == 3
