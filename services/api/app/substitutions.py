"""Bounded substitution proposals; gateway retains all mandate/policy authority."""

from tta_contracts import PriceAvailabilityRequest, PriceAvailabilityResult, SubstituteGroup, SubstitutesRequest
from tta_contracts.limits import MAX_OPTIONS, MAX_PRICE_IDS, MAX_SUBSTITUTE_IDS

from .gateway import GatewayUnavailable
from .options import build_options


async def shortage_options(gateway, parsed, intent_id, hits, quote, options):
    if options and options[0].option.label != "split_or_substitute":
        return quote, options
    parents = list(dict.fromkeys(hit.product_id for hit in hits if hit.has_substitutes))[:MAX_SUBSTITUTE_IDS]
    if not parents:
        return quote, options
    groups = await gateway.call(
        "get_substitutes",
        SubstitutesRequest(
            intent_id=intent_id,
            product_ids=parents,
        ).model_dump(mode="json"),
        list[SubstituteGroup],
    )
    if any(group.product_id not in parents for group in groups):
        raise GatewayUnavailable("Gateway returned substitutes for an unrelated product")
    originals = {hit.product_id: hit for hit in hits}
    alternatives = {}
    sources = {}
    for group in groups:
        for hit in group.substitutes:
            if hit.product_id not in originals and hit.product_id not in alternatives:
                alternatives[hit.product_id] = hit
                sources[hit.product_id] = group.product_id
    if not alternatives:
        return quote, options
    # Preserve all lines of the already feasible original split before reserving
    # room for new products. One fresh quote covers every proposed purchase line.
    required = list(dict.fromkeys(line.product_id for option in options for line in option.option.lines))
    retained = list(dict.fromkeys(required + parents))
    candidates = [originals[product_id] for product_id in retained]
    candidates += list(alternatives.values())[: MAX_PRICE_IDS - len(candidates)]
    updated = await gateway.call(
        "get_price_availability",
        PriceAvailabilityRequest(
            intent_id=intent_id,
            product_ids=[hit.product_id for hit in candidates],
            delivery_date=parsed.delivery_date,
        ).model_dump(mode="json"),
        PriceAvailabilityResult,
    )
    if updated.delivery_date != parsed.delivery_date:
        raise GatewayUnavailable("Gateway quote has a different delivery date")
    proposed = build_options(parsed, candidates, updated.offers, substitute_for=sources)
    original_splits = build_options(parsed, [hit for hit in candidates if hit.product_id in originals], updated.offers)
    seen = {tuple(sorted((line.product_id, line.quantity_units) for line in item.option.lines)) for item in proposed}
    for item in original_splits:
        signature = tuple(sorted((line.product_id, line.quantity_units) for line in item.option.lines))
        if signature not in seen and len(proposed) < MAX_OPTIONS:
            proposed.append(item)
            seen.add(signature)
    return updated, proposed
