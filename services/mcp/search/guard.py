"""Layer 2 of the SQL guards: parse generated SQL and allow only one safe SELECT shape."""
import sqlglot
import sqlglot.expressions as exp

from tta_contracts.limits import MAX_SEARCH_LIMIT

VIEW = ("wholesaler", "v_product_search")
RANK_FN = ("wholesaler", "hybrid_rank")
BANNED = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Merge,
          exp.Union, exp.Intersect, exp.Except, exp.With, exp.Subquery, exp.Into, exp.Lock)


class SqlRejected(ValueError):
    pass


def check(sql: str, allowed_params: set[str], required_params: set[str]) -> str:
    """Return the SQL rewritten with psycopg placeholders %(name)s, or raise SqlRejected."""
    try:
        trees = [t for t in sqlglot.parse(sql, read="postgres") if t is not None]
    except sqlglot.errors.ParseError as e:
        raise SqlRejected(f"parse error: {str(e).splitlines()[0][:200]}")
    if len(trees) != 1:
        raise SqlRejected(f"expected exactly 1 statement, got {len(trees)}")
    tree = trees[0]
    if not isinstance(tree, exp.Select):
        raise SqlRejected(f"only SELECT is allowed, got {type(tree).__name__}")

    used_view = used_rank = False
    for node in tree.walk():
        if isinstance(node, BANNED):
            raise SqlRejected(f"{type(node).__name__} is not allowed")
        if isinstance(node, exp.Table):
            fn = node.this if isinstance(node.this, exp.Anonymous) else None
            name = (node.db.lower(), (fn.name if fn else node.name).lower())
            if fn and name == RANK_FN:
                used_rank = True
            elif not fn and name == VIEW:
                used_view = True
            else:
                raise SqlRejected(f"relation not allowed: {node.sql(dialect='postgres')[:80]}")
        elif isinstance(node, exp.Anonymous) and node.name.lower() != "hybrid_rank":
            raise SqlRejected(f"function not allowed: {node.name}")
        elif isinstance(node, exp.Literal) and node.is_string:
            raise SqlRejected("string literals are not allowed; use bind parameters")
    if not (used_view and used_rank):
        raise SqlRejected("must read wholesaler.v_product_search joined with wholesaler.hybrid_rank")

    used = {p.name for p in tree.find_all(exp.Placeholder)}
    if unknown := used - allowed_params:
        raise SqlRejected(f"unknown parameters: {sorted(unknown)}")
    if missing := required_params - used:
        raise SqlRejected(f"parameters not used (a hard constraint would be ignored): {sorted(missing)}")

    limit = tree.args.get("limit")
    expr = limit.expression if limit is not None else None
    if expr is None:
        raise SqlRejected("LIMIT is required")
    if isinstance(expr, exp.Placeholder):
        if expr.name != "limit":
            raise SqlRejected("LIMIT must be :limit")
    elif not (isinstance(expr, exp.Literal) and expr.is_int and int(expr.this) <= MAX_SEARCH_LIMIT):
        raise SqlRejected(f"LIMIT must be :limit or a number <= {MAX_SEARCH_LIMIT}")

    return tree.sql(dialect="postgres")
