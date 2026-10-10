"""BankGPT: answers member questions from dated website snapshots.

build() returns an object with ingest(snapshot), answer(question, as_of=None) and conflicts(),
as defined in harness/contract.py. See NOTES.md for how it decides what is current.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_h = next((d / "harness" for d in HERE.parents if (d / "harness" / "contract.py").exists()), None)
if _h is not None:
    sys.path.insert(0, str(_h))

from contract import Answer, Conflict, Snapshot, Source  # noqa: E402

from bankgpt.conflicts import find_conflicts  # noqa: E402
from bankgpt.answer import Answerer
from bankgpt.engine import Store  # noqa: E402
from bankgpt import llm  # noqa: E402
from bankgpt.text import tokens  # noqa: E402


class BankGPT:
    def __init__(self):
        self.store = Store()
        self.answerer = Answerer(self.store)
        self._conflicts = None

    def ingest(self, snapshot: Snapshot) -> None:
        self.store.add_snapshot(snapshot)
        self.store.build()          # untimed: keep answer() fast
        self.answerer.cache.clear()
        self._conflicts = None

    def _ready(self):
        if not self.store._built:
            self.store.build()

    def answer(self, question: str, as_of=None) -> Answer:
        self._ready()
        try:
            text, units, confident, note = self.answerer.answer(question, as_of)
        except Exception as e:  # never crash the harness; an honest refusal beats a stack trace
            return Answer(text="I don't know. Please contact Dupaco directly.", confident=False,
                          conflict_note=f"internal error: {type(e).__name__}")
        view = self.store.view_for(self.answerer.today if as_of or self.answerer.today != self.store.snaps[-1][1] else None)
        sid = self.store.snaps[view][0]
        if not confident and llm.enabled():
            got = self._ask_claude(question, view)
            if got:
                text, url = got
                return Answer(text=text, sources=[Source(url=url, snapshot_id=sid)], confident=True,
                              conflict_note="answered by the optional Claude fallback from retrieved passages")
        srcs = [Source(url=u.url, snapshot_id=sid, quote=u.text[:200]) for u in units]
        return Answer(text=text, sources=srcs, confident=confident, conflict_note=note)

    def _ask_claude(self, question, view):
        q = {t: 1.0 for t in tokens(question)}
        hits = self.store.search(q, view, limit=12)
        passages = [(self.store.units[uid].url,
                     f"{self.store.units[uid].context}: {self.store.units[uid].text}"[:1200]) for _, uid in hits]
        return llm.ask(question, passages)

    def conflicts(self) -> list[Conflict]:
        self._ready()
        if self._conflicts is None:
            self._conflicts = find_conflicts(self.store, self.answerer)
        return [Conflict(topic=c["topic"], values=c["values"],
                         sources=[Source(url=u, snapshot_id=s) for u, s in c["sources"]],
                         resolution=c["resolution"], reason=c["reason"]) for c in self._conflicts]


def build() -> BankGPT:
    return BankGPT()
