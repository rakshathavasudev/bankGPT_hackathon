"""Question analysis, ranking with qualifier rules, honest refusal and answer composition."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from .engine import Store
from .pages import PageInfo, Unit
from .text import (MONTH_YEAR, MONTHS, SYNONYMS, clean, mask_numbers, money_ranges, money_values,
                   terms, tokens, year_ranges)

RATE_WORDS = {"rate", "apr", "apy", "yield", "interest", "dividend", "percent"}
URL_WORDS = {"link", "url", "website", "webpage", "web", "page", "site"}
GENERIC_WORDS = RATE_WORDS | {
    "interest", "between", "balance", "year", "month", "term", "tell", "repeat", "again", "provide", "show",
    "find", "need", "help", "information", "info", "much", "many", "number", "kind", "type", "option",
    "available", "what", "current", "currently", "today", "now", "right", "offer", "charge", "pay", "earn",
    "account", "loan", "savings", "saving", "dupaco", "credit", "union", "your", "rates", "fee", "cost",
    "price", "get", "have", "has", "do", "does", "is", "annual", "percentage", "standard", "typical",
    "general", "member", "members", "same", "keep", "all", "different", "vary", "still", "highest",
    "lowest", "best", "top", "maximum", "minimum", "max", "min", "going", "new", "become", "use", "used",
    "should", "link", "url", "website", "page", "site", "online", "web", "anything", "something", "there",
    "currently", "latest", "recent", "right", "now", "were", "was", "last", "ago", "options", "flexibility",
    "flexible", "looking", "want", "would", "like", "know", "explain", "describe", "details", "detail",
    "address", "street", "location", "located", "where", "hours", "hour", "phone", "telephone", "routing",
    "share", "regular", "fixed", "plain", "basic", "normal", "usual", "regularly"} | set(MONTHS)
GENERIC = set()
for _w in GENERIC_WORDS:
    GENERIC.update(tokens(_w, keep_stop=True))
NOT_NAMES = {"dupaco", "i", "what", "how", "can", "do", "does", "is", "are", "who", "when", "where", "which",
             "why", "the", "a", "my", "visa", "apr", "apy", "ira", "cd", "cds", "atm", "pm", "am", "usa", "us"}
BIO = re.compile(r"\b(?:joined|came to|worked at|working for) (?:the )?(?:Dupaco|prominent|firm)|"
                 r"\b(?:recently )?retired as\b|\bjoined the Dupaco board\b", re.I)
REFUSAL = ("I don't know. That isn't listed on Dupaco's website, so I can't confirm it. "
           "Please contact Dupaco directly.")
RATE_NEAR = re.compile(
    r"\d\s?%\s*(?:\(|fixed\s|variable\s|intro(?:ductory)?\s|annual\s)?(?:apr|apy|annual percentage|interest rate|rate|dividend|yield)|"
    r"(?:apr|apy|rate|dividend|yield)s?\)?\s*(?:of|is|at|:|=|as low as|from|starting at|will be|was|to)\s*(?:just\s|only\s)?\d+(?:\.\d+)?\s?%", re.I)


def infer_as_of(question: str, as_of: str | None, latest: date) -> date | None:
    if as_of:
        try:
            return date.fromisoformat(as_of[:10])
        except ValueError:
            return None
    m = MONTH_YEAR.search(question)
    if m and re.search(r"\b(as of|in|during|back in|was|were|had|on)\b", question, re.I):
        y, mo = int(m.group(2)), MONTHS[m.group(1).lower().rstrip(".")]
        d = date(y + (mo == 12), mo % 12 + 1, 1)
        d = date.fromordinal(d.toordinal() - 1)
        return None if d >= latest else d
    return None


class Answerer:
    def __init__(self, store: Store):
        self.s = store
        self.cache: dict[tuple, tuple] = {}
        self.today = date.today()

    # ------------------------------------------------------------- query analysis
    def analyse(self, q: str) -> dict:
        ql = clean(q).lower()
        toks = tokens(q)
        qt: dict[str, float] = {}
        for t in toks:
            qt[t] = qt.get(t, 0) + 1.0
            for syn in SYNONYMS.get(t, []):
                for st in tokens(syn):
                    qt.setdefault(st, 0.5)
        words = re.findall(r"[A-Za-z][A-Za-z$'’-]*", clean(q))
        names = set()
        for i, w in enumerate(words):
            wl = w.lower().strip("'’")
            if i > 0 and w[0].isupper() and wl not in NOT_NAMES and wl not in MONTHS and not w.isupper():
                names.update(t for t in tokens(w) if t not in GENERIC)
        url_intent = bool(URL_WORDS & set(re.findall(r"[a-z]+", ql))) or \
            bool(re.search(r"\bwhere (?:can|do|should) i (?:see|find|view|check|look up)\b[^?]*\b(?:online|rates?)\b", ql)) or \
            bool(re.search(r"\b(join|apply|open|sign up|enroll)\b[^?]*\bonline\b", ql))
        return {
            "q": q, "ql": ql, "toks": toks, "qt": qt, "names": names,
            "terms": terms(q),
            "years": [int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", q)
                      if re.search(r"model|vehicle|car\b|auto|truck|motorcycle", ql)],
            "money": money_values(q),
            "rate": bool(RATE_WORDS & set(tokens(q, keep_stop=True))) or bool(
                re.search(r"\b(pay|pays|paying|earn|earns|return)\b", ql) and re.search(
                    r"\b(savings?|certificates?|cds?|checking|money market|ira|account|club)\b", ql)
                and not re.search(r"\b(fee|charge|cost|bill|loan)\b", ql)),
            "intro": bool(re.search(r"\b(intro|introductory|promo|promotional|balance transfer|transfer)\b", ql)),
            "url": url_intent,
            "list": bool(re.search(r"\b(rates|aprs|apys|yields|all|list|which|cards|loans|accounts|certificates|cds|"
                                   r"do you (?:offer|have)|options|right now)\b", ql)),
            "super": ("max" if re.search(r"\b(highest|best|top|maximum|most)\b", ql) else
                      "min" if re.search(r"\b(lowest|minimum|cheapest|least)\b", ql) else ""),
            "business": "business" in ql, "jumbo": "jumbo" in ql,
            "service": (lambda m: m.group(1) if m else "")(re.search(
                r"\bhow (?:can|do|would|should) i (?:update|change|correct|edit)\b.*?\bmy ((?:contact|mailing|home|email|e-mail)"
                r"(?: information| address)?|address|phone number|phone|email address|name)\b", ql)),
            "join": bool(re.search(r"\beligib|\bwho can (?:join|become)|\bbecome a (?:dupaco )?member|\bqualify for membership", ql)),
            "ask_pct": re.findall(r"(\d+(?:\.\d+)?)\s?%", ql) if re.match(r"(?:do|does|is|are|can) ", ql) else [],
            "ira": bool(re.search(r"\b(ira|iras|roth|traditional|retirement)\b", ql)),
            "refi": bool(re.search(r"refinanc", ql)),
            "kind": ("routing" if "routing" in ql else
                     "phone" if re.search(r"\b(phone|telephone|call you|call dupaco|number to call)\b", ql) else
                     "hours" if re.search(r"\bhours?\b|\bopen (?:on|until|at)\b|\bwhat time\b", ql) else
                     "address" if re.search(r"\baddress\b|\bwhere is\b|\blocated\b", ql) and not url_intent else
                     "count" if re.search(r"\bhow many\b|^(?:what is the |what's the )?(?:total )?number of\b", ql) else
                     "date" if re.search(r"^when\b|\bwhat date\b|\bwhen (?:does|did|will|is|was)\b", ql) else ""),
        }

    def coverage(self, a: dict, uid: int) -> float:
        st = self.s
        hay = st.hay[uid]
        spec = [t for t in set(a["toks"]) if t not in GENERIC and not re.fullmatch(r"[\d.]+", t) and t in st.idf]
        if not spec:
            return 1.0

        def ok(t):
            return t in hay or any(x in hay for syn in SYNONYMS.get(t, []) for x in tokens(syn))
        tot = sum(st.idf[t] for t in spec)
        return sum(st.idf[t] for t in spec if ok(t)) / tot if tot else 1.0

    def _has_rarest(self, a: dict, uid: int) -> bool:
        """The question's most distinctive word (e.g. 'ticker', 'jumbo') must appear in the passage."""
        st = self.s
        spec = [t for t in set(a["toks"]) if t not in GENERIC and not re.fullmatch(r"[\d.]+", t) and t in st.idf]
        if not 2 <= len(spec) <= 3:      # long conversational questions carry incidental words
            return True
        if len(spec) >= 3:      # verbs say little about the topic
            spec = [t for t in spec if not re.search(r"(ed|ing)$", t)] or spec
        t = max(spec, key=lambda t: st.idf[t])
        hay = st.hay[uid]
        return t in hay or any(x in hay for syn in SYNONYMS.get(t, []) for x in tokens(syn))

    def unit_score(self, base: float, u: Unit, info: PageInfo, a: dict) -> float:
        s = base * info.authority
        text = u.text
        tl = text.lower()
        if a["terms"]:
            ut = terms(text if u.kind == "text" else " ".join(u.cells) + " " + u.context)
            if ut:
                s *= 1.6 if a["terms"] & ut else 0.15
            elif u.kind == "row":
                s *= 0.5
        if a["years"]:
            yr = year_ranges(" ".join(u.cells) if u.kind == "row" else text)
            single = [int(y) for y in re.findall(r"\b((?:19|20)\d{2}) model year", tl)]
            if yr or single:
                ok = any(lo <= y <= hi for y in a["years"] for lo, hi in yr) or any(y in single for y in a["years"])
                s *= 1.6 if ok else 0.2
        if a["money"] and u.kind == "row":
            rng = money_ranges(" ".join(u.cells[:3]))
            if rng:
                ok = any(lo - 0.5 <= m <= hi + 0.5 for m in a["money"] for lo, hi in rng)
                s *= 1.8 if ok else 0.25
        if a["rate"]:
            s *= (1.6 if u.kind == "row" else 1.2) if "%" in text else 0.45
        if not a["business"] and ("business" in u.url or "business" in u.context.lower()):
            s *= 0.55
        if a["business"] and "business" not in (u.url + u.context + text).lower():
            s *= 0.6
        if not a["jumbo"] and "jumbo" in tl:
            s *= 0.8
        if not a["ira"] and re.search(r"\b(ira|roth|traditional)\b", tl + " " + u.context.lower()):
            s *= 0.4
        if not a["refi"] and u.kind == "row" and "refinanc" in tl:
            s *= 0.7
        if re.search(r"\b(offer|promotion|special|promo) (?:has )?(?:ended|expired)|\bexpired\b", tl):
            s *= 0.2
        if info.published:
            age = (self.today - info.published).days / 365.25
            s *= max(0.55, 1.0 - 0.06 * max(0.0, age))
        if u.kind == "text" and len(text) > 1500:
            s *= 0.8
        cov = self.coverage(a, u.uid)
        s *= 0.3 + 0.7 * cov
        if a["names"] and not self._has_names(a, u.uid):
            s *= 0.1
        if a["join"]:
            if re.search(r"eligib|who can join", u.context.lower()):
                s *= 3.0
            elif "/join" in u.url:
                s *= 1.3
        if a["kind"] == "address" and re.search(r"\b(dupaco|branch)\b", tl):
            s *= 2.5
        return s

    def _has_names(self, a, uid):
        hay = self.s.hay[uid]
        return all(n in hay or any(x in hay for x in SYNONYMS.get(n, [])) for n in a["names"])

    # ------------------------------------------------------------- main entry
    def answer(self, question: str, as_of: str | None = None):
        st = self.s
        latest = st.snaps[-1][1]
        d = infer_as_of(question, as_of, latest)
        view = st.view_for(d)
        self.today = d or latest
        a = self.analyse(question)
        key = (" ".join(sorted(set(a["toks"]))), view)
        if key in self.cache:
            return self.cache[key]
        res = self._answer(a, view)
        if res[2]:
            res = (res[0], self._more_sources(res[0], res[1], view, a), res[2], res[3])
        self.cache[key] = res
        return res

    def _more_sources(self, text, units, view, a):
        """Cite the other official pages that carry the same fact (same table row, same routing number)."""
        st = self.s
        bit = 1 << view
        urls = [u.url.rstrip("/") for u in units]
        extra = []

        def add(uid):
            u = st.units[uid]
            if st.unit_views[uid] & bit and u.url.rstrip("/") not in urls and st.info(uid, view).kind == "official":
                urls.append(u.url.rstrip("/"))
                extra.append(u)
        for u in units:
            if u.kind == "row" and u.cells:
                for uid in st.rows_by_cells.get(tuple(u.cells), []):
                    add(uid)
        if a["kind"] in ("routing", "phone"):
            for val in re.findall(r"\b\d{9}\b|\b\d{3}-\d{3}-\d{4}\b", text):
                tok = (tokens(val) or [val])[0]
                for uid in list(st.post.get(tok, {}))[:400]:
                    add(uid)
        if a["kind"] == "hours" and not a["names"]:
            for uid, u in enumerate(st.units):
                if u.url.rstrip("/").endswith("dupaco.com/contact") and re.search(r"\d\s?a\.?m", u.text):
                    add(uid)
                    break
        return list(units) + extra[:6]

    def main_phone(self, view):
        """The number the site repeats most is the main line; returns (number, unit)."""
        st = self.s
        bit = 1 << view
        freq, where = {}, {}
        for uid, u in enumerate(st.units):
            if not st.unit_views[uid] & bit or u.kind != "text":
                continue
            for m in re.findall(r"\b\d{3}-\d{3}-\d{4}\b", u.text):
                freq[m] = freq.get(m, 0) + 1
                if m not in where or "contact" in u.url:
                    where[m] = u
        if not freq:
            return None, None
        num = max(freq, key=freq.get)
        return num, where[num]

    def ranked(self, a: dict, view: int):
        out = []
        for base, uid in self.s.search(a["qt"], view):
            u = self.s.units[uid]
            info = self.s.info(uid, view)
            out.append((self.unit_score(base, u, info, a), u, info))
        out.sort(key=lambda x: -x[0])
        return out

    def grouped(self, ranked):
        """Collapse copies of the same fact (same row label / same sentence with numbers masked)."""
        groups: dict[tuple, list] = defaultdict(list)
        for sc, u, info in ranked[:250]:
            if u.kind == "row":
                g = ("row", u.context.lower(), " | ".join("#" if re.search(r"\d\s?%", c) else c.lower() for c in u.cells))
            else:
                g = ("text", mask_numbers(u.text))
            groups[g].append((sc, u, info))
        out = []
        for items in groups.values():
            top = max(x[0] for x in items)
            rep = max(items, key=lambda x: (round(x[2].authority, 2), x[2].published or date.min,
                                            x[2].effective or date.min, x[0]))
            vals = {self._values(x[1]) for x in items} - {""}
            note = None
            if len(vals) > 1:
                note = (f"Sources disagree ({' vs '.join(sorted(vals))}); used {rep[1].url} "
                        f"(most authoritative / newest page).")
            urls = []
            for x in sorted(items, key=lambda x: -x[2].authority):
                if x[1].url not in urls:
                    urls.append(x[1].url)
            out.append({"score": top, "u": rep[1], "info": rep[2], "note": note, "urls": urls[:4]})
        out.sort(key=lambda g: -g["score"])
        return out

    def _fits(self, a: dict, g: dict) -> bool:
        u = g["u"]
        t = u.text
        if u.kind == "row":
            # A row for a different term / model year / balance is not an answer, it is a trap.
            cells = " ".join(u.cells)
            if a["terms"]:
                ut = terms(cells + " " + u.context)
                if ut and not a["terms"] & ut:
                    return False
            if a["years"]:
                yr = year_ranges(cells)
                if yr and not any(lo <= y <= hi for y in a["years"] for lo, hi in yr):
                    return False
            if a["money"]:
                rng = money_ranges(" ".join(u.cells[:3]))
                if rng and not any(lo - 0.5 <= m <= hi + 0.5 for m in a["money"] for lo, hi in rng):
                    return False
        if a["rate"] and a["terms"]:
            # "7-year certificate": a passage with no matching term is about some other product.
            ut = terms(" ".join(u.cells) + " " + u.context if u.kind == "row" else t)
            if not a["terms"] & ut:
                return False
        if a["rate"]:
            if u.kind == "row":
                if not any(re.search(r"\d\s?%", c) for c in u.cells):
                    return False
            else:
                ok = False
                for sent in re.split(r"(?<=[.!?;])\s+", t):
                    if RATE_NEAR.search(sent):
                        ok = True
                        break
                if not ok:
                    return False
        k = a["kind"]
        if k == "hours":
            return bool(re.search(r"\d\s?(a\.?m|p\.?m)|\bnoon\b", t, re.I))
        if k == "address":
            return bool(re.search(r"\b\d{2,6} [A-Z0-9][\w.]* ", t))
        if k == "count":
            return bool(re.search(r"\d", t))
        if k == "date":
            return bool(re.search(r"(19|20)\d{2}|\d{1,2}/\d{1,2}", t))
        if k == "phone":
            return bool(re.search(r"\(?\d{3}\)?[ -.]\d{3}-\d{4}", t))
        if k == "routing":
            return bool(re.search(r"\b\d{9}\b", t))
        return True

    def _answer(self, a: dict, view: int):
        st = self.s
        if a["service"]:
            res = self._service_answer(a, view)
            if res:
                return res
        if a["ask_pct"] and a["rate"]:
            # "Do any savings tiers pay 0.35%?": look the product up without the number (which would pull in
            # whatever other product happens to pay it), then say whether that rate is on its sheet.
            nums = set(a["ask_pct"])
            b = dict(a, ask_pct=[], toks=[t for t in a["toks"] if t not in nums],
                     qt={k: v for k, v in a["qt"].items() if k not in nums}, list=True)
            text, srcs, conf, note = self._answer(b, view)
            if not conf:
                return text, srcs, conf, note
            found = any(re.search(rf"(?<![\d.]){re.escape(n)}0*\s?%", text) for n in nums)
            lead = ("Yes. " if found else
                    f"No. None of the listed tiers pays {', '.join(n + '%' for n in sorted(nums))}. ")
            return lead + text, srcs, conf, note
        missing = [t for t in set(a["toks"]) if t not in GENERIC and not re.fullmatch(r"[\d.]+", t) and t not in st.post]
        missing_names = [n for n in a["names"] if n not in st.post and not any(x in st.post for x in SYNONYMS.get(n, []))]
        if missing_names or missing:
            return REFUSAL, [], False, None
        ranked = self.ranked(a, view)
        if not ranked:
            return REFUSAL, [], False, None
        groups = self.grouped(ranked)
        if a["kind"] == "count":
            res = self._count_answer(a, groups, view)
            if res:
                return res
        if a["url"]:
            return self._url_answer(a, groups, view)
        if a["kind"] == "phone":
            res = self._phone_answer(a, view)
            if res:
                return res
        top = groups[0]["score"]
        need = 0.6 if (a["rate"] or a["kind"]) else 0.3
        spec = [t for t in set(a["toks"]) if t not in GENERIC and not re.fullmatch(r"[\d.]+", t) and t in st.idf]
        if len(spec) == 2:
            need = 0.99        # short question: both of its topic words must be there
        yesno = bool(re.match(r"(does|do|is|are|can|will) ", a["ql"]))
        ok = []
        for g in groups[:40]:
            if g["score"] < 0.25 * top:
                break
            if a["names"] and not self._has_names(a, g["u"].uid):
                continue
            if not a["ql"].startswith("who") and BIO.search(g["u"].text):
                continue        # a person's career history is not a fact about Dupaco's products or places
            if yesno and re.search(r"[“\"].{20,}[”\"]|\bsays\b", g["u"].text):
                continue        # someone's quote in a news story doesn't say what Dupaco offers
            if self.coverage(a, g["u"].uid) >= need and self._has_rarest(a, g["u"].uid) and self._fits(a, g):
                ok.append(g)
        if not ok:
            return REFUSAL, [], False, None
        pick = ok[0]
        if a["rate"]:   # rate questions: a rate-table row beats a sentence that mentions a percentage
            rows = [g for g in ok if g["u"].kind == "row" and g["score"] >= 0.4 * pick["score"]]
            if rows and pick["u"].kind != "row":
                pick = rows[0]
        if pick["u"].kind == "text":
            near = [g for g in ok if g["u"].kind == "text" and g["score"] >= 0.75 * pick["score"]]
            # Several comparable news items: the newest one describes the current state.
            news = [g for g in near if g["info"].published]
            if len(news) >= 2 and all(g["info"].kind in ("news", "blog") for g in near):
                pick = max(news, key=lambda g: (g["info"].published, g["score"]))
            if pick["info"].kind in ("news", "blog") or re.match(r"^(what|who) (is|are)\b|^what's\b|^tell me about\b", a["ql"]):
                # A page's first strong mention (the lead paragraph) usually states the fact itself.
                same = [g for g in ok if g["u"].kind == "text" and g["u"].url == pick["u"].url
                        and g["score"] >= 0.5 * pick["score"]]
                pick = min(same, key=lambda g: g["u"].uid)
        groups.remove(pick)
        groups.insert(0, pick)
        if a["kind"] == "hours":
            return self._hours_answer(a, groups, ok)
        u0 = pick["u"]
        if u0.kind == "row":
            text, srcs = self._rows_answer(a, groups, view)
        else:
            text, srcs = self._text_answer(a, groups, view)
        return text, srcs, True, pick["note"]

    # ------------------------------------------------------------- composition
    @staticmethod
    def _values(u: Unit) -> str:
        if u.kind == "row":
            return ", ".join(c for c in u.cells if "%" in c)
        return ", ".join(re.findall(r"\d[\d,]*(?:\.\d+)?%?", u.text))[:80]

    def _segment_fits(self, a: dict, seg: str) -> bool:
        if a["terms"]:
            st = terms(seg)
            if st and not a["terms"] & st:
                return False
        if a["money"]:
            rng = money_ranges(seg)
            if rng and not any(lo - 0.5 <= m <= hi + 0.5 for m in a["money"] for lo, hi in rng):
                return False
        if a["years"]:
            yr = year_ranges(seg)
            if yr and not any(lo <= y <= hi for y in a["years"] for lo, hi in yr):
                return False
        return True

    def _sentences(self, a: dict, text: str, budget: int = 360) -> str:
        qualified = a["terms"] or a["money"] or a["years"]
        if len(text) <= budget and not (qualified and "\n" in text):
            return text.replace("\n", " ")
        sents = [x for line in text.split("\n") for x in re.split(r"(?<=[.!?])\s+(?=[A-Z\"\u201c$0-9-])", line) if x.strip()]
        qset = set(a["qt"])
        scored = []
        for i, s in enumerate(sents):
            ts = set(tokens(s))
            sc = sum(self.s.idf.get(t, 0) * a["qt"][t] for t in ts & qset)
            if a["rate"] and RATE_NEAR.search(s):
                sc *= 1.5
            if qualified and not self._segment_fits(a, s):
                sc *= 0.05
            scored.append((sc, -i, i))
        scored.sort(reverse=True)
        i = scored[0][2]
        pick = [i]
        if not qualified and (len(sents[i]) < 230 or sents[i].rstrip().endswith(":")) and i + 1 < len(sents):
            pick.append(i + 1)
        return " ".join(sents[j] for j in sorted(set(pick)))

    def _text_answer(self, a, groups, view):
        g0 = groups[0]
        u0 = g0["u"]
        text = self._sentences(a, u0.text)
        if a["rate"] and not RATE_NEAR.search(text) and not re.search(r"\d\s?%", text):
            text = self._sentences(dict(a, terms=a["terms"]), u0.text, 0)
        srcs = [u0]
        # Short answers borrow the next strongest passage from the same page (e.g. a program's lead paragraph).
        if len(text) < 220:
            for g in groups[1:6]:
                u = g["u"]
                if u.kind == "text" and u.url == u0.url and g["score"] >= 0.5 * g0["score"]:
                    extra = self._sentences(a, u.text, 260)
                    if extra not in text and text not in extra:
                        text = text + " " + extra
                    break
        if re.search(r"\b(charter|service|field of membership) area\b", text, re.I):
            where = self._service_area(view)
            if where and where[0] not in text:
                text += " " + where[0]
                srcs.append(where[1])
        return text, srcs + [type(u0)(0, url, "text", "", "") for url in g0["urls"] if url != u0.url][:2]

    def _service_area(self, view):
        """The site's own one-line statement of where Dupaco operates ('You can find us in A, B and C.')."""
        st = self.s
        bit = 1 << view
        best = {}
        for uid in st.post.get("find", {}):
            u = st.units[uid]
            if not st.unit_views[uid] & bit:
                continue
            for m in re.finditer(r"[^.]*\bfind us in [A-Z][a-z]+(?:, [A-Z][a-z]+)* and [A-Z][a-z]+\.", u.text):
                sent = m.group(0).strip()
                best.setdefault(sent, [0, u])[0] += 1
        if not best:
            return None
        sent = max(best, key=lambda k: best[k][0])
        return sent, best[sent][1]

    def _table_rows(self, table: str, view: int) -> list[Unit]:
        bit = 1 << view
        return sorted((u for u in self.s.table_units.get(table, []) if self.s.unit_views[u.uid] & bit),
                      key=lambda u: u.row_idx)

    def _rows_answer(self, a, groups, view):
        g0 = groups[0]
        u0 = g0["u"]
        pinned = bool(a["terms"] or a["years"] or a["money"])
        if a["super"] and not pinned:
            rows = [u for u in self._table_rows(u0.table, view) if u.cells and u.cells[0] == u0.cells[0]] \
                or self._table_rows(u0.table, view)

            def val(u):
                nums = [float(x) for c in u.cells for x in re.findall(r"(\d+(?:\.\d+)?)\s?%", c)]
                return max(nums) if nums else -1
            same_prod = [u for u in self._table_rows(u0.table, view)]
            label = u0.cells[0].lower() if u0.cells else ""
            cands = [u for u in same_prod if u.cells and u.cells[0].lower() == label] or same_prod
            best = max(cands, key=val) if a["super"] == "max" else min(cands, key=lambda u: val(u) if val(u) >= 0 else 1e9)
            chosen = [best]
        elif a["list"] and not pinned:
            chosen = self._table_rows(u0.table, view)[:14]
        else:
            chosen = [g["u"] for g in groups if g["u"].kind == "row" and g["u"].table == u0.table
                      and g["score"] >= 0.8 * g0["score"]][:6] or [u0]
            chosen = sorted(chosen, key=lambda u: u.row_idx)
        if not a["intro"]:
            kept = [u for u in chosen if not re.search(r"\b(intro|introductory|promo)", " ".join(u.cells[:1]), re.I)]
            chosen = kept or chosen
        # Amount tiers read best smallest first ("$100,000 or less" before "$832,750 or less").
        if all(money_ranges(" ".join(u.cells[:2])) for u in chosen) and len(chosen) > 1:
            chosen = sorted(chosen, key=lambda u: (u.cells[0].split(" ")[0:2], money_ranges(" ".join(u.cells[:2]))[0][1]))
        lines = [self._fmt_row(u, a) for u in chosen]
        srcs = [u0] + [Unit(0, url, "row", "", "") for url in g0["urls"] if url != u0.url][:2]
        if a["list"] and not pinned and not a["super"]:
            seen = {u0.table}
            for g in groups[1:15]:
                u = g["u"]
                if u.kind == "row" and u.table not in seen and g["score"] >= 0.55 * g0["score"] \
                        and g["info"].authority >= g0["info"].authority:
                    seen.add(u.table)
                    cap = u.text.split(":", 1)[0]
                    lines += [cap + ": " + self._fmt_row(x, a) for x in self._table_rows(u.table, view)[:8]
                              if a["intro"] or not re.search(r"\b(intro|introductory|promo)", " ".join(x.cells[:1]), re.I)]
                    srcs.append(u)
                    if len(seen) >= 4:
                        break
        caption = u0.text.split(":", 1)[0]
        text = f"{caption}: " + "; ".join(lines)
        eff = g0["info"].effective
        if eff:
            text += f" (rates effective {eff.strftime('%m/%d/%Y')})"
        return text, srcs

    @staticmethod
    def _fmt_row(u: Unit, a: dict) -> str:
        parts = []
        for h, c in zip(u.header, u.cells):
            if not c or c in ("-", "—"):
                continue
            hl = h.lower()
            if re.search(r"min\.? to|minimum|payment|points", hl) and not re.search(
                    r"\b(min|minimum|deposit|open|payment|points)\b", a["ql"]):
                continue
            parts.append(f"{h} {c}" if h and h.lower() != c.lower() and not c.lower().startswith(hl[:8]) else c)
        return ", ".join(parts)

    def _hours_answer(self, a, groups, ok):
        def trim(t):
            m = re.search(r"(branch hours|hours (?:are|:)|lobby|drive[- ]up)", t, re.I)
            t = t[m.start():] if m and m.start() < len(t) - 40 else t
            return re.sub(r"\s*ABOUT DUPACO.*$", "", t).strip()
        out, srcs, sigs = [], [], set()
        for g in ok[:10]:
            sig = " ".join(re.findall(r"\d{1,2}(?::\d\d)?\s?[ap]\.?m|noon", g["u"].text.lower()))
            if sig in sigs:
                continue        # same schedule worded differently on another page
            sigs.add(sig)
            t = trim(g["u"].text.replace("\n", " ")[:600] if len(g["u"].text) <= 600 else self._sentences(a, g["u"].text, 420))
            if t not in out:
                out.append(t)
                srcs.append(g["u"])
            if len(out) == (1 if a["names"] else 3):
                break
        text = out[0] if len(out) == 1 else "Hours vary by branch. For example: " + " | Another branch: ".join(out)
        return text, srcs, True, None

    def _phone_answer(self, a, view):
        """General 'what's your number' questions: the number the site repeats most is the main line."""
        spec = {t for t in a["toks"] if t not in GENERIC} - {"phone", "number", "call", "contact", "telephone", "reach"}
        if spec:
            return None
        num, u = self.main_phone(view)
        if not num:
            return None
        return f"You can reach Dupaco at {num}.", [u], True, None

    def _service_answer(self, a, view):
        """'How do I update my address / phone / email': members do this in Shine or by calling."""
        num, u = self.main_phone(view)
        if not num:
            return None
        what = a["service"]
        return (f"You can update your {what} by logging in to Shine Online or Mobile Banking and using the "
                f"\"Contact Us\" tab, or by calling Dupaco at {num}.", [u], True, None)

    def _count_answer(self, a, groups, view):
        """'How many X': find '<number> ... X' statements about Dupaco itself; trust the newest dated one."""
        st = self.s
        spec = [t for t in set(a["toks"]) if t not in GENERIC and not t.isdigit() and t in st.post]
        if not spec:
            return None
        bit = 1 << view
        noun = max(spec, key=lambda t: len(st.post[t]))     # the counted thing ("branch"), not a qualifier
        cands = []
        for uid in st.post[noun]:
            if not st.unit_views[uid] & bit or st.units[uid].kind != "text":
                continue
            u = st.units[uid]
            info = st.info(uid, view)
            for sent in re.split(r"(?<=[.!?])\s+", u.text):
                if not re.search(r"\b(dupaco|it has|we have|we now|our|the credit union)\b", sent, re.I):
                    continue
                for m in re.finditer(r"\b(\d[\d,]*)\s+((?:[\w-]+\s+){0,2}?)([\w-]+)", sent):
                    if noun not in set(tokens(m.group(2) + " " + m.group(3))):
                        continue
                    if not all(t in set(tokens(sent)) or t == noun for t in spec):
                        continue
                    when = info.published or info.effective or date.min
                    cands.append(((when, info.authority), sent.strip(), u, m.group(1)))
        if not cands:
            return None
        cands.sort(key=lambda c: c[0], reverse=True)
        return cands[0][1], [cands[0][2]], True, None

    def _rates_section(self, qset, view):
        st = self.s
        bit = 1 << view
        anchors = set()
        for uid in st.post.get("rate", {}):
            u = st.units[uid]
            if st.unit_views[uid] & bit:
                anchors.update(re.findall(r"\((https://www\.dupaco\.com/[^)\s#]+)/?#rates\)", u.raw))
        if not anchors:
            return None
        spec = {t for t in qset if t in st.idf}
        scored = []
        for base in anchors:
            toks = set()
            for tid, rows in st.table_units.items():
                if tid.rsplit("#", 1)[0].rstrip("/") == base.rstrip("/"):
                    for r in rows:
                        if st.unit_views[r.uid] & bit:
                            toks.update(st.hay[r.uid])
            ov = sum(st.idf[t] for t in spec & toks)
            if ov:
                scored.append((ov, -len(toks), base))
        if not scored:
            return None
        return max(scored)[2].rstrip("/") + "#rates"

    def _url_answer(self, a, groups, view):
        st = self.s
        qset = {t for t in a["qt"] if t not in GENERIC}
        cands: dict[str, float] = defaultdict(float)
        for rank, g in enumerate(groups[:20]):
            u = g["u"]
            cands[u.url] += g["score"] * g["info"].authority / (1 + rank)
            for anchor, link in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", u.raw):
                lt = set(tokens(anchor + " " + re.sub(r"[/_\-.#?=]", " ", link)))
                ov = sum(st.idf.get(t, 0) for t in lt & qset)
                if ov:
                    cands[link.split("?")[0]] += ov * g["score"] / 8
        if not cands:
            return REFUSAL, [], False, None
        if a["rate"] or "rate" in a["toks"]:
            # "Where do I see X rates?": the product page's own rates section (linked site-wide as page#rates)
            # beats the catch-all rate sheet.
            best = self._rates_section(qset, view)
            if best:
                cands[best] = max(cands.values()) * 1.5
        ranked = [u.rstrip("/") for u, _ in sorted(cands.items(), key=lambda x: -x[1])]
        picks = []
        for u in ranked:
            if u not in picks:
                picks.append(u)
            if len(picks) == 2:
                break
        text = f"You can use {picks[0]}" + (f" (see also {picks[1]})" if len(picks) > 1 else "")
        return text, [groups[0]["u"]], True, None
