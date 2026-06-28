"""Support for OKOK Scale sensors."""

from homeassistant import config_entries
from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothDataProcessor,
    PassiveBluetoothDataUpdate,
    PassiveBluetoothEntityKey,
    PassiveBluetoothProcessorCoordinator,
    PassiveBluetoothProcessorEntity,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfMass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.sensor import sensor_device_info_to_hass_device_info

from .const import DOMAIN
from .device import device_key_to_bluetooth_entity_key
from .measurements import PERIODS, PERIOD_ALL
from .okokscale import SensorDeviceClass as OKOKScaleSensorDeviceClass
from .okokscale import SensorUpdate, Units
from .runtime import OKOKScaleRuntimeData

# Coordinator is used to centralize the data updates
PARALLEL_UPDATES = 0

SENSOR_DESCRIPTIONS: dict[str, SensorEntityDescription] = {
    (OKOKScaleSensorDeviceClass.MASS, Units.MASS_KILOGRAMS): SensorEntityDescription(
        key="weight",
        device_class=SensorDeviceClass.WEIGHT,
        icon="mdi:scale-bathroom",
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    (OKOKScaleSensorDeviceClass.MASS, Units.MASS_POUNDS): SensorEntityDescription(
        key="weight",
        device_class=SensorDeviceClass.WEIGHT,
        icon="mdi:scale-bathroom",
        native_unit_of_measurement=UnitOfMass.POUNDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    (
        OKOKScaleSensorDeviceClass.SIGNAL_STRENGTH,
        Units.SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    ): SensorEntityDescription(
        key=OKOKScaleSensorDeviceClass.SIGNAL_STRENGTH,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    (OKOKScaleSensorDeviceClass.BATTERY, Units.PERCENTAGE): SensorEntityDescription(
        key=f"{OKOKScaleSensorDeviceClass.BATTERY}_percent",
        device_class=SensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    (OKOKScaleSensorDeviceClass.IMPEDANCE, Units.OHM): SensorEntityDescription(
        key=OKOKScaleSensorDeviceClass.IMPEDANCE,
        icon="mdi:omega",
        translation_key=OKOKScaleSensorDeviceClass.IMPEDANCE,
        native_unit_of_measurement="Ω",
        state_class=SensorStateClass.MEASUREMENT,
    ),
}


def sensor_update_to_bluetooth_data_update(
    sensor_update: SensorUpdate,
) -> PassiveBluetoothDataUpdate:
    """Convert a sensor update to a bluetooth data update."""
    entity_descriptions: dict[PassiveBluetoothEntityKey, EntityDescription] = {
        device_key_to_bluetooth_entity_key(device_key): SENSOR_DESCRIPTIONS[
            (description.device_class, description.native_unit_of_measurement)
        ]
        for device_key, description in sensor_update.entity_descriptions.items()
        if description.device_class
    }

    return PassiveBluetoothDataUpdate(
        devices={
            device_id: sensor_device_info_to_hass_device_info(device_info)
            for device_id, device_info in sensor_update.devices.items()
        },
        entity_descriptions=entity_descriptions,
        entity_data={
            device_key_to_bluetooth_entity_key(device_key): sensor_values.native_value
            for device_key, sensor_values in sensor_update.entity_values.items()
        },
        entity_names={
            device_key_to_bluetooth_entity_key(device_key): sensor_values.name
            for device_key, sensor_values in sensor_update.entity_values.items()
            # Add names where the entity description has neither a translation_key nor
            # a device_class
            if (
                description := entity_descriptions.get(
                    device_key_to_bluetooth_entity_key(device_key)
                )
            )
            is None
            or (
                description.translation_key is None and description.device_class is None
            )
        },
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the OKOK Scale sensors."""
    runtime_data: OKOKScaleRuntimeData = entry.runtime_data
    coordinator: PassiveBluetoothProcessorCoordinator | None = (
        runtime_data.coordinator
    )
    assert coordinator is not None
    processor = PassiveBluetoothDataProcessor(sensor_update_to_bluetooth_data_update)
    entry.async_on_unload(
        processor.async_add_entities_listener(
            OKOKScaleBluetoothSensorEntity, async_add_entities
        )
    )
    entry.async_on_unload(
        coordinator.async_register_processor(processor, SensorEntityDescription)
    )

    measurement_store = runtime_data.measurement_store
    if measurement_store is None:
        return

    measurement_entities: dict[
        tuple[str, str], OKOKScaleMeasurementSensorEntity
    ] = {}

    def _add_missing_measurement_entities() -> None:
        new_entities: list[OKOKScaleMeasurementSensorEntity] = []
        for user in measurement_store.users():
            for period in PERIODS:
                key = (user["id"], period)
                if key in measurement_entities:
                    continue
                entity = OKOKScaleMeasurementSensorEntity(
                    entry,
                    measurement_store,
                    user["id"],
                    period,
                )
                measurement_entities[key] = entity
                new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    def _handle_measurements_updated() -> None:
        _add_missing_measurement_entities()
        for entity in measurement_entities.values():
            if entity.hass is not None:
                entity.async_write_ha_state()

    _add_missing_measurement_entities()
    entry.async_on_unload(measurement_store.add_listener(_handle_measurements_updated))


class OKOKScaleBluetoothSensorEntity(
    PassiveBluetoothProcessorEntity[
        PassiveBluetoothDataProcessor[str | float | None, SensorUpdate]
    ],
    SensorEntity,
):
    """Representation of an OKOK Scale sensor."""

    @property
    def native_value(self) -> str | float | None:
        """Return the native value."""
        return self.processor.entity_data.get(self.entity_key)

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        The sensor is only created when the device is seen.

        Since these are sleepy devices which stop broadcasting
        when not in use, we can't rely on the last update time
        so once we have seen the device we always return True.
        """
        return True

    @property
    def assumed_state(self) -> bool:
        """Return True if the device is no longer broadcasting."""
        return not self.processor.available


class OKOKScaleMeasurementSensorEntity(SensorEntity):
    """Graphable per-user measurement sensor."""

    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_icon = "mdi:scale-bathroom"
    _attr_native_unit_of_measurement = UnitOfMass.KILOGRAMS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        entry: config_entries.ConfigEntry,
        measurement_store,
        user_id: str,
        period: str,
    ) -> None:
        """Initialize a per-user measurement sensor."""
        self._entry = entry
        self._measurement_store = measurement_store
        self._user_id = user_id
        self._period = period
        assert entry.unique_id is not None
        self._attr_unique_id = (
            f"{entry.unique_id}_measurement_{user_id}_{period}"
        )

    @property
    def name(self) -> str:
        """Return sensor name."""
        user = self._user()
        if self._period == PERIOD_ALL:
            return f"{user['name']} weight"
        return f"{user['name']} {self._period} weight"

    @property
    def native_value(self) -> float | None:
        """Return latest measurement value."""
        measurement = self._measurement()
        if measurement is None:
            return None
        return measurement["weight_kg"]

    @property
    def available(self) -> bool:
        """Return true once the user has a measurement for this period."""
        return self._measurement() is not None

    @property
    def extra_state_attributes(self):
        """Return measurement metadata."""
        measurement = self._measurement()
        user = self._user()
        attrs = {
            "user_id": self._user_id,
            "user_name": user["name"],
            "period": self._period,
            "recent_measurements": self._recent_measurements(),
        }
        if measurement is not None:
            attrs.update(
                {
                    "measurement_id": measurement["id"],
                    "measured_at": measurement["measured_at"],
                    "source": measurement.get("source"),
                }
            )
        return attrs

    @property
    def device_info(self):
        """Return parent scale device info."""
        assert self._entry.unique_id is not None
        return {
            "identifiers": {(DOMAIN, self._entry.unique_id)},
            "manufacturer": "MAXXMEE",
            "name": self._entry.title or "MAXXMEE BLE Scale",
        }

    def _measurement(self):
        """Return the relevant latest measurement."""
        return self._measurement_store.latest_measurement(
            self._user_id,
            self._period,
        )

    def _recent_measurements(self) -> list[dict]:
        """Return compact recent measurement metadata."""
        measurements = self._measurement_store.recent_measurements(
            self._user_id,
            self._period,
        )
        return [
            {
                "id": measurement["id"],
                "weight_kg": measurement["weight_kg"],
                "measured_at": measurement["measured_at"],
                "period": measurement["period"],
                "source": measurement.get("source"),
            }
            for measurement in measurements
        ]

    def _user(self) -> dict:
        """Return current user data."""
        for user in self._measurement_store.users():
            if user["id"] == self._user_id:
                return user
        return {"id": self._user_id, "name": self._user_id}
