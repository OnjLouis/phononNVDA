# SPDX-License-Identifier: GPL-2.0-or-later
"""Settings-panel regressions without live NVDA, native synthesis or wx."""
import builtins
import importlib.util
import sys
import types
import unittest
from unittest.mock import Mock, patch

import test_driver as fixture


class Control:
    def __init__(self, parent, label="", choices=(), **kwargs):
        self.label = self.name = label
        self.choices = choices
        self.selection = -1
        self.style = kwargs.get("style", 0)
        self.bindings = {}
        parent.children.append(self)

    def SetName(self, name): self.name = name
    def SetValue(self, value): self.value = value
    def GetValue(self): return self.value
    def SetSelection(self, value): self.selection = value
    def GetSelection(self): return self.selection
    def Enable(self, value): self.enabled = value
    def Bind(self, event, handler): self.bindings[event] = handler


class Panel:
    def __init__(self): self.children = []; self.bindings = {}
    def Bind(self, event, handler, *args): self.bindings[event] = handler
    def onPanelActivated(self): pass
    def onPanelDeactivated(self): pass


class Helper:
    def __init__(self, parent, sizer): self.parent = parent
    def addLabeledControl(self, label, control, **kwargs):
        return control(self.parent, label=label, **kwargs)
    def addItem(self, item): return item


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.addon = types.SimpleNamespace(initTranslation=lambda: None, getCodeAddon=lambda: None)
        self.gui = types.ModuleType("gui")
        self.gui.messageBox = Mock()
        self.gui.guiHelper = types.ModuleType("gui.guiHelper")
        self.gui.guiHelper.BoxSizerHelper = Helper
        self.gui.settingsDialogs = types.ModuleType("gui.settingsDialogs")
        self.gui.settingsDialogs.SettingsPanel = Panel
        wx = types.SimpleNamespace(Choice=Control, Button=Control, CheckBox=Control, TextCtrl=Control,
            TE_READONLY=1, TE_MULTILINE=2, EVT_BUTTON="button", EVT_TIMER="timer", EVT_WINDOW_DESTROY="destroy",
            Timer=lambda owner: Mock(), OK=4, ICON_ERROR=8)
        dependencies = {
            "addonHandler": self.addon,
            "globalPluginHandler": types.SimpleNamespace(GlobalPlugin=object),
            "gui": self.gui, "gui.guiHelper": self.gui.guiHelper,
            "gui.settingsDialogs": self.gui.settingsDialogs, "wx": wx,
            "isolated_settings_panel._updater": types.SimpleNamespace(GitHubReleaseUpdater=Mock()),
        }
        modules = patch.dict(sys.modules, dependencies)
        modules.start()
        self.addCleanup(modules.stop)
        translation = patch.object(builtins, "_", lambda text: text, create=True)
        translation.start()
        self.addCleanup(translation.stop)
        config = patch.dict(fixture.config.conf, {"phononLocalUpdates": {"interval": "daily"},
            "phononLocalEngine": {"useGpu": False}})
        config.start()
        self.addCleanup(config.stop)
        spec = importlib.util.spec_from_file_location("isolated_settings_panel",
            fixture.ADDON / "globalPlugins/phononLocalSettings/__init__.py",
            submodule_search_locations=[])
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.panel = self.module.PhononSettingsPanel()
        self.panel.makeSettings(object())

    def test_gpu_updates_and_help_have_unique_mnemonics(self):
        labels = [control.label for control in self.panel.children]
        self.assertEqual(labels, ["Use &GPU acceleration when available", "Synthesis &backend:", "Check for &updates:", "&Check for updates now", "&Help"])
        self.assertEqual([label.split("&", 1)[1][0].lower() for label in labels], ["g", "b", "u", "c", "h"])
        self.assertFalse(self.panel.gpu.GetValue())
        self.assertEqual(self.panel.interval.name, "Check for updates")
        self.assertEqual(self.panel.interval.choices, ["Never", "Hourly", "Daily"])
        self.assertEqual(self.panel.interval.GetSelection(), 2)
        self.assertFalse(hasattr(self.panel, "onUse"))

    def test_backend_status_does_not_probe_or_assume_gpu(self):
        self.assertTrue(hasattr(self.panel, "backend"))
        self.assertIn("not running", self.panel.backend.GetValue())
        for value, expected in (("starting", "Initializing"), ("gpu", "GPU (Vulkan)"),
                                ("cpu", "GPU disabled"), ("cpu-fallback", "fallback")):
            synth = types.SimpleNamespace(name="phononLocal", backendStatus=value)
            with patch.object(fixture.handler, "getSynth", return_value=synth):
                self.panel.refreshBackend()
                self.assertIn(expected, self.panel.backend.GetValue())

    def test_backend_is_read_only_and_tabbable_after_gpu(self):
        self.assertEqual(self.panel.backend.style, self.module.wx.TE_READONLY | self.module.wx.TE_MULTILINE)
        self.assertEqual(self.panel.children[:3], [self.panel.gpu, self.panel.backend, self.panel.interval])

    def test_backend_refresh_does_not_rewrite_unchanged_text(self):
        original = self.panel.backend.SetValue
        self.panel.backend.SetValue = Mock(side_effect=original)
        self.panel.refreshBackend()
        self.panel.refreshBackend()
        self.panel.backend.SetValue.assert_not_called()

    def test_backend_timer_stops_outside_active_panel(self):
        self.panel.onPanelActivated()
        self.panel._backendTimer.Start.assert_called_once_with(1000)
        self.panel.onPanelDeactivated()
        self.panel._backendTimer.Stop.assert_called_once_with()
        event = types.SimpleNamespace(GetEventObject=lambda: self.panel, Skip=Mock())
        self.panel.onBackendDestroy(event)
        self.assertEqual(self.panel._backendTimer.Stop.call_count, 2)
        event.Skip.assert_called_once_with()

    def test_each_update_interval_is_saved_and_applied(self):
        updater = Mock()
        with patch.object(self.module, "_updater", updater):
            for selection, value in enumerate(("never", "hourly", "daily")):
                with self.subTest(value=value):
                    self.panel.interval.SetSelection(selection)
                    self.panel.onSave()
                    self.assertEqual(fixture.config.conf["phononLocalUpdates"]["interval"], value)
                    updater.setInterval.assert_called_with(value)

    def test_save_without_updater(self):
        self.panel.interval.SetSelection(0)
        self.panel.onSave()
        self.assertEqual(fixture.config.conf["phononLocalUpdates"]["interval"], "never")

    def test_gpu_preference_round_trips_and_rejects_ambiguous_values(self):
        for enabled in (True, False):
            self.panel.gpu.SetValue(enabled)
            self.panel.onSave()
            self.assertIs(self.module._settings.use_gpu(), enabled)
        with self.assertRaises(ValueError):
            self.module._settings.save_use_gpu('false')

    def test_missing_preference_defaults_to_gpu_without_overriding_opt_out(self):
        fixture.config.conf['phononLocalEngine'].pop('useGpu')
        panel = self.module.PhononSettingsPanel()
        panel.makeSettings(object())
        self.assertTrue(panel.gpu.GetValue())
        self.assertEqual(fixture.config.conf.spec['phononLocalEngine']['useGpu'], 'boolean(default=True)')
        self.module._settings.save_use_gpu(False)
        self.assertFalse(self.module._settings.use_gpu())

    def test_check_now_button_dispatches_only_when_updater_exists(self):
        handler = self.panel.check.bindings["button"]
        handler(None)
        updater = Mock()
        with patch.object(self.module, "_updater", updater):
            handler(None)
        updater.checkNow.assert_called_once_with()

    def test_help_button_opens_manual(self):
        self.addon.getCodeAddon = lambda: types.SimpleNamespace(getDocFilePath=lambda: "manual.html")
        with patch.object(self.module.os, "startfile") as startfile:
            self.panel.help.bindings["button"](None)
        startfile.assert_called_once_with("manual.html")

    def test_missing_manual_reports_error(self):
        for addon in (None, types.SimpleNamespace(getDocFilePath=lambda: None)):
            with self.subTest(addon=addon):
                self.addon.getCodeAddon = lambda: addon
                self.gui.messageBox.reset_mock()
                self.panel.onHelp(None)
                self.gui.messageBox.assert_called_once_with(
                    "The Phonon manual could not be found.", "Phonon help", 12)


if __name__ == "__main__":
    unittest.main()
