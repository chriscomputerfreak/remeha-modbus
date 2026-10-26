"""Platform for number entities in the Remeha Modbus integration."""

import logging
from dataclasses import dataclass
from functools import cached_property
from typing import cast

from aio_remeha_modbus.gtw08 import GTW08
from aio_remeha_modbus.gtw08.climate_zone import ClimateZone
from aio_remeha_modbus.gtw08.const import Limits
from aio_remeha_modbus.gtw08.system_discovery_table import DeviceBoard
from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.remeha_modbus.const import (
    DOMAIN,
    TEMPERATURE_STEP,
)
from custom_components.remeha_modbus.coordinator import RemehaUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add all Remeha Modbus number entities based on the given config entry."""

    api: GTW08 = entry.runtime_data["api"]
    coordinator: RemehaUpdateCoordinator = entry.runtime_data["coordinator"]

    entities: list[NumberEntity] = []

    # TODO Is this the correct parent device board?
    mainboards: list[DeviceBoard] = coordinator.get_devices(lambda device: device.is_mainboard())
    if mainboards:
        entities.append(
            RemehaSummerWinterNumber(
                api=api, coordinator=coordinator, parent_device_id=mainboards[0].id
            )
        )
        entities.append(
            RemehaNeutralBandNumber(
                api=api, coordinator=coordinator, parent_device_id=mainboards[0].id
            )
        )

    climates: list[ClimateZone] = coordinator.get_climates(lambda c: c.is_domestic_hot_water())
    if climates:
        entities.extend(
            DhwHysteresisEntity(api=api, coordinator=coordinator, zone_id=climate.id)
            for climate in climates
        )
    else:
        _LOGGER.debug("No DHW climates found so not adding any DhwHysteresis entities.")

    entities.extend(
        RemehaHeatingCurveNumber(coordinator=coordinator, zone=climate, description=description)
        for climate in coordinator.get_climates(lambda c: c.is_central_heating())
        for description in HEATING_CURVE_NUMBERS
    )

    async_add_entities(entities)


@dataclass(frozen=True, kw_only=True)
class HeatingCurveNumberDescription:
    """Describes a heating curve parameter of a central heating zone."""

    field: str
    """The name of the `ClimateZone` field."""

    min_value: float
    max_value: float
    step: float
    unit: str | None = None
    device_class: NumberDeviceClass | None = None


HEATING_CURVE_NUMBERS: tuple[HeatingCurveNumberDescription, ...] = (
    # CP230: slope of the heating curve.
    HeatingCurveNumberDescription(
        field="heating_curve_slope", min_value=0.0, max_value=4.0, step=0.1
    ),
    # CP210: curve base temperature in comfort mode, 15 °C means off.
    HeatingCurveNumberDescription(
        field="heating_curve_base_comfort",
        min_value=15.0,
        max_value=90.0,
        step=TEMPERATURE_STEP,
        unit="°C",
        device_class=NumberDeviceClass.TEMPERATURE,
    ),
    # CP220: curve base temperature in reduced mode, 15 °C means off.
    HeatingCurveNumberDescription(
        field="heating_curve_base_reduced",
        min_value=15.0,
        max_value=90.0,
        step=TEMPERATURE_STEP,
        unit="°C",
        device_class=NumberDeviceClass.TEMPERATURE,
    ),
)


class DhwHysteresisEntity(CoordinatorEntity[RemehaUpdateCoordinator], NumberEntity):
    """Hysteresis entity linked to a RemehaDhwClimate."""

    _attr_has_entity_name = True
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_max_value = Limits.HYSTERESIS_MAX_TEMP
    _attr_native_min_value = Limits.HYSTERESIS_MIN_TEMP
    _attr_native_step = TEMPERATURE_STEP
    _attr_native_unit_of_measurement = "°C"
    _attr_should_poll = False
    _attr_translation_key = DOMAIN

    def __init__(self, api: GTW08, coordinator: RemehaUpdateCoordinator, zone_id: int):
        """Create a new DHW hysteresis entity."""

        super().__init__(coordinator)

        self._api: GTW08 = api
        self._climate_zone_id = zone_id
        self._attr_unique_id = f"hysteresis_{zone_id}"
        self._attr_name = "dhw_hysteresis"

    @property
    def _zone(self) -> ClimateZone:
        """Return the modbus climate zone."""
        return self.coordinator.data["climates"][self._climate_zone_id]

    @property
    def native_value(self) -> float:
        """Return the current hysteris."""

        return cast(float, self._zone.dhw_calorifier_hysteresis)

    async def async_set_native_value(self, value: float) -> None:
        """Update the current hysteris value."""

        zone: ClimateZone = self._zone
        await zone.async_set_dhw_calorifier_hysteresis(value)

        # TODO Update the value so users don't have to wait until the next sync.

        self.async_write_ha_state()

    @cached_property
    def device_info(self) -> DeviceInfo | None:
        """Return information about the device this instance belongs to.

        Returns
            `DeviceInfo | None`: The device info, or `None` if this instance is not owned by any device.

        """
        zone: ClimateZone = self._zone

        if zone.owning_device is None:
            return None

        device_instance: DeviceBoard | None = self.coordinator.get_device(id=zone.owning_device)
        return (
            DeviceInfo(
                identifiers={(DOMAIN, str(device_instance.article_number))},
                hw_version=f"HW{device_instance.hardware_version[0]:02d}.{device_instance.hardware_version[1]:02d}",
                manufacturer="Remeha",
                model=str(device_instance.board_category),
                sw_version=f"SW{device_instance.software_version[0]:02d}.{device_instance.software_version[1]:02d}",
            )
            if device_instance is not None
            and device_instance.hardware_version is not None
            and device_instance.software_version is not None
            else None
        )


class RemehaSummerWinterNumber(CoordinatorEntity[RemehaUpdateCoordinator], NumberEntity):
    """Number entity for the summer/winter outdoor temperature threshold (parameter AP073).

    Above this outside temperature the appliance switches to summer mode and stops heating.
    """

    _attr_has_entity_name = True
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_min_value = 10.0
    _attr_native_max_value = 30.5
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "°C"
    _attr_should_poll = False
    _attr_translation_key = DOMAIN

    def __init__(
        self, api: GTW08, coordinator: RemehaUpdateCoordinator, parent_device_id: int | None
    ):
        """Create a new summer/winter threshold entity."""

        super().__init__(coordinator)

        self._api: GTW08 = api
        self._parent_device_id = parent_device_id
        self._attr_unique_id = "summer_winter"
        self._attr_name = "summer_winter"

    @property
    def native_value(self) -> float:
        """Return the current summer/winter threshold."""

        return cast(float, self.coordinator.get_appliance().summer_winter)

    async def async_set_native_value(self, value: float) -> None:
        """Update the summer/winter threshold."""

        await self.coordinator.get_appliance().async_set_summer_winter(value)

        # TODO Update the value so users don't have to wait until the next sync.
        self.async_write_ha_state()

    @cached_property
    def device_info(self) -> DeviceInfo | None:
        """Return information about the device this instance belongs to.

        Returns
            `DeviceInfo | None`: The device info, or `None` if this instance is not owned by any device.

        """

        if self._parent_device_id is None:
            return None

        device_instance: DeviceBoard | None = self.coordinator.get_device(id=self._parent_device_id)
        return (
            DeviceInfo(
                identifiers={(DOMAIN, str(device_instance.article_number))},
                hw_version=f"HW{device_instance.hardware_version[0]:02d}.{device_instance.hardware_version[1]:02d}",
                manufacturer="Remeha",
                model=str(device_instance.board_category),
                sw_version=f"SW{device_instance.software_version[0]:02d}.{device_instance.software_version[1]:02d}",
            )
            if device_instance is not None
            and device_instance.hardware_version is not None
            and device_instance.software_version is not None
            else None
        )


class RemehaNeutralBandNumber(CoordinatorEntity[RemehaUpdateCoordinator], NumberEntity):
    """Number entity for the neutral band below the summer/winter limit (parameter AP075).

    Within this band the appliance neither heats nor cools (transition season).
    """

    _attr_has_entity_name = True
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_min_value = 0.0
    _attr_native_max_value = 20.0
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "°C"
    _attr_should_poll = False
    _attr_translation_key = DOMAIN

    def __init__(
        self, api: GTW08, coordinator: RemehaUpdateCoordinator, parent_device_id: int | None
    ):
        """Create a new neutral-band entity."""

        super().__init__(coordinator)

        self._api: GTW08 = api
        self._parent_device_id = parent_device_id
        self._attr_unique_id = "neutral_band_summer_winter"
        self._attr_name = "neutral_band_summer_winter"

    @property
    def native_value(self) -> float | None:
        """Return the current neutral band."""

        return self.coordinator.get_appliance().neutral_band_summer_winter

    async def async_set_native_value(self, value: float) -> None:
        """Update the neutral band."""

        await self.coordinator.get_appliance().async_set_neutral_band_summer_winter(value)

        # TODO Update the value so users don't have to wait until the next sync.
        self.async_write_ha_state()

    @cached_property
    def device_info(self) -> DeviceInfo | None:
        """Return information about the device this instance belongs to.

        Returns
            `DeviceInfo | None`: The device info, or `None` if this instance is not owned by any device.

        """

        if self._parent_device_id is None:
            return None

        device_instance: DeviceBoard | None = self.coordinator.get_device(id=self._parent_device_id)
        return (
            DeviceInfo(
                identifiers={(DOMAIN, str(device_instance.article_number))},
                hw_version=f"HW{device_instance.hardware_version[0]:02d}.{device_instance.hardware_version[1]:02d}",
                manufacturer="Remeha",
                model=str(device_instance.board_category),
                sw_version=f"SW{device_instance.software_version[0]:02d}.{device_instance.software_version[1]:02d}",
            )
            if device_instance is not None
            and device_instance.hardware_version is not None
            and device_instance.software_version is not None
            else None
        )


class RemehaHeatingCurveNumber(CoordinatorEntity[RemehaUpdateCoordinator], NumberEntity):
    """Number entity for a heating curve parameter of a central heating zone."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = DOMAIN

    def __init__(
        self,
        coordinator: RemehaUpdateCoordinator,
        zone: ClimateZone,
        description: HeatingCurveNumberDescription,
    ):
        """Create a new heating curve entity."""

        super().__init__(coordinator)

        self._climate_zone_id = zone.id
        self._field = description.field
        self._attr_unique_id = f"{description.field}_{zone.id}"
        zone_name = (zone.short_name or f"zone_{zone.id}").lower()
        self._attr_name = f"{zone_name}_{description.field}"
        self._attr_native_min_value = description.min_value
        self._attr_native_max_value = description.max_value
        self._attr_native_step = description.step
        self._attr_native_unit_of_measurement = description.unit
        self._attr_device_class = description.device_class

    @property
    def _zone(self) -> ClimateZone:
        """Return the modbus climate zone."""
        return self.coordinator.data["climates"][self._climate_zone_id]

    @property
    def native_value(self) -> float | None:
        """Return the current value of the heating curve parameter."""

        return getattr(self._zone, self._field)

    async def async_set_native_value(self, value: float) -> None:
        """Update the heating curve parameter."""

        await self._zone.write(self._field, value)

        # The zone retains the written value until the next update.
        self.async_write_ha_state()

    @cached_property
    def device_info(self) -> DeviceInfo | None:
        """Return information about the device this instance belongs to.

        Returns
            `DeviceInfo | None`: The device info, or `None` if this instance is not owned by any device.

        """
        zone: ClimateZone = self._zone

        if zone.owning_device is None:
            return None

        device_instance: DeviceBoard | None = self.coordinator.get_device(id=zone.owning_device)
        return (
            DeviceInfo(
                identifiers={(DOMAIN, str(device_instance.article_number))},
                hw_version=f"HW{device_instance.hardware_version[0]:02d}.{device_instance.hardware_version[1]:02d}",
                manufacturer="Remeha",
                model=str(device_instance.board_category),
                sw_version=f"SW{device_instance.software_version[0]:02d}.{device_instance.software_version[1]:02d}",
            )
            if device_instance is not None
            and device_instance.hardware_version is not None
            and device_instance.software_version is not None
            else None
        )
