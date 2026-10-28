# SPDX-License-Identifier: GPL-2.0-or-later
"""Updater trust boundaries using an ephemeral, never-persisted test key."""
import base64
import builtins
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import test_driver as fixture


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        settings = patch.dict(fixture.config.conf, {"phononLocalUpdates": {"interval": "never"}})
        settings.start()
        self.addCleanup(settings.stop)

    @classmethod
    def setUpClass(cls):
        builtins._ = lambda text: text
        dependencies = {
            "addonHandler": types.SimpleNamespace(initTranslation=lambda: None),
            "gui": types.SimpleNamespace(ExecAndPump=lambda *a: None),
            "wx": types.SimpleNamespace(),
            "core": types.SimpleNamespace(callLater=lambda *a: None),
        }
        path = fixture.ADDON / "globalPlugins" / "phononLocalSettings" / "_updater.py"
        spec = importlib.util.spec_from_file_location("isolated_updater", path)
        cls.updater = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, dependencies):
            spec.loader.exec_module(cls.updater)
        cls.info = {"addon": "phononLocal", "version": "0.2.0", "size": 4,
                    "sha256": hashlib.sha256(b"test").hexdigest(),
                    "url": "https://github.com/OnjLouis/phononNVDA/releases/download/v0.2.0/phonon-0.2.0.nvda-addon"}
        cls.cases = [cls.info]
        for change in ({"addon": "samsungTVVoices"}, {"url": "https://example.invalid/package"},
                       {"version": "bad"}, {"size": 0}, {"size": True}, {"sha256": "x" * 64}):
            cls.cases.append(dict(cls.info, **change))
        payloads = [json.dumps(info, sort_keys=True, separators=(",", ":"), ensure_ascii=True) for info in cls.cases]
        script = """
$ErrorActionPreference='Stop'
$items = [Console]::In.ReadToEnd() | ConvertFrom-Json
$rsa = New-Object Security.Cryptography.RSACryptoServiceProvider 2048
$rsa.PersistKeyInCsp=$false
try {
  $signatures = @($items | ForEach-Object { [Convert]::ToBase64String($rsa.SignData([Text.Encoding]::UTF8.GetBytes($_),'SHA256')) })
  @{modulus=[Convert]::ToBase64String($rsa.ExportParameters($false).Modulus);signatures=$signatures} | ConvertTo-Json -Compress
} finally { $rsa.Dispose() }
"""
        result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                input=json.dumps(payloads), capture_output=True, text=True,
                                timeout=20, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        data = json.loads(result.stdout)
        cls.modulus = data["modulus"]
        cls.cases = [dict(info, signature=signature) for info, signature in zip(cls.cases, data["signatures"])]

    def test_valid_signature_and_tamper_rejection(self):
        with patch.object(self.updater, "PUBLIC_MODULUS", self.modulus):
            self.updater._verifyManifest(self.cases[0])
            for change in ({"version": "0.3.0"}, {"signature": "bad"}, {"signature": ""}):
                with self.subTest(change=change), self.assertRaises(RuntimeError):
                    self.updater._verifyManifest(dict(self.cases[0], **change))
        with self.assertRaises(RuntimeError):
            self.updater._verifyManifest(self.cases[0])

    def test_even_signed_wrong_identity_url_size_or_hash_is_rejected(self):
        with patch.object(self.updater, "PUBLIC_MODULUS", self.modulus):
            for info in self.cases[1:]:
                with self.subTest(info=info), self.assertRaises(RuntimeError):
                    self.updater._verifyManifest(info)

    def test_download_hash_and_size(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.nvda-addon"
            path.write_bytes(b"test")
            self.updater._verifyDownload(path, self.info)
            path.write_bytes(b"fail")
            with self.assertRaises(RuntimeError):
                self.updater._verifyDownload(path, self.info)
            path.write_bytes(b"extra")
            with self.assertRaises(RuntimeError):
                self.updater._verifyDownload(path, self.info)

    def test_staging_is_outside_addons_and_product_identity_is_distinct(self):
        fixture.config.conf["phononLocalUpdates"] = {"interval": "never"}
        with patch.object(self.updater.globalVars.appArgs, "configPath", "isolated-config", create=True):
            updater = self.updater.GitHubReleaseUpdater("phononLocal", "Phonon", "OnjLouis", "phononNVDA")
        self.assertEqual(Path(updater.updatesDir), Path("isolated-config") / "updates")
        self.assertEqual(Path(updater.stateFile).name, "phononLocalGithubUpdate.json")
        self.assertEqual(updater.apiUrl, "https://api.github.com/repos/OnjLouis/phononNVDA/releases/latest")

    def test_nvda_staging_identity_cleanup_and_restart_order(self):
        module = self.updater
        for valid in (True, False):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as folder:
                actions = []
                previous = types.SimpleNamespace(name="phononLocal", isPendingInstall=False, isPendingRemove=False,
                    requestRemove=lambda: actions.append("remove previous"))
                staged = types.SimpleNamespace(_cleanupAddonImports=lambda: actions.append("clean staged imports"))
                addon = types.SimpleNamespace(
                    AddonBundle=lambda path: types.SimpleNamespace(manifest={"name": "phononLocal" if valid else "other", "version": "0.2.0"}),
                    getAvailableAddons=lambda: [previous],
                    installAddonBundle=lambda bundle: actions.append("stage") or staged)
                progress = types.SimpleNamespace(Update=lambda value: (True, False), Destroy=lambda: actions.append("destroy progress"))
                wx = types.SimpleNamespace(ProgressDialog=lambda *a, **k: progress, PD_CAN_ABORT=1, PD_ELAPSED_TIME=2,
                    PD_REMAINING_TIME=4, PD_AUTO_HIDE=8, OK=1, ICON_ERROR=2, CallAfter=lambda fn, *args: fn(*args))
                gui = types.SimpleNamespace(mainFrame=types.SimpleNamespace(prePopup=lambda: None, postPopup=lambda: None),
                    messageBox=lambda *a: actions.append("error"))
                log = types.SimpleNamespace(error=lambda *a, **k: None)
                def download(url, destination, update, size):
                    self.assertEqual(Path(destination).parent, Path(folder) / "updates")
                    Path(destination).write_bytes(b"test")
                    return True
                def pump(function, *args):
                    return types.SimpleNamespace(funcRes=function(*args))
                with patch.object(module, "addonHandler", addon), patch.object(module, "wx", wx), \
                     patch.object(module, "gui", gui), patch.object(module, "log", log), \
                     patch.object(module, "ExecAndPump", pump), patch.object(module, "_downloadFile", download), \
                     patch.object(module, "promptUserForRestart", lambda: actions.append("restart prompt")), \
                     patch.object(module.globalVars.appArgs, "configPath", folder, create=True):
                    updater = module.GitHubReleaseUpdater("phononLocal", "Phonon", "OnjLouis", "phononNVDA")
                    updater._downloadAndInstall(self.info)
                if valid:
                    self.assertEqual(actions, ["stage", "remove previous", "clean staged imports", "restart prompt", "destroy progress"])
                else:
                    self.assertEqual(actions, ["error", "destroy progress"])
                self.assertEqual(list((Path(folder) / "updates").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
