# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Jeff Culverhouse
"""Tests for mqtt_helper.exceptions."""

import pytest

from mqtt_helper import ConfigError


class TestConfigError:
    def test_is_value_error_subclass(self):
        assert issubclass(ConfigError, ValueError)

    def test_message_propagation(self):
        err = ConfigError("bad config")
        assert str(err) == "bad config"

    def test_raises_and_catches_as_value_error(self):
        with pytest.raises(ValueError, match="missing key"):
            raise ConfigError("missing key")

    def test_empty_message(self):
        err = ConfigError()
        assert str(err) == ""
