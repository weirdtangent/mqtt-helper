# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Jeff Culverhouse
import random
import string
import re
from typing import Sequence

class MqttHelper:
    def __init__(self, service: str) -> None:
        self.service = service
        self.service_slug = re.sub(r"[a-zA-Z0-9]+", "", service)

    # MQTT connections ----------------------------------------------------------------------------

    def client_id(self) -> str:
        return "-".join([self.service_slug, "".join(random.choices(string.ascii_lowercase + string.digits, k=8))])

    # IDs -----------------------------------------------------------------------------------------

    def svc_unique_id(self, entity: str="") -> str:
        return "_".join([self.service_slug, re.sub(r"[^a-zA-Z0-9]+", "", entity)])
    def dev_unique_id(self, device_id: str, entity: str="") -> str:
        return "_".join([self.service_slug, self.device_slug(device_id), re.sub(r"[^a-zA-Z0-9]+", "", entity)])

    # Slug strings --------------------------------------------------------------------------------

    def device_slug(self, device_id: str) -> str:
        return "_".join(filter(None, [self.service_slug, re.sub(r"[^a-zA-Z0-9]+", "", device_id)]))

    # Topic strings -------------------------------------------------------------------------------

    def svc_t(self, topic: str) -> str:
        return "/".join([self.service_slug, "status", topic])

    def device_t(self, component_type: str, device_id: str, *parts: str) -> str:
        if device_id == "service":
            return "/".join([self.service_slug, *map(str, parts)])
        return "/".join([self.service_slug, component_type, self.device_slug(device_id), *map(str, parts)])

    def disc_t(self, component: str, item: str) -> str:
        return "/".join(["homeassistant",component, item, "config"])

    def stat_t(self, device_id: str, category: str, *parts: str) -> str:
        if device_id == "service":
            return  "/".join([self.service_slug, category, *map(str, parts)])
        return "/".join([self.service_slug, category, self.device_slug(device_id), *map(str, parts)])

    def avty_t(self, device_id: str, category: str="availability", *parts: str) -> str:
        if device_id == "service":
            return  "/".join([self.service_slug, category, *map(str, parts)])
        return "/".join([self.service_slug, "devices", self.device_slug(device_id), category, *map(str, parts)])

    def attr_t(self, device_id: str, category: str, *parts: str) -> str:
        if device_id == "service":
            return  "/".join([self.service_slug, category, *map(str, parts)])
        return "/".join(["homeassistant", category, self.device_slug(device_id), *map(str, parts)])

    def cmd_t(self, device_id, category, *parts: str) -> str:
        if device_id == "service":
            return  "/".join([self.service_slug, "service", category, "set"])
        return "/".join([self.service_slug, category, self.device_slug(device_id), *map(str, parts), "set"])

    # Misc helpers --------------------------------------------------------------------------------

    def device_block(self, name: str, id: str, mfr: str, device_version: str="", service_version: str="") -> dict[str, Sequence[str]]:
        device = {"name": name, "identifiers": [id], "manufacturer": mfr}

        if device_version:
            device["sw_version"] = device_version
        if name == self.service_slug:
            device.update({
                "suggested_area": "House",
                "manufacturer": "weirdTangent",
                "sw_version": service_version,
            })
        return device
