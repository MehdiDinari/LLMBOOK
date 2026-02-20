"""
Simple data store for the catalogue API.

The ``BOOKS`` list is populated at import time from the sample
dataset. If you wish to replace this with a database, you can
refactor this module to load data from your preferred backend (e.g.
PostgreSQL, MongoDB, etc.). Each entry is converted into a
``Book`` instance from ``schemas``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

# We no longer depend on the Google Books API, so requests is unused.
# Instead, use the Open Library service defined in openlibrary_service.
from .openlibrary_service import search_books_openlibrary, get_book_openlibrary

from .schemas import Book

# Optional local sample data (unused by Google API but kept for fallback/testing)
DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "sample_books.json"


def _load_sample_books() -> List[Book]:
    """Load local sample books for fallback or testing.

    Returns
    -------
    List[Book]
        A list of Book instances loaded from ``sample_books.json``.
    """
    books: List[Book] = []
    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        for entry in raw:
            authors = entry.get("authors") or []
            author_str = ", ".join(authors) if isinstance(authors, list) else str(authors)
            book_id = entry.get("work_id") or entry.get("id") or ""
            subjects = entry.get("subjects") or []
            categories = subjects if isinstance(subjects, list) else []
            tags = [s.strip().lower().replace(" ", "-") for s in categories[:3]]
            # Parse optional rating from sample data. Use float if provided
            # and not None/empty; otherwise leave as None to allow the
            # front‑end to display a dash. Ratings count is not
            # available in sample data, so it defaults to 0.
            rating_val = entry.get("rating")
            try:
                rating = float(rating_val) if rating_val not in (None, "") else None
            except Exception:
                rating = None
            books.append(
                Book(
                    id=str(book_id),
                    title=str(entry.get("title") or ""),
                    author=author_str or "",
                    short_description=str(entry.get("description") or ""),
                    cover_url=entry.get("cover_url") or "",
                    categories=categories,
                    tags=tags,
                    language=entry.get("language") or "fr",
                    year=entry.get("year"),
                    rating=rating,
                    ratings_count=int(entry.get("ratings_count") or 0),
                )
            )
    except Exception:
        # Ignore errors if sample file is missing or malformed
        pass
    return books


# In-memory collection of books used for local fallback (not used for Google API)
BOOKS: List[Book] = _load_sample_books()


def _norm(s: Optional[str]) -> str:
    """Normalize a string for case-insensitive comparison.

    Parameters
    ----------
    s : Optional[str]
        The string to normalize.

    Returns
    -------
    str
        The normalized string (lowercased and stripped). An empty string is
        returned when the input is ``None`` or empty.
    """
    return (s or "").strip().lower()


def _search_books_local(
    q: Optional[str] = None,
    category: Optional[str] = None,
    tag: Optional[str] = None,
    language: Optional[str] = None,
    sort: str = "relevance",
    page: int = 1,
    page_size: int = 12,
) -> Tuple[List[Book], int]:
    """Search books from the local ``BOOKS`` collection.

    This function serves as a fallback when the Google Books API is
    unreachable or returns no results. It performs simple full‑text
    matching on the title, author, description and category/tag fields,
    applies optional category/tag/language filters and supports basic
    sorting. Pagination is performed after filtering and sorting.

    Parameters
    ----------
    q : Optional[str]
        Text search query. If provided, books are filtered to those
        containing the query (case-insensitive) in the title, author,
        description, categories or tags.
    category : Optional[str]
        Category filter. Matches any of the book's categories exactly
        (case-insensitive).
    tag : Optional[str]
        Tag filter. Matches any of the book's tags exactly
        (case-insensitive).
    language : Optional[str]
        Language filter. Matches the book's language exactly
        (case-insensitive). If ``None`` or empty, all languages are
        accepted.
    sort : str
        Sort field. One of 'relevance', 'title', 'author', 'year',
        'rating'. The 'relevance' option returns the natural order (as
        found).
    page : int
        1-indexed page number.
    page_size : int
        Number of books per page.

    Returns
    -------
    Tuple[List[Book], int]
        A list of ``Book`` objects for the requested page and the
        total number of items matching the query (before pagination).
    """
    # Start with all books
    items = BOOKS.copy()
    nq = _norm(q)
    ncat = _norm(category)
    ntag = _norm(tag)
    nlang = _norm(language)

    # Apply free‑text search
    if nq:
        def _matches(book: Book) -> bool:
            # Concatenate searchable fields into a single lowercase string
            blob_parts = [
                _norm(book.title),
                _norm(book.author),
                _norm(book.short_description),
                " ".join([_norm(c) for c in (book.categories or [])]),
                " ".join([_norm(t) for t in (book.tags or [])]),
            ]
            blob = " ".join(blob_parts)
            return nq in blob
        items = [b for b in items if _matches(b)]

    # Apply category filter
    if ncat:
        items = [b for b in items if any(_norm(c) == ncat for c in (b.categories or []))]

    # Apply tag filter
    if ntag:
        items = [b for b in items if any(_norm(t) == ntag for t in (b.tags or []))]

    # Apply language filter
    if nlang:
        items = [b for b in items if _norm(b.language) == nlang]

    # Sorting
    if sort == "title":
        items.sort(key=lambda b: (_norm(b.title), _norm(b.author)))
    elif sort == "author":
        items.sort(key=lambda b: (_norm(b.author), _norm(b.title)))
    elif sort == "year":
        items.sort(key=lambda b: (b.year or 0), reverse=True)
    elif sort == "rating":
        items.sort(key=lambda b: (b.rating if b.rating is not None else 0.0), reverse=True)
    # 'relevance' uses natural order (no additional sort)

    total = len(items)
    # Pagination: clamp page_size and page
    try:
        ps = max(1, int(page_size))
    except Exception:
        ps = 12
    try:
        p = max(1, int(page))
    except Exception:
        p = 1
    start = (p - 1) * ps
    end = start + ps
    return items[start:end], total


def _parse_year(published: str) -> Optional[int]:
    """Extract year from a published date string.

    Google Books ``publishedDate`` can be a year (YYYY), year-month, or full date.
    This helper extracts the first four digits if present.

    Parameters
    ----------
    published : str
        The published date string from Google Books.

    Returns
    -------
    Optional[int]
        The extracted year or ``None`` if parsing fails.
    """
    if not published:
        return None
    m = re.match(r"(\d{4})", published)
    return int(m.group(1)) if m else None


def search_books_google(
    q: Optional[str] = None,
    category: Optional[str] = None,
    tag: Optional[str] = None,
    language: Optional[str] = None,
    sort: str = "relevance",
    page: int = 1,
    page_size: int = 12,
) -> Tuple[List[Book], int]:
    """Search books using Open Library instead of Google Books.

    This replacement maintains the same signature as the previous
    ``search_books_google()`` function so that existing routes and
    clients continue to function. Internally it delegates to
    ``search_books_openlibrary()`` which performs an anonymous
    search against Open Library. If Open Library returns no results
    or is unreachable, the function falls back to the local sample
    dataset via ``_search_books_local()``.

    Parameters
    ----------
    q : Optional[str]
        Text search query (title/author/etc.). May be None or empty.
    category : Optional[str]
        Category filter; adds a subject filter to the search query.
    tag : Optional[str]
        Tag filter; adds a subject filter to the search query.
    language : Optional[str]
        Language filter (e.g. 'fr', 'en'). Passed to Open Library.
    sort : str
        Sort field. Sorting is performed locally in ``router.py``.
    page : int
        Page number (1-indexed).
    page_size : int
        Number of results per page.

    Returns
    -------
    Tuple[List[Book], int]
        A tuple containing the list of books for the current page and
        the total number of items returned by Open Library (or the
        local fallback).
    """
    try:
        # Delegate to Open Library search. This returns a list of Book
        # objects or an empty list.
        books = search_books_openlibrary(
            q=q,
            category=category,
            tag=tag,
            language=language,
            sort=sort,
            page=page,
            page_size=page_size,
        )
    except Exception:
        books = []
    if not books:
        # When no results are found, fallback to the local collection.
        local_books, total_local = _search_books_local(
            q=q,
            category=category,
            tag=tag,
            language=language,
            sort=sort,
            page=page,
            page_size=page_size,
        )
        return local_books, total_local
    # Compute a simple total count. Open Library returns a limited
    # number of documents per page; we cannot know the global total
    # without making additional requests, so we use the length of
    # returned results for pagination purposes. The router performs
    # additional filtering and sorting.
    total_items = len(books)
    return books, total_items


def get_book_google(book_id: str) -> Optional[Book]:
    """Fetch a single book by its ID using Open Library.

    This replacement maintains the signature of the previous
    ``get_book_google()`` so that existing routes continue to work.
    It delegates to ``get_book_openlibrary()``, which retrieves a
    work from Open Library by its identifier. If the work cannot be
    found or an error occurs, the local sample dataset is searched
    for a matching ID.

    Parameters
    ----------
    book_id : str
        The Open Library work ID. For backwards compatibility, Google
        volume IDs are no longer supported.

    Returns
    -------
    Optional[Book]
        A Book instance if found; otherwise ``None``.
    """
    try:
        book = get_book_openlibrary(book_id)
        if book is not None:
            return book
    except Exception:
        book = None
    # Fallback to local dataset
    for b in BOOKS:
        if str(b.id) == str(book_id):
            return b
    return None