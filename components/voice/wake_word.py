"""Offline wake-phrase matching and a small, locally generated prompt sound."""

import unicodedata

import numpy as np


DEFAULT_WAKE_PHRASE = "你好小助手"
CANCEL_PHRASES = frozenset(("取消", "不用了", "休眠"))


def normalize_phrase(text):
    return "".join(char for char in unicodedata.normalize("NFKC", text).casefold()
                   if char.isalnum())


def wake_remainder(text, phrase=DEFAULT_WAKE_PHRASE):
    """Return the question after an initial wake phrase, or None if unmatched.

    Preserve the question's punctuation/case. Only a phrase at the beginning
    qualifies, so an incidental mention later in a sentence does not wake it.
    No fuzzy matches: ASR substitutions should be visible during board testing.
    """
    needle = normalize_phrase(phrase)
    if not needle:
        raise ValueError("唤醒词必须包含文字或数字")
    characters = []
    positions = []
    for index, char in enumerate(text):
        for normalized in unicodedata.normalize("NFKC", char).casefold():
            if normalized.isalnum():
                characters.append(normalized)
                positions.append(index)
    if not "".join(characters).startswith(needle):
        return None
    end = positions[len(needle) - 1] + 1
    while end < len(text) and (text[end].isspace()
                               or unicodedata.category(text[end]).startswith("P")):
        end += 1
    return text[end:].strip()


def is_cancel(text):
    return normalize_phrase(text) in CANCEL_PHRASES


def prompt_samples(sample_rate=16000):
    """Two soft notes, with fades at every note edge and silence in between."""
    length = int(sample_rate * 0.12)
    fade = max(2, int(sample_rate * 0.008))
    phase = np.arange(length, dtype=np.float32) / sample_rate
    notes = []
    for frequency in (660, 880):
        note = (0.08 * np.sin(2 * np.pi * frequency * phase)).astype(np.float32)
        note[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
        note[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
        notes.append(note)
    return np.concatenate((notes[0], np.zeros(int(sample_rate * 0.05), dtype=np.float32), notes[1]))
