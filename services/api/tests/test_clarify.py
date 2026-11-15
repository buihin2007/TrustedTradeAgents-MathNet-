"""Dates and quantities in free-text answers are read by code."""

from datetime import date

import pytest

from app.clarify import parse_date, parse_quantity

TODAY = date(2026, 11, 15)  # a Sunday


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026-11-24", date(2026, 11, 24)),
        ("2026-11-17 (Tue)", date(2026, 11, 17)),
        ("24.11.", date(2026, 11, 24)),
        ("24/11/2026", date(2026, 11, 24)),
        ("deliver on 3.1.", date(2027, 1, 3)),          # past this year -> next year
        ("monday", date(2026, 11, 16)),
        ("next Friday please", date(2026, 11, 20)),
        ("perjantai", date(2026, 11, 20)),
        ("tomorrow", date(2026, 11, 16)),
        ("2026-11-01", None),                            # in the past
        ("31.02.", None),
        ("whenever", None),
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text, TODAY) == expected


@pytest.mark.parametrize(
    "text,expected",
    [("20 kg", (20, "kg")), ("40 litres", (40, "l")), ("2,5 kilos", (2.5, "kg")), ("12 pcs", (12, "pcs")),
     ("lots", None), ("20 boxes", None)],
)
def test_parse_quantity(text, expected):
    assert parse_quantity(text) == expected
