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


def use_gpu():
    config.conf.spec['phononLocalEngine'] = {'useGpu': 'boolean(default=True)'}
    return config.conf['phononLocalEngine'].get('useGpu', True) is True


def save_use_gpu(value):
    if not isinstance(value, bool):
        raise ValueError('GPU acceleration must be enabled or disabled')
    use_gpu()
    config.conf['phononLocalEngine']['useGpu'] = value
