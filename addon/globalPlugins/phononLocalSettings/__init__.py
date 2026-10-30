# SPDX-License-Identifier: GPL-2.0-or-later
import os

import addonHandler
import globalPluginHandler
import globalVars
import gui
import gui.guiHelper
import gui.settingsDialogs
import wx
import synthDriverHandler

from synthDrivers.phononLocal import _settings
from ._updater import GitHubReleaseUpdater

addonHandler.initTranslation()
_updater = None


class PhononSettingsPanel(gui.settingsDialogs.SettingsPanel):
    title = _("Phonon")

    def makeSettings(self, settingsSizer):
        helper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
        self.gpu = helper.addItem(wx.CheckBox(self, label=_("Use &GPU acceleration when available")))
        self.gpu.SetValue(_settings.use_gpu())
        # Windows skips read-only single-line text controls during Tab navigation.
        self.backend = helper.addLabeledControl(_("Synthesis &backend:"), wx.TextCtrl,
                                               style=wx.TE_READONLY | wx.TE_MULTILINE)
        self.backend.SetName(_("Synthesis backend"))
        self.backend.SetValue("")
        self.refreshBackend()
        self._backendTimer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.refreshBackend, self._backendTimer)
        self.Bind(wx.EVT_WINDOW_DESTROY, self.onBackendDestroy)
        self.interval = helper.addLabeledControl(_("Check for &updates:"), wx.Choice,
                                               choices=[_("Never"), _("Hourly"), _("Daily")])
        self.interval.SetName(_("Check for updates"))
        self.interval.SetSelection(_settings.INTERVALS.index(_settings.update_interval()))
        self.check = helper.addItem(wx.Button(self, label=_("&Check for updates now")))
        self.check.Bind(wx.EVT_BUTTON, self.onCheck)
        self.help = helper.addItem(wx.Button(self, label=_("&Help")))
        self.help.Bind(wx.EVT_BUTTON, self.onHelp)

    def onSave(self):
        _settings.save_use_gpu(self.gpu.GetValue())
        value = _settings.INTERVALS[self.interval.GetSelection()]
        _settings.save_interval(value)
        if _updater is not None:
            _updater.setInterval(value)

    def refreshBackend(self, event=None):
        synth = synthDriverHandler.getSynth()
        if synth is None or getattr(synth, "name", "") != "phononLocal":
            text = _("Phonon is not running; backend has not been tested.")
        else:
            text = {
                "starting": _("Initializing Phonon."),
                "gpu": _("GPU (Vulkan)."),
                "cpu": _("CPU (GPU disabled)."),
                "cpu-fallback": _("CPU fallback (GPU initialization unavailable)."),
                "failed": _("Phonon initialization or synthesis failed."),
                "stopped": _("Phonon is stopped."),
            }.get(getattr(synth, "backendStatus", "unknown"), _("Current engine has not reported its backend."))
        if self.backend.GetValue() != text:
            self.backend.SetValue(text)

    def onPanelActivated(self):
        super().onPanelActivated()
        self.refreshBackend()
        self._backendTimer.Start(1000)

    def onPanelDeactivated(self):
        self._backendTimer.Stop()
        super().onPanelDeactivated()

    def onBackendDestroy(self, event):
        if event.GetEventObject() is self:
            self._backendTimer.Stop()
        event.Skip()

    def onCheck(self, event):
        if _updater is not None:
            _updater.checkNow()

    def onHelp(self, event):
        addon = addonHandler.getCodeAddon()
        path = addon.getDocFilePath() if addon is not None else None
        if path:
            os.startfile(path)
        else:
            gui.messageBox(_("The Phonon manual could not be found."), _("Phonon help"), wx.OK | wx.ICON_ERROR)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    def __init__(self):
        global _updater
        super().__init__()
        self._registered = False
        self._updater = None
        if getattr(globalVars.appArgs, "secure", False):
            return
        _settings.update_interval()
        _settings.use_gpu()
        self._updater = GitHubReleaseUpdater("phononLocal", "Phonon", "OnjLouis", "phononNVDA")
        _updater = self._updater
        self._updater.start()
        categories = gui.settingsDialogs.NVDASettingsDialog.categoryClasses
        if PhononSettingsPanel not in categories:
            categories.append(PhononSettingsPanel)
        self._registered = True

    def terminate(self):
        global _updater
        if self._updater is not None:
            self._updater.stop()
        if _updater is self._updater:
            _updater = None
        categories = gui.settingsDialogs.NVDASettingsDialog.categoryClasses
        if self._registered and PhononSettingsPanel in categories:
            categories.remove(PhononSettingsPanel)
        super().terminate()
