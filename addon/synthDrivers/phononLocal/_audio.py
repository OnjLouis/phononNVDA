# SPDX-License-Identifier: GPL-2.0-or-later
"""Convert native float PCM and apply NVDA's streaming Sonic controls."""
from collections import deque

from ._runtime import SAMPLE_RATE

PAUSE_LENGTHS = {"shorter": "Shorter", "default": "Default", "longer": "Longer"}
_FRAME_MS = 5
_FRAME_BYTES = SAMPLE_RATE * _FRAME_MS // 1000 * 2
_QUIET_PEAK = 96
_SHORT_HEAD_FRAMES = 95 // _FRAME_MS
_SHORT_TAIL_FRAMES = 25 // _FRAME_MS
_LONG_MIN_FRAMES = 120 // _FRAME_MS
_LONG_EXTRA = bytes(80 // _FRAME_MS * _FRAME_BYTES)


def percent(value):
    return max(0, min(100, int(value)))


class PauseAdjuster:
    """Adjust sustained low-level gaps in streaming PCM, not punctuation itself."""

    def __init__(self, mode, volume):
        self._shorten = mode == "shorter"
        self._threshold = max(1, _QUIET_PEAK * percent(volume) // 100)
        self._heard_speech = False
        self._quiet_frames = 0
        self._partial = b""
        self._tail = deque(maxlen=_SHORT_TAIL_FRAMES)

    def _emit_frame(self, frame, out):
        threshold = self._threshold
        if any(sample < -threshold or sample > threshold for sample in memoryview(frame).cast("h")):
            if self._shorten:
                out.extend(self._tail)
                self._tail.clear()
            elif self._quiet_frames >= _LONG_MIN_FRAMES:
                out.append(_LONG_EXTRA)
            self._quiet_frames = 0
            self._heard_speech = True
        elif self._heard_speech:
            self._quiet_frames += 1
            if self._shorten and self._quiet_frames > _SHORT_HEAD_FRAMES:
                # Preserve both edges of the pause; remove only its quiet middle.
                self._tail.append(frame)
                return
        out.append(frame)

    def process(self, pcm):
        pcm = self._partial + pcm
        end = len(pcm) - len(pcm) % _FRAME_BYTES
        self._partial = pcm[end:]
        out = []
        for start in range(0, end, _FRAME_BYTES):
            self._emit_frame(pcm[start:start + _FRAME_BYTES], out)
        return b"".join(out)

    def finish(self):
        # A short final fragment can contain speech; preserve it intact.
        return b"".join(self._tail) + self._partial


class Processor:
    def __init__(self, rate, pitch, volume, boost):
        from synthDrivers import _sonic
        _sonic.initialize()
        self._mute = percent(volume) == 0
        self._stream = _sonic.SonicStream(SAMPLE_RATE, 1)
        position = (percent(rate) - 50) / 50
        if boost:
            self._stream.speed = 2 ** (position + 1)
        else:
            self._stream.speed = (3 if position > 0 else 2) ** position
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
