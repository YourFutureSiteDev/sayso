"""Everything dictated, kept on this machine.

Stored as JSON Lines next to the settings so it survives a restart and a
rebuild. Nothing is uploaded: there is nowhere for it to go, the whole app is
offline.

You can clear it, and `keep_days` trims old entries on load, so it never
grows without limit.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import DATA_DIR

log = logging.getLogger(__name__)

HISTORY_PATH = DATA_DIR / "history.jsonl"
MAX_ENTRIES = 2000


@dataclass
class Entry:
    when: str                 # ISO 8601, local time
    text: str
    audio_seconds: float
    took_seconds: float
    words: int

    @property
    def stamp(self) -> datetime:
        return datetime.fromisoformat(self.when)


class History:
    def __init__(self, path: Path = HISTORY_PATH, keep_days: int = 0) -> None:
        self.path = path
        self.keep_days = keep_days       # 0 means forever
        self.entries: list[Entry] = []
        self.load()

    def load(self) -> None:
        self.entries = []
        if not self.path.is_file():
            return
        cutoff = (datetime.now() - timedelta(days=self.keep_days)
                  if self.keep_days else None)
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = Entry(**json.loads(line))
                if cutoff and entry.stamp < cutoff:
                    continue
                self.entries.append(entry)
            except (ValueError, TypeError):
                continue          # a torn last line should not lose the rest
        del self.entries[:-MAX_ENTRIES]

    def add(self, text: str, audio_seconds: float, took_seconds: float) -> Entry:
        entry = Entry(when=datetime.now().isoformat(timespec="seconds"),
                      text=text, audio_seconds=round(audio_seconds, 2),
                      took_seconds=round(took_seconds, 2),
                      words=len(text.split()))
        self.entries.append(entry)
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(entry)) + "\n")
        except OSError:
            log.exception("could not write history")
        return entry

    def clear(self) -> None:
        self.entries = []
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            log.exception("could not clear history")

    # -- totals, for the numbers on the home screen ------------------------

    @property
    def total_words(self) -> int:
        return sum(e.words for e in self.entries)

    @property
    def total_seconds(self) -> float:
        return sum(e.audio_seconds for e in self.entries)

    @property
    def words_per_minute(self) -> float:
        minutes = self.total_seconds / 60
        return self.total_words / minutes if minutes else 0.0

    @property
    def minutes_saved(self) -> float:
        """Against typing at 40 words a minute, a fair desk-typing pace."""
        typed = self.total_words / 40
        return max(0.0, typed - self.total_seconds / 60)

    def today(self) -> list[Entry]:
        day = datetime.now().date()
        return [e for e in self.entries if e.stamp.date() == day]
