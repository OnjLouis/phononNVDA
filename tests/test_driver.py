"""Speech lifecycle regressions with isolated NVDA interfaces and no playback."""
import ctypes
import importlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

ADDON = Path(os.environ.get("PHONON_LOCAL_ADDON_ROOT", Path(__file__).resolve().parents[1] / "addon"))
sys.path.insert(0, str(ADDON))


class Base:
    VoiceSetting = RateSetting = RateBoostSetting = PitchSetting = VolumeSetting = staticmethod(lambda: None)

    def __getattr__(self, name):
        method = getattr(type(self), "_get_" + name, None)
        if method:
            return method(self)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        method = getattr(type(self), "_set_" + name, None) if not name.startswith("_") else None
        method(self, value) if method else object.__setattr__(self, name, value)

    def invalidateCache(self): pass
    def terminate(self): pass
    def initSettings(self): pass
    def loadSettings(self, onlyChanged=False): pass


class Command:
    def __init__(self, value=None, **values):
        self.index = self.state = self.time = self.newValue = value
        self.isDefault = value is None
        self.__dict__.update(values)


class Notice:
    def __init__(self): self.items = []
    def notify(self, **values): self.items.append(values)


class Player:
    def __init__(self, *args, **kwargs):
        self.audio, self.callbacks = [], []
        self.closed, self.paused = False, False
    def feed(self, data, size=None, onDone=None):
        assert not self.closed
        if data: self.audio.append(bytes(data))
        if onDone: self.callbacks.append(onDone)
    def stop(self): self.callbacks.clear()
    def pause(self, value): self.paused = value
    def idle(self): pass
    def close(self): self.closed = True
    def drain(self):
        while self.callbacks: self.callbacks.pop(0)()


class Config(dict):
    spec = {}


config = types.SimpleNamespace(conf=Config(audio={"outputDevice": "fixture"}, speech={"phononLocal": {"pitch": 50}},
    phononLocalEngine={"useGpu": False}))
notice, done = Notice(), Notice()
handler = types.SimpleNamespace(SynthDriver=Base, VoiceInfo=lambda id, name, language=None:
    types.SimpleNamespace(id=id, displayName=name, language=language),
    synthIndexReached=notice, synthDoneSpeaking=done, getSynth=lambda: None)
commands = types.ModuleType("speech.commands")
for name in ("IndexCommand", "PitchCommand", "CharacterModeCommand", "BreakCommand", "LangChangeCommand"):
    setattr(commands, name, type(name, (Command,), {}))
sys.modules.update({"config": config, "globalVars": types.SimpleNamespace(appArgs=types.SimpleNamespace(secure=False)),
    "nvwave": types.SimpleNamespace(WavePlayer=Player), "synthDriverHandler": handler,
    "speech": types.ModuleType("speech"), "speech.commands": commands,
    "autoSettingsUtils": types.ModuleType("autoSettingsUtils"),
    "autoSettingsUtils.driverSetting": types.SimpleNamespace(DriverSetting=lambda id, label, **kw: types.SimpleNamespace(id=id, **kw)),
    "autoSettingsUtils.utils": types.SimpleNamespace(StringParameterInfo=lambda id, name: types.SimpleNamespace(id=id, displayName=name)),
    "languageHandler": types.SimpleNamespace(getLanguageDescription=lambda code: code),
    "logHandler": types.SimpleNamespace(log=types.SimpleNamespace(**{name: lambda *a, **k: None for name in ("info", "error", "warning", "debug", "debugWarning")}))})
driver_module = importlib.import_module("synthDrivers.phononLocal")


class FakeEngine:
    calls = []
    started = threading.Event()
    closed = []

    def __init__(self, use_gpu=False):
        self.owner = threading.get_ident()
        self.backend = 'GPU (Vulkan)' if use_gpu else 'CPU'
    def speak(self, text, voice, locale, cancel, sink):
        self.calls.append((text, voice, locale))
        if text == "block":
            self.started.set()
            cancel.wait(2)
        sink(None, 8)
    def close(self):
        assert self.owner == threading.get_ident()
        self.closed.append(self.owner)


class Processor:
    def __init__(self, *args): pass
    def process(self, pcm, count): return b"\1\0" * count
    def finish(self): return b""


class VoiceChoice:
    def __init__(self, voices):
        self.items = [voice.displayName for voice in voices]
        self.selection = 0
        self.refreshes = 0

    def SetItems(self, items):
        self.items = list(items)
        self.refreshes += 1

    def SetSelection(self, index):
        self.selection = index


class VoicePanel:
    def __init__(self, driver):
        self.driver = driver
        self._voices = list(driver.availableVoices.values())
        self.voiceList = VoiceChoice(self._voices)
        self.deleted = False

    def getSettings(self): return self.driver
    def IsBeingDeleted(self): return self.deleted
    def GetParent(self): return None

    def updateDriverSettings(self, changedSetting=None):
        # NVDA updates the selection from this cache, not availableVoices.
        ids = [voice.id for voice in self._voices]
        if self.driver.voice in ids:
            self.voiceList.SetSelection(ids.index(self.driver.voice))


def wait_for(predicate):
    end = time.monotonic() + 3
    while not predicate():
        if time.monotonic() > end: raise AssertionError("Fixture worker timed out")
        time.sleep(0.002)


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        (root / "voices").mkdir()
        for name in ("Freya", "Pippa", "Zoey", "Juniper", "Nele", "Noa"):
            (root / "voices" / (name + ".safetensors")).touch()
        for name, value in (("MODEL", root), ("available", lambda: True), ("Engine", FakeEngine), ("Processor", Processor)):
            patched = patch.object(driver_module, name, value)
            patched.start(); self.addCleanup(patched.stop)
        FakeEngine.calls, FakeEngine.closed = [], []
        FakeEngine.started.clear()
        notice.items.clear(); done.items.clear()
        self.driver = driver_module.SynthDriver()
        self.addCleanup(self.driver.terminate)

    def test_notification_waits_for_playback(self):
        self.driver.speak(["hello", commands.IndexCommand(2)])
        wait_for(lambda: len(self.driver._player.callbacks) == 2)
        self.assertFalse(notice.items or done.items)
        self.driver._player.drain()
        self.assertEqual(notice.items[0]["index"], 2)
        self.assertEqual(len(done.items), 1)

    def test_backend_status_reports_initialized_cpu(self):
        self.assertTrue(hasattr(self.driver, "backendStatus"))
        wait_for(lambda: self.driver.backendStatus != "starting")
        self.assertEqual(self.driver.backendStatus, "cpu")

    def test_backend_status_distinguishes_gpu_and_cpu_fallback(self):
        class CpuFallback(FakeEngine):
            def __init__(self, use_gpu=False):
                super().__init__(use_gpu=False)
        for engine, expected in ((FakeEngine, "gpu"), (CpuFallback, "cpu-fallback")):
            with self.subTest(expected=expected), patch.object(driver_module, "Engine", engine), \
                    patch.object(driver_module._settings, "use_gpu", return_value=True):
                driver = driver_module.SynthDriver()
                try:
                    self.assertTrue(hasattr(driver, "backendStatus"))
                    wait_for(lambda: driver.backendStatus != "starting")
                    self.assertEqual(driver.backendStatus, expected)
                finally:
                    driver.terminate()

    def test_backend_status_reports_stop_without_native_calls(self):
        self.driver.terminate()
        self.assertEqual(self.driver.backendStatus, "stopped")

    def test_cancel_discards_old_audio_and_keeps_new_utterance(self):
        self.driver.speak(["block", commands.IndexCommand(1)])
        self.assertTrue(FakeEngine.started.wait(2))
        self.driver.cancel()
        self.driver.speak(["next", commands.IndexCommand(2)])
        wait_for(lambda: len(self.driver._player.callbacks) == 2)
        self.driver._player.drain()
        self.assertEqual([item["index"] for item in notice.items], [2])
        self.assertEqual(len(self.driver._player.audio), 1)
        self.assertEqual(FakeEngine.calls[-1][0], "next")

    def test_spelling_punctuation_and_all_indices_survive(self):
        self.driver.speak([commands.CharacterModeCommand(True), "1", commands.CharacterModeCommand(False),
            "dot", commands.IndexCommand(1), commands.CharacterModeCommand(True), "AB",
            commands.CharacterModeCommand(False), "space", commands.IndexCommand(2)])
        wait_for(lambda: len(self.driver._player.callbacks) == 3)
        self.driver._player.drain()
        self.assertEqual([item[0] for item in FakeEngine.calls], ["1", "dot", "A B", "space"])
        self.assertEqual([item["index"] for item in notice.items], [1, 2])

    def test_language_filter_has_no_model_selector(self):
        self.driver.locale = "de_DE"
        self.assertEqual(list(self.driver.availableVoices), ["Nele"])
        self.assertEqual(self.driver.voice, "Nele")
        self.assertEqual(self.driver.language, "de_DE")
        self.assertNotIn("model", [getattr(s, "id", None) for s in self.driver.supportedSettings])

    def test_reopened_speech_panel_refreshes_voice_choices_after_filter_change(self):
        self.driver.locale = 'en_GB'
        panel = VoicePanel(self.driver)
        pending = []
        gui = types.ModuleType('gui')
        dialogs = types.ModuleType('gui.settingsDialogs')
        dialogs.VoiceSettingsPanel = VoicePanel
        gui.settingsDialogs = dialogs
        modules = {'gui': gui, 'gui.settingsDialogs': dialogs,
            'wx': types.SimpleNamespace(CallAfter=lambda fn: pending.append(fn),
                Window=types.SimpleNamespace(FindFocus=lambda: panel))}
        with patch.dict(sys.modules, modules), patch.object(handler, 'getSynth', lambda: self.driver), \
                patch.object(handler, 'changeVoice', lambda *args: None, create=True):
            self.driver.locale = 'en_US'
            self.assertEqual(len(pending), 1)
            pending.pop()()
            self.assertEqual([voice.id for voice in panel._voices], ['Zoey', 'Juniper'])
            self.assertEqual(panel.voiceList.items, ['Zoey', 'Juniper'])
            self.assertEqual(panel._voices[panel.voiceList.selection].id, self.driver.voice)
            self.driver.voice = panel._voices[1].id
            self.assertEqual(self.driver.voice, 'Juniper')

    def test_speech_panel_does_not_rebuild_unchanged_voice_choices(self):
        self.driver.locale = 'en_US'
        panel = VoicePanel(self.driver)
        gui = types.ModuleType('gui')
        dialogs = types.ModuleType('gui.settingsDialogs')
        dialogs.VoiceSettingsPanel = VoicePanel
        gui.settingsDialogs = dialogs
        modules = {'gui': gui, 'gui.settingsDialogs': dialogs,
            'wx': types.SimpleNamespace(Window=types.SimpleNamespace(FindFocus=lambda: panel))}
        with patch.dict(sys.modules, modules), patch.object(handler, 'getSynth', lambda: self.driver), \
                patch.object(handler, 'changeVoice', lambda *args: None, create=True):
            self.driver._refreshControls()
            self.driver._refreshControls()
        self.assertEqual(panel.voiceList.refreshes, 0)

    def test_language_command_and_restore(self):
        self.driver.speak([commands.LangChangeCommand(lang="fr_FR"), "bonjour", commands.LangChangeCommand(lang=None), "hello"])
        wait_for(lambda: len(FakeEngine.calls) == 2)
        self.assertEqual([item[2] for item in FakeEngine.calls], ["fr", "en"])

    def test_pause_and_close_are_owned_by_worker(self):
        self.driver.pause(True)
        self.assertTrue(self.driver._player.paused)
        self.driver.pause(False)
        self.assertFalse(self.driver._player.paused)
        self.driver.terminate()
        self.assertFalse(self.driver._worker.is_alive())
        self.assertTrue(self.driver._player.closed)
        self.assertEqual(len(FakeEngine.closed), 1)

    def test_numeric_controls_clamp_and_persist_values(self):
        self.driver.rate = 150
        self.driver.pitch = -10
        self.driver.volume = 65
        self.driver.rateBoost = True
        self.assertEqual((self.driver.rate, self.driver.pitch, self.driver.volume, self.driver.rateBoost), (100, 0, 65, True))

    def test_invalid_saved_filter_falls_back_to_all(self):
        self.driver.locale = "invalid"
        self.assertEqual(self.driver.locale, "all")

    def test_new_driver_is_safe_while_old_worker_finishes_after_shutdown_wait(self):
        entered, release = threading.Event(), threading.Event()
        original = FakeEngine.speak
        def slow(engine, text, voice, locale, cancel, sink):
            if text == 'slow native request':
                entered.set()
                release.wait(4)
            original(engine, text, voice, locale, cancel, sink)
        with patch.object(FakeEngine, 'speak', slow):
            self.driver.speak(['slow native request', commands.IndexCommand(10)])
            self.assertTrue(entered.wait(1))
            self.driver.terminate()
            self.assertTrue(self.driver._worker.is_alive())
            replacement = driver_module.SynthDriver()
            try:
                replacement.speak(['replacement', commands.IndexCommand(99)])
                wait_for(lambda: len(replacement._player.callbacks) == 2)
                replacement._player.drain()
                self.assertEqual([item['index'] for item in notice.items], [99])
                release.set()
                self.driver._worker.join(2)
                self.assertFalse(self.driver._worker.is_alive())
                self.assertFalse(self.driver._player.audio)
                self.assertFalse(replacement._player.closed)
                self.assertEqual([item['index'] for item in notice.items], [99])
            finally:
                release.set()
                self.driver._worker.join(2)
                replacement.terminate()


if __name__ == "__main__": unittest.main()
