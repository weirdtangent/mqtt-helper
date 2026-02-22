# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Jeff Culverhouse
"""Generic healthcheck for MQTT services.

Run as: python -m mqtt_helper.healthcheck

Environment variables:
    READY_FILE      Path to the readiness sentinel file (default: /tmp/healthcheck.ready)
    HEALTH_MAX_AGE  Maximum age in seconds before the file is considered stale (default: 90)
"""

import os
import sys
import time


def main() -> int:
    path = os.getenv("READY_FILE", "/tmp/healthcheck.ready")
    max_age = int(os.getenv("HEALTH_MAX_AGE", "90"))

    try:
        st = os.stat(path)
        return 0 if time.time() - st.st_mtime < max_age else 1
    except FileNotFoundError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
