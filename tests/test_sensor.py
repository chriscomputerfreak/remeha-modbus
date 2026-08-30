"""Test the sensor component."""

from unittest.mock import patch

import pytest
from homeassistant.components.sensor.const import DOMAIN as SensorDomain
from homeassistant.core import HomeAssistant

from custom_components.remeha_modbus.const import REMEHA_SENSORS

from .conftest import get_api, setup_platform


@pytest.mark.parametrize("mock_modbus_client", ["modbus_store.json"], indirect=True)
async def test_sensors(hass: HomeAssistant, mock_modbus_client, mock_config_entry):
    """Test available sensors."""

    api = get_api(mock_modbus_client=mock_modbus_client)
    with patch(
        "aio_remeha_modbus.api.api.RemehaApi.create",
        new=lambda *args, **kwargs: api,
    ):
        await setup_platform(hass=hass, config_entry=mock_config_entry)
        await hass.async_block_till_done()

        assert len(hass.states.async_all(domain_filter=SensorDomain)) == 35

        for sd in REMEHA_SENSORS.values():
            state = hass.states.get(f"sensor.remeha_modbus_test_hub_{sd.name}")
            assert state.name == f"Remeha Modbus test_hub {sd.name}"


@pytest.mark.parametrize("mock_modbus_client", ["modbus_store.json"], indirect=True)
async def test_buffer_temperature_sensors(
    hass: HomeAssistant, mock_modbus_client, mock_config_entry
):
    """Buffer tank temperatures (BM001/BM002, registers 7600/7601) are exposed and scaled."""

    api = get_api(mock_modbus_client=mock_modbus_client)
    with patch(
        "aio_remeha_modbus.api.api.RemehaApi.create",
        new=lambda *args, **kwargs: api,
    ):
        await setup_platform(hass=hass, config_entry=mock_config_entry)
        await hass.async_block_till_done()

        bottom = hass.states.get("sensor.remeha_modbus_test_hub_buffer_temperature_bottom")
        assert bottom is not None
        assert float(bottom.state) == pytest.approx(23.2)
        assert bottom.attributes["unit_of_measurement"] == "°C"
        assert bottom.attributes["device_class"] == "temperature"

        top = hass.states.get("sensor.remeha_modbus_test_hub_buffer_temperature_top")
        assert top is not None
        assert float(top.state) == pytest.approx(21.5)
