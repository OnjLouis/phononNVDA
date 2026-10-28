# SPDX-License-Identifier: GPL-2.0-or-later
import config

INTERVALS = ("never", "hourly", "daily")


def update_interval():
    config.conf.spec["phononLocalUpdates"] = {"interval": "option(never, hourly, daily, default=daily)"}
    value = config.conf["phononLocalUpdates"].get("interval", "daily")
    return value if value in INTERVALS else "daily"


def save_interval(value):
    if value not in INTERVALS:
        raise ValueError("Unknown update interval")
    update_interval()
    config.conf["phononLocalUpdates"]["interval"] = value
