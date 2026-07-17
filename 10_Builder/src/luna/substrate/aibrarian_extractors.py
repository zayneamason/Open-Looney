"""
AiBrarian Extractors — entity, date, and analytics utilities
=============================================================

Regex-based extraction for entities (persons, orgs, dates) and
text analytics (word frequency, n-grams, context words).

Ported from DatabaseProject patterns and generalized for any collection.
No hardcoded domain-specific names — those come from collection config.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Entity Extraction
# ---------------------------------------------------------------------------

# Person name: Capitalized words (First Last, First M. Last, First Middle Last)
PERSON_PATTERN = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
)

# Org: ends with Inc, LLC, Corp, Foundation, University, etc.
ORG_PATTERN = re.compile(
    r"\b((?:[A-Z][a-z]+\s+)*(?:Inc|LLC|Corp|Corporation|Company|Foundation"
    r"|University|Institute|Bank|Department|Office|Bureau|Agency|Police"
    r"|Court|Prison|Center)\.?)\b"
)

# Default false-positive filter for person names
DEFAULT_NAME_BLACKLIST: frozenset[str] = frozenset({
    "The Court", "The State", "The United", "The Department", "United States",
    "New York", "Palm Beach", "Little Saint", "Virgin Islands", "St James",
    "St Thomas", "Dear Sir", "Dear Madam", "First Amendment", "Fifth Amendment",
    "Pursuant To", "In Re", "Ex Parte", "Pro Se", "Et Al", "Inter Alia",
    "The Honorable", "Your Honor", "The Plaintiff", "The Defendant",
    # Month prefixes that look like names
    "On January", "On February", "On March", "On April", "On May", "On June",
    "On July", "On August", "On September", "On October", "On November", "On December",
    "In January", "In February", "In March", "In April", "In May", "In June",
    "In July", "In August", "In September", "In October", "In November", "In December",
    # Orgs that look like person names
    "Deutsche Bank", "JP Morgan", "Goldman Sachs", "Morgan Stanley",
})

# Common legal-document false positives for org extraction
ORG_FILTER_WORDS = {"Court", "State", "County", "District"}


def extract_persons(
    text: str,
    known_persons: Optional[list[str]] = None,
    blacklist: Optional[frozenset[str]] = None,
) -> list[str]:
    """Extract person names from text via regex + optional known-persons list."""
    persons: set[str] = set()
    bl = blacklist or DEFAULT_NAME_BLACKLIST

    # High-confidence: known persons
    if known_persons:
        text_upper = text.upper()
        for person in known_persons:
            if person.upper() in text_upper:
                persons.add(person)

    # Pattern matching
    for match in PERSON_PATTERN.findall(text):
        name = match.strip()
        if len(name) > 4 and name not in bl:
            parts = name.split()
            if len(parts) >= 2 and not any(w in name for w in ORG_FILTER_WORDS):
                persons.add(name)

    return sorted(persons)


def extract_organizations(
    text: str,
    known_orgs: Optional[list[str]] = None,
) -> list[str]:
    """Extract organization names from text."""
    orgs: set[str] = set()

    if known_orgs:
        text_upper = text.upper()
        for org in known_orgs:
            if org.upper() in text_upper:
                orgs.add(org)

    for match in ORG_PATTERN.findall(text):
        org = match.strip()
        if len(org) > 3:
            orgs.add(org)

    return sorted(orgs)


# ---------------------------------------------------------------------------
# Date Extraction
# ---------------------------------------------------------------------------

MONTH_MAP = {
    "january": 1, "jan": 1, "february": 2, "feb": 2,
    "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

# Date patterns for legal documents (sorted most → least specific)
DATE_EXTRACTION_PATTERNS: list[tuple] = [
    # ISO: 2015-03-14
    (
        re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
        "iso", "high",
        lambda m: f"{m.group(1)}-{m.group(2)}-{m.group(3)}",
    ),
    # US slash: 03/14/2015
    (
        re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),
        "us_slash", "high",
        lambda m: f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}",
    ),
    # Written full: March 14, 2015
    (
        re.compile(
            r"\b(January|February|March|April|May|June|July|August|September"
            r"|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
            re.IGNORECASE,
        ),
        "written_full", "high", None,
    ),
    # "on March 14, 2015"
    (
        re.compile(
            r"\bon\s+(January|February|March|April|May|June|July|August|September"
            r"|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
            re.IGNORECASE,
        ),
        "written_on", "high", None,
    ),
    # Abbreviated: Mar 14, 2015
    (
        re.compile(
            r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
            r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
            re.IGNORECASE,
        ),
        "abbrev_full", "high", None,
    ),
    # Month Year: March 2015
    (
        re.compile(
            r"\b(January|February|March|April|May|June|July|August|September"
            r"|October|November|December)\s+(\d{4})\b",
            re.IGNORECASE,
        ),
        "month_year", "medium", None,
    ),
    # Abbreviated month year: Mar 2015
    (
        re.compile(
            r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+(\d{4})\b",
            re.IGNORECASE,
        ),
        "abbrev_year", "medium", None,
    ),
    # Year in context: "in 2015"
    (
        re.compile(
            r"\b(?:in|during|since|from|by|before|after|around|circa|c\.?)\s+(\d{4})\b",
            re.IGNORECASE,
        ),
        "year_context", "low",
        lambda m: f"{m.group(1)}-01-01",
    ),
]


def _parse_month_date(match: re.Match, pattern_type: str) -> Optional[str]:
    """Parse dates with month names into ISO format."""
    try:
        if pattern_type in ("written_full", "written_on"):
            month_str = match.group(1).lower()
            day = int(match.group(2))
            year = int(match.group(3))
        elif pattern_type == "abbrev_full":
            month_str = match.group(1).lower()
            day = int(match.group(2))
            year = int(match.group(3))
        elif pattern_type in ("month_year", "abbrev_year"):
            month_str = match.group(1).lower()
            day = 1
            year = int(match.group(2))
        else:
            return None

        month = MONTH_MAP.get(month_str)
        if not month or day < 1 or day > 31 or year < 1900 or year > 2100:
            return None

        return f"{year}-{month:02d}-{day:02d}"
    except (ValueError, IndexError):
        return None


@dataclass
class TimelineEvent:
    date: str
    context: str
    doc_id: str
    confidence: str
    date_format: str


def extract_dates_from_text(text: str, doc_id: str) -> list[TimelineEvent]:
    """Extract all dates from text with surrounding context."""
    events: list[TimelineEvent] = []
    seen_positions: set[tuple[int, int]] = set()

    for regex, pattern_type, confidence, formatter in DATE_EXTRACTION_PATTERNS:
        for match in regex.finditer(text):
            if formatter:
                try:
                    iso_date = formatter(match)
                except (ValueError, IndexError):
                    continue
            else:
                iso_date = _parse_month_date(match, pattern_type)

            if not iso_date:
                continue

            try:
                parsed = datetime.strptime(iso_date, "%Y-%m-%d")
                if parsed.year < 1900 or parsed.year > 2100:
                    continue
            except ValueError:
                continue

            pos_key = (match.start(), match.end())
            if pos_key in seen_positions:
                continue
            seen_positions.add(pos_key)

            start = max(0, match.start() - 100)
            end = min(len(text), match.end() + 100)
            context = re.sub(r"\s+", " ", text[start:end].strip())
            if start > 0:
                context = "..." + context
            if end < len(text):
                context = context + "..."

            events.append(TimelineEvent(
                date=iso_date,
                context=context,
                doc_id=doc_id,
                confidence=confidence,
                date_format=pattern_type,
            ))

    events.sort(key=lambda e: e.date)
    return events


def extract_dates_simple(text: str) -> list[str]:
    """Extract just date strings (no context). For entity-style extraction."""
    dates: set[str] = set()
    patterns = [
        re.compile(
            r"\b(?:January|February|March|April|May|June|July|August|September"
            r"|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept"
            r"|Oct|Nov|Dec)\.?\s+\d{1,2},?\s+\d{4}\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
        re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
        re.compile(
            r"\b(?:January|February|March|April|May|June|July|August|September"
            r"|October|November|December)\s+\d{4}\b",
            re.IGNORECASE,
        ),
    ]
    for p in patterns:
        dates.update(p.findall(text))
    return sorted(dates)


# ---------------------------------------------------------------------------
# Text Analytics
# ---------------------------------------------------------------------------

STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the",
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your",
    "yours", "yourself", "yourselves", "he", "him", "his", "himself", "she",
    "her", "hers", "herself", "it", "its", "itself", "they", "them", "their",
    "theirs", "themselves", "what", "which", "who", "whom", "this", "that",
    "these", "those",
    "am", "is", "are", "was", "were", "be", "been", "being", "have", "has",
    "had", "having", "do", "does", "did", "doing", "would", "should", "could",
    "ought", "will", "shall", "can", "may", "might", "must",
    "at", "by", "for", "from", "in", "into", "of", "on", "to", "with",
    "about", "against", "between", "through", "during", "before", "after",
    "above", "below", "under", "over", "again", "further", "then", "once",
    "and", "but", "or", "nor", "so", "yet", "both", "either", "neither",
    "not", "only", "own", "same", "than", "too", "very", "just",
    "as", "if", "because", "until", "while", "when", "where", "why", "how",
    "all", "each", "few", "more", "most", "other", "some", "such", "no",
    "any", "here", "there", "now", "also", "out", "up", "down", "off",
    "s", "t", "d", "ll", "ve", "re", "m",
})

WORD_PATTERN = re.compile(r"\b[a-zA-Z]{2,}\b")


def tokenize(text: str) -> list[str]:
    """Extract lowercase words, filtering stopwords."""
    words = WORD_PATTERN.findall(text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def extract_ngrams(words: list[str], n: int) -> list[str]:
    """Extract n-grams from a word list."""
    if len(words) < n:
        return []
    return [" ".join(words[i : i + n]) for i in range(len(words) - n + 1)]


def get_context_words(text: str, target_term: str, window: int = 5) -> list[str]:
    """Extract words appearing near the target term."""
    words = WORD_PATTERN.findall(text.lower())
    target_lower = target_term.lower()
    context: list[str] = []

    for i, word in enumerate(words):
        if word == target_lower or target_lower in word:
            start = max(0, i - window)
            end = min(len(words), i + window + 1)
            for j in range(start, end):
                if j != i:
                    w = words[j]
                    if w not in STOPWORDS and len(w) > 1:
                        context.append(w)
    return context


# ---------------------------------------------------------------------------
# SQL Validation
# ---------------------------------------------------------------------------

FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|MERGE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)
ALLOWED_SQL_START = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


def validate_readonly_sql(query: str) -> Optional[str]:
    """Validate SQL is read-only. Returns error message or None if valid."""
    if FORBIDDEN_SQL.search(query):
        return "Only SELECT queries are allowed"
    if not ALLOWED_SQL_START.match(query):
        return "Query must start with SELECT or WITH"
    return None
