"""Hard caps shared by the gateway (enforces) and the buyer agent (respects)."""

MAX_SEARCH_LIMIT = 50            # rows returned by one search_products call
MAX_PRICE_IDS = 50               # product ids per get_price_availability call
MAX_SUBSTITUTE_IDS = 10          # product ids per get_substitutes call
MAX_DISTINCT_PER_INTENT = 200    # distinct products an agent may touch per intent
MAX_OPTIONS = 4                  # options per place_order call
MAX_ORDER_LINES = 40             # lines per option: a menu basket has one line per ingredient
MAX_BATCH_QUERIES = 30           # searches per search_products_batch call
INTENT_TTL_HOURS = 24
SQL_TIMEOUT_S = 2
JEV_TIMEOUT_S = 2
JEV_ALLOW = 0.9                  # every question >= this -> allow
JEV_BLOCK = 0.1                  # any question <= this -> block
