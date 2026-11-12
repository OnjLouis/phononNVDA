# SPDX-License-Identifier: GPL-2.0-or-later
"""Profile-local voice files; immutable IDs keep selections stable across upgrades."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
import stat
import tempfile
from uuid import uuid4

import globalVars

from ._voice_languages import GROUPS

MAX_BYTES = 4 * 1024 * 1024
MAX_VOICES = 64
MAX_NAME = 80
LOCALES = tuple(code for code in GROUPS if code != "en")
_ID = re.compile(r"imported-[0-9a-f]{32}\Z")
_TENSOR = "voice.safetensors"
_METADATA = "voice.json"


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    locale: str


def profile_root():
    profile = getattr(globalVars.appArgs, "configPath", None)
    return Path(profile) / "phononLocalVoices" if profile else None


def is_imported(voice_id):
    return _ID.fullmatch(voice_id) is not None


def _ordinary(path, check):
    try:
        info = path.lstat()
        return check(info.st_mode) and not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return False


def _name(value):
    if not isinstance(value, str):
        raise ValueError("Invalid voice name")
    value = value.strip()
    if not 1 <= len(value) <= MAX_NAME or not value.isprintable():
        raise ValueError("Invalid voice name")
    return value


def _read(path, limit):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError("Invalid voice file size")
    return data


def list_voices(root):
    if root is None or not _ordinary(root, stat.S_ISDIR):
        return ()
    result = []
    for entry in root.iterdir():
        if len(result) >= MAX_VOICES:
            break
        if not is_imported(entry.name) or not _ordinary(entry, stat.S_ISDIR):
            continue
        if not all(_ordinary(entry / file, stat.S_ISREG) for file in (_METADATA, _TENSOR)):
            continue
        try:
            metadata = json.loads(_read(entry / _METADATA, 4096))
            name = _name(metadata["name"])
            locale = metadata["locale"]
            if locale in LOCALES:
                result.append(Voice(entry.name, name, locale))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return tuple(sorted(result, key=lambda voice: (voice.name.casefold(), voice.id)))


def read_voice(root, voice_id):
    if root is None or not is_imported(voice_id) or not _ordinary(root, stat.S_ISDIR):
        raise ValueError("Invalid imported voice identifier")
    entry = root / voice_id
    if not _ordinary(entry, stat.S_ISDIR) or not _ordinary(entry / _TENSOR, stat.S_ISREG):
        raise ValueError("Imported voice file is missing")
    return _read(entry / _TENSOR, MAX_BYTES)


def add_voice(root, source, name, locale, validate):
    name = _name(name)
    if root is None or locale not in LOCALES or source.suffix.lower() != ".safetensors":
        raise ValueError("Invalid voice file or language")
    if len(list_voices(root)) >= MAX_VOICES:
        raise ValueError("Too many imported voices")
    data = _read(source, MAX_BYTES)
    validate(data)
    root.mkdir(parents=True, exist_ok=True)
    if not _ordinary(root, stat.S_ISDIR):
        raise ValueError("Invalid imported voices folder")
    voice = Voice("imported-" + uuid4().hex, name, locale)
    stage = Path(tempfile.mkdtemp(prefix=".import-", dir=root))
    try:
        (stage / _TENSOR).write_bytes(data)
        (stage / _METADATA).write_text(json.dumps({"name": name, "locale": locale}), encoding="utf-8")
        stage.rename(root / voice.id)
    finally:
        if stage.exists():
            for file in (_METADATA, _TENSOR):
                (stage / file).unlink(missing_ok=True)
            stage.rmdir()
    return voice


def remove_voice(root, voice_id):
    if root is None or not is_imported(voice_id) or not _ordinary(root, stat.S_ISDIR):
        raise ValueError("Invalid imported voice identifier")
    entry = root / voice_id
    if not _ordinary(entry, stat.S_ISDIR) or {p.name for p in entry.iterdir()} != {_METADATA, _TENSOR}:
        raise ValueError("Invalid imported voice folder")
    if not all(_ordinary(entry / file, stat.S_ISREG) for file in (_METADATA, _TENSOR)):
        raise ValueError("Invalid imported voice files")
    discarded = root / (".remove-" + uuid4().hex)
    entry.rename(discarded)
    for file in (_METADATA, _TENSOR):
        (discarded / file).unlink()
    discarded.rmdir()
