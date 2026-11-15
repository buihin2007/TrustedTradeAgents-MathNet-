"""Deterministic sourcing: needs -> catalog candidates -> baskets.

No LLM and no policy decisions here: the gateway re-prices and judges whatever is sent to it.
"""

import re
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from tta_contracts import MandateLimits, Offer, ProductHit

from .brain import Dish, Item

FRACTIONAL_UNITS = {"KG", "KGM", "L", "LTR"}  # sales units that can be ordered in fractions
STRATEGIES = (("best_match", "Best match"), ("cheapest", "Cheapest"), ("most_reliable", "Most reliable"))
CENT = Decimal("0.01")


@dataclass
class Need:
    key: str
    name: str
    search_en: str
    search_fi: str | None
    amount: Decimal  # in unit
    unit: str  # kg | l | pcs
    claims: tuple[str, ...] = ()
    essential: bool = True
    dishes: tuple[str, ...] = ()
    alt_of: str | None = None  # key of the need this one can replace (recipe-level substitute)
    note: str = ""

    @property
    def ref(self) -> str:
        return f"{norm(self.name)}|{self.unit}"


@dataclass
class Candidate:
    need: Need
    hit: ProductHit
    offer: Offer
    rank: int
    size: Decimal
    step: Decimal
    units: Decimal
    cost: Decimal
    available: Decimal

    @property
    def enough(self) -> bool:
        return self.available >= self.units


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def qty(amount, unit: str = "") -> str:
    text = f"{Decimal(str(amount)).quantize(Decimal('0.01')):f}".rstrip("0").rstrip(".")
    return f"{text} {unit}".strip()


def needs_from_items(items: list[Item]) -> list[Need]:
    return [
        Need(
            key=f"i{index}",
            name=item.name,
            search_en=item.search_en,
            search_fi=item.search_fi,
            amount=Decimal(str(item.quantity)),
            unit=item.unit,
            claims=tuple(item.claims),
        )
        for index, item in enumerate(items)
        if item.quantity and item.unit
    ]


def needs_from_dishes(dishes: list[Dish], headcount: int, dairy_claims: tuple[str, ...] = ()) -> list[Need]:
    """Scale per-person amounts, merge ingredients shared by several dishes, attach recipe alternatives."""
    merged: dict[str, Need] = {}
    alternatives: dict[str, Need] = {}
    for dish in dishes:
        for ingredient in dish.ingredients:
            unit = "l" if ingredient.unit == "ml" else "kg"
            amount = (Decimal(str(ingredient.amount)) * headcount / 1000).quantize(Decimal("0.001"))
            ref = f"{norm(ingredient.name)}|{unit}"
            need = merged.get(ref)
            if need is None:
                need = merged[ref] = Need(
                    key=f"n{len(merged)}",
                    name=ingredient.name,
                    search_en=ingredient.search_en,
                    search_fi=ingredient.search_fi,
                    amount=Decimal(0),
                    unit=unit,
                    claims=dairy_claims if ingredient.dairy else (),
                    essential=False,
                )
            need.amount += amount
            need.essential = need.essential or ingredient.essential
            if dish.name not in need.dishes:
                need.dishes += (dish.name,)
            alt = ingredient.alt
            if alt and ingredient.essential and need.key not in alternatives and norm(alt.name) != norm(need.name):
                alternatives[need.key] = Need(
                    key=need.key + "a",
                    name=alt.name,
                    search_en=alt.search_en,
                    search_fi=alt.search_fi,
                    amount=Decimal(0),
                    unit=unit,
                    claims=need.claims,
                    alt_of=need.key,
                    note=alt.note,
                )
    by_key = {need.key: need for need in merged.values()}
    for key, alt in alternatives.items():
        alt.amount = by_key[key].amount
    return list(merged.values()) + list(alternatives.values())


def criteria(need: Need, avoid: list[str]) -> dict:
    return {
        "query_text": (need.search_en or need.name)[:200],
        "query_text_alt": need.search_fi[:200] if need.search_fi else None,
        "must": {"claims_all": list(need.claims), "allergens_none": avoid},
        "limit": 5,
    }


def size_of(hit: ProductHit, unit: str) -> Decimal | None:
    """Size of ONE sales unit in the need's unit. Weight and volume stand in for each other (1 l ~ 1 kg)."""
    if unit == "pcs":
        return Decimal(1)
    first, second = (hit.unit_weight_kg, hit.unit_volume_l) if unit == "kg" else (hit.unit_volume_l, hit.unit_weight_kg)
    value = first if first and first > 0 else second
    return Decimal(str(value)) if value and value > 0 else None


def relevant(hit: ProductHit, need: Need) -> bool:
    """Cheap lexical sanity check: one word of the need must appear in the product's name or category."""
    text = norm(" ".join(filter(None, [hit.name_en, hit.name_fi, hit.category_name])))
    words = re.findall(r"[a-zäöå]{3,}", norm(" ".join(filter(None, [need.name, need.search_en, need.search_fi]))))
    return any(word[:5] in text for word in words) if words else True


def pick(needs: list[Need], hits: dict[str, list[ProductHit]], cap: int = 50):
    """Candidates per need and the product ids to price, within the gateway's 50 ids per quote."""
    primaries = [need for need in needs if not need.alt_of]
    alternatives = [need for need in needs if need.alt_of]
    per_need = max(1, min(5, (cap - len(alternatives)) // max(1, len(primaries))))
    chosen: dict[str, list[ProductHit]] = {}
    ids: list[int] = []
    for need in primaries + alternatives:
        usable = [hit for hit in hits.get(need.ref, []) if size_of(hit, need.unit)]
        good = [hit for hit in usable if relevant(hit, need)] or usable[:1]
        chosen[need.ref] = good[: 1 if need.alt_of else per_need]
        ids += [hit.product_id for hit in chosen[need.ref] if hit.product_id not in ids]
    return chosen, ids[:cap]


def candidates(need: Need, hits: list[ProductHit], offers: dict[int, Offer]) -> list[Candidate]:
    result = []
    for rank, hit in enumerate(hits):
        offer = offers.get(hit.product_id)
        size = size_of(hit, need.unit)
        if not offer or offer.error or offer.sales_block or offer.unit_price_eur is None or size is None:
            continue
        lot = Decimal(str(hit.allowed_lot_size)) if hit.allowed_lot_size and hit.allowed_lot_size > 0 else Decimal(1)
        # Cartons and packs are whole units even when the catalog allows 0.01 lots.
        step = lot if (hit.sales_unit or "").upper() in FRACTIONAL_UNITS or lot >= 1 else Decimal(1)
        units = max((need.amount / size / step).to_integral_value(rounding=ROUND_CEILING) * step, step)
        result.append(
            Candidate(
                need=need,
                hit=hit,
                offer=offer,
                rank=rank,
                size=size,
                step=step,
                units=units,
                cost=(offer.unit_price_eur * units).quantize(CENT),
                available=Decimal(str(offer.available_on_date or 0)),
            )
        )
    return result


def choose(options: list[Candidate], strategy: str) -> Candidate | None:
    enough = [option for option in options if option.enough]
    if not enough:
        return None
    if strategy == "cheapest":
        return min(enough, key=lambda c: (c.cost, c.rank))
    if strategy == "most_reliable":
        return max(enough, key=lambda c: (c.hit.delivery_reliability or -1, -c.rank))
    return min(enough, key=lambda c: c.rank)


def earliest(options: list[Candidate], day: date) -> date | None:
    days = [
        line.available_date
        for option in options
        for line in option.offer.schedule
        if line.available_date > day and not line.sales_block and Decimal(str(line.available_amount)) >= option.units
    ]
    return min(days) if days else None


def resolve(need: Need, alt: Need | None, pool: dict[str, list[Candidate]], strategy: str, day: date):
    """One basket line for a need: the product, a recipe alternative, a partial fill, or why it is missing."""
    options = pool.get(need.key, [])
    found = choose(options, strategy)
    if found:
        return found, "", None
    if alt and (found := choose(pool.get(alt.key, []), strategy)):
        note = f"Instead of {need.name}: {alt.name}" + (f" ({alt.note})" if alt.note else "")
        return found, note, None
    partial = max((o for o in options if o.available >= o.step), key=lambda o: o.available, default=None)
    if partial:
        units = (partial.available / partial.step).to_integral_value(rounding=ROUND_FLOOR) * partial.step
        short = replace(partial, units=units, cost=(partial.offer.unit_price_eur * units).quantize(CENT))
        return short, f"Only {qty(units)} of {qty(partial.units)} units in stock that day", None
    when = earliest(options, day)
    reason = "no matching product in the catalog" if not options else "not in stock on that date"
    return None, "", {"need": need.name, "essential": need.essential, "reason": reason,
                      "earliest": when.isoformat() if when else None}


def gate(total: Decimal, limits: MandateLimits | None) -> tuple[str, str]:
    if limits is None:
        return "unknown", "The gateway decides when you order"
    if total > limits.order_limit_eur:
        return "blocked", f"Above your {limits.order_limit_eur:.0f} EUR per-order limit: the gateway will block it"
    if total > limits.auto_limit_eur:
        return "approval", f"Above your {limits.auto_limit_eur:.0f} EUR auto limit: it will wait in Approvals"
    return "auto", "Within your auto limit: placed immediately"


def build_baskets(needs: list[Need], chosen: dict, offers: dict[int, Offer], day: date, limits=None) -> list[dict]:
    primaries = [need for need in needs if not need.alt_of]
    alternative = {need.alt_of: need for need in needs if need.alt_of}
    pool = {need.key: candidates(need, chosen.get(need.ref, []), offers) for need in needs}
    baskets, seen = [], set()
    for label, title in STRATEGIES:
        lines, missing, total = [], [], Decimal(0)
        for need in primaries:
            found, note, problem = resolve(need, alternative.get(need.key), pool, label, day)
            if found is None:
                missing.append(problem)
                continue
            total += found.cost
            lines.append(
                {
                    "product_id": found.hit.product_id,
                    "name": found.hit.name_en or found.hit.name_fi or f"#{found.hit.product_id}",
                    "units": float(found.units),
                    "sales_unit": found.hit.sales_unit,
                    "pack": qty(found.size, "pcs" if need.unit == "pcs" else found.need.unit),
                    "unit_price": str(found.offer.unit_price_eur),
                    "line_total": str(found.cost),
                    "need": need.name,
                    "amount": qty(need.amount, need.unit),
                    "note": note,
                    "reliability": found.hit.delivery_reliability,
                    "dishes": list(need.dishes),
                }
            )
        signature = tuple((line["product_id"], line["units"]) for line in lines)
        if not lines or signature in seen:
            continue
        seen.add(signature)
        level, gate_note = gate(total, limits)
        baskets.append(
            {
                "label": label,
                "title": title,
                "total": str(total.quantize(CENT)),
                "lines": lines,
                "missing": missing,
                "gate": level,
                "gate_note": gate_note,
                "explanation": f"{title}: {len(lines)} lines, {total.quantize(CENT)} EUR"
                + (f", {len(missing)} not available" if missing else ""),
            }
        )
    return baskets


def dish_cards(dishes: list[Dish], headcount: int, chosen: dict, offers: dict, day: date, dairy_claims=()) -> list[dict]:
    """What each suggested dish would need from Valio's stock on the delivery date."""
    cards = []
    for dish in dishes:
        needs = needs_from_dishes([dish], headcount, dairy_claims)
        primaries = [need for need in needs if not need.alt_of]
        alternative = {need.alt_of: need for need in needs if need.alt_of}
        pool = {need.key: candidates(need, chosen.get(need.ref, []), offers) for need in needs}
        covered, cost, absent, swaps = 0, Decimal(0), [], []
        for need in primaries:
            found, note, problem = resolve(need, alternative.get(need.key), pool, "best_match", day)
            if found is None or note.startswith("Only"):
                absent.append(need.name)
            else:
                covered += 1
                if note:
                    swaps.append(note)
            if found is not None:
                cost += found.cost
        per_person = (cost / headcount).quantize(CENT) if headcount else None
        stock = (
            f"all {len(primaries)} ingredients in stock"
            if not absent
            else f"{covered} of {len(primaries)} in stock (missing: {', '.join(absent)})"
        )
        cards.append(
            {
                "name": dish.name,
                "why": dish.why,
                "covered": covered,
                "total": len(primaries),
                "missing": absent,
                "swaps": swaps,
                "per_person": str(per_person) if per_person is not None else None,
                "ingredients": [f"{qty(i.amount, i.unit)} {i.name}" for i in dish.ingredients],
                "pantry": dish.pantry,
                "line": " · ".join(filter(None, [dish.why, stock, f"about {per_person} EUR per person" if per_person else ""])),
            }
        )
    return sorted(cards, key=lambda card: (-(card["covered"] / max(1, card["total"])), card["name"]))


def missing_report(needs: list[Need], chosen: dict, offers: dict[int, Offer], day: date) -> list[dict]:
    """Why each need that cannot be sourced is missing (used when no basket could be built)."""
    pool = {need.key: candidates(need, chosen.get(need.ref, []), offers) for need in needs}
    alternative = {need.alt_of: need for need in needs if need.alt_of}
    report = []
    for need in needs:
        if need.alt_of:
            continue
        problem = resolve(need, alternative.get(need.key), pool, "best_match", day)[2]
        if problem:
            report.append(problem)
    return report
