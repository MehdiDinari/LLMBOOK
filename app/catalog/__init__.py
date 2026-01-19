"""
Catalog package for the book catalog API.

This package contains schemas and route definitions that expose a
simple REST API for browsing a collection of books. The intent is to
provide a separate set of endpoints from the existing chatbot
functionality so that a front‑end (for example a WordPress page) can
retrieve a list of books along with metadata such as title, author,
cover image URL, categories and tags. The API supports basic
filtering, searching and pagination. Should your needs evolve, you
can extend the store module to load data from a database instead of
the in‑memory list defined here.
"""

from .router import router as catalog_router  # noqa: F401