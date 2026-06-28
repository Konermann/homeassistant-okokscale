"""Measurement history and user assignment helpers for MAXXMEE BLE Scale."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any
from uuid import uuid4

from .const import DOMAIN

EVENT_WEIGHT_RECORDED = f"{DOMAIN}_weight_recorded"
MEASUREMENT_STORE_VERSION = 1
DEFAULT_USER_ID = "person_1"
DUPLICATE_WINDOW_SECONDS = 20

PERIOD_ALL = "all"
PERIOD_MORNING = "morning"
PERIOD_MIDDAY = "midday"
PERIOD_EVENING = "evening"
PERIODS = (PERIOD_ALL, PERIOD_MORNING, PERIOD_MIDDAY, PERIOD_EVENING)


def utc_now_iso() -> str:
    """Return the current time as an ISO UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_datetime(value: str | None) -> datetime:
    """Parse an ISO timestamp, defaulting to now on empty input."""
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def period_for_time(value: datetime) -> str:
    """Classify a timestamp into a coarse day period."""
    local_time = value.timetz().replace(tzinfo=None)
    if time(5, 0) <= local_time < time(11, 0):
        return PERIOD_MORNING
    if time(11, 0) <= local_time < time(17, 0):
        return PERIOD_MIDDAY
    return PERIOD_EVENING


def default_measurement_data() -> dict[str, Any]:
    """Return empty measurement data with one default user."""
    return {
        "users": [
            {
                "id": DEFAULT_USER_ID,
                "name": "Person 1",
                "created_at": utc_now_iso(),
            }
        ],
        "measurements": [],
    }


def ensure_measurement_data(data: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize stored measurement data."""
    if not data:
        return default_measurement_data()

    normalized = {
        "users": list(data.get("users", [])),
        "measurements": list(data.get("measurements", [])),
    }
    if not normalized["users"]:
        normalized["users"] = default_measurement_data()["users"]
    return normalized


def user_name(data: Mapping[str, Any], user_id: str) -> str:
    """Return a user display name."""
    for user in data.get("users", []):
        if user["id"] == user_id:
            return user["name"]
    return user_id


def slugify_user_id(name: str, existing_ids: set[str]) -> str:
    """Create a stable user id from a name."""
    slug = "".join(
        char.lower() if char.isalnum() else "_" for char in name.strip()
    ).strip("_")
    slug = slug or "person"
    candidate = slug
    counter = 2
    while candidate in existing_ids:
        candidate = f"{slug}_{counter}"
        counter += 1
    return candidate


def latest_measurement(
    data: Mapping[str, Any],
    user_id: str,
    period: str = PERIOD_ALL,
) -> dict[str, Any] | None:
    """Return the latest measurement for a user and period."""
    for measurement in reversed(data.get("measurements", [])):
        if measurement.get("user_id") != user_id:
            continue
        if period != PERIOD_ALL and measurement.get("period") != period:
            continue
        return dict(measurement)
    return None


def recent_measurements(
    data: Mapping[str, Any],
    user_id: str,
    period: str = PERIOD_ALL,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return recent measurements for a user and period."""
    results: list[dict[str, Any]] = []
    for measurement in reversed(data.get("measurements", [])):
        if measurement.get("user_id") != user_id:
            continue
        if period != PERIOD_ALL and measurement.get("period") != period:
            continue
        results.append(dict(measurement))
        if len(results) >= limit:
            return results
    return results


def assign_user_for_weight(data: Mapping[str, Any], weight_kg: float) -> str:
    """Assign a new measurement to the user with the closest last weight."""
    users = data.get("users", [])
    if not users:
        return DEFAULT_USER_ID
    if len(users) == 1:
        return users[0]["id"]

    closest_user_id: str | None = None
    closest_distance: float | None = None
    for user in users:
        user_id = user["id"]
        last_measurement = latest_measurement(data, user_id)
        if last_measurement is None:
            continue

        distance = abs(float(last_measurement["weight_kg"]) - weight_kg)
        if closest_distance is None or distance < closest_distance:
            closest_distance = distance
            closest_user_id = user_id

    return closest_user_id or users[0]["id"]


def find_recent_duplicate(
    data: Mapping[str, Any],
    user_id: str,
    weight_kg: float,
    measured_at: datetime,
) -> dict[str, Any] | None:
    """Find a likely duplicate broadcast from the same weighing burst."""
    for measurement in reversed(data.get("measurements", [])):
        if measurement.get("user_id") != user_id:
            continue
        if abs(float(measurement["weight_kg"]) - weight_kg) > 0.005:
            continue

        previous_time = parse_datetime(measurement.get("measured_at"))
        delta = abs((measured_at - previous_time).total_seconds())
        if delta <= DUPLICATE_WINDOW_SECONDS:
            return dict(measurement)
        return None
    return None


def add_measurement_to_data(
    data: dict[str, Any],
    weight_kg: float,
    measured_at: str | None = None,
    user_id: str | None = None,
    source: str = "ble",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Add a measurement, returning None for duplicate broadcasts."""
    measured_datetime = parse_datetime(measured_at)
    assigned_user_id = user_id or assign_user_for_weight(data, weight_kg)
    if find_recent_duplicate(data, assigned_user_id, weight_kg, measured_datetime):
        return None

    measurement = {
        "id": uuid4().hex,
        "user_id": assigned_user_id,
        "weight_kg": round(float(weight_kg), 2),
        "measured_at": measured_datetime.isoformat(timespec="seconds"),
        "created_at": utc_now_iso(),
        "period": period_for_time(measured_datetime),
        "source": source,
    }
    if metadata:
        measurement["metadata"] = dict(metadata)

    data["measurements"].append(measurement)
    return dict(measurement)


def update_measurement_in_data(
    data: dict[str, Any],
    measurement_id: str,
    weight_kg: float | None = None,
    user_id: str | None = None,
    measured_at: str | None = None,
) -> dict[str, Any] | None:
    """Update one stored measurement."""
    for measurement in data.get("measurements", []):
        if measurement.get("id") != measurement_id:
            continue
        if weight_kg is not None:
            measurement["weight_kg"] = round(float(weight_kg), 2)
        if user_id is not None:
            measurement["user_id"] = user_id
        if measured_at is not None:
            measured_datetime = parse_datetime(measured_at)
            measurement["measured_at"] = measured_datetime.isoformat(
                timespec="seconds"
            )
            measurement["period"] = period_for_time(measured_datetime)
        measurement["updated_at"] = utc_now_iso()
        return dict(measurement)
    return None


def delete_measurement_from_data(
    data: dict[str, Any],
    measurement_id: str,
) -> dict[str, Any] | None:
    """Delete one stored measurement."""
    measurements = data.get("measurements", [])
    for index, measurement in enumerate(measurements):
        if measurement.get("id") == measurement_id:
            return dict(measurements.pop(index))
    return None


@dataclass
class OKOKScaleMeasurementStore:
    """Persistent measurement store backed by Home Assistant storage."""

    hass: Any
    entry_id: str
    data: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """Initialize listeners."""
        self._listeners: list[Callable[[], None]] = []
        self._store: Any = None

    async def async_load(self) -> None:
        """Load stored measurements."""
        from homeassistant.helpers.storage import Store

        self._store = Store(
            self.hass,
            MEASUREMENT_STORE_VERSION,
            f"{DOMAIN}_measurements_{self.entry_id}",
        )
        self.data = ensure_measurement_data(await self._store.async_load())
        await self._async_save()

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a listener for store updates."""
        self._listeners.append(listener)

        def remove_listener() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove_listener

    def users(self) -> list[dict[str, Any]]:
        """Return stored users."""
        assert self.data is not None
        return [dict(user) for user in self.data["users"]]

    def user_exists(self, user_id: str) -> bool:
        """Return true if a user exists."""
        assert self.data is not None
        return any(user["id"] == user_id for user in self.data["users"])

    def latest_measurement(
        self,
        user_id: str,
        period: str = PERIOD_ALL,
    ) -> dict[str, Any] | None:
        """Return the latest measurement for a user and period."""
        assert self.data is not None
        return latest_measurement(self.data, user_id, period)

    def recent_measurements(
        self,
        user_id: str,
        period: str = PERIOD_ALL,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return recent measurements for a user and period."""
        assert self.data is not None
        return recent_measurements(self.data, user_id, period, limit)

    async def async_add_user(self, name: str) -> dict[str, Any]:
        """Add a user."""
        assert self.data is not None
        existing_ids = {user["id"] for user in self.data["users"]}
        user = {
            "id": slugify_user_id(name, existing_ids),
            "name": name.strip() or "Person",
            "created_at": utc_now_iso(),
        }
        self.data["users"].append(user)
        await self._async_changed()
        return dict(user)

    async def async_rename_user(self, user_id: str, name: str) -> dict[str, Any] | None:
        """Rename a user."""
        assert self.data is not None
        for user in self.data["users"]:
            if user["id"] == user_id:
                user["name"] = name.strip() or user["name"]
                user["updated_at"] = utc_now_iso()
                await self._async_changed()
                return dict(user)
        return None

    async def async_record_measurement(
        self,
        weight_kg: float,
        measured_at: str | None = None,
        user_id: str | None = None,
        source: str = "ble",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Record a new measurement."""
        assert self.data is not None
        if measured_at is None:
            from homeassistant.util import dt as dt_util

            measured_at = dt_util.now().isoformat(timespec="seconds")
        measurement = add_measurement_to_data(
            self.data,
            weight_kg=weight_kg,
            measured_at=measured_at,
            user_id=user_id,
            source=source,
            metadata=metadata,
        )
        if measurement is None:
            return None

        await self._async_changed()
        self.hass.bus.async_fire(
            EVENT_WEIGHT_RECORDED,
            self._event_payload(measurement),
        )
        return measurement

    async def async_update_measurement(
        self,
        measurement_id: str,
        weight_kg: float | None = None,
        user_id: str | None = None,
        measured_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Update an existing measurement."""
        assert self.data is not None
        measurement = update_measurement_in_data(
            self.data,
            measurement_id=measurement_id,
            weight_kg=weight_kg,
            user_id=user_id,
            measured_at=measured_at,
        )
        if measurement is None:
            return None
        await self._async_changed()
        return measurement

    async def async_delete_measurement(
        self,
        measurement_id: str,
    ) -> dict[str, Any] | None:
        """Delete an existing measurement."""
        assert self.data is not None
        measurement = delete_measurement_from_data(self.data, measurement_id)
        if measurement is None:
            return None
        await self._async_changed()
        return measurement

    async def _async_changed(self) -> None:
        """Persist and notify listeners."""
        await self._async_save()
        for listener in list(self._listeners):
            listener()

    async def _async_save(self) -> None:
        """Persist store data."""
        assert self._store is not None
        await self._store.async_save(deepcopy(self.data))

    def _event_payload(self, measurement: Mapping[str, Any]) -> dict[str, Any]:
        """Build a Home Assistant event payload."""
        assert self.data is not None
        payload = dict(measurement)
        payload["entry_id"] = self.entry_id
        payload["user_name"] = user_name(self.data, measurement["user_id"])
        payload["apple_health_shortcut_text"] = (
            f'{payload["user_name"]};{payload["weight_kg"]};'
            f'{payload["measured_at"]}'
        )
        return payload
