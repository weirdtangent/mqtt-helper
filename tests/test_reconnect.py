# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Jeff Culverhouse
"""Tests for BaseMqttMixin reconnection / retry-with-backoff logic."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mqtt_helper.mixins.base_mqtt import BaseMqttMixin


# ---------------------------------------------------------------------------
# Concrete subclass that satisfies the mixin's requirements
# ---------------------------------------------------------------------------
class FakeService(BaseMqttMixin):
    reconnect_initial_delay = 0.01  # speed up tests
    reconnect_max_delay = 0.08
    reconnect_backoff_factor = 2

    def __init__(self):
        self.running = True
        self.loop = asyncio.get_event_loop()
        self.logger = logging.getLogger("test")
        self.mqtt_config = {"host": "broker.test", "port": 1883}
        self.mqtt_helper = MagicMock()
        self.mqtt_helper.client_id.return_value = "test-client-abc"
        self.mqtt_helper.avty_t.return_value = "test/avty"

        # async stubs the mixin calls after connecting
        self.publish_service_discovery = AsyncMock()
        self.publish_service_availability = AsyncMock()
        self.publish_service_state = AsyncMock()

    def mqtt_subscription_topics(self) -> list[str]:
        return ["test/topic"]

    async def mqtt_on_message(self, client, userdata, message):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_reason_code(value: int = 0, name: str = "Success"):
    rc = MagicMock()
    rc.value = value
    rc.getName.return_value = name
    return rc


def _make_disconnect_flags():
    return MagicMock()


# ---------------------------------------------------------------------------
# Tests: mqttc_create retry loop
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestMqttcCreateRetry:
    @patch("mqtt_helper.mixins.base_mqtt.mqtt.Client")
    async def test_connects_on_first_try(self, MockClient):
        """Happy path — broker is available, connects immediately."""
        svc = FakeService()
        mock_client = MockClient.return_value

        await svc.mqttc_create()

        mock_client.connect.assert_called_once()
        mock_client.loop_start.assert_called_once()

    @patch("mqtt_helper.mixins.base_mqtt.asyncio.sleep", new_callable=AsyncMock)
    @patch("mqtt_helper.mixins.base_mqtt.mqtt.Client")
    async def test_retries_then_connects(self, MockClient, mock_sleep):
        """Broker unavailable twice, then comes back on third attempt."""
        svc = FakeService()
        mock_client = MockClient.return_value
        mock_client.connect.side_effect = [
            ConnectionRefusedError("refused"),
            OSError("network unreachable"),
            None,  # success on third try
        ]

        await svc.mqttc_create()

        assert mock_client.connect.call_count == 3
        assert mock_sleep.call_count == 2
        mock_client.loop_start.assert_called_once()

    @patch("mqtt_helper.mixins.base_mqtt.asyncio.sleep", new_callable=AsyncMock)
    @patch("mqtt_helper.mixins.base_mqtt.mqtt.Client")
    async def test_backoff_increases(self, MockClient, mock_sleep):
        """Verify delay doubles each retry up to max."""
        svc = FakeService()
        mock_client = MockClient.return_value
        # Fail 4 times, then succeed
        mock_client.connect.side_effect = [
            OSError("fail")] * 4 + [None]

        await svc.mqttc_create()

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays[0] == svc.reconnect_initial_delay
        assert delays[1] == svc.reconnect_initial_delay * 2
        assert delays[2] == svc.reconnect_initial_delay * 4
        # Fourth delay should be capped at max
        assert delays[3] == svc.reconnect_max_delay

    @patch("mqtt_helper.mixins.base_mqtt.asyncio.sleep", new_callable=AsyncMock)
    @patch("mqtt_helper.mixins.base_mqtt.mqtt.Client")
    async def test_stops_when_running_set_false(self, MockClient, mock_sleep):
        """If self.running becomes False during retry, loop exits without connecting."""
        svc = FakeService()
        mock_client = MockClient.return_value

        call_count = 0

        def fail_then_stop(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                svc.running = False
            raise OSError("down")

        mock_client.connect.side_effect = fail_then_stop

        await svc.mqttc_create()

        # Should have exited the loop without calling loop_start
        mock_client.loop_start.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: mqtt_on_disconnect
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestMqttOnDisconnect:
    @patch.object(FakeService, "mqttc_create", new_callable=AsyncMock)
    async def test_reconnects_when_running(self, mock_create):
        """On unexpected disconnect while running, mqttc_create is called."""
        svc = FakeService()
        svc.running = True

        await svc.mqtt_on_disconnect(
            client=MagicMock(),
            userdata=None,
            flags=_make_disconnect_flags(),
            reason_code=_make_reason_code(7, "ServerBusy"),
            properties=None,
        )

        mock_create.assert_awaited_once()
        svc.mqtt_helper.clear_client.assert_called_once()

    @patch.object(FakeService, "mqttc_create", new_callable=AsyncMock)
    async def test_does_not_reconnect_when_not_running(self, mock_create):
        """On disconnect with running=False, does not attempt reconnect."""
        svc = FakeService()
        svc.running = False

        await svc.mqtt_on_disconnect(
            client=MagicMock(),
            userdata=None,
            flags=_make_disconnect_flags(),
            reason_code=_make_reason_code(0),
            properties=None,
        )

        mock_create.assert_not_awaited()

    @patch.object(FakeService, "mqttc_create", new_callable=AsyncMock)
    async def test_graceful_disconnect_while_running_still_reconnects(self, mock_create):
        """Even a clean disconnect (rc=0) reconnects if running is True."""
        svc = FakeService()
        svc.running = True

        await svc.mqtt_on_disconnect(
            client=MagicMock(),
            userdata=None,
            flags=_make_disconnect_flags(),
            reason_code=_make_reason_code(0),
            properties=None,
        )

        mock_create.assert_awaited_once()
