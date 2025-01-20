import logging
import requests
import voluptuous as vol

from homeassistant import config_entries
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class SEVEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for SEV Energy."""
    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial config flow step."""
        errors = {}

        if user_input is not None:
            user_id = user_input["user_id"]  # Brúkara ID
            api_key = user_input["api_key"]  # API-lykil

            # Check credentials by calling the API
            is_valid = await self.hass.async_add_executor_job(
                self._validate_credentials, user_id, api_key
            )
            if is_valid:
                return self.async_create_entry(title="SEV Energy", data=user_input)
            else:
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("user_id"): str,
                vol.Required("api_key"): str,
            }),
            errors=errors,
        )

    def _validate_credentials(self, user_id, api_key) -> bool:
        """Hit the SEV login endpoint to verify credentials."""
        try:
            url = "https://api.sev.fo/api/CustomerRESTApi/login_and_get_jwt_token"
            headers = {
                "Content-Type": "application/json-patch+json",
                "accept": "*/*"
            }
            payload = {"user_name": user_id, "password": api_key}

            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code == 200:
                _LOGGER.debug("SEV authentication success! JWT: %s", response.text)
                return True
            else:
                _LOGGER.error(
                    "SEV auth failed! status=%d, response=%s",
                    response.status_code,
                    response.text
                )
                return False
        except Exception as e:
            _LOGGER.error("SEV auth exception: %s", e)
            return False
