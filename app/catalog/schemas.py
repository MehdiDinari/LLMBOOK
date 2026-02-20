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
    ``categories`` and ``tags`` can be empty. The ``rating`` field
    is optional and represents the average rating from the data
    provider (e.g., Google Books). If no rating is available, it
    will be ``None``. ``ratings_count`` reflects the number of
    reviews and defaults to 0.
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
    # Rating is optional; use None when unavailable so the front‑end
    # can display a dash (—) instead of 0.0. Ratings are typically
    # between 0 and 5 when provided.
    rating: Optional[float] = None
    # Count of ratings/reviews. Defaults to 0 when not provided.
    ratings_count: int = 0

    # Optional URL to read the book online. When available, this link points to
    # the Google Books web reader. Clients can embed this URL in an iframe to
    # allow users to read a preview or the full text when permitted by the
    # provider. If no reader link is available, the value will be ``None``.
    web_reader_link: Optional[str] = None


class PaginatedBooks(BaseModel):
    """A wrapper for paginated results returned from ``/books`` endpoint."""

    page: int
    page_size: int
    total: int
    total_pages: int
    items: List[Book]