# SPDX-License-Identifier: GPL-2.0-or-later
import config
import re

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


def _engine_preferences():
    config.conf.spec['phononLocalEngine'] = {
        'useGpu': 'boolean(default=True)', 'gpuAdapter': 'string(default="")',
    }
    return config.conf['phononLocalEngine']


def use_gpu():
    return _engine_preferences().get('useGpu', True) is True


def save_use_gpu(value):
    if not isinstance(value, bool):
        raise ValueError('GPU acceleration must be enabled or disabled')
    _engine_preferences()['useGpu'] = value


def _valid_adapter(value):
    return isinstance(value, str) and (not value or re.fullmatch(r"[0-9a-f]{32}", value) is not None)


def gpu_adapter():
    value = _engine_preferences().get('gpuAdapter', '')
    return value if _valid_adapter(value) else ''


def save_gpu_adapter(value):
    if not _valid_adapter(value):
        raise ValueError('Invalid graphics adapter preference')
    _engine_preferences()['gpuAdapter'] = value
