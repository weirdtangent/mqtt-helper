# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Jeff Culverhouse
"""Tests for mqtt_helper.healthcheck."""

import time

from mqtt_helper.healthcheck import main


class TestHealthcheck:
    def test_fresh_file_returns_0(self, tmp_path, monkeypatch):
        ready = tmp_path / "test.ready"
        ready.touch()
        monkeypatch.setenv("READY_FILE", str(ready))
        monkeypatch.setenv("HEALTH_MAX_AGE", "90")
        assert main() == 0

    def test_stale_file_returns_1(self, tmp_path, monkeypatch):
        ready = tmp_path / "test.ready"
        ready.touch()
        import os

        stale_time = time.time() - 200
        os.utime(ready, (stale_time, stale_time))
        monkeypatch.setenv("READY_FILE", str(ready))
        monkeypatch.setenv("HEALTH_MAX_AGE", "90")
        assert main() == 1

    def test_missing_file_returns_1(self, tmp_path, monkeypatch):
        monkeypatch.setenv("READY_FILE", str(tmp_path / "nonexistent.ready"))
        assert main() == 1

    def test_custom_max_age(self, tmp_path, monkeypatch):
        ready = tmp_path / "test.ready"
        ready.touch()
        import os

        stale_time = time.time() - 5
        os.utime(ready, (stale_time, stale_time))
        monkeypatch.setenv("READY_FILE", str(ready))
        monkeypatch.setenv("HEALTH_MAX_AGE", "3")
        assert main() == 1

    def test_custom_max_age_still_fresh(self, tmp_path, monkeypatch):
        ready = tmp_path / "test.ready"
        ready.touch()
        monkeypatch.setenv("READY_FILE", str(ready))
        monkeypatch.setenv("HEALTH_MAX_AGE", "3")
        assert main() == 0
