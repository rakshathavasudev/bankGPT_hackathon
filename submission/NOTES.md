# BankGPT: notes

## API keys

**None are needed.** The default mode is rules-only and uses just the Python standard library
(3.9+, tested on 3.11). It makes no network calls.

There is one optional extra, off by default. A Claude fallback can try to answer questions the
rules engine would otherwise answer with "I don't know". To turn it on:

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...      # your Anthropic API key
export BANKGPT_USE_CLAUDE=1              # explicit opt-in
# optional: BANKGPT_CLAUDE_MODEL (default claude-opus-5-5), BANKGPT_CLAUDE_TIMEOUT (seconds, default 20)
```

- Claude sees only passages retrieved from the snapshot that applies to the question. It never sees the live website.
- If Claude's answer contains any number that isn't in those passages, the answer is dropped and the engine refuses instead.
- Any error (SDK missing, timeout, API error) also falls back to the honest refusal.
- All scores below are for the default mode. The fallback was only checked against a mocked SDK, because no key was available while building.

## How to run

The website snapshots are not in this repo, because the data belongs to the credit union. First
copy the kit's `snapshots_zip/` folder (or the unpacked `snapshots/0*` folders) into the repo
root. Then run:

```bash
python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl
```

There is nothing to install for the default mode (`submission/requirements.txt` is empty apart
from the optional Anthropic SDK line). Ingesting all five snapshots takes about 30 seconds. After
that, answers take about 0.05 s each (p95 under 0.1 s).

My own question sets live in `submission/dev/`:

- `dev_eval.jsonl`: 40 questions
- `para_eval.jsonl`: 15 questions, each in 3 wordings
- `refuse_eval.jsonl`: 16 questions the site doesn't answer

You can run any of them by passing `--eval`.

| Set | Score (dev sets use an unweighted mean) |
|---|---|
| public_eval (40), official scorer | 0.946 final (accuracy 0.946, consistency 1.0, speed 1.0, p95 0.08 s) |
| dev_eval (40) | 0.975 |
| para_eval (45) | 1.0 |
| refuse_eval (16) | 0.875 |

## How it decides what is current

1. **Keep every version.** Each page is stored by content hash, along with the snapshots it appeared in. A question is answered from a *view*: the newest copy of every URL on or before the snapshot date. `as_of=None` means the newest snapshot. An `as_of` date, or a phrase like "as of June 2026" in the question, picks the matching older snapshot. A page that drops out of a later crawl is carried forward from its last copy.
2. **Break pages into facts.** Each table row becomes one unit, keeping its caption, section and heading, so "Share Savings: $0-$999.99: 0.05%" keeps its tier. Short lines such as hours and addresses are merged into text blocks. All units go into a BM25 index.
3. **Rank sources by authority.** Each URL is weighted by its page type:

   | Page type | Weight |
   |---|---|
   | Official pages (`/rates`, `/personal/...`, `/about`) | 1.0 |
   | PDFs | 0.85 |
   | Application sites | 0.8 |
   | Other | 0.75 |
   | Newsroom | 0.55 |
   | Blog/learn | 0.5 |
   | Campaign/landing pages | 0.45 |
   | Calculators | 0.35 |

   Calculators, old campaigns and press releases are where stale rates survive. Dated news also loses weight as it ages.
4. **Respect qualifiers.** Before an answer is accepted, these must match the question:
   - term, with years and months converted ("5-year" = "60 months");
   - vehicle model year, including ranges such as "2026-23";
   - balance tier;
   - business, IRA, jumbo and refinance variants;
   - intro vs ongoing rates.

   A row for a different term or tier is rejected as a trap, not offered as a near miss.
5. **Collapse duplicates.** When the same fact appears on several pages, the copy on the most authoritative and newest page wins. If the copies disagree, the answer carries a `conflict_note`.
6. **Say "I don't know" honestly.** The engine refuses, without stating any numbers, when any of these hold:
   - the question's key words don't appear in the passage (in a short question, all of its topic words must);
   - a word in the question appears nowhere on the site;
   - the passage isn't the right kind of fact, e.g. no time for an hours question or no % for a rate question.

   Quotes in news stories and staff career bios are never used to answer "does Dupaco offer/have..." questions.
7. **Special answer paths** handle:
   - branch hours, picking the right schedule;
   - counts, from the newest dated "Dupaco has N branches" statement;
   - phone and routing numbers;
   - "what link should I use" / "where do I see X rates" questions, preferring the product page's own rates section;
   - "how do I update my address/phone/email" questions (Shine or the main phone line);
   - "does any tier pay X%" questions, answered yes or no against the product's rate sheet;
   - membership eligibility, with the site's own statement of where Dupaco operates;
   - highest/lowest questions;
   - "list all the rates" questions.
8. **Stay consistent.** Answers are cached by the question's sorted word set plus the snapshot view, so a repeated or re-ordered question returns exactly the same text.

**Two public questions still score 0, and I left them on purpose:**

- *"Get me the rate for the certificate of accounts"* has no required values in the answer key, so the scorer can't award it points without its optional LLM judge. BankGPT reads back the full certificate rate sheet, which is what the expected answer describes.
- *"How many employees used Volunteer Time Off last year?"* expects 294, from an April 2024 article about 2023. Every snapshot also contains an April 2025 article saying 256 employees used it in 2024. BankGPT follows its rule that the newest dated statement wins. Special-casing this one question would break that rule everywhere else.

## conflicts()

Three kinds of contradiction are reported, each with a winner and a reason:

- **The same table row shows different rates on different pages of the newest snapshot.** Rows match when they have the same product, term, tier and rate column; model years are masked so that only the tier position is compared. The winner is the value on the most authoritative page, usually `/rates`.
- **The same row's rate changed between snapshots on official pages.** The newest snapshot wins.
- **The same "Dupaco has N ..." sentence shows different numbers on different pages.** Examples are counts of branches, offices, employees and counties, and "fifth time" style ordinals. The newest dated page wins.

Text "Label: $value" fee lines are compared the same way. Copies of one contradiction (e.g. the IRA and business versions of a certificate table) are reported once.

## Layout

```
submission/
  solution.py          build() -> BankGPT with ingest / answer / conflicts
  bankgpt/pages.py     page -> units (table rows, text blocks), page type and authority
  bankgpt/engine.py    versioned store, per-snapshot views, BM25 index
  bankgpt/answer.py    question analysis, ranking, qualifier checks, answer composition
  bankgpt/conflicts.py contradiction finder
  bankgpt/llm.py       optional Claude fallback (off by default)
  bankgpt/text.py      tokenising, term/date/money parsing
  dev/                 my own question sets
```
