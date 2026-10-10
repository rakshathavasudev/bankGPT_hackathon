"""Optional Claude fallback, used only when the rules engine would refuse.

Off by default. It runs only when BOTH are set:
    ANTHROPIC_API_KEY=...        (your Anthropic API key)
    BANKGPT_USE_CLAUDE=1

Claude sees only passages retrieved from the snapshot that applies to the question, never the
live web, and is told to say "I don't know" when the passages don't answer it. Any failure
(no SDK, no key, timeout, API error) falls back to the rules engine's honest refusal.
"""
from __future__ import annotations

import os
import re

MODEL = os.environ.get("BANKGPT_CLAUDE_MODEL", "claude-opus-5-5")
SYSTEM = (
    "You answer member questions for Dupaco Credit Union using ONLY the website passages provided. "
    "Each passage comes from the same dated snapshot of the site. If the passages do not clearly "
    "answer the question, reply exactly: I don't know. Never guess a rate, fee, date or number, and "
    "never use outside knowledge. Answer in one to three plain sentences and quote figures exactly "
    "as written. End with a line 'SOURCE: <url>' naming the passage you used."
)


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("BANKGPT_USE_CLAUDE") == "1"


_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic  # imported lazily so the default mode needs no third-party packages
        _client = anthropic.Anthropic(timeout=float(os.environ.get("BANKGPT_CLAUDE_TIMEOUT", "20")),
                                      max_retries=1)
    return _client


def ask(question: str, passages: list[tuple[str, str]]) -> tuple[str, str] | None:
    """passages: [(url, text)]. Returns (answer, source_url) or None when Claude can't answer."""
    if not enabled() or not passages:
        return None
    ctx = "\n\n".join(f"[{i + 1}] {url}\n{text}" for i, (url, text) in enumerate(passages))
    try:
        client = _get_client()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=SYSTEM,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": f"Passages:\n{ctx}\n\nQuestion: {question}"}],
        )
    except Exception:
        return None
    if resp.stop_reason not in ("end_turn", "stop_sequence"):
        return None
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    if not text or re.match(r"^\s*i don.?t know", text, re.I):
        return None
    m = re.search(r"SOURCE:\s*(\S+)", text)
    src = m.group(1) if m else passages[0][0]
    body = re.sub(r"\n?\s*SOURCE:.*$", "", text, flags=re.S).strip()
    # Only keep numbers that actually appear in the passages; otherwise refuse rather than risk a guess.
    nums = re.findall(r"\d[\d,]*(?:\.\d+)?", body)
    if any(n not in ctx for n in nums):
        return None
    return body, src
