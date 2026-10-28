# SPDX-License-Identifier: GPL-2.0-or-later
"""Prepare bounded requests without changing NVDA's command ordering."""
from speech.commands import BreakCommand, CharacterModeCommand, IndexCommand, PitchCommand, LangChangeCommand
from ._runtime import MAX_INPUT


def chunks(text, limit=MAX_INPUT):
    while len(text) > limit:
        cut = text.rfind(" ", 0, limit + 1)
        if cut < limit // 2:
            cut = limit
        yield text[:cut]
        text = text[cut:].lstrip()
    if text:
        yield text


def events(sequence, pitch):
    result, parts = [], []
    character_mode = False

    def flush():
        text = " ".join(parts).strip()
        result.extend(("text", part) for part in chunks(text))
        parts.clear()

    for item in sequence:
        if isinstance(item, str):
            if character_mode:
                parts.extend(c for c in item if not c.isspace())
            else:
                parts.append(item)
        elif isinstance(item, IndexCommand):
            flush()
            result.append(("index", item.index))
        elif isinstance(item, PitchCommand):
            flush()
            result.append(("pitch", item.newValue if not item.isDefault else pitch))
        elif isinstance(item, BreakCommand):
            flush()
            result.append(("break", max(0, min(10000, int(item.time)))))
        elif isinstance(item, CharacterModeCommand):
            flush()
            character_mode = item.state
        elif isinstance(item, LangChangeCommand):
            flush()
            result.append(("language", item.lang))
    flush()
    return result


