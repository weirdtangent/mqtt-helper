# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Jeff Culverhouse
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def decode_mqtt_payload(raw: bytes | bytearray) -> Any | None:
    """Decode an MQTT payload: try JSON first, then UTF-8 string, or None on failure."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        pass
    try:
        return raw.decode("utf-8")
    except Exception as err:
        logger.warning(f"failed to decode MQTT payload: {err!r}")
        return None


def parse_device_topic(components: list[str]) -> tuple[str, str, str] | None:
    """Extract (vendor, device_id, attribute) from MQTT topic components.

    Expects a topic like: ``vendor_deviceId/.../attribute/set``
    Returns None if the topic doesn't end with "set" or cannot be parsed.
    """
    try:
        if components[-1] != "set":
            return None

        vendor, device_id = components[1].split("_", 1)
        attribute = components[-2]
        return (vendor, device_id, attribute)
    except Exception as err:
        logger.warning(f"malformed device topic with {components}: {err!r}")
        return None


def safe_split_device(topic: str, segment: str) -> list[str]:
    """Split a topic segment on '-' with error handling."""
    try:
        return segment.split("-", 1)
    except ValueError as err:
        logger.warning(f"ignoring malformed topic {topic}: {err!r}")
        return []
