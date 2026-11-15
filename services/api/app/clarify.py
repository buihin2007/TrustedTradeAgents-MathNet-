"""Read dates and quantities from the buyer's free-text answers in code, not with the LLM."""

import re
from datetime import date, timedelta

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
