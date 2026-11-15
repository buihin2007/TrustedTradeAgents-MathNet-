"""Deterministic handling of clarification answers.

The LLM re-parses the whole request each round and often ignores answers or nulls a typed date as
"ambiguous", which made the pipeline ask the same question forever. Fields the buyer has already
given are therefore filled in by code: earlier parse first, then the answers, latest first.
"""

import re
from datetime import date, timedelta

from .models import ParsedRequest, Question

QUESTIONS = {
    "quantity": "How much do you need? For example: 20 kg, 40 l or 12 pcs.",
    "delivery_date": "Which delivery date? Pick one or type a date (YYYY-MM-DD, 24.11. or Monday).",
}
MAX_ASKS = 3  # same question asked this often without a usable answer: stop instead of looping

_WEEKDAYS = {}
for _i, _names in enumerate(
    [
        ("monday", "mon", "maanantai", "ma"),
        ("tuesday", "tue", "tues", "tiistai", "ti"),
        ("wednesday", "wed", "keskiviikko", "ke"),
        ("thursday", "thu", "thur", "thurs", "torstai", "to"),
        ("friday", "fri", "perjantai", "pe"),
        ("saturday", "sat", "lauantai", "la"),
        ("sunday", "sun", "sunnuntai", "su"),
    ]
):
    _WEEKDAYS.update(dict.fromkeys(_names, _i))

_UNITS = {
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "l": "l", "ltr": "l", "litre": "l", "litres": "l", "liter": "l", "liters": "l",
    "pcs": "pcs", "pc": "pcs", "piece": "pcs", "pieces": "pcs", "unit": "pcs", "units": "pcs",
}


def parse_date(text: str, today: date) -> date | None:
    """A delivery date from free text, never in the past. Day-first like Finnish dates (24.11.)."""
    t = text.strip().lower()
    candidate = None
    if m := re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t):
        candidate = _safe(int(m[1]), int(m[2]), int(m[3]))
    elif m := re.search(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b", t):
        year = int(m[3]) + (2000 if m[3] and len(m[3]) == 2 else 0) if m[3] else today.year
        candidate = _safe(year, int(m[2]), int(m[1]))
        if candidate and not m[3] and candidate < today:
            candidate = _safe(year + 1, int(m[2]), int(m[1]))
    elif re.search(r"\b(tomorrow|huomenna)\b", t):
        candidate = today + timedelta(days=1)
    elif re.search(r"\b(today|tänään)\b", t):
        candidate = today
    else:
        for word in re.findall(r"[a-zäö]+", t):
            if word in _WEEKDAYS:
                ahead = (_WEEKDAYS[word] - today.weekday()) % 7 or 7
                candidate = today + timedelta(days=ahead)
                break
    return candidate if candidate and candidate >= today else None


def _safe(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_quantity(text: str) -> tuple[float, str] | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*([a-z]+)\b", text.lower())
    if not m or m[2] not in _UNITS:
        return None
    value = float(m[1].replace(",", "."))
    return (value, _UNITS[m[2]]) if value > 0 else None


def fill_missing(parsed: ParsedRequest, previous: ParsedRequest | None, questions: list[Question], today: date):
    """Fill quantity/unit/date the LLM left null from the earlier parse, then from answers (latest first)."""
    updates = {}
    for name in ("quantity", "quantity_unit", "delivery_date"):
        if getattr(parsed, name) is None and previous is not None and getattr(previous, name) is not None:
            updates[name] = getattr(previous, name)
    answers = [q.answer for q in sorted(questions, key=lambda q: q.asked_at, reverse=True) if q.answer]
    if parsed.delivery_date is None and "delivery_date" not in updates:
        updates["delivery_date"] = next((d for a in answers if (d := parse_date(a, today))), None)
    if (parsed.quantity is None or parsed.quantity_unit is None) and "quantity" not in updates:
        found = next((q for a in answers if (q := parse_quantity(a))), None)
        if found:
            updates["quantity"], updates["quantity_unit"] = found
    if updates.get("delivery_date") is not None and updates["delivery_date"] < today:
        updates["delivery_date"] = None
    return parsed.model_copy(update={k: v for k, v in updates.items() if v is not None})


def next_question(parsed: ParsedRequest, questions: list[Question], today: date):
    """(field, question, choices) for the first missing field, or None. Raises if asked MAX_ASKS times."""
    field = (
        "quantity" if parsed.quantity is None or parsed.quantity_unit is None
        else "delivery_date" if parsed.delivery_date is None
        else None
    )
    if field is None:
        return None
    text = QUESTIONS[field]
    if sum(q.question == text for q in questions) >= MAX_ASKS:
        raise ValueError(field)
    if field == "delivery_date":
        days = [today + timedelta(days=n) for n in range(1, 9)]
        choices = [f"{d.isoformat()} ({d.strftime('%a')})" for d in days if d.weekday() != 6][:6]
    else:
        choices = []
    return field, text, choices
