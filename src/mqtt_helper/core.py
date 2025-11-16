# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Jeff Culverhouse
import logging
from paho.mqtt.client import PayloadType, Client
import random
import re
import string
from typing import Any, cast


class MqttHelper:
    def __init__(self, service: str, default_qos: int | None = None, default_retain: bool | None = None) -> None:
        self.logger = logging.getLogger(__name__)
        self.service = service
        self.service_slug = re.sub(r"[^a-zA-Z0-9]+", "", service)

        self.client: Client = None
        self.default_qos = default_qos
        self.default_retain = default_retain

    def set_client(self, client: Client) -> None:
        self.client = client

    def clear_client(self) -> None:
        self.client = None

    # IDs -----------------------------------------------------------------------------------------

    def client_id(self) -> str:
        return "-".join(
            [
                self.service_slug,
                "".join(random.choices(string.ascii_lowercase + string.digits, k=8)),
            ]
        )

    def svc_unique_id(self, entity: str = "") -> str:
        return "_".join([self.service_slug, re.sub(r"[^a-zA-Z0-9]+", "", entity)])

    def dev_unique_id(self, device_id: str, entity: str = "") -> str:
        return "_".join([self.device_slug(device_id), re.sub(r"[^a-zA-Z0-9]+", "", entity)])

    # Slug strings --------------------------------------------------------------------------------

    def device_slug(self, device_id: str) -> str:
        return "_".join(filter(None, [self.service_slug, re.sub(r"[^a-zA-Z0-9]+", "", device_id)]))

    # Topic strings -------------------------------------------------------------------------------

    def svc_t(self, topic: str) -> str:
        return "/".join([self.service_slug, "status", topic])

    def device_t(self, component_type: str, device_id: str, *parts: str) -> str:
        if device_id == "service":
            return "/".join([self.service_slug, *map(str, parts)])
        return "/".join(
            [
                self.service_slug,
                component_type,
                self.device_slug(device_id),
                *map(str, parts),
            ]
        )

    def disc_t(self, component: str, item: str) -> str:
        if not component or not item:
            raise ValueError("[disc_t] component and item have to be non-blank")
        return "/".join(["homeassistant", component, self.service_slug + "_" + item, "config"])

    def stat_t(self, device_id: str, category: str = "", *parts: str) -> str:
        if device_id == "service":
            return "/".join([self.service_slug, category, *map(str, parts)])
        return "/".join([self.service_slug, self.device_slug(device_id), category, *map(str, parts)])

    def avty_t(self, device_id: str, category: str = "availability", *parts: str) -> str:
        if device_id == "service":
            return "/".join([self.service_slug, category, *map(str, parts)])
        return "/".join([self.service_slug, self.device_slug(device_id), category, *map(str, parts)])

    def attr_t(self, device_id: str, category: str = "attributes", *parts: str) -> str:
        if device_id == "service":
            return "/".join([self.service_slug, category, *map(str, parts)])
        return "/".join(["homeassistant", self.device_slug(device_id), category, *map(str, parts)])

    def cmd_t(self, device_id: str, category: str = "cmd", *parts: str) -> str:
        if device_id == "service":
            return "/".join([self.service_slug, "service", category, "set"])
        return "/".join(
            [
                self.service_slug,
                self.device_slug(device_id),
                category,
                *map(str, parts),
                "set",
            ]
        )

    # Misc helpers --------------------------------------------------------------------------------

    def safe_publish(self, topic: str, payload: Any, **kwargs: Any) -> None:
        if not self.client:
            raise SystemError("Mqtt client not connected, cannot publish")
        if not topic:
            raise ValueError("Cannot post to a blank topic")
        if isinstance(payload, dict) and ("component" in payload or "//////" in payload):
            self.logger.warning("questionable payload includes 'component' or string of slashes - wont't send to HA")
            self.logger.warning(f"topic: {topic}")
            self.logger.warning(f"payload: {payload}")
            raise ValueError("Possible invalid payload. topic: {topic} payload: {payload}")

        try:
            if "qos" not in kwargs and self.default_qos is not None:
                kwargs["qos"] = self.default_qos
            if "retain" not in kwargs and self.default_retain is not None:
                kwargs["retain"] = self.default_retain
            if payload is None:
                self.client.publish(topic, "null", **kwargs)
            else:
                self.client.publish(topic, cast(PayloadType, payload), **kwargs)
        except Exception as err:
            self.logger.warning(
                f"MQTT publish failed for {topic} with payload type {type(payload)}: {payload[:120] if isinstance(payload, str) else payload}: {err}"
            )
