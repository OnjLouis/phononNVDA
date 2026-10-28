# SPDX-License-Identifier: GPL-2.0-or-later
import os

import addonHandler
import globalPluginHandler
import globalVars
import gui
import gui.guiHelper
import gui.settingsDialogs
import wx

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
