"""Deterministic time-range parsing for Copilot questions ("past 15 days", "on October 2").

The result becomes the ``list_stories`` filter the agent prefetches, so counts for a period are
computed from the database and never estimated by the language model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_DATE = (
    r"(?:(\d{4})-(\d{1,2})-(\d{1,2})"
    rf"|({_MONTH})\.?\s+{_DAY}(?:,?\s+(\d{{4}}))?"
    rf"|{_DAY}\s+({_MONTH})(?:\s+(\d{{4}}))?"
    r"|(\d{1,2})/(\d{1,2})(?:/(\d{4}))?)"
)
_NUM_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "ten": 10,
    "fourteen": 14,
    "fifteen": 15,
    "thirty": 30,
}


@dataclass(frozen=True)
class DateRef:
    month: int
    day: int
    year: int | None

    def resolve(self, default_year: int) -> date:
        return date(self.year or default_year, self.month, self.day)


@dataclass(frozen=True)
class TimeFilter:
    last_days: int | None = None
    since: DateRef | None = None
    until: DateRef | None = None

    def args(self, default_year: int) -> dict[str, object]:
        out: dict[str, object] = {}
        if self.last_days is not None:
            out["last_days"] = self.last_days
        if self.since is not None:
            out["since"] = self.since.resolve(default_year).isoformat()
        if self.until is not None:
            out["until"] = self.until.resolve(default_year).isoformat()
        return out


def _parse_date(text: str) -> DateRef | None:
    m = re.search(_DATE, text, re.I)
    if not m:
        return None
    g = m.groups()
    try:
        if g[0]:
            ref = DateRef(int(g[1]), int(g[2]), int(g[0]))
        elif g[3]:
            ref = DateRef(MONTHS[g[3].lower()], int(g[4]), int(g[5]) if g[5] else None)
        elif g[7]:
            ref = DateRef(MONTHS[g[7].lower()], int(g[6]), int(g[8]) if g[8] else None)
        else:
            ref = DateRef(int(g[9]), int(g[10]), int(g[11]) if g[11] else None)
        date(ref.year or 2000, ref.month, ref.day)  # validate
    except (ValueError, KeyError):
        return None
    return ref


def parse_time_filter(question: str) -> TimeFilter | None:
    q = question.lower()
    m = re.search(r"\b(?:past|last|previous)\s+(\d+|[a-z]+)\s+(day|week)s?\b", q)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _NUM_WORDS.get(m.group(1))
        if n:
            return TimeFilter(last_days=n * (7 if m.group(2) == "week" else 1))
    if re.search(r"\b(?:past|last|this|previous)\s+week\b", q):
        return TimeFilter(last_days=7)
    if re.search(r"\b(?:last|past)\s+(?:day|24\s*hours?)\b|\btoday\b|\byesterday\b", q):
        return TimeFilter(last_days=1)
    between = re.search(r"\bbetween\s+(.+?)\s+and\s+(.+)", q)
    if between:
        a, b = _parse_date(between.group(1)), _parse_date(between.group(2))
        if a and b:
            return TimeFilter(since=a, until=b)
    since = re.search(r"\b(?:since|after|from)\s+(.+)", q)
    if since and (d := _parse_date(since.group(1))):
        return TimeFilter(since=d)
    before = re.search(r"\b(?:before|until|up to)\s+(.+)", q)
    if before and (d := _parse_date(before.group(1))):
        return TimeFilter(until=d)
    if d := _parse_date(q):
        return TimeFilter(since=d, until=d)
    return None
