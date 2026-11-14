"""Search layer of the Valio Aimo gateway.

catalog.Catalog is what the gateway tools use (owner: P2).
service.SearchEngine is the standalone P1 engine kept for tests and offline use.
"""
from .guard import SqlRejected
from .service import SearchEngine, SearchInputError, SearchTrace

__all__ = ["SearchEngine", "SearchInputError", "SearchTrace", "SqlRejected"]
