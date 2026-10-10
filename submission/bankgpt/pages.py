"""Turn one page's markdown into retrievable units: table rows and text blocks."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

from .text import clean, parse_date, strip_md

JUNK_LINE = re.compile(
    r"^(\[!\[|#{1,6}\s*\[|!\[|\* \* \*|---|\[[^\]]*\]\([^)]*\)\s*(\[[^\]]*\]\([^)]*\)\s*)*$)")
FORM_WORDS = ("Please Select", "First Name*", "Last Name*", "Loading locations", "Continue Cancel",
              "Sorry, we couldn't find", "Select a Branch")


@dataclass
class Unit:
    uid: int
    url: str
    kind: str                 # "row" | "text"
    text: str                 # plain text shown to members
    context: str              # heading / table caption the unit sits under
    raw: str = ""             # original markdown (keeps links)
    header: list = field(default_factory=list)
    cells: list = field(default_factory=list)
    table: str = ""           # table id (url#n) for rows
    row_idx: int = 0
    tier_idx: int = 0         # position of this row's label among the table's distinct labels


@dataclass
class PageInfo:
    url: str
    title: str
    authority: float
    published: date | None
    effective: date | None
    kind: str


def page_kind(url: str) -> str:
    p = urlparse(url)
    host, path = p.netloc.lower(), p.path.lower().rstrip("/")
    if host.startswith("join.") or host.startswith("shine."):
        return "app"
    if re.match(r"^/(19|20)\d{2}/", path + "/") or path.startswith("/learn") or "/blog" in path:
        return "blog"
    if path.startswith("/newsroom"):
        return "news"
    if path.startswith("/calculators") or "calculator" in path:
        return "calculator"
    if path.endswith("-lp") or "-lp/" in path or "offer" in path or "switch-and-earn" in path \
            or "guidance" in path or "challenge" in path or "landing" in path:
        return "campaign"
    if path.startswith("/hubfs") or path.endswith(".pdf") or "-pdf" in path:
        return "pdf"
    if path in ("", "/rates", "/business-rates", "/contact", "/about", "/about/join") \
            or path.startswith(("/personal", "/business", "/about", "/checking", "/savings", "/connect",
                                "/disclosures", "/mobile", "/insurance", "/investments", "/rates")):
        return "official"
    return "other"


AUTHORITY = {"official": 1.0, "pdf": 0.85, "other": 0.75, "app": 0.8, "news": 0.55,
             "blog": 0.5, "campaign": 0.45, "calculator": 0.35}


def _split_row(line: str) -> list[str]:
    return [clean(strip_md(c)) for c in line.strip().strip("|").split("|")]


def parse_page(url: str, title: str, md: str, uid0: int) -> tuple[PageInfo, list[Unit]]:
    kind = page_kind(url)
    lines = md.split("\n")
    head = "\n".join(lines[:12])
    published = parse_date(head) if kind in ("news", "blog") else None
    if published is None:
        m = re.search(r"/((?:19|20)\d{2})/(\d{2})/(\d{2})/", url)
        if m:
            published = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    eff = [parse_date(m.group(0)) for m in re.finditer(
        r"effective (?:as of )?(?:\d{1,2}/\d{1,2}/\d{4}|[A-Z][a-z]+\.? \d{1,2},? \d{4})", md)]
    eff = [d for d in eff if d]
    info = PageInfo(url, title, AUTHORITY[kind], published, max(eff) if eff else None, kind)

    units: list[Unit] = []
    heading, caption = title, ""
    i, n_tables = 0, 0
    block: list[str] = []
    block_raw: list[str] = []
    lead = ""

    def flush():
        nonlocal block, block_raw, lead
        if block:
            txt = " ".join(block)
            if lead and lead not in txt:
                txt = lead + " " + txt
            units.append(Unit(uid0 + len(units), url, "text", txt, heading, " ".join(block_raw)))
        block, block_raw, lead = [], [], ""

    while i < len(lines):
        line = lines[i].rstrip()
        s = line.strip()
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            flush()
            header = _split_row(s)
            j = i + 2
            rows = []
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(_split_row(lines[j]))
                j += 1
            n_tables += 1
            tid = f"{url}#t{n_tables}"
            ctx = " - ".join(x for x in (heading, caption) if x)
            labels = []
            for r_i, cells in enumerate(rows):
                if not any(cells):
                    continue
                if cells[0] not in labels:
                    labels.append(cells[0])
                pairs = [f"{h}: {c}" if h and c and h != c else c for h, c in zip(header, cells) if c]
                txt = f"{caption or heading}: " + "; ".join(pairs)
                units.append(Unit(uid0 + len(units), url, "row", txt, ctx, header=header, cells=cells,
                                  table=tid, row_idx=r_i, tier_idx=labels.index(cells[0])))
            i = j
            continue
        if not s:
            # Blank line: keep short consecutive lines (hours, addresses) together.
            if block and (len(" ".join(block)) > 400 or len(block[-1]) > 90):
                flush()
            i += 1
            continue
        if JUNK_LINE.match(s) or any(w in s for w in FORM_WORDS) or len(s) > 2500:
            i += 1
            continue
        if s.startswith("#"):
            flush()
            heading = clean(strip_md(s))
            caption = ""
            i += 1
            continue
        plain = clean(strip_md(s))
        if not plain:
            i += 1
            continue
        short = len(plain) < 60 and not plain.endswith((".", ":", "!", "?")) and not re.search(r"\d", plain)
        nxt = next((l.strip() for l in lines[i + 1:i + 4] if l.strip()), "")
        if short and nxt.startswith("|"):
            flush()
            caption = plain          # table caption like "Share Savings"
            i += 1
            continue
        if block and len(plain) > 90:
            flush()
        if not block and plain.endswith(":") and len(plain) < 200:
            lead = plain
            block_raw.append(s)
            i += 1
            continue
        if short and not block:
            caption = plain
        block.append(plain)
        block_raw.append(s)
        i += 1
    flush()
    return info, units
