# BankGPT: a credit-union assistant that knows what's true *now*

My entry for the Knowledge Hackathon. BankGPT answers member questions about Dupaco Credit Union, such as "What's the 12-month certificate rate?", "What are the drive-up hours?" or "Who can join?", using only five crawls of the credit union's website taken between May and September 2026.

## The problem I was solving

The website contradicts itself, and the contradictions get worse over time:

- A rate changes on the official rate sheet, but a calculator, PDF or old campaign page still shows the old number.
- Promotions end, but their pages stay up.
- Tables have tiers by term, balance, loan amount and vehicle model year. Read carelessly, every tier looks like a conflict.
- Press releases and blog posts describe offers and facts from years ago.

A plain search-and-answer bot repeats whichever stale number it finds first. My goal was a system that gives:

- the **current** value;
- the **right tier**;
- a **source**;
- an honest "I don't know" when the site doesn't say.

## How I approached it

I built a rules-based engine. It has no required LLM and makes no network calls, so it's fast, deterministic and free to run.

1. **Keep history, not just the latest copy.** Every page version is stored with the snapshots it appeared in. A question is answered from the site as it stood on a date: the newest crawl by default, or an older one for "as of" questions.
2. **Break pages into facts.** Each table row becomes a fact that keeps its caption and section, so "$0-$999.99: 0.05%" stays attached to "Share Savings". Text is split into short passages, and everything is indexed with BM25.
3. **Decide which source wins.** Official product pages and the rate sheet outrank:
   - PDFs;
   - application pages;
   - newsroom and blog posts;
   - campaign landing pages;
   - calculators, which are where stale rates hide most.

   Older dated news loses weight as it ages.
4. **Respect qualifiers.** These are treated as different facts, not conflicts:
   - term, so "5-year" matches "60 months";
   - model year;
   - balance tier;
   - business vs personal, IRA, jumbo and refinance;
   - promo vs ongoing.

   A row for the wrong term or tier is rejected outright instead of offered as a near miss.
5. **Be honest.** If the question's key words aren't in the best passage, or the passage isn't the right kind of fact, it says "I don't know" and quotes no numbers.
6. **List contradictions.** `conflicts()` reports, each with a winner and a reason:
   - rates that differ between pages;
   - rates that changed over time;
   - "Dupaco has N branches"-style facts that disagree across pages.

There is also an optional Claude fallback for questions the rules can't answer. It's off unless an API key is set. The full write-up, including every rule for deciding what is current, is in [`submission/NOTES.md`](submission/NOTES.md).

## Results

| What | Result |
|---|---|
| Public practice set (40 questions, official scorer) | 0.946 (every category 1.0 except two items, explained in NOTES.md) |
| My reworded-question set (45) | 1.0 |
| My extra question set (40) | 0.975 |
| My "site doesn't say" set (16) | 0.875 |
| Answer speed (p95) | about 0.06 s |
| Loading all five crawls | about 25 s |

## Run it yourself

You need Python 3.9+ (I tested on 3.11). Nothing needs installing, and no API keys are needed.

```bash
git clone https://github.com/rakshathavasudev/bankGPT_hackathon.git
cd bankGPT_hackathon
# Copy the hackathon kit's snapshots_zip/ folder in here. The crawl data isn't committed,
# because it is the credit union's website.
python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl
```

The first run unpacks the crawls into `snapshots/`. Per-question results go to `results/`.

Other checks:

```bash
# My own question sets
python harness/run_eval.py --submission submission --eval submission/dev/para_eval.jsonl
python harness/run_eval.py --submission submission --eval submission/dev/refuse_eval.jsonl

# conflicts() on the kit's small synthetic test site
python harness/run_eval.py --submission submission --snapshots harness/selftest/snapshots \
  --eval harness/selftest/eval.jsonl --conflicts-gold harness/selftest/conflicts_gold.json
```

## What's in the repo

```
submission/            my solution
  solution.py          build() -> BankGPT with ingest / answer / conflicts
  bankgpt/             page parsing, versioned store, ranking rules, conflicts, optional Claude fallback
  dev/                 question sets I wrote to test rewordings, refusals and edge cases
  NOTES.md             approach, how "current" is decided, how to run, API keys
  requirements.txt     empty for the default mode (standard library only)
harness/, eval/        the hackathon's scorer and public practice questions
starter/               the hackathon's naive baseline, kept for comparison
```
