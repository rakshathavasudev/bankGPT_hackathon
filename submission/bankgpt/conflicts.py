"""Find places where the website contradicts itself, pick a winner and say why.

Three kinds of contradiction are reported:
  1. The same table row (same product, term and tier) shows different rates on different pages of the
     newest snapshot, e.g. a stale calculator page vs the official rates page.
  2. The same row's rate changed between snapshots (an older page or crawl still carries the old value).
  3. The same factual sentence about Dupaco carries different numbers on different pages
     (e.g. "22 branch office locations" vs "23 branch office locations" in press-release footers).
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from .text import YEAR_RANGE, clean, mask_numbers, terms, tokens

PCT = re.compile(r"\d+(?:\.\d+)?\s?%")
QUALIFIERS = re.compile(r"\b(over|nearly|more than|about|approximately|almost|around|roughly|some|just over|"
                        r"close to|upwards of)\b")
ORDINAL = re.compile(r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th))\b", re.I)


def _pct_cells(u):
    """Rate cells only (a 'Points: 0.00%' column is not a rate)."""
    return [(h, c) for h, c in zip(u.header, u.cells) if PCT.search(c) and "point" not in h.lower()]


def _row_key(u) -> tuple | None:
    vals = _pct_cells(u)
    if not vals:
        return None
    label = [c.lower() for c in u.cells if not PCT.search(c) and c and not re.fullmatch(r"\$?[\d,.]+", c)]
    has_yr = any(YEAR_RANGE.search(c) for c in label)
    label = [YEAR_RANGE.sub("<yr>", c) for c in label]
    label = [re.sub(r"[^a-z0-9<>$+ ]+", " ", c).replace(",", "") for c in label]
    label = [re.sub(r"\s+", " ", c).strip() for c in label]
    pct_headers = tuple(re.sub(r"\W+", "", h.lower())[:8] for h, c in _pct_cells(u))
    cap = u.text.split(":", 1)[0].lower()
    product = "auto" if re.search(r"auto|vehicle", cap + " " + u.url) else ""
    return (product, tuple(label), pct_headers, u.tier_idx if has_yr else -1)


def _row_value(u) -> str:
    return " / ".join(clean(c) for h, c in _pct_cells(u))


def _topic(u) -> str:
    cap = u.text.split(":", 1)[0]
    label = " ".join(c for c in u.cells if not PCT.search(c) and not re.fullmatch(r"\$?[\d,.]+", c))
    ctx = " ".join(w for w in u.context.split(" - ") if w.lower() not in cap.lower())
    t = f"{cap} {label} [{ctx}]".strip() if ctx else f"{cap} {label}".strip()
    for n, unit in sorted(terms(label + " " + cap)):
        t += f" ({n}-{unit})"
    return re.sub(r"\s+", " ", t)


def find_conflicts(store, answerer) -> list[dict]:
    nv = len(store.snaps)
    latest = nv - 1
    out: dict[tuple, dict] = {}

    def page(uid, v):
        return store.info(uid, v)

    # ---- 1 + 2: table rows, within the newest snapshot and across snapshots
    rows_by_key: dict[tuple, list] = defaultdict(list)       # key -> [(view, uid)]
    for v in range(nv):
        bit = 1 << v
        for table, units in store.table_units.items():
            for u in units:
                if not store.unit_views[u.uid] & bit:
                    continue
                k = _row_key(u)
                if k:
                    rows_by_key[k].append((v, u.uid))
    for k, occ in rows_by_key.items():
        now = [(uid) for v, uid in occ if v == latest]
        now_vals = defaultdict(list)
        for uid in now:
            now_vals[_row_value(store.units[uid])].append(uid)
        hist = defaultdict(set)      # value -> views seen on official pages
        for v, uid in occ:
            if page(uid, v).kind == "official":
                hist[_row_value(store.units[uid])].add(v)
        within = len(now_vals) > 1
        over_time = len(hist) > 1 and any(latest in vs for vs in hist.values())
        if not (within or over_time):
            continue
        # Winner: the value on the most authoritative page of the newest snapshot, then the most pages.
        if now_vals:
            win = max(now_vals, key=lambda val: (max(page(uid, latest).authority for uid in now_vals[val]),
                                                  len(now_vals[val])))
            win_uid = max(now_vals[win], key=lambda uid: (page(uid, latest).authority,
                                                           "business" not in store.units[uid].url,
                                                           store.units[uid].url.rstrip("/").endswith("/rates")))
        else:
            continue
        values = list(dict.fromkeys([win] + list(now_vals) + sorted(hist, key=lambda x: min(hist[x]))))
        losers_now = [val for val in now_vals if val != win]
        if within:
            bad = [store.units[now_vals[val][0]].url for val in losers_now]
            reason = (f"{store.units[win_uid].url} (official rate page) is authoritative; "
                      f"{', '.join(bad[:3])} still shows an outdated value")
        else:
            first = {val: min(vs) for val, vs in hist.items()}
            reason = (f"rate changed over time; newest snapshot ({store.snaps[latest][0]}) shows {win}, "
                      f"earlier snapshots showed {', '.join(x for x in values if x != win)}")
        srcs = []
        for val in values:
            uid = (now_vals.get(val) or [None])[0]
            if uid is not None:
                srcs.append((store.units[uid].url, store.snaps[latest][0]))
            else:
                v0, uid0 = next((v, uid) for v, uid in occ if _row_value(store.units[uid]) == val)
                srcs.append((store.units[uid0].url, store.snaps[v0][0]))
        out[("row",) + k] = {"topic": _topic(store.units[win_uid]), "values": values, "sources": srcs,
                             "resolution": win, "reason": reason}

    # ---- 2b: "Label: value" facts written as text (fee lists, simple rate lines)
    facts: dict[str, list] = defaultdict(list)          # label -> [(view, uid, value)]
    for v in range(nv):
        bit = 1 << v
        for uid, u in enumerate(store.units):
            if u.kind != "text" or not store.unit_views[uid] & bit:
                continue
            if page(uid, v).kind in ("news", "blog"):
                continue
            for line in u.text.split("\n"):
                m = re.match(r"^\s*([A-Za-z0-9][^:]{2,70}?):\s*(\$?\d[\d,]*(?:\.\d+)?\s?%?)\s*$", line.strip()) or \
                    re.match(r"^\s*([A-Za-z][A-Za-z0-9 ()/&'-]{2,70}?)\s+(\$\d[\d,]*(?:\.\d+)?|\d+(?:\.\d+)?\s?%)(?:\*?\s+per item)?\s*$", line.strip())
                if m and not re.fullmatch(r"(19|20)\d{2}", m.group(2).strip()):
                    facts[re.sub(r"\s+", " ", m.group(1).lower()).strip()].append((v, uid, m.group(2).replace(" ", "")))
    for label, occ in facts.items():
        now = defaultdict(list)
        for v, uid, val in occ:
            if v == latest:
                now[val].append(uid)
        allv = list(dict.fromkeys(val for _, _, val in sorted(occ)))
        if len(set(allv)) < 2 or not now:
            continue
        win = max(now, key=lambda val: (max(page(uid, latest).authority for uid in now[val]), len(now[val])))
        others = [x for x in allv if x != win]
        within = len(now) > 1
        out[("fact", label)] = {
            "topic": label, "values": [win] + others,
            "sources": [(store.units[next(uid for vv, uid, val in occ if val == x)].url,
                         store.snaps[next(vv for vv, uid, val in occ if val == x)][0]) for x in [win] + others],
            "resolution": win,
            "reason": ("most authoritative page in the newest snapshot" if within else
                       f"value changed over time; newest snapshot shows {win}"),
        }

    # ---- 3: factual sentences about Dupaco with different numbers on different pages (newest snapshot)
    bit = 1 << latest
    sent_groups: dict[str, list] = defaultdict(list)
    for uid, u in enumerate(store.units):
        if u.kind != "text" or not store.unit_views[uid] & bit:
            continue
        for sent in re.split(r"(?<=[.!?])\s+", u.text):
            if len(sent) > 260 or not re.search(r"\b(dupaco|it has|the credit union)\b", sent, re.I):
                continue
            if not re.search(r"\b(branch|branches|locations?|offices?|employees?|count(?:y|ies)|times?)\b", sent, re.I):
                continue
            nums = re.findall(r"\b\d[\d,]*(?:\.\d+)?%?|\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b", sent, re.I)
            nums = [n for n in nums if not n[0].isdigit() or len(re.sub(r"\D", "", n)) >= 2]
            if not nums or re.search(r"\b(19|20)\d{2}\b", sent):
                continue
            key = QUALIFIERS.sub(" ", mask_numbers(sent))
            key = re.sub(r"^(it|this|that|there) (was|is) ", "", key)
            key = re.sub(r"\s+", " ", key).strip()
            if len(key.split()) < 5:
                continue
            sent_groups[key].append((uid, sent.strip(), tuple(n.lower() for n in nums)))
    for key, items in sent_groups.items():
        by_val = defaultdict(list)
        for uid, sent, nums in items:
            by_val[nums].append((uid, sent))
        if len(by_val) < 2 or len({store.units[uid].url for uid, _, _ in items}) < 2:
            continue

        def recency(val):
            return max((page(uid, latest).published or page(uid, latest).effective or date.min,
                        page(uid, latest).authority) for uid, _ in by_val[val])
        win = max(by_val, key=recency)
        # Keep only the differing numbers so values read like "22" vs "23".
        diff_pos = [i for i in range(min(len(v) for v in by_val)) if len({v[i] for v in by_val}) > 1] or [0]
        def show(val):
            return " ".join(val[i] for i in diff_pos if i < len(val))
        values = list(dict.fromkeys([show(win)] + [show(v) for v in by_val if v != win]))
        if len(values) < 2:
            continue
        wsent = by_val[win][0][1]
        topic_words = [w for w in tokens(wsent) if not w[0].isdigit()][:8]
        newest = recency(win)[0]
        out[("sent", key)] = {
            "topic": " ".join(topic_words) + f" ({wsent[:120]})",
            "values": values,
            "sources": [(store.units[by_val[v][0][0]].url, store.snaps[latest][0]) for v in by_val],
            "resolution": show(win),
            "reason": (f"newest dated page ({newest.isoformat()}) says {show(win)}" if newest != date.min
                       else f"most authoritative page says {show(win)}"),
        }
    # The same contradiction often repeats on sibling products (IRA / business copies of a certificate
    # table); report it once, under its most general name.
    seen, result = {}, []
    for c in sorted(out.values(), key=lambda c: (bool(re.search(r"\b(ira|roth|traditional|business)\b", c["topic"], re.I)), len(c["topic"]))):
        sig = frozenset(c["values"])
        if sig in seen:
            continue
        seen[sig] = True
        result.append(c)
    return result
