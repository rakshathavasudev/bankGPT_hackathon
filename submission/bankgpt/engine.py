"""Temporal page store, retrieval and answer composition (no network, no LLM)."""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from datetime import date

from .pages import PageInfo, Unit, parse_page
from .text import (MONTH_YEAR, MONTHS, SYNONYMS, clean, mask_numbers, money_ranges, money_values,
                   terms, tokens, year_ranges)

RATE_WORDS = {"rate", "apr", "apy", "yield", "interest", "dividend", "percent"}
URL_WORDS = {"link", "url", "website", "web", "page", "site", "address"}
GENERIC_WORDS = RATE_WORDS | {"interest", "between", "balance", "year", "month", "term", "tell", "repeat",
                               "again", "provide", "show", "find", "need", "help", "information", "info",
                               "much", "many", "number", "kind", "type", "option", "available","what", "current", "today", "now", "right", "offer", "charge", "pay", "earn", "much",
                        "account", "loan", "savings", "saving", "dupaco", "credit", "union", "your", "rates",
                        "fee", "cost", "price", "get", "have", "do", "does", "is", "the", "a", "on", "for",
                        "annual", "percentage", "standard", "typical", "general", "member", "members"}
GENERIC = set()
for _w in GENERIC_WORDS:
    GENERIC.update(tokens(_w, keep_stop=True))
REFUSAL = "I don't know. That isn't listed on Dupaco's website, so I can't confirm it. Please contact Dupaco directly."


class Store:
    def __init__(self):
        self.snaps: list[tuple[str, date]] = []
        self.pages: dict[tuple[str, str], PageInfo] = {}           # (url, hash) -> info
        self.page_units: dict[tuple[str, str], list[int]] = {}    # (url, hash) -> uids
        self.versions: dict[str, list[tuple[int, str]]] = defaultdict(list)  # url -> [(view, hash)]
        self.units: list[Unit] = []
        self.unit_key: dict[tuple, int] = {}
        self.unit_versions: dict[int, set] = defaultdict(set)
        self.titles: dict[str, str] = {}
        self._built = False

    # ---------------------------------------------------------------- ingest
    def add_snapshot(self, snapshot) -> None:
        v = len(self.snaps)
        self.snaps.append((snapshot.snapshot_id, date.fromisoformat(snapshot.taken_on[:10])))
        for p in snapshot.pages:
            h = hashlib.sha1(p.markdown.encode("utf-8", "ignore")).hexdigest()[:16]
            self.versions[p.url].append((v, h))
            self.titles[p.url] = p.title
            key = (p.url, h)
            if key in self.pages:
                continue
            info, units = parse_page(p.url, p.title or "", p.markdown, len(self.units))
            self.pages[key] = info
            uids = []
            for u in units:
                k = (u.url, u.kind, u.text)
                uid = self.unit_key.get(k)
                if uid is None:
                    uid = len(self.units)
                    u.uid = uid
                    self.units.append(u)
                    self.unit_key[k] = uid
                uids.append(uid)
                self.unit_versions[uid].add(key)
            self.page_units[key] = uids
        self._built = False

    def build(self) -> None:
        nv = len(self.snaps)
        # Active page version per view: the newest copy seen on or before that snapshot.
        self.active: list[dict[str, str]] = []
        for v in range(nv):
            act = {}
            for url, vers in self.versions.items():
                hs = [h for (vi, h) in vers if vi <= v]
                if hs:
                    act[url] = hs[-1]
            self.active.append(act)
        self.unit_views = [0] * len(self.units)
        for v, act in enumerate(self.active):
            for url, h in act.items():
                for uid in self.page_units[(url, h)]:
                    self.unit_views[uid] |= 1 << v
        # Inverted index (BM25) over unit text + its context and page title (lower weight).
        self.post: dict[str, dict[int, float]] = defaultdict(dict)
        self.dl = [0.0] * len(self.units)
        for u in self.units:
            tf = Counter()
            for t in tokens(u.text):
                tf[t] += 1.0
            for t in tokens(u.context + " " + self.titles.get(u.url, "")):
                tf[t] += 0.4
            for t, c in tf.items():
                self.post[t][u.uid] = c
            self.dl[u.uid] = sum(tf.values())
        self.hay = [set(tokens(u.text + " " + u.context + " " + self.titles.get(u.url, "") + " "
                              + re.sub(r"[/_\-.#?=]", " ", u.url))) for u in self.units]
        self.table_units = defaultdict(list)
        for u in self.units:
            if u.kind == "row":
                self.table_units[u.table].append(u)
        self.N = len(self.units)
        self.avgdl = sum(self.dl) / max(1, self.N)
        self.idf = {t: math.log(1 + (self.N - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.post.items()}
        self._built = True

    # ---------------------------------------------------------------- helpers
    def view_for(self, as_of: date | None) -> int:
        if as_of is None:
            return len(self.snaps) - 1
        best = 0
        for i, (_, d) in enumerate(self.snaps):
            if d <= as_of:
                best = i
        return best

    def info(self, uid: int, view: int) -> PageInfo:
        u = self.units[uid]
        h = self.active[view].get(u.url)
        return self.pages.get((u.url, h)) or next(iter(self.pages[k] for k in self.unit_versions[uid]))

    def search(self, qtoks: dict[str, float], view: int, limit: int = 400) -> list[tuple[float, int]]:
        bit = 1 << view
        scores: dict[int, float] = defaultdict(float)
        k1, b = 1.2, 0.6
        for t, w in qtoks.items():
            post = self.post.get(t)
            if not post:
                continue
            idf = self.idf[t]
            for uid, tf in post.items():
                if not self.unit_views[uid] & bit:
                    continue
                scores[uid] += w * idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * self.dl[uid] / self.avgdl))
        return sorted(((s, u) for u, s in scores.items()), reverse=True)[:limit]


