# SPDX-License-Identifier: GPL-2.0-or-later
"""Convert native float PCM and apply NVDA's streaming Sonic controls."""
from ._runtime import SAMPLE_RATE


def percent(value):
    return max(0, min(100, int(value)))


class Processor:
    def __init__(self, rate, pitch, volume, boost):
        from synthDrivers import _sonic
        _sonic.initialize()
        self._mute = percent(volume) == 0
        self._stream = _sonic.SonicStream(SAMPLE_RATE, 1)
        self._stream.speed = 2 ** ((percent(rate) - 50) / 50) * (2 if boost else 1)
        self._stream.pitch = 2 ** ((percent(pitch) - 50) / 100)
        self._stream.volume = percent(volume) / 100

    def process(self, pcm, count):
        self._stream.writeFloat(pcm, count)
        output = bytes(self._stream.readShort())
        return bytes(len(output)) if self._mute else output

    def finish(self):
        self._stream.flush()
        output = bytes(self._stream.readShort())
        return bytes(len(output)) if self._mute else output
