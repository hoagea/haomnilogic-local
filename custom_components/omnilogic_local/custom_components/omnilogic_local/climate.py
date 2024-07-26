from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal, cast

from pyomnilogic_local.models.telemetry import TelemetryBoW
from pyomnilogic_local.types import OmniType

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF, STATE_ON, UnitOfTemperature

from .const import DOMAIN, KEY_COORDINATOR
from .entity import OmniLogicEntity
from .types.entity_index import EntityIndexHeater, EntityIndexHeaterEquip
from .utils import get_entities_of_hass_type

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import OmniLogicCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the water heater platform."""

    coordinator = hass.data[DOMAIN][entry.entry_id][KEY_COORDINATOR]

    all_heaters = get_entities_of_hass_type(coordinator.data, "climate")

    virtual_heater = {system_id: data for system_id, data in all_heaters.items() if data.msp_config.omni_type == OmniType.VIRT_HEATER}
    heater_equipment_ids = [system_id for system_id, data in all_heaters.items() if data.msp_config.omni_type == OmniType.HEATER_EQUIP]

    entities = []
    for system_id, vheater in virtual_heater.items():
        _LOGGER.debug(
            "Configuring climate heater with ID: %s, Name: %s",
            vheater.msp_config.system_id,
            vheater.msp_config.name,
        )
        entities.append(
            OmniLogicClimateEntity(
                coordinator=coordinator,
                context=system_id,
                heater_equipment_ids=heater_equipment_ids,
            )
        )

    async_add_entities(entities)


class OmniLogicClimateEntity(OmniLogicEntity[EntityIndexHeater], ClimateEntity):
    """An entity using CoordinatorEntity.
    The CoordinatorEntity class provides:
      should_poll
      async_update
      async_added_to_hass
      available
    """

    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_name = "Heater"

    def __init__(self, coordinator: OmniLogicCoordinator, context: int, heater_equipment_ids: list[int]) -> None:
        """Pass coordinator to CoordinatorEntity."""
        super().__init__(
            coordinator,
            context=context,
        )
        self.heater_equipment_ids = heater_equipment_ids

    @property
    def temperature_unit(self) -> str:
        return str(UnitOfTemperature.CELSIUS) if self.get_system_config().units == "Metric" else str(UnitOfTemperature.FAHRENHEIT)

    @property
    def min_temp(self) -> float:
        return self.data.msp_config.min_temp

    @property
    def max_temp(self) -> float:
        return self.data.msp_config.max_temp

    @property
    def target_temperature(self) -> float | None:
        return self.data.telemetry.current_set_point

    @property
    def current_temperature(self) -> float | None:
        current_temp = cast(TelemetryBoW, self.get_telemetry_by_systemid(self.bow_id)).water_temp
        return current_temp if current_temp != -1 else None

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return current operation."""
        return HVACMode.HEAT if self.data.telemetry.enabled else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        """Return the current running hvac operation if supported.
        Need to be one of CURRENT_HVAC_*.
        """
        if self.hvac_mode == HVACMode.HEAT:
            return HVACAction.HEATING        
        return HVACAction.OFF

    @property
    def current_operation(self) -> str:
        return str(STATE_ON) if self.data.telemetry.enabled else str(STATE_OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        await self.coordinator.omni_api.async_set_heater(
            self.bow_id,
            self.system_id,
            int(kwargs[ATTR_TEMPERATURE]),
            unit=self.temperature_unit,
        )
        self.set_telemetry({"current_set_point": int(kwargs[ATTR_TEMPERATURE])})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set hvac mode."""
        if hvac_mode == HVACMode.HEAT:
            self._hvac_mode = HVACMode.HEAT
            await self.coordinator.omni_api.async_set_heater_enable(self.bow_id, self.system_id, True)
            self.set_telemetry({"enabled": "yes"})
        elif hvac_mode == HVACMode.OFF:
            self._hvac_mode = HVACMode.OFF
            await self.coordinator.omni_api.async_set_heater_enable(self.bow_id, self.system_id, False)
            self.set_telemetry({"enabled": "no"})
        else:
            _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
            return
        # Ensure we update the current operation after changing the mode
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, str | int]:
        extra_state_attributes = super().extra_state_attributes | {"solar_set_point": self.data.msp_config.solar_set_point}
        for system_id in self.heater_equipment_ids:
            heater_equipment = cast(EntityIndexHeaterEquip, self.coordinator.data[system_id])
            prefix = f"omni_heater_{heater_equipment.msp_config.name.lower()}"
            extra_state_attributes = extra_state_attributes | {
                f"{prefix}_enabled": heater_equipment.msp_config.enabled,
                f"{prefix}_system_id": system_id,
                f"{prefix}_bow_id": heater_equipment.msp_config.bow_id,
                f"{prefix}_state": heater_equipment.telemetry.state.pretty(),
                f"{prefix}_sensor_temp": heater_equipment.telemetry.temp,
            }
        return extra_state_attributes
