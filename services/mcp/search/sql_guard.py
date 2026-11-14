"""Accept only the approved query structure for the requested criteria shape."""

from sqlglot import exp, parse
from sqlglot.errors import SqlglotError


def checked_sql(candidate: str, template: str) -> str:
    """Parse both trees so the model can never introduce a new SQL capability."""
    if len(candidate) > 8000:
        raise ValueError("SQL exceeds maximum size")
    try:
        trees = parse(candidate, read="postgres")
        reference = parse(template, read="postgres")[0]
    except (SqlglotError, RecursionError) as exc:
        raise ValueError("SQL could not be parsed") from exc
    if len(trees) != 1 or not isinstance(trees[0], exp.Select) or trees[0] != reference:
        raise ValueError("Use the approved SELECT, predicates, ordering and named parameters")
    # Execute the service's canonical SQL, including its original placeholder syntax.
    return template
