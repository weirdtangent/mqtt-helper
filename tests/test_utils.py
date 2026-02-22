# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Jeff Culverhouse
"""Tests for mqtt_helper.utils."""

import json

from mqtt_helper import decode_mqtt_payload, parse_device_topic, safe_split_device


class TestDecodeMqttPayload:
    def test_json_dict(self):
        raw = json.dumps({"key": "value"}).encode()
        assert decode_mqtt_payload(raw) == {"key": "value"}

    def test_json_list(self):
        raw = json.dumps([1, 2, 3]).encode()
        assert decode_mqtt_payload(raw) == [1, 2, 3]

    def test_json_string(self):
        raw = json.dumps("online").encode()
        assert decode_mqtt_payload(raw) == "online"

    def test_plain_utf8_fallback(self):
        raw = b"online"
        assert decode_mqtt_payload(raw) == "online"

    def test_invalid_bytes_returns_none(self):
        raw = bytes([0xFF, 0xFE, 0x80, 0x81])
        assert decode_mqtt_payload(raw) is None

    def test_empty_bytes_decodes_as_empty_string(self):
        assert decode_mqtt_payload(b"") == ""

    def test_bytearray_input(self):
        raw = bytearray(json.dumps({"a": 1}).encode())
        assert decode_mqtt_payload(raw) == {"a": 1}

    def test_json_number(self):
        raw = b"42"
        assert decode_mqtt_payload(raw) == 42


class TestParseDeviceTopic:
    def test_valid_switch_topic(self):
        components = ["svc2mqtt", "svc2mqtt_DEVICE123", "switch", "motion", "set"]
        result = parse_device_topic(components)
        assert result == ("svc2mqtt", "DEVICE123", "motion")

    def test_valid_button_topic(self):
        components = ["svc2mqtt", "svc2mqtt_ABC", "button", "reboot", "set"]
        result = parse_device_topic(components)
        assert result == ("svc2mqtt", "ABC", "reboot")

    def test_not_set_suffix_returns_none(self):
        components = ["svc2mqtt", "svc2mqtt_ABC", "switch", "motion", "get"]
        assert parse_device_topic(components) is None

    def test_malformed_no_underscore_returns_none(self):
        components = ["svc2mqtt", "nounderscore", "switch", "motion", "set"]
        assert parse_device_topic(components) is None

    def test_empty_list_returns_none(self):
        assert parse_device_topic([]) is None

    def test_returns_tuple(self):
        components = ["svc", "svc_dev", "switch", "attr", "set"]
        result = parse_device_topic(components)
        assert isinstance(result, tuple)


class TestSafeSplitDevice:
    def test_normal_split(self):
        result = safe_split_device("some/topic", "vendor-device123")
        assert result == ["vendor", "device123"]

    def test_no_dash(self):
        result = safe_split_device("some/topic", "nodash")
        assert result == ["nodash"]

    def test_multi_dash(self):
        result = safe_split_device("some/topic", "vendor-device-extra")
        assert result == ["vendor", "device-extra"]
