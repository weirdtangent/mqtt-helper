# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Jeff Culverhouse
import asyncio
import ssl

from typing import Any, Callable, Coroutine, TypeVar

import paho.mqtt.client as mqtt
from paho.mqtt.client import Client, ConnectFlags, DisconnectFlags
from paho.mqtt.enums import CallbackAPIVersion, LogLevel
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

_T = TypeVar("_T")


class MqttError(ValueError):
    """Raised when the connection to the MQTT server fails"""


class BaseMqttMixin:
    mqtt_keepalive = 60
    reconnect_initial_delay = 5
    reconnect_max_delay = 300
    reconnect_backoff_factor = 2

    # Discovery schema versioning -----------------------------------------------------------------
    # Bump DISCOVERY_SCHEMA_VERSION in a service ONLY when its entity layout changes: unique_ids,
    # entity names, or the set of components it publishes. A bump makes the service clear its
    # retained discovery topics on next connect, which deletes the entities from Home Assistant's
    # registry (losing user renames, areas, and hidden/disabled flags) before recreating them.
    # That is the whole point — it is the only way to unstick an entity_id that HA assigned from a
    # previous release's name — but it is destructive, so never bump it casually.
    #
    # 0 (the default) opts a service out entirely: no version topic, no reset, no behaviour change.
    DISCOVERY_SCHEMA_VERSION: int = 0

    # How long to wait for the broker to deliver the retained version after subscribing. Nothing
    # within this window means "unknown", NOT "changed" — see _maybe_reset_discovery.
    discovery_schema_read_timeout = 5.0

    # Pause between clearing retained discovery and republishing it, so HA processes the removals
    # before the recreates arrive.
    discovery_reset_settle_delay = 1.0

    # Subclasses must implement -------------------------------------------------------------------
    def mqtt_subscription_topics(self) -> list[str]:
        """Return a list of topics to subscribe to after connecting."""
        raise NotImplementedError

    # Subclasses must implement if DISCOVERY_SCHEMA_VERSION > 0 -----------------------------------
    async def clear_discovery(self) -> None:
        """Clear every retained discovery topic this service owns, via clear_discovery_topic()."""
        raise NotImplementedError

    async def rediscover_all(self) -> None:
        """Republish all discovery payloads (and the state that backs them)."""
        raise NotImplementedError

    # Core MQTT plumbing --------------------------------------------------------------------------
    async def mqttc_create(self) -> None:
        """Configure and connect the MQTT client."""
        # Clean up any existing client before creating a new one (reconnect path)
        old = getattr(self, "mqttc", None)
        if old is not None:
            try:
                old.loop_stop()
            except Exception:
                pass
            try:
                old.disconnect()
            except Exception:
                pass

        protocol_version = str(self.mqtt_config.get("protocol_version", "5"))
        if protocol_version in ("3.1.1", "3"):
            self.mqtt_protocol = mqtt.MQTTv311
            self.logger.info("using MQTT protocol version 3.1.1")
        elif protocol_version == "5":
            self.mqtt_protocol = mqtt.MQTTv5
            self.logger.info("using MQTT protocol version 5")
        else:
            self.mqtt_protocol = mqtt.MQTTv5
            self.logger.warning(f"invalid MQTT protocol_version '{protocol_version}', defaulting to version 5")

        self.client_id = self.mqtt_helper.client_id()
        self.mqttc = mqtt.Client(
            client_id=self.client_id,
            callback_api_version=CallbackAPIVersion.VERSION2,
            reconnect_on_failure=False,
            protocol=self.mqtt_protocol,
        )

        if self.mqtt_config.get("tls_enabled"):
            self.mqttc.tls_set(
                ca_certs=self.mqtt_config.get("tls_ca_cert"),
                certfile=self.mqtt_config.get("tls_cert"),
                keyfile=self.mqtt_config.get("tls_key"),
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS,
            )
        else:
            self.logger.warning("MQTT TLS is disabled — credentials and data will be sent in cleartext")

        if self.mqtt_config.get("username") or self.mqtt_config.get("password"):
            self.mqttc.username_pw_set(
                username=self.mqtt_config.get("username") or None,
                password=self.mqtt_config.get("password") or None,
            )

        self.mqttc.on_connect = self._wrap_async(self.mqtt_on_connect)
        self.mqttc.on_disconnect = self._wrap_async(self.mqtt_on_disconnect)
        self.mqttc.on_message = self._wrap_async(self.mqtt_on_message)
        self.mqttc.on_subscribe = self._wrap_async(self.mqtt_on_subscribe)
        self.mqttc.on_log = self._wrap_async(self.mqtt_on_log)

        self.mqttc.will_set(self.mqtt_helper.avty_t("service"), "offline", qos=1, retain=True)

        host = self.mqtt_config["host"]
        port = self.mqtt_config["port"]
        delay = self.reconnect_initial_delay

        # IMPORTANT: must be `while True`, not `while self.running` — apps
        # set self.running = True AFTER mqttc_create() returns.
        while True:
            try:
                self.logger.info(f"connecting to MQTT broker at {host}:{port} as client id: {self.client_id}")

                # Only use Properties for MQTT v5
                if self.mqtt_protocol == mqtt.MQTTv5:
                    props = Properties(PacketTypes.CONNECT)
                    props.SessionExpiryInterval = 0
                    self.mqttc.connect(host=host, port=port, keepalive=self.mqtt_keepalive, properties=props)
                else:
                    self.mqttc.connect(host=host, port=port, keepalive=self.mqtt_keepalive)

                self.logger.info(f"successful connection to {host} MQTT broker")
                self.mqttc.loop_start()
                return
            except Exception as error:
                self.logger.warning(f"cannot connect to MQTT host {host}: {error} — retrying in {delay}s")
                await asyncio.sleep(delay)
                delay = min(delay * self.reconnect_backoff_factor, self.reconnect_max_delay)

    def _wrap_async(
        self,
        coro_func: Callable[..., Coroutine[Any, Any, _T]],
    ) -> Callable[..., None]:
        """Ensure Paho callbacks run inside the service event loop."""

        def wrapper(*args: Any, **kwargs: Any) -> None:
            self.loop.call_soon_threadsafe(lambda: asyncio.create_task(coro_func(*args, **kwargs)))

        return wrapper

    async def mqtt_on_connect(
        self,
        client: Client,
        userdata: dict[str, Any],
        flags: ConnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None,
    ) -> None:
        if reason_code.value != 0:
            self.logger.error(f"MQTT failed to connect ({reason_code.getName()})")
            return

        self.mqtt_helper.set_client(client)

        # A schema bump republishes discovery itself, so only publish it here when no reset ran.
        if not await self._maybe_reset_discovery(client):
            await self.publish_service_discovery()

        await self.publish_service_availability()
        await self.publish_service_state()

        self.logger.info("subscribing to topics on MQTT")
        for topic in self.mqtt_subscription_topics():
            client.subscribe(topic)

    # Discovery schema versioning -----------------------------------------------------------------

    def discovery_schema_version_topic(self) -> str:
        return "/".join([self.mqtt_helper.service_slug, "service", "discovery_schema_version"])

    async def clear_discovery_topic(self, topic: str) -> None:
        """Delete a retained discovery topic (and the HA registry entry behind it).

        The payload must be genuinely empty — that is what HA reads as "remove this". Passing None
        would publish the string "null", which HA parses as a malformed config instead.
        """
        await asyncio.to_thread(self.mqtt_helper.safe_publish, topic, "", retain=True)

    async def publish_discovery_schema_version(self, version: int | None = None) -> None:
        """Stamp the current schema version as a retained value.

        Retained on the broker rather than a local file on purpose: it has to outlive container
        replacement, which is exactly the upgrade path that strands entities in HA's registry.
        """
        if version is None:
            version = self.DISCOVERY_SCHEMA_VERSION
        await asyncio.to_thread(
            self.mqtt_helper.safe_publish,
            self.discovery_schema_version_topic(),
            str(version),
            retain=True,
        )

    async def read_discovery_schema_version(self, client: Client) -> int | None:
        """Return the retained schema version, or None if the broker has none to give us.

        Uses a topic-scoped callback so the value never reaches the service's own mqtt_on_message,
        which in most services would route it straight into command handling.
        """
        topic = self.discovery_schema_version_topic()
        future: asyncio.Future[bytes] = self.loop.create_future()

        def _on_version(_client: Client, _userdata: Any, message: Any) -> None:
            # Runs on paho's network thread — hop back to the service loop to settle the future.
            payload = message.payload

            def _settle() -> None:
                if not future.done():
                    future.set_result(payload)

            self.loop.call_soon_threadsafe(_settle)

        client.message_callback_add(topic, _on_version)
        client.subscribe(topic)
        try:
            raw = await asyncio.wait_for(future, timeout=self.discovery_schema_read_timeout)
        except TimeoutError:
            return None
        finally:
            client.message_callback_remove(topic)
            client.unsubscribe(topic)

        try:
            text = raw.decode("utf-8").strip() if isinstance(raw, (bytes, bytearray)) else str(raw).strip()
        except Exception as err:
            self.logger.warning(f"could not decode retained discovery schema version: {err!r}")
            return None
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            self.logger.warning(f"ignoring unparsable retained discovery schema version: {text!r}")
            return None

    async def reset_discovery(self, reason: str = "requested") -> None:
        """Clear all retained discovery, wait for HA to catch up, then republish it."""
        self.logger.warning(f"resetting HA discovery ({reason}) — entity customizations will be lost")
        await self.clear_discovery()
        await asyncio.sleep(self.discovery_reset_settle_delay)
        await self.rediscover_all()
        await self.publish_discovery_schema_version()
        self.logger.info("HA discovery reset complete")

    async def _maybe_reset_discovery(self, client: Client) -> bool:
        """Reset discovery if the retained schema version disagrees with ours. Returns True if it ran.

        Runs at most once per process: after a reset the new version is retained, so a reconnect
        would be a no-op anyway, and skipping it keeps a flapping connection from re-resetting.
        """
        if self.DISCOVERY_SCHEMA_VERSION <= 0 or getattr(self, "_discovery_schema_checked", False):
            return False

        try:
            stored = await self.read_discovery_schema_version(client)
        except Exception as err:
            self.logger.warning(f"could not read discovery schema version, skipping reset: {err!r}")
            return False

        self._discovery_schema_checked = True

        if stored is None:
            # No retained value: a fresh install, or a broker that lost its retained set. Either way
            # this is "unknown", not "changed" — resetting here would churn every healthy install
            # whose broker was rebuilt. Stamp the version so the next start has something to read.
            self.logger.info(f"no retained discovery schema version — recording {self.DISCOVERY_SCHEMA_VERSION} without resetting")
            await self.publish_discovery_schema_version()
            return False

        if stored == self.DISCOVERY_SCHEMA_VERSION:
            return False

        await self.reset_discovery(reason=f"discovery schema version {stored} -> {self.DISCOVERY_SCHEMA_VERSION}")
        return True

    async def mqtt_on_disconnect(
        self,
        client: Client,
        userdata: Any,
        flags: DisconnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None,
    ) -> None:
        self.mqtt_helper.clear_client()

        if reason_code.value != 0:
            self.logger.error(f"mqtt lost connection ({reason_code.getName()})")
        else:
            self.logger.info("closed MQTT connection")

        if self.running:
            self.logger.info("will attempt to reconnect to MQTT broker...")
            await asyncio.sleep(self.reconnect_initial_delay)
            await self.mqttc_create()
        else:
            self.logger.info("mqtt disconnect — stopping service loop")

    async def mqtt_on_log(self, client: Client, userdata: Any, paho_log_level: int, msg: str) -> None:
        if paho_log_level == LogLevel.MQTT_LOG_ERR:
            self.logger.error(f"mqtt logged: {msg}")
        if paho_log_level == LogLevel.MQTT_LOG_WARNING:
            self.logger.warning(f"mqtt logged: {msg}")

    async def mqtt_on_subscribe(
        self,
        client: Client,
        userdata: Any,
        mid: int,
        reason_code_list: list[ReasonCode],
        properties: Properties,
    ) -> None:
        reason_names = [rc.getName() for rc in reason_code_list]
        joined = "; ".join(reason_names) if reason_names else "none"
        self.logger.debug(f"mqtt subscribed (mid={mid}): {joined}")
