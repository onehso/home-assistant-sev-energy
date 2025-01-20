"""SEV Energy Integration."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the SEV Energy integration (YAML not used, so just return True)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up SEV Energy from a config entry."""
    _LOGGER.debug("Starting async_setup_entry for SEV Energy integration.")

    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    # Forward to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    _LOGGER.debug("SEV Energy integration setup completed.")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a SEV Energy config entry."""
    _LOGGER.debug("Unloading SEV Energy integration.")
    await hass.config_entries.async_forward_entry_unload(entry, "sensor")
    hass.data.pop(DOMAIN, None)
    return True
