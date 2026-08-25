"""Stable per-advisory identity for security alerts, so re-filing is a no-op."""

import re

_ADVISORY_ID = re.compile(
    r"\b(?:CVE-\d{4}-\d{4,}|GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4})\b",
    re.IGNORECASE,
)


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def alert_dedupe_key(cve, name, affected_component, summary) -> str:
    """Never returns NULL, unlike the raw cve column (89% NULL in practice),
    which made the old UNIQUE constraint a no-op under SQLite's NULLs-are-distinct
    rule. Preference order:

      1. the cve field (CVE or GHSA id)
      2. an advisory id embedded in the name or summary text
      3. normalized name + affected_component
      4. normalized summary
    """
    if cve and cve.strip():
        return cve.strip().upper()
    for text in (name, summary):
        m = _ADVISORY_ID.search(text or "")
        if m:
            return m.group(0).upper()
    key = "|".join(p for p in (_norm(name), _norm(affected_component)) if p)
    return key or _norm(summary)
