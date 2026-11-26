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

_PCM16_MAX = 32767
_NVDA_SILENCE_PEAK = _PCM16_MAX // 1024
_DECODER_START_SCAN_SAMPLES = SAMPLE_RATE * 20 // 1000
_DECODER_START_QUIET_SAMPLES = SAMPLE_RATE * 5 // 1000
_DECODER_START_MAX_PEAK = 512
_DECODER_SIGNATURE_TOLERANCE = 16
# The bundled decoder emits this waveform before either CPU or Vulkan speech.
# Check raw samples, before Sonic changes their amplitude or timing.
_DECODER_START_SIGNATURE = (
    (0, -35), (2, -66), (4, -170), (10, -244), (21, -192),
    (32, 70), (49, 66), (64, 84), (80, 2), (110, -16),
)


def _is_decoder_start(pcm, count):
    return count > _DECODER_START_SIGNATURE[-1][0] and all(
        abs(pcm[index] * _PCM16_MAX - sample) <= _DECODER_SIGNATURE_TOLERANCE
        for index, sample in _DECODER_START_SIGNATURE
    )


def _quiet_decoder_start(pcm):
    # Silence the pulse only if a quiet run separates it from speech. Leave
    # all subsequent audio unchanged and let NVDA handle the actual trimming.
    quiet = 0
    for index, sample in enumerate(memoryview(pcm).cast("h")[:_DECODER_START_SCAN_SAMPLES]):
        peak = abs(sample)
        if peak > _DECODER_START_MAX_PEAK:
            return pcm
        quiet = quiet + 1 if peak <= _NVDA_SILENCE_PEAK else 0
        if quiet == _DECODER_START_QUIET_SAMPLES:
            cutoff = (index + 1 - quiet) * 2
            return bytes(cutoff) + pcm[cutoff:] if cutoff else pcm
    return pcm


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
    def __init__(self, rate, pitch, volume, boost, trim_leading_silence=False):
        from synthDrivers import _sonic
        _sonic.initialize()
        self._mute = percent(volume) == 0
        self._check_decoder_start = trim_leading_silence and not self._mute
        self._has_decoder_start = False
        self._stream = _sonic.SonicStream(SAMPLE_RATE, 1)
        position = (percent(rate) - 50) / 50
        if boost:
            self._stream.speed = 2 ** (position + 1)
        else:
            self._stream.speed = (3 if position > 0 else 2) ** position
        self._stream.pitch = 2 ** ((percent(pitch) - 50) / 100)
        self._stream.volume = percent(volume) / 100

    def process(self, pcm, count):
        if count and self._check_decoder_start:
            self._check_decoder_start = False
            self._has_decoder_start = _is_decoder_start(pcm, count)
        self._stream.writeFloat(pcm, count)
        return self._read()

    def finish(self):
        self._stream.flush()
        return self._read()

    def _read(self):
        output = bytes(self._stream.readShort())
        if self._mute:
            return bytes(len(output))
        if output and self._has_decoder_start:
            self._has_decoder_start = False
            return _quiet_decoder_start(output)
        return output
