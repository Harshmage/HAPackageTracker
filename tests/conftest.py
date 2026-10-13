"""Shared fixtures: load the HA test plugin and allow custom_components to be set up."""

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Custom integrations are ignored by the HA loader unless explicitly enabled in tests."""
    return
