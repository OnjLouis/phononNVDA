# license:BSD-3-Clause
# Copyright (c) 2026 Andre Louis. See UPDATER-LICENSE.txt.
"""Phonon signed GitHub updater."""

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
import tempfile
import urllib.error
import urllib.request

import addonHandler
import config
import globalVars
import gui
import wx
from core import callLater
from logHandler import log

addonHandler.initTranslation()

try:
	from gui.addonGui import promptUserForRestart
except ImportError:
	promptUserForRestart = None

try:
	from systemUtils import ExecAndPump
except ImportError:
	from gui import ExecAndPump


CHECK_INTERVAL_MS = 86400 * 1000
INTERVALS = {"never": 0, "hourly": 3600 * 1000, "daily": CHECK_INTERVAL_MS}
DOWNLOAD_BLOCK_SIZE = 8192
MAX_MANIFEST_SIZE = 128 * 1024
MAX_PACKAGE_SIZE = 256 * 1024 * 1024
PUBLIC_MODULUS = "wcBm9EMsIbzawOOLc0a9HXZKVuGymcWePJlV2g611J7QiAc5PuZCebgmttAg+Iq2PI/+BX1bBRmnFeG89OLOmPosKxsrjYqpO2PtNoSTsAo+TPwlaNXntrrWDrnJNYdlugeypanNfTuAgir9yEE3WtKyENl5xZPQvwYweP1utOaUes95C0bxYNijumc+4dsRfxXRqxF5+TJw7QhX4MRC3Yid4TISyIz+M9rfVxJiVVQvDdbanoJ1m3qr7SKzXpHuLe0ZsKOnJ0fSYbFU78d0VZv7t748RzGuCYlleqQ4VVxTrVtF/0pszkwWmLhANY4hDOs3laWXOr4PLBlG2TgeKqomDocW9ko62jYA6LrFUdjjnEmm1KJeJKFshSlwCzcwD7lDiy0jV75ncRyzfGOxTYZ+xwd6Siys8p1EsfWot2BgjJTdocOV219ucD6tqfyudZ9olioHrZHSd95XV6u1ZNBOF+57ko0p5nTRXGWswznSl5VTUP+jxadMWLObLhBt"
PUBLIC_EXPONENT = 65537
SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _verifyManifest(info):
	try:
		signature = base64.b64decode(info["signature"], validate=True)
		unsigned = {key: value for key, value in info.items() if key != "signature"}
		payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
	except (KeyError, ValueError, TypeError, AttributeError) as error:
		raise RuntimeError("The update manifest has no valid signature.") from error
	modulus = int.from_bytes(base64.b64decode(PUBLIC_MODULUS), "big")
	length = (modulus.bit_length() + 7) // 8
	value = int.from_bytes(signature, "big")
	if len(signature) != length or value >= modulus:
		raise RuntimeError("The update signature has an invalid size or value.")
	encoded = pow(value, PUBLIC_EXPONENT, modulus).to_bytes(length, "big")
	digest = SHA256_DIGEST_INFO + hashlib.sha256(payload).digest()
	expected = b"\x00\x01" + b"\xff" * (length - len(digest) - 3) + b"\x00" + digest
	if not hmac.compare_digest(encoded, expected):
		raise RuntimeError("The update signature is not valid.")
	version = info.get("version")
	if info.get("addon") != "phononLocal" or not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", version):
		raise RuntimeError("The signed update does not identify an Phonon version.")
	url = f"https://github.com/OnjLouis/phononNVDA/releases/download/v{version}/phonon-{version}.nvda-addon"
	if info.get("url") != url:
		raise RuntimeError("The signed update URL does not match its version.")
	if type(info.get("size")) is not int or not 0 < info["size"] <= MAX_PACKAGE_SIZE:
		raise RuntimeError("The signed update has an invalid package size.")
	if not isinstance(info.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", info["sha256"]):
		raise RuntimeError("The signed update has an invalid package hash.")


def _verifyDownload(path, info):
	if os.path.getsize(path) != info["size"]:
		raise RuntimeError("The downloaded update has the wrong size.")
	digest = hashlib.sha256()
	with open(path, "rb") as source:
		while block := source.read(DOWNLOAD_BLOCK_SIZE):
			digest.update(block)
	if not hmac.compare_digest(digest.hexdigest(), info["sha256"]):
		raise RuntimeError("The downloaded update does not match its signed hash.")


def _parseVersion(version):
	try:
		parts = tuple(int(part) for part in str(version).strip().lstrip("vV").split("."))
		return parts + (0,) * max(0, 3 - len(parts))
	except Exception:
		return (0,)


def _downloadFile(url, destination, update=None, maxSize=MAX_PACKAGE_SIZE):
	request = urllib.request.Request(url, headers={"User-Agent": "Phonon NVDA add-on updater"})
	with urllib.request.urlopen(request, timeout=120) as remote, open(destination, "wb") as local:
		size = int(remote.headers.get("content-length") or 0)
		read = 0
		while True:
			block = remote.read(DOWNLOAD_BLOCK_SIZE)
			if not block:
				break
			if read + len(block) > maxSize:
				raise RuntimeError("The downloaded update exceeds its signed size.")
			local.write(block)
			read += len(block)
			if update and update(min(100, int(read / size * 100)) if size else 0):
				return False
	return True


class GitHubReleaseUpdater:
	def __init__(self, addonName, addonLabel, owner, repo):
		self.addonName = addonName
		self.addonLabel = addonLabel
		self.apiUrl = "https://api.github.com/repos/%s/%s/releases/latest" % (owner, repo)
		self.updatesDir = os.path.join(globalVars.appArgs.configPath, "updates")
		self.stateFile = os.path.join(globalVars.appArgs.configPath, "phononLocalGithubUpdate.json")
		self.timer = None
		self.busy = False
		self.stopped = False
		self.interval = config.conf["phononLocalUpdates"]["interval"]
		self.state = self._loadState()

	def _loadState(self):
		try:
			with open(self.stateFile, "r", encoding="utf-8") as source:
				state = json.load(source)
			value = state.get("lastCheck", 0)
			if type(value) not in (int, float) or not 0 <= value <= time.time() * 1000:
				return {"lastCheck": 0}
			return {"lastCheck": value}
		except Exception:
			return {"lastCheck": 0}

	def _saveState(self):
		try:
			with open(self.stateFile, "w", encoding="utf-8") as destination:
				json.dump(self.state, destination)
		except Exception:
			log.debugWarning("Could not save updater state for %s", self.addonName, exc_info=True)

	def start(self):
		if getattr(config, "isAppX", False) or getattr(globalVars.appArgs, "secure", False):
			return
		self.stopped = False
		self._scheduleNext()

	def stop(self):
		self.stopped = True
		try:
			if self.timer and self.timer.IsRunning():
				self.timer.Stop()
		except Exception:
			pass
		self.timer = None

	def checkNow(self, fromGui=True):
		if self.stopped:
			return
		if self.busy:
			if fromGui:
				gui.messageBox(_("An update check is already in progress."), _("Phonon updates"), wx.OK, gui.mainFrame)
			return
		self.busy = True
		threading.Thread(
			target=self._checkWorker,
			args=(fromGui,),
			name="Phonon update check",
			daemon=True,
		).start()

	def _scheduleNext(self):
		if self.stopped or self.interval == "never":
			return
		try:
			if self.timer and self.timer.IsRunning():
				self.timer.Stop()
		except Exception:
			pass
		interval = INTERVALS.get(self.interval, CHECK_INTERVAL_MS)
		delay = int(interval - (time.time() * 1000 - self.state.get("lastCheck", 0)))
		self.timer = callLater(max(10000, delay), self.checkNow, False)

	def setInterval(self, interval):
		if interval not in INTERVALS:
			raise ValueError("Unknown update interval")
		if self.timer:
			self.timer.Stop()
			self.timer = None
		self.interval = interval
		self._scheduleNext()

	def _currentAddon(self):
		for addon in addonHandler.getAvailableAddons():
			if addon.name == self.addonName and not getattr(addon, "isPendingInstall", False):
				return addon
		return None

	def _getUpdateInfo(self):
		request = urllib.request.Request(self.apiUrl, headers={"User-Agent": "Phonon NVDA add-on updater"})
		try:
			with urllib.request.urlopen(request, timeout=20) as response:
				payload = response.read(MAX_MANIFEST_SIZE + 1)
		except urllib.error.HTTPError as error:
			if error.code == 404:
				return None
			raise
		if len(payload) > MAX_MANIFEST_SIZE:
			raise RuntimeError("The release metadata is too large.")
		data = json.loads(payload.decode("utf-8"))
		if data.get("draft") or data.get("prerelease"):
			raise RuntimeError("The release is not a stable public update.")
		asset = next(
			(item for item in data.get("assets", []) if item.get("name") == "phonon-update.json"),
			None,
		)
		if not asset:
			raise RuntimeError("The latest release has no signed Phonon update manifest.")
		version = str(data.get("tag_name", "")).removeprefix("v")
		manifestUrl = f"https://github.com/OnjLouis/phononNVDA/releases/download/v{version}/phonon-update.json"
		if asset.get("browser_download_url") != manifestUrl:
			raise RuntimeError("The update manifest is not from the expected Phonon release.")
		request = urllib.request.Request(manifestUrl, headers={"User-Agent": "Phonon NVDA add-on updater"})
		with urllib.request.urlopen(request, timeout=20) as response:
			payload = response.read(MAX_MANIFEST_SIZE + 1)
		if len(payload) > MAX_MANIFEST_SIZE:
			raise RuntimeError("The update manifest is too large.")
		info = json.loads(payload.decode("utf-8"))
		_verifyManifest(info)
		if info["version"] != version:
			raise RuntimeError("The signed update does not match its release tag.")
		return {
			"version": info["version"],
			"name": f"phonon-{version}.nvda-addon",
			"url": info["url"],
			"size": info["size"],
			"sha256": info["sha256"],
			"notes": info.get("changes") or _("No release notes are available."),
		}

	def _checkWorker(self, fromGui):
		try:
			info = self._getUpdateInfo()
			error = None
		except Exception as caught:
			info = None
			error = caught
		wx.CallAfter(self._handleCheck, fromGui, info, error)

	def _handleCheck(self, fromGui, info, error):
		self.busy = False
		if self.stopped:
			return
		current = self._currentAddon()
		if error or current is None:
			log.debugWarning("Could not check GitHub updates for %s: %s", self.addonName, error or "add-on not found")
			if fromGui:
				gui.messageBox(_("Unable to check for updates right now."), _("Update check failed"), wx.OK | wx.ICON_ERROR, gui.mainFrame)
			self.state["lastCheck"] = time.time() * 1000
			self._saveState()
			self._scheduleNext()
			return
		self.state["lastCheck"] = time.time() * 1000
		self._saveState()
		if info is None or _parseVersion(info["version"]) <= _parseVersion(current.version):
			if fromGui:
				message = (_("No public Phonon release is available yet.") if info is None else
				           _("There are no updates available for %s.") % self.addonLabel)
				gui.messageBox(message, _("No updates available"), wx.OK | wx.ICON_INFORMATION, gui.mainFrame)
			self._scheduleNext()
			return
		self.busy = True
		message = _(
			"A new version of {addon} is available: {version}.\n\n"
			"Do you want to download and install it now?\n\n{notes}"
		).format(addon=self.addonLabel, version=info["version"], notes=info["notes"])
		try:
			if gui.messageBox(message, _("Update available"), wx.YES | wx.NO | wx.ICON_INFORMATION, gui.mainFrame) == wx.YES:
				self._downloadAndInstall(info)
		finally:
			self.busy = False
		self._scheduleNext()

	def _downloadAndInstall(self, info):
		if self.stopped:
			return
		os.makedirs(self.updatesDir, exist_ok=True)
		with tempfile.NamedTemporaryFile(prefix="phonon-", suffix=".nvda-addon", dir=self.updatesDir, delete=False) as temporary:
			destination = temporary.name
		gui.mainFrame.prePopup()
		dialog = wx.ProgressDialog(
			_("Downloading Phonon update"),
			_("Downloading update"),
			style=wx.PD_CAN_ABORT | wx.PD_ELAPSED_TIME | wx.PD_REMAINING_TIME | wx.PD_AUTO_HIDE,
			parent=gui.mainFrame,
		)
		cancelled = threading.Event()
		progressActive = True
		try:
			def report(value):
				if progressActive and not dialog.Update(value)[0]:
					cancelled.set()
			def update(value):
				wx.CallAfter(report, value)
				return self.stopped or cancelled.is_set()
			result = ExecAndPump(_downloadFile, info["url"], destination, update, info["size"])
			if result.funcRes is False or self.stopped:
				return
			_verifyDownload(destination, info)
			bundle = addonHandler.AddonBundle(destination)
			if bundle.manifest.get("name") != self.addonName or str(bundle.manifest.get("version")) != info["version"]:
				raise RuntimeError("The downloaded add-on identity or version is incorrect.")
			previous = self._currentAddon()
			if any(addon.name == self.addonName and (getattr(addon, "isPendingInstall", False) or getattr(addon, "isPendingRemove", False))
			       for addon in addonHandler.getAvailableAddons()):
				raise RuntimeError("Restart NVDA to complete the pending Phonon add-on operation first.")
			installResult = ExecAndPump(addonHandler.installAddonBundle, bundle)
			installed = installResult.funcRes
			if getattr(bundle, "_installExceptions", None):
				raise RuntimeError("NVDA rejected the downloaded add-on.")
			if installed is None:
				raise RuntimeError("NVDA did not stage the downloaded add-on.")
			if previous:
				previous.requestRemove()
			if installed:
				installed._cleanupAddonImports()
			if promptUserForRestart:
				promptUserForRestart()
		except Exception:
			log.error("Could not install the Phonon update", exc_info=True)
			gui.messageBox(_("Failed to install the update."), _("Update failed"), wx.OK | wx.ICON_ERROR, gui.mainFrame)
		finally:
			progressActive = False
			dialog.Destroy()
			gui.mainFrame.postPopup()
			try:
				os.remove(destination)
			except OSError:
				pass
