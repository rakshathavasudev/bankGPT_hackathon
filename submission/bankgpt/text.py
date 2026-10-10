"""Text normalisation, tokenising and small parsers shared by the engine."""
from __future__ import annotations

import re
from datetime import date

MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9

WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "eighteen": 18,
           "twenty": 20, "thirty": 30, "forty": 40, "sixty": 60}

STOP = set("""a an the and or of to in on for at by with from is are was were be been being do does did
i me my we our us you your it its this that these those what which who whom whose when where why how
can could would should will shall may might must there their they them he she his her as if than then
so such not no yes any all some each tell please give get know like want need am im id about into don doesn didn isn aren won can
dupaco dupacos s just much many also more most per via over under above below less than after before
up out off very too only own other during while until because through again""".split())

# Query-side expansions: word -> extra words that carry the same meaning on the site.
SYNONYMS = {
    "cd": ["certificate"], "cds": ["certificate"], "certificates": ["certificate"],
    "mortgage": ["home", "conventional", "fixed"], "mortgages": ["mortgage", "home", "conventional", "fixed"],
    "interest": ["rate"], "apy": ["yield"], "dividend": ["rate"],
    "auto": ["vehicle", "car"], "car": ["auto", "vehicle"], "vehicle": ["auto"],
    "atm": ["atm"], "locator": ["location", "find"], "hours": ["hour"], "open": ["hour"],
    "join": ["membership", "member"], "member": ["membership"], "eligible": ["eligibility", "membership", "live", "work"],
    "heloc": ["home", "equity", "line"], "visa": ["card"], "cards": ["card"],
    "student": ["student", "college", "school"],
    "illinois": ["il"], "iowa": ["ia"], "wisconsin": ["wi", "wis"], "il": ["illinois"], "ia": ["iowa"],
    "wi": ["wisconsin"], "university": ["college", "student"],
    "wallet": ["pay", "apple", "google", "samsung"],
}

_DASH = re.compile(r"[‐-―−]")
_FOOT = re.compile(r"(\d%|APR|APY|\))\d\b")  # "5.39% APR1" -> drop footnote marker
TOKEN = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?|[a-z][a-z0-9'&$]*")


def clean(s: str) -> str:
    s = _DASH.sub("-", s).replace(" ", " ").replace("\\r\\n", " ").replace("\\n", " ")
    s = _FOOT.sub(r"\1", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def strip_md(s: str) -> str:
    """Markdown -> plain text: links keep their anchor text, images vanish."""
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"[*_`#>]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _stem(w: str) -> str:
    if len(w) > 5 and w.endswith(("ches", "shes", "sses", "xes", "zes")):
        return w[:-2]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss") and not w.endswith("us"):
        return w[:-1]
    return w


def tokens(s: str, keep_stop: bool = False) -> list[str]:
    s = clean(s).lower().replace("\u2019", "'")
    s = re.sub(r"'(s|m|re|ve|ll|d|t)\b", "", s)
    s = re.sub(r"\b([ap])\.\s?m\.?", r"\1m", s)
    s = re.sub(r"\b(\d{1,2})/(\d{1,2})\b(?!/)", r"\1_\2", s)   # "7/1 ARM" stays one token
    s = s.replace("-", " ").replace("/", " / ")
    out = []
    for t in re.findall(r"\b\d{1,2}_\d{1,2}\b", s):
        out.append(t.replace("_", "/"))
    s = re.sub(r"\b\d{1,2}_\d{1,2}\b", " ", s)
    for t in TOKEN.findall(s):
        t = t.strip("'")
        if not t:
            continue
        if t[0].isdigit() or t[0] == "$":
            t = t.lstrip("$").replace(",", "").rstrip("%")
            if "." in t:
                t = t.rstrip("0").rstrip(".") or "0"
            out.append(t)
            continue
        if t in WORDNUM:
            out.append(str(WORDNUM[t]))
            continue
        if not keep_stop and t in STOP:
            continue
        out.append(_stem(t))
    return out


# --- numbers and qualifiers -------------------------------------------------------------

PCT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
TERM = re.compile(r"\b(\d{1,3}|" + "|".join(WORDNUM) + r")[\s-]*(month|mo|year|yr)s?\b", re.I)
YEAR_RANGE = re.compile(r"\b((?:19|20)\d{2})\s?-\s?((?:19|20)?\d{2})\b")
DATE_LONG = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2}),?\s+((?:19|20)\d{2})\b", re.I)
DATE_NUM = re.compile(r"\b(\d{1,2})/(\d{1,2})/((?:19|20)\d{2})\b")
MONTH_YEAR = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?,?\s+((?:19|20)\d{2})\b", re.I)


def terms(s: str) -> set[tuple[int, str]]:
    """{(6, 'month'), (30, 'year')} from '6-month' / 'six month' / '30-Year'."""
    out = set()
    for n, unit in TERM.findall(s or ""):
        n = int(n) if n.isdigit() else WORDNUM[n.lower()]
        unit = "month" if unit.lower().startswith("mo") else "year"
        out.add((n, unit))
        # "5-year auto loan" == "60 Months"; "36 months" == "3-year".
        if unit == "year" and n <= 10:
            out.add((n * 12, "month"))
        if unit == "month" and n >= 12 and n % 12 == 0:
            out.add((n // 12, "year"))
    return out


def year_ranges(s: str) -> list[tuple[int, int]]:
    out = []
    for a, b in YEAR_RANGE.findall(s or ""):
        a = int(a)
        b = int(b) if len(b) == 4 else (a // 100) * 100 + int(b)
        out.append((min(a, b), max(a, b)))
    return out


def money_ranges(s: str) -> list[tuple[float, float]]:
    """'$10,000 - $24,999.99' -> (10000, 24999.99); '$25,000+' -> (25000, inf)."""
    s = clean(s)
    out = []
    for m in re.finditer(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(?:-|to)\s*\$?\s?(\d[\d,]*(?:\.\d+)?)", s):
        out.append((float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))))
    for m in re.finditer(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(?:\+|and (?:up|over|above)|or more)", s):
        out.append((float(m.group(1).replace(",", "")), float("inf")))
    for m in re.finditer(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*or less", s):
        out.append((0.0, float(m.group(1).replace(",", ""))))
    return out


def money_values(s: str) -> list[float]:
    return [float(x.replace(",", "")) for x in MONEY.findall(s or "")]


def parse_date(s: str) -> date | None:
    m = DATE_LONG.search(s or "")
    if m:
        try:
            return date(int(m.group(3)), MONTHS[m.group(1).lower().rstrip(".")], int(m.group(2)))
        except (ValueError, KeyError):
            pass
    m = DATE_NUM.search(s or "")
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    return None


def mask_numbers(s: str) -> str:
    """Template of a sentence with its numbers removed, used to spot 'same fact, different value'."""
    s = clean(s).lower()
    s = re.sub(r"\$?\d[\d,]*(?:\.\d+)?%?", "#", s)
    for w in ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"):
        s = re.sub(rf"\b{w}\b", "#", s)
    return re.sub(r"\W+", " ", s).strip()


# Synonym keys are matched against stemmed tokens ("illinois" -> "illinoi").
SYNONYMS = {(tokens(k) or [k])[0]: v for k, v in SYNONYMS.items()}
