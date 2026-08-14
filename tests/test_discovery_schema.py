# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Jeff Culverhouse
"""Tests for BaseMqttMixin discovery schema versioning / self-healing reset."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from mqtt_helper.mixins.base_mqtt import BaseMqttMixin


# ---------------------------------------------------------------------------
# Concrete subclass that opts in to schema versioning
# ---------------------------------------------------------------------------
class FakeService(BaseMqttMixin):
    DISCOVERY_SCHEMA_VERSION = 3
    discovery_schema_read_timeout = 0.05
    discovery_reset_settle_delay = 0

    def __init__(self):
        self.running = True
        self.loop = asyncio.get_event_loop()
        self.logger = logging.getLogger("test")
        self.mqtt_config = {"host": "broker.test", "port": 1883}
        self.mqtt_helper = MagicMock()
        self.mqtt_helper.service_slug = "testsvc"
        self.mqtt_helper.avty_t.return_value = "testsvc/availability"

        self.publish_service_discovery = AsyncMock()
        self.publish_service_availability = AsyncMock()
        self.publish_service_state = AsyncMock()
        self.clear_discovery = AsyncMock()
        self.rediscover_all = AsyncMock()

    def mqtt_subscription_topics(self) -> list[str]:
        return ["testsvc/service/+/set"]

    async def mqtt_on_message(self, client, userdata, message):
        pass


class OptedOutService(FakeService):
    DISCOVERY_SCHEMA_VERSION = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_client(retained: bytes | None):
    """A fake paho client that replays `retained` to the topic callback on subscribe."""
    client = MagicMock()
    holder: dict = {}

    def _add(topic, callback):
        holder["topic"] = topic
        holder["cb"] = callback

    def _subscribe(topic, *args, **kwargs):
        if retained is not None and holder.get("topic") == topic:
            message = MagicMock()
            message.topic = topic
            message.payload = retained
            holder["cb"](client, None, message)

    client.message_callback_add.side_effect = _add
    client.subscribe.side_effect = _subscribe
    return client


def _make_reason_code(value: int = 0, name: str = "Success"):
    rc = MagicMock()
    rc.value = value
    rc.getName.return_value = name
    return rc


# ---------------------------------------------------------------------------
# Tests: reading the retained version
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_read_returns_retained_version():
    svc = FakeService()
    client = _make_client(b"2")

    assert await svc.read_discovery_schema_version(client) == 2
    client.subscribe.assert_called_once_with("testsvc/service/discovery_schema_version")
    # the topic-scoped callback must be torn down so it can't shadow later traffic
    client.message_callback_remove.assert_called_once_with("testsvc/service/discovery_schema_version")
    client.unsubscribe.assert_called_once_with("testsvc/service/discovery_schema_version")


@pytest.mark.asyncio
async def test_read_returns_none_on_timeout():
    svc = FakeService()
    client = _make_client(None)

    assert await svc.read_discovery_schema_version(client) is None
    client.message_callback_remove.assert_called_once()
    client.unsubscribe.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"", b"   ", b"not-a-number", b"\xff\xfe"])
async def test_read_returns_none_on_junk(payload):
    svc = FakeService()
    assert await svc.read_discovery_schema_version(_make_client(payload)) is None


# ---------------------------------------------------------------------------
# Tests: the reset decision
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bump_triggers_exactly_one_clear_then_republish():
    svc = FakeService()

    assert await svc._maybe_reset_discovery(_make_client(b"2")) is True

    svc.clear_discovery.assert_awaited_once()
    svc.rediscover_all.assert_awaited_once()
    # and the new version is stamped so the next start is a no-op
    versions = [call.args[1] for call in svc.mqtt_helper.safe_publish.call_args_list if call.args[0] == "testsvc/service/discovery_schema_version"]
    assert versions == ["3"]


@pytest.mark.asyncio
async def test_matching_version_does_nothing():
    svc = FakeService()

    assert await svc._maybe_reset_discovery(_make_client(b"3")) is False

    svc.clear_discovery.assert_not_awaited()
    svc.rediscover_all.assert_not_awaited()
    svc.mqtt_helper.safe_publish.assert_not_called()


@pytest.mark.asyncio
async def test_absent_version_records_without_resetting():
    """A broker that lost its retained set must not trigger mass entity churn."""
    svc = FakeService()

    assert await svc._maybe_reset_discovery(_make_client(None)) is False

    svc.clear_discovery.assert_not_awaited()
    svc.rediscover_all.assert_not_awaited()
    svc.mqtt_helper.safe_publish.assert_called_once()
    assert svc.mqtt_helper.safe_publish.call_args.args == (
        "testsvc/service/discovery_schema_version",
        "3",
    )


@pytest.mark.asyncio
async def test_absent_version_restamps_if_the_stamp_was_dropped():
    """A stamp lost to a disconnect must not latch: the broker would keep no baseline at all."""
    svc = FakeService()
    svc.mqtt_helper.safe_publish.side_effect = lambda *a, **kw: setattr(svc.mqtt_helper, "client", None)

    assert await svc._maybe_reset_discovery(_make_client(None)) is False

    assert getattr(svc, "_discovery_schema_checked", False) is False


@pytest.mark.asyncio
async def test_version_zero_opts_out_entirely():
    svc = OptedOutService()

    assert await svc._maybe_reset_discovery(_make_client(b"2")) is False

    svc.clear_discovery.assert_not_awaited()
    svc.mqtt_helper.safe_publish.assert_not_called()


@pytest.mark.asyncio
async def test_reset_runs_once_per_process():
    """A reconnect loop must not re-reset, even if the version stamp never landed."""
    svc = FakeService()

    assert await svc._maybe_reset_discovery(_make_client(b"2")) is True
    assert await svc._maybe_reset_discovery(_make_client(b"2")) is False

    svc.clear_discovery.assert_awaited_once()
    svc.rediscover_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_reset_retries_on_the_next_connect():
    """A reset that dies partway must not be latched off — that would strand a half-cleared install."""
    svc = FakeService()
    svc.rediscover_all = AsyncMock(side_effect=RuntimeError("republish blew up"))

    assert await svc._maybe_reset_discovery(_make_client(b"2")) is False

    svc.clear_discovery.assert_awaited_once()
    assert getattr(svc, "_discovery_schema_checked", False) is False

    # the version was never stamped, so the retry still sees a mismatch and finishes the job
    svc.rediscover_all = AsyncMock()
    assert await svc._maybe_reset_discovery(_make_client(b"2")) is True
    svc.rediscover_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_reset_still_lets_on_connect_finish():
    """An exception here must not skip availability and the topic subscriptions."""
    svc = FakeService()
    svc.rediscover_all = AsyncMock(side_effect=RuntimeError("republish blew up"))
    client = _make_client(b"2")

    await svc.mqtt_on_connect(client, {}, MagicMock(), _make_reason_code(), None)

    # falls back to a plain discovery publish rather than leaving HA with nothing
    svc.publish_service_discovery.assert_awaited_once()
    svc.publish_service_availability.assert_awaited_once()
    client.subscribe.assert_any_call("testsvc/service/+/set")


@pytest.mark.asyncio
async def test_losing_the_broker_mid_reset_retries():
    """safe_publish drops publishes silently when disconnected — no exception to catch."""
    svc = FakeService()

    async def _drop_connection():
        svc.mqtt_helper.client = None

    svc.rediscover_all = AsyncMock(side_effect=_drop_connection)

    assert await svc._maybe_reset_discovery(_make_client(b"2")) is True

    assert getattr(svc, "_discovery_schema_checked", False) is False


@pytest.mark.asyncio
async def test_read_failure_does_not_reset():
    svc = FakeService()
    client = MagicMock()
    client.message_callback_add.side_effect = RuntimeError("broker went away")

    assert await svc._maybe_reset_discovery(client) is False

    svc.clear_discovery.assert_not_awaited()
    # a failed read must stay retryable on the next connect
    assert getattr(svc, "_discovery_schema_checked", False) is False


# ---------------------------------------------------------------------------
# Tests: clearing publishes a truly empty payload
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_clear_discovery_topic_publishes_empty_retained():
    svc = FakeService()

    await svc.clear_discovery_topic("homeassistant/device/testsvc_service/config")

    svc.mqtt_helper.safe_publish.assert_called_once()
    args, kwargs = svc.mqtt_helper.safe_publish.call_args
    # "" removes the entity; None would publish the string "null" and HA would log a parse error
    assert args == ("homeassistant/device/testsvc_service/config", "")
    assert kwargs == {"retain": True}


# ---------------------------------------------------------------------------
# Tests: scanning the broker for what we actually own
# ---------------------------------------------------------------------------
def _make_scan_client(topics):
    """A fake client that replays retained configs on the wildcard subscribe."""
    client = MagicMock()
    holder: dict = {}

    def _add(topic, callback):
        holder[topic] = callback

    def _subscribe(topic, *args, **kwargs):
        cb = holder.get(topic)
        if cb is None:
            return
        for t, payload in topics.items():
            message = MagicMock()
            message.topic = t
            message.payload = payload
            cb(client, None, message)

    client.message_callback_add.side_effect = _add
    client.subscribe.side_effect = _subscribe
    return client


def _scanning_service(topics):
    svc = FakeService()
    svc.discovery_scan_timeout = 0
    svc.mqtt_helper.client = _make_scan_client(topics)
    return svc


@pytest.mark.asyncio
async def test_scan_finds_only_our_own_topics():
    svc = _scanning_service(
        {
            "homeassistant/device/testsvc_service/config": b'{"x":1}',
            "homeassistant/device/testsvc_CAM1/config": b'{"x":1}',
            "homeassistant/device/othersvc_CAM1/config": b'{"x":1}',
            "homeassistant/device/testsvcextra_CAM1/config": b'{"x":1}',
        }
    )

    assert await svc.collect_retained_discovery_topics() == [
        "homeassistant/device/testsvc_CAM1/config",
        "homeassistant/device/testsvc_service/config",
    ]


@pytest.mark.asyncio
async def test_scan_ignores_already_cleared_topics():
    svc = _scanning_service(
        {
            "homeassistant/device/testsvc_service/config": b'{"x":1}',
            "homeassistant/device/testsvc_GONE/config": b"",
        }
    )

    assert await svc.collect_retained_discovery_topics() == ["homeassistant/device/testsvc_service/config"]


@pytest.mark.asyncio
async def test_scan_tears_down_its_subscription():
    """The wildcard would otherwise shadow every other service's discovery traffic."""
    svc = _scanning_service({})

    await svc.collect_retained_discovery_topics()

    svc.mqtt_helper.client.message_callback_remove.assert_called_once_with("homeassistant/+/+/config")
    svc.mqtt_helper.client.unsubscribe.assert_called_once_with("homeassistant/+/+/config")


@pytest.mark.asyncio
async def test_scan_honours_a_custom_discovery_prefix():
    svc = _scanning_service({})
    svc.mqtt_config = {"discovery_prefix": "ha"}

    await svc.collect_retained_discovery_topics()

    svc.mqtt_helper.client.subscribe.assert_called_once_with("ha/+/+/config")


@pytest.mark.asyncio
async def test_scan_without_a_client_returns_empty():
    svc = FakeService()
    svc.mqtt_helper.client = None

    assert await svc.collect_retained_discovery_topics() == []


@pytest.mark.asyncio
async def test_clear_retained_discovery_clears_every_topic_found():
    """Covers per-device topics the device map does not know about yet at connect time."""
    svc = _scanning_service(
        {
            "homeassistant/device/testsvc_service/config": b'{"x":1}',
            "homeassistant/device/testsvc_CAM1/config": b'{"x":1}',
        }
    )

    assert await svc.clear_retained_discovery() == 2

    cleared = [c.args[0] for c in svc.mqtt_helper.safe_publish.call_args_list if c.args[1] == ""]
    assert cleared == [
        "homeassistant/device/testsvc_CAM1/config",
        "homeassistant/device/testsvc_service/config",
    ]


# ---------------------------------------------------------------------------
# Tests: on_connect wiring
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_on_connect_skips_normal_discovery_when_reset_ran():
    svc = FakeService()
    client = _make_client(b"2")

    await svc.mqtt_on_connect(client, {}, MagicMock(), _make_reason_code(), None)

    svc.rediscover_all.assert_awaited_once()
    svc.publish_service_discovery.assert_not_awaited()
    svc.publish_service_availability.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_connect_publishes_discovery_normally_when_no_reset():
    svc = FakeService()
    client = _make_client(b"3")

    await svc.mqtt_on_connect(client, {}, MagicMock(), _make_reason_code(), None)

    svc.publish_service_discovery.assert_awaited_once()
    svc.rediscover_all.assert_not_awaited()
    client.subscribe.assert_any_call("testsvc/service/+/set")
