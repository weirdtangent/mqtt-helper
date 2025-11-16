# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Jeff Culverhouse
import asyncio
from datetime import datetime, timedelta
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
    reconnect_retry_grace_seconds = 10
    mqtt_keepalive = 60

    # Subclasses must implement -------------------------------------------------------------------
    def mqtt_subscription_topics(self) -> list[str]:
        """Return a list of topics to subscribe to after connecting."""
        raise NotImplementedError

    # Core MQTT plumbing --------------------------------------------------------------------------
    async def mqttc_create(self) -> None:
        """Configure and connect the MQTT client."""
        self.client_id = self.mqtt_helper.client_id()
        self.mqttc = mqtt.Client(
            client_id=self.client_id,
            callback_api_version=CallbackAPIVersion.VERSION2,
            reconnect_on_failure=False,
            protocol=mqtt.MQTTv5,
        )

        if self.mqtt_config.get("tls_enabled"):
            self.mqttc.tls_set(
                ca_certs=self.mqtt_config.get("tls_ca_cert"),
                certfile=self.mqtt_config.get("tls_cert"),
                keyfile=self.mqtt_config.get("tls_key"),
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS,
            )

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

        try:
            host = self.mqtt_config["host"]
            port = self.mqtt_config["port"]
            self.logger.info(f"connecting to MQTT broker at {host}:{port} as client id: {self.client_id}")

            props = Properties(PacketTypes.CONNECT)
            props.SessionExpiryInterval = 0

            self.mqttc.connect(host=host, port=port, keepalive=self.mqtt_keepalive, properties=props)
            self.logger.info(f"successful connection to {host} MQTT broker")

            self.mqtt_connect_time = datetime.now()
            self.mqttc.loop_start()
        except ConnectionError as error:
            self.logger.error(f"failed to connect to MQTT host {host}: {error}")
            self.running = False
            raise SystemExit(1)
        except Exception as error:
            self.logger.error(f"network problem trying to connect to MQTT host {host}: {error}")
            self.running = False
            raise SystemExit(1)

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
            raise MqttError(f"MQTT failed to connect ({reason_code.getName()})")

        self.mqtt_helper.set_client(client)

        await self.publish_service_discovery()
        await self.publish_service_availability()
        await self.publish_service_state()

        self.logger.info("subscribing to topics on MQTT")
        for topic in self.mqtt_subscription_topics():
            client.subscribe(topic)

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

        reconnect_after = self.mqtt_connect_time is None or datetime.now() > self.mqtt_connect_time + timedelta(seconds=self.reconnect_retry_grace_seconds)

        if self.running and reconnect_after:
            await self.mqttc_create()
        else:
            self.logger.info("mqtt disconnect — stopping service loop")
            self.running = False

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
