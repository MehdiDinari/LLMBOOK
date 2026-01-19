"""
Pydantic schema definitions for the catalog module.

The ``Book`` model captures the minimal fields required to render a
catalogue card in the front‑end. Additional fields (e.g. price or
page count) can be added here as needed. The ``PaginatedBooks``
model bundles together a list of books with pagination metadata so
that clients know how many pages of results are available.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class Book(BaseModel):
    """A single book entry.

    This model intentionally keeps a narrow set of fields so that
    clients only receive the information necessary to render a
    catalogue view. Fields such as ``author``, ``short_description``
    and ``cover_url`` are plain strings. Lists such as
    ``categories`` and ``tags`` can be empty.
    """

    id: str
    title: str
    author: str
    short_description: str = ""
    cover_url: str = ""
    categories: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    language: str = "fr"
    year: Optional[int] = None
    rating: float = 0.0  # 0..5


class PaginatedBooks(BaseModel):
    """A wrapper for paginated results returned from ``/books`` endpoint."""

    page: int
    page_size: int
    total: int
    total_pages: int
    items: List[Book]