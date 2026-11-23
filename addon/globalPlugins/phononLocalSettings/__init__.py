# SPDX-License-Identifier: GPL-2.0-or-later
import os
from pathlib import Path
import threading

import addonHandler
import config
import globalPluginHandler
import globalVars
import gui
import gui.guiHelper
import gui.settingsDialogs
import wx
import synthDriverHandler
import languageHandler
import nvwave
from logHandler import log

from synthDrivers.phononLocal import GROUPS, normalise, stock_voice_files, _imports, _runtime, _settings
from synthDrivers.phononLocal._audio import Processor
from ._updater import GitHubReleaseUpdater

addonHandler.initTranslation()
_updater = None


def _reportVoiceError(message):
    gui.messageBox(message, _("Phonon voices"), wx.OK | wx.ICON_ERROR)


_SAMPLES = {
    "en": "Hello. This is a preview of the selected voice.",
    "fr": "Bonjour. Voici un extrait de la voix choisie.",
    "de": "Guten Tag. So klingt die ausgewählte Stimme.",
    "es": "Hola. Así suena la voz seleccionada.",
    "pt": "Olá. Esta é uma amostra da voz selecionada.",
}


def _voiceDetails(parent, locales, name, data, existing=None):
    with wx.Dialog(parent, title=_("Change voice language") if existing else _("Import voice")) as form:
        helper = gui.guiHelper.BoxSizerHelper(form, orientation=wx.VERTICAL)
        naming = None
        if not existing:
            naming = helper.addLabeledControl(_("&Voice name:"), wx.TextCtrl)
            naming.SetValue(name)
        languages = [_("Choose a language")] + [languageHandler.getLanguageDescription(code) or code for code in locales]
        choice = helper.addLabeledControl(_("Spoken &language:"), wx.Choice, choices=languages)
        choice.SetSelection(locales.index(existing) + 1 if existing else 0)
        play = helper.addItem(wx.Button(form, label=_("&Preview voice")))
        stop = helper.addItem(wx.Button(form, label=_("&Stop preview")))
        stop.Disable()
        save = helper.addItem(wx.Button(form, wx.ID_OK, label=_("Sa&ve") if existing else _("&Import")))
        helper.addItem(wx.Button(form, wx.ID_CANCEL, label=_("&Cancel")))
        form.SetSizerAndFit(helper.sizer)
        cancel = threading.Event()
        player = None
        closed = False

        def stopPreview(event=None):
            cancel.set()
            if player is not None:
                player.stop()

        def finish(failure):
            nonlocal player
            if closed:
                return
            player = None
            play.Enable()
            stop.Disable()
            if failure and not cancel.is_set():
                _reportVoiceError(_("Phonon could not preview this voice."))

        def start(event):
            nonlocal player
            selected = choice.GetSelection()
            locale = locales[selected - 1] if selected > 0 else "en_US"
            sample = _SAMPLES.get(locale.split("_")[0], _SAMPLES["en"])
            device = config.conf["audio"]["outputDevice"]
            player = nvwave.WavePlayer(1, _runtime.SAMPLE_RATE, 16, outputDevice=device)
            output = player
            cancel.clear()
            play.Disable()
            stop.Enable()
            stop.SetFocus()

            def run():
                engine = None
                failed = False
                try:
                    engine = _runtime.Engine()
                    processor = Processor(50, 50, 100, False)

                    def feed(pcm, count):
                        if cancel.is_set():
                            return False
                        audio = processor.process(pcm, count)
                        if audio:
                            output.feed(audio)
                        return not cancel.is_set()

                    engine.speak(sample, "", locale, cancel, feed, draft=data)
                    if not cancel.is_set():
                        output.feed(processor.finish())
                        output.idle()
                except Exception:
                    failed = True
                    log.exception("Phonon voice preview failed")
                finally:
                    if engine is not None:
                        engine.close()
                    output.close()
                    wx.CallAfter(finish, failed)

            threading.Thread(target=run, name="Phonon voice preview", daemon=True).start()

        def updateSave(event=None):
            save.Enable(choice.GetSelection() > 0 and (naming is None or bool(naming.GetValue().strip())))

        play.Bind(wx.EVT_BUTTON, start)
        stop.Bind(wx.EVT_BUTTON, stopPreview)
        choice.Bind(wx.EVT_CHOICE, updateSave)
        if naming is not None:
            naming.Bind(wx.EVT_TEXT, updateSave)
        updateSave()
        (naming if naming is not None else choice).SetFocus()
        try:
            if form.ShowModal() == wx.ID_OK and choice.GetSelection() > 0:
                return naming.GetValue() if naming is not None else name, locales[choice.GetSelection() - 1]
        finally:
            closed = True
            stopPreview()


def _manageVoices(parent):
    root = _imports.profile_root()
    dialog = wx.Dialog(parent, title=_("Manage imported Phonon voices"))
    try:
        helper = gui.guiHelper.BoxSizerHelper(dialog, orientation=wx.VERTICAL)
        voices = helper.addLabeledControl(_("&Imported voices:"), wx.ListBox)
        voices.SetName(_("Imported voices"))
        add = helper.addItem(wx.Button(dialog, label=_("&Add voice...")))
        edit = helper.addItem(wx.Button(dialog, label=_("&Change language...")))
        remove = helper.addItem(wx.Button(dialog, label=_("&Remove voice")))
        close = helper.addItem(wx.Button(dialog, wx.ID_CLOSE, label=_("&Done")))
        dialog.SetEscapeId(wx.ID_CLOSE)
        dialog.SetSizerAndFit(helper.sizer)
        entries = ()
        locales = sorted(_imports.LOCALES, key=lambda code: languageHandler.getLanguageDescription(code) or code)

        def updateRemoval(event=None):
            remove.Enable(voices.GetSelection() != wx.NOT_FOUND)
            edit.Enable(remove.IsEnabled())

        def refresh(preferred=None):
            nonlocal entries
            try:
                entries = _imports.list_voices(root)
            except OSError:
                entries = ()
                _reportVoiceError(_("Phonon could not read the imported voice folder."))
            voices.Set([_("{name}, {language}").format(name=voice.name,
                language=languageHandler.getLanguageDescription(voice.locale) or voice.locale) for voice in entries])
            if entries:
                voices.SetSelection(next((index for index, voice in enumerate(entries) if voice.id == preferred), 0))
            updateRemoval()
            synth = synthDriverHandler.getSynth()
            if getattr(synth, "name", None) == "phononLocal":
                synth.refreshImportedVoices()

        def onAdd(event):
            with wx.FileDialog(dialog, _("Choose a Phonon voice"), wildcard=_("Phonon voice (*.safetensors)|*.safetensors"),
                               style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as picker:
                if picker.ShowModal() != wx.ID_OK:
                    return
                path = Path(picker.GetPath())
            try:
                data = _imports.read_source(path)
                details = _voiceDetails(dialog, locales, path.stem[:_imports.MAX_NAME], data)
                if details is None:
                    return
                voice = _imports.save_voice(root, data, *details, _runtime.validate_import)
            except (OSError, ValueError, _runtime.NativeError):
                _reportVoiceError(_("Could not import this voice. Choose a compatible Phonon .safetensors file and enter a valid name."))
                return
            refresh(voice.id)
            voices.SetFocus()

        def onEdit(event):
            index = voices.GetSelection()
            if not 0 <= index < len(entries):
                return
            voice = entries[index]
            try:
                details = _voiceDetails(dialog, locales, voice.name, _imports.read_voice(root, voice.id), voice.locale)
                if details is not None:
                    _imports.update_locale(root, voice.id, details[1])
                    refresh(voice.id)
            except (OSError, ValueError):
                _reportVoiceError(_("Could not change the language of this voice."))
            voices.SetFocus()

        def onRemove(event):
            index = voices.GetSelection()
            if not 0 <= index < len(entries):
                return
            voice = entries[index]
            if gui.messageBox(_("Remove the imported voice {name}?").format(name=voice.name),
                              _("Phonon voices"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION) != wx.YES:
                return
            try:
                _imports.remove_voice(root, voice.id)
            except (OSError, ValueError):
                _reportVoiceError(_("Could not completely remove the imported voice."))
            refresh()
            (voices if entries else add).SetFocus()

        voices.Bind(wx.EVT_LISTBOX, updateRemoval)
        add.Bind(wx.EVT_BUTTON, onAdd)
        edit.Bind(wx.EVT_BUTTON, onEdit)
        remove.Bind(wx.EVT_BUTTON, onRemove)
        close.Bind(wx.EVT_BUTTON, lambda event: dialog.EndModal(wx.ID_CLOSE))
        refresh()
        (voices if entries else add).SetFocus()
        dialog.ShowModal()
    finally:
        dialog.Destroy()


class PhononSettingsPanel(gui.settingsDialogs.SettingsPanel):
    title = _("Phonon")

    def makeSettings(self, settingsSizer):
        helper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
        self.gpu = helper.addItem(wx.CheckBox(self, label=_("Use &GPU acceleration when available")))
        self.gpu.SetValue(_settings.use_gpu())
        self._adapterScanStarted = False
        self._adapterDestroyed = False
        self._adapterIds = [_settings.gpu_adapter()]
        self.adapter = helper.addLabeledControl(_("Graphics &adapter:"), wx.Choice, choices=[])
        self.adapter.SetName(_("Graphics adapter"))
        self.applyGraphicsAdapters([])
        self.gpu.Bind(wx.EVT_CHECKBOX, self.onGraphicsAcceleration)
        # Windows skips read-only single-line text controls during Tab navigation.
        self.backend = helper.addLabeledControl(_("&Status:"), wx.TextCtrl,
                                               style=wx.TE_READONLY | wx.TE_MULTILINE)
        self.backend.SetName(_("Status"))
        self.backend.SetValue("")
        files = stock_voice_files()
        self._stockCount = sum(normalise(name) in files for names in GROUPS.values() for name in names)
        self.countImported()
        self.refreshBackend()
        self._backendTimer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.refreshBackend, self._backendTimer)
        self.Bind(wx.EVT_WINDOW_DESTROY, self.onBackendDestroy)
        self.imports = helper.addItem(wx.Button(self, label=_("&Manage imported voices...")))
        self.imports.Bind(wx.EVT_BUTTON, self.onManage)
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
        index = self.adapter.GetSelection()
        if 0 <= index < len(self._adapterIds):
            _settings.save_gpu_adapter(self._adapterIds[index])
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
            gpu_name = getattr(synth, "backendGpuName", "")
            if gpu_name and getattr(synth, "backendStatus", "") == "gpu":
                text = _("GPU (Vulkan): {name}.").format(name=gpu_name)
        text = _("Built-in voices: {builtIn}. Imported voices: {imported}.").format(
            builtIn=self._stockCount, imported=self._importCount
        ) + "\n" + text
        if self.backend.GetValue() != text:
            self.backend.SetValue(text)

    def countImported(self):
        try:
            self._importCount = len(_imports.list_voices(_imports.profile_root()))
        except OSError:
            self._importCount = _("Unavailable")

    def onManage(self, event):
        _manageVoices(self)
        self.countImported()
        self.refreshBackend()

    def onPanelActivated(self):
        super().onPanelActivated()
        self.countImported()
        self.refreshBackend()
        self._backendTimer.Start(1000)
        self.onGraphicsAcceleration(None)

    def onGraphicsAcceleration(self, event):
        enabled = self.gpu.GetValue()
        self.adapter.Enable(enabled and len(self._adapterIds) > 1)
        if not enabled or self._adapterScanStarted:
            return
        self._adapterScanStarted = True

        def scan():
            try:
                adapters = _runtime.gpu_adapters()
            except (OSError, _runtime.NativeError) as error:
                log.info("Phonon could not list graphics adapters: %s", error)
                adapters = []
            wx.CallAfter(self.applyGraphicsAdapters, adapters)

        threading.Thread(target=scan, daemon=True, name="Phonon graphics adapters").start()

    def applyGraphicsAdapters(self, adapters):
        if self._adapterDestroyed:
            return
        index = self.adapter.GetSelection()
        selected = self._adapterIds[index] if 0 <= index < len(self._adapterIds) else self._adapterIds[0]
        choices = [("", _("Automatic")), *adapters]
        if selected and selected not in {adapter_id for adapter_id, _ in choices}:
            choices.append((selected, _("Previously selected GPU (unavailable)")))
        self._adapterIds = [adapter_id for adapter_id, _ in choices]
        self.adapter.SetItems([name for _, name in choices])
        self.adapter.SetSelection(self._adapterIds.index(selected))
        self.adapter.Enable(self.gpu.GetValue() and len(choices) > 1)

    def onPanelDeactivated(self):
        self._backendTimer.Stop()
        super().onPanelDeactivated()

    def onBackendDestroy(self, event):
        if event.GetEventObject() is self:
            self._adapterDestroyed = True
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
