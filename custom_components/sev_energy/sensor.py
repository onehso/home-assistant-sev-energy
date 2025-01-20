import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
    UpdateFailed
)

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

FAROE_TZ = ZoneInfo("Atlantic/Faroe")
UPDATE_INTERVAL = timedelta(hours=1)  # Adjust to your preference (hours=1 => once per hour)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up SEV sensors via DataUpdateCoordinator."""

    user_id = entry.data["user_id"]
    api_key = entry.data["api_key"]

    coordinator = SEVCumulativeCoordinator(hass, user_id, api_key)

    # Fetch initial data so we can create sensors for the correct meters
    await coordinator.async_config_entry_first_refresh()

    # Create sensors: one for each meter ID
    sensors = []
    for meter_id in coordinator.meter_ids:
        sensors.append(SEVCumulativeSensor(coordinator, meter_id))

    async_add_entities(sensors, update_before_add=False)


# ------------------------------------------------
# AUTH + METER FETCH
# ------------------------------------------------
def sev_authenticate(user_id: str, api_key: str) -> str | None:
    """Authenticate to SEV, return JWT if successful."""
    url = "https://api.sev.fo/api/CustomerRESTApi/login_and_get_jwt_token"
    headers = {
        "Content-Type": "application/json-patch+json",
        "accept": "*/*"
    }
    payload = {"user_name": user_id, "password": api_key}

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            return resp.text.strip()
        else:
            _LOGGER.error("SEV auth failed: status=%d, resp=%s", resp.status_code, resp.text)
            return None
    except Exception as ex:
        _LOGGER.error("SEV auth error: %s", ex)
        return None


def sev_get_meters(jwt: str) -> list[int]:
    """Retrieve available meters from SEV, return list of meter IDs."""
    url = "https://api.sev.fo/api/CustomerRESTApi/get_available_meters"
    headers = {"Authorization": f"Bearer {jwt}"}
    try:
        resp = requests.post(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()  # A list of customers, each with installations->meters
            meter_ids = []
            for customer in data:
                for inst in customer.get("installations", []):
                    for m in inst.get("meters", []):
                        if "meter_id" in m:
                            meter_ids.append(m["meter_id"])
            return meter_ids
        else:
            _LOGGER.error("SEV get_meters failed: status=%d, resp=%s", resp.status_code, resp.text)
            return []
    except Exception as ex:
        _LOGGER.error("SEV get_meters exception: %s", ex)
        return []


def sev_fetch_cumulative_meter(jwt: str, meter_id: int) -> float | None:
    """
    Fetch the last 24h of usage for one meter, parse out the maximum `cumulative_value`.
    If no valid data, return None.
    """
    # Now in local Faroe time
    now = datetime.now(FAROE_TZ)
    from_date = now - timedelta(days=1)
    from_date_str = from_date.strftime("%Y-%m-%dT%H:%M:%S")
    to_date_str = now.strftime("%Y-%m-%dT%H:%M:%S")

    url = "https://api.sev.fo/api/CustomerRESTApi/hourly_kwh_usage"
    headers = {"Authorization": f"Bearer {jwt}"}
    payload = {
        "meters": [meter_id],
        "from_date": from_date_str,
        "to_date": to_date_str,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            # data is typically [ {"meter_id": 123, "readings": [ { "time_stamp": "...", "reading": x, "cumulative_value": y }, ... ]}, ... ]
            if isinstance(data, list) and len(data) > 0:
                readings = data[0].get("readings", [])
                if not readings:
                    _LOGGER.warning("No readings for meter %s in last 24h", meter_id)
                    return None

                # Find the maximum 'cumulative_value'
                max_val = 0.0
                for r in readings:
                    cum = r.get("cumulative_value")
                    if cum is not None and cum > max_val:
                        max_val = cum
                return max_val
            else:
                _LOGGER.error("Unexpected usage response for meter %s: %s", meter_id, data)
                return None
        else:
            _LOGGER.error(
                "Failed to fetch usage for meter=%s, status=%d, resp=%s",
                meter_id, resp.status_code, resp.text
            )
            return None
    except Exception as ex:
        _LOGGER.error("Exception fetching usage for meter=%s: %s", meter_id, ex)
        return None


# ------------------------------------------------
# DATA UPDATE COORDINATOR
# ------------------------------------------------
class SEVCumulativeCoordinator(DataUpdateCoordinator[dict]):
    """
    Fetch a cumulative reading (max 'cumulative_value') for each meter
    once per hour. We'll let Home Assistant do daily/weekly/monthly rollups
    via Utility Meter or the Energy Dashboard.
    """

    def __init__(self, hass: HomeAssistant, user_id: str, api_key: str):
        super().__init__(
            hass,
            _LOGGER,
            name="SEV Cumulative Coordinator",
            update_interval=UPDATE_INTERVAL,
        )
        self.user_id = user_id
        self.api_key = api_key
        self.meter_ids: list[int] = []
        # This coordinator will store data like: { meter_id: float (max cumulative_value) }

    async def _async_update_data(self) -> dict[int, float]:
        """
        Called periodically by HA. We:
         1) Re-auth to get JWT
         2) If needed, fetch meter IDs
         3) For each meter, fetch usage -> find max cumulative_value
        """
        # (1) Re-auth
        jwt = await self.hass.async_add_executor_job(sev_authenticate, self.user_id, self.api_key)
        if not jwt:
            raise UpdateFailed("Failed to authenticate with SEV")

        # (2) If we don't have meter IDs yet, fetch them once
        if not self.meter_ids:
            ids = await self.hass.async_add_executor_job(sev_get_meters, jwt)
            if not ids:
                raise UpdateFailed("No meters found from SEV API")
            self.meter_ids = ids

        # (3) For each meter, fetch max cumulative_value
        results = {}
        for m_id in self.meter_ids:
            val = await self.hass.async_add_executor_job(sev_fetch_cumulative_meter, jwt, m_id)
            results[m_id] = val
        return results


# ------------------------------------------------
# SENSOR ENTITY
# ------------------------------------------------
class SEVCumulativeSensor(CoordinatorEntity, SensorEntity):
    """Represents a single meter's cumulative usage reading."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = "kWh"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: SEVCumulativeCoordinator, meter_id: int):
        super().__init__(coordinator)
        self._meter_id = meter_id
        self._attr_name = f"SEV Cumulative ({meter_id})"
        self._attr_unique_id = f"sev_cumulative_{meter_id}"

    @property
    def native_value(self):
        """
        Return the "max cumulative_value" for this meter from the coordinator.
        This is an ever-increasing reading (the meter reading).
        """
        data = self.coordinator.data
        if data is None:
            return None
        return data.get(self._meter_id)

    @property
    def available(self) -> bool:
        """Mark the sensor as unavailable if we have no data or an error occurred."""
        if not super().available:
            return False
        data = self.coordinator.data or {}
        return (self._meter_id in data) and (data[self._meter_id] is not None)
