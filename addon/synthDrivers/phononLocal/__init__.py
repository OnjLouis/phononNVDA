# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline Phonon speech with NVDA playback-timed notifications."""
from collections import OrderedDict
import queue
import threading
import time

import config
import globalVars
import nvwave
from autoSettingsUtils.driverSetting import DriverSetting
from autoSettingsUtils.utils import StringParameterInfo
from logHandler import log
from speech.commands import BreakCommand, CharacterModeCommand, IndexCommand, PitchCommand, LangChangeCommand
from synthDriverHandler import SynthDriver as BaseSynth, VoiceInfo, synthDoneSpeaking, synthIndexReached

from ._audio import Processor, percent
from ._runtime import Engine, MODEL, SAMPLE_RATE, available, language
from ._sequence import events
from . import _settings
from ._voice_languages import GROUPS, LANGUAGES, normalise


class SynthDriver(BaseSynth):
    name = "phononLocal"
    description = "Phonon"
    supportedSettings = (
        DriverSetting("locale", "Language &filter", availableInSettingsRing=True, defaultVal="all"),
        BaseSynth.VoiceSetting(), BaseSynth.RateSetting(), BaseSynth.RateBoostSetting(),
        BaseSynth.PitchSetting(), BaseSynth.VolumeSetting(),
    )
    supportedCommands = {IndexCommand, PitchCommand, BreakCommand, CharacterModeCommand, LangChangeCommand}
    supportedNotifications = {synthIndexReached, synthDoneSpeaking}

    @classmethod
    def check(cls):
        return not getattr(globalVars.appArgs, "secure", False) and available()

    def __init__(self):
        if not self.check():
            raise RuntimeError("Phonon's bundled model or supported 64-bit CPU runtime is unavailable.")
        super().__init__()
        files = {normalise(path.stem): path.stem for path in (MODEL / "voices").glob("*.safetensors")}
        self._voices = OrderedDict((files[normalise(name)], VoiceInfo(files[normalise(name)], name, language=locale))
                                  for locale, names in GROUPS.items() for name in names if normalise(name) in files)
        if not self._voices:
            raise RuntimeError("Phonon has no installed catalogue voices.")
        self._voice = "Freya" if "Freya" in self._voices else next(iter(self._voices))
        self._locale = "all"
        self._rate, self._pitch, self._volume, self._rateBoost = 50, 50, 100, False
        self._lock = threading.Lock()
        self._control = threading.RLock()
        self._epoch = threading.Event()
        self._stopping = threading.Event()
        self._fallbackPending = threading.Event()
        self._latest_job = 0
        self._jobs = queue.Queue()
        self._useGpu = _settings.use_gpu()
        self._player = nvwave.WavePlayer(1, SAMPLE_RATE, 16, outputDevice=config.conf["audio"]["outputDevice"])
        self._worker = threading.Thread(target=self._run, name="Phonon native speech", daemon=True)
        self._worker.start()

    def initSettings(self):
        saved = config.conf["speech"].get(self.name)
        if saved is not None and saved.get("locale") is None:
            saved["locale"] = "all"
        try:
            super().initSettings()
        except Exception:
            self.terminate()
            raise

    def loadSettings(self, onlyChanged=False):
        self.locale = config.conf["speech"][self.name].get("locale") or "all"
        super().loadSettings(onlyChanged)

    def _get_availableVoices(self):
        if self._locale == "all":
            return self._voices
        return OrderedDict((name, info) for name, info in self._voices.items()
                           if LANGUAGES.get(normalise(name)) == self._locale)

    def _get_availableLocales(self):
        from languageHandler import getLanguageDescription
        codes = {LANGUAGES[normalise(name)] for name in self._voices}
        labels = {code: ("English Character Voices" if code == "en" else getLanguageDescription(code) or code)
                  for code in codes}
        result = OrderedDict([("all", StringParameterInfo("all", "All languages"))])
        result.update((code, StringParameterInfo(code, label)) for code, label in
                      sorted(labels.items(), key=lambda item: item[1].casefold()))
        return result

    def _get_locale(self):
        return self._locale

    def _set_locale(self, value):
        value = value if value in self._get_availableLocales() else "all"
        if value == self._locale:
            return
        self.cancel()
        self._locale = value
        self.invalidateCache()
        self.voice = self._voice
        import synthDriverHandler
        if synthDriverHandler.getSynth() is self:
            import wx
            wx.CallAfter(self._refreshControls)

    def _refreshControls(self):
        import synthDriverHandler
        if self._stopping.is_set() or synthDriverHandler.getSynth() is not self:
            return
        import gui.settingsDialogs
        import wx
        synthDriverHandler.changeVoice(self, None)
        window = wx.Window.FindFocus()
        while window is not None:
            if isinstance(window, gui.settingsDialogs.VoiceSettingsPanel):
                if not window.IsBeingDeleted() and window.getSettings() is self:
                    window.updateDriverSettings(changedSetting="locale")
                return
            window = window.GetParent()
        self.saveSettings()

    def _get_voice(self):
        return self._voice

    def _set_voice(self, value):
        voices = self._get_availableVoices()
        self._voice = value if value in voices else next(iter(voices))

    def _get_language(self):
        return LANGUAGES.get(normalise(self._voice), "en_GB")

    def _get_rate(self):
        return self._rate

    def _set_rate(self, value):
        self._rate = percent(value)

    def _get_pitch(self):
        return self._pitch

    def _set_pitch(self, value):
        self._pitch = percent(value)

    def _get_volume(self):
        return self._volume

    def _set_volume(self, value):
        self._volume = percent(value)

    def _get_rateBoost(self):
        return self._rateBoost

    def _set_rateBoost(self, value):
        self._rateBoost = bool(value)

    def _current(self, epoch):
        return not epoch.is_set() and not self._stopping.is_set()

    def speak(self, speechSequence):
        prepared = events(speechSequence, self._pitch)
        with self._control:
            with self._lock:
                if self._stopping.is_set():
                    return
                self._latest_job += 1
                settings = (self._voice, self._rate, self._pitch, self._volume, self._rateBoost)
                self._jobs.put((self._epoch, self._latest_job, settings, prepared))

    def cancel(self):
        with self._control:
            with self._lock:
                self._epoch.set()
                self._epoch = threading.Event()
                while True:
                    try:
                        self._jobs.get_nowait()
                        self._jobs.task_done()
                    except queue.Empty:
                        break
            self._player.stop()

    def pause(self, switch):
        self._player.pause(switch)

    def _feed(self, epoch, data=None, callback=None):
        if not self._current(epoch):
            return False
        self._player.feed(data, None if data is not None else 0, onDone=callback)
        if not self._current(epoch):
            self._player.stop()
            return False
        return True

    def _index(self, epoch, index):
        if self._current(epoch):
            synthIndexReached.notify(synth=self, index=index)

    def _done(self, epoch, job):
        with self._lock:
            current = self._current(epoch) and job == self._latest_job
        if current:
            synthDoneSpeaking.notify(synth=self)

    def _render(self, engine, epoch, job, settings, prepared):
        voice, rate, pitch, volume, boost = settings
        locale = self._voice_language(voice)
        for kind, value in prepared:
            if not self._current(epoch):
                return
            if kind == "pitch":
                pitch = percent(value)
            elif kind == "language":
                locale = language(value or self._voice_language(voice))
            elif kind == "index":
                self._feed(epoch, callback=lambda index=value: self._index(epoch, index))
            elif kind == "break":
                self._feed(epoch, b"\0\0" * (SAMPLE_RATE * value // 1000))
            elif kind == "text":
                processor = Processor(rate, pitch, volume, boost)

                def consume(pcm, count):
                    if not self._current(epoch):
                        return False
                    data = processor.process(pcm, count)
                    return self._feed(epoch, data) if data else self._current(epoch)

                engine.speak(value, voice, locale, epoch, consume)
                if self._current(epoch):
                    tail = processor.finish()
                    if tail:
                        self._feed(epoch, tail)
        self._feed(epoch, callback=lambda: self._done(epoch, job))

    @staticmethod
    def _voice_language(voice):
        return language(LANGUAGES.get(normalise(voice), "en"))

    def _failure(self):
        if self._fallbackPending.is_set():
            return
        self._fallbackPending.set()
        import wx
        wx.CallAfter(self._useFallback)

    def _useFallback(self):
        import synthDriverHandler
        if self._stopping.is_set() or synthDriverHandler.getSynth() is not self:
            return
        log.warning("Phonon failed; switching to eSpeak NG for continued access.")
        if synthDriverHandler.setSynth("espeak"):
            import ui
            ui.message("Phonon is unavailable. Switched to eSpeak NG.")

    def _run(self):
        engine = None
        try:
            start = time.monotonic()
            engine = Engine(use_gpu=self._useGpu)
            log.info("Phonon 1.0 %s engine ready: %d voices; load %.3f s.", engine.backend,
                     len(self._voices), time.monotonic() - start)
            while True:
                job = self._jobs.get()
                try:
                    if job is None:
                        return
                    if self._current(job[0]) and not self._fallbackPending.is_set():
                        self._render(engine, *job)
                finally:
                    self._jobs.task_done()
                if self._jobs.empty():
                    self._player.idle()
        except Exception:
            log.error("Phonon native speech worker failed.", exc_info=True)
            self._failure()
        finally:
            if engine is not None:
                engine.close()
            self._player.close()

    def terminate(self):
        if self._stopping.is_set():
            return
        self._stopping.set()
        self.cancel()
        self._jobs.put(None)
        self._worker.join(timeout=1)
        if self._worker.is_alive():
            log.warning("Phonon is still completing its current native calculation; NVDA remains available.")
        super().terminate()
