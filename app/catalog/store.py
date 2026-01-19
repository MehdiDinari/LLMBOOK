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

import requests

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
                    rating=float(entry.get("rating") or 0.0),
                )
            )
    except Exception:
        # Ignore errors if sample file is missing or malformed
        pass
    return books


# In-memory collection of books used for local fallback (not used for Google API)
BOOKS: List[Book] = _load_sample_books()


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
    """Search books using Google Books API.

    This function queries the Google Books volumes API and converts results
    into ``Book`` instances. It also returns the total number of items
    reported by Google so that callers can calculate pagination metadata.

    Parameters
    ----------
    q : Optional[str]
        Text search query (title/author/etc.). May be None or empty.
    category : Optional[str]
        Category filter; if provided, adds a ``subject:`` filter to the search query.
    tag : Optional[str]
        Tag filter; if provided, adds a ``subject:`` filter to the search query.
    language : Optional[str]
        Language filter (e.g. 'fr', 'en'). Passed via ``langRestrict`` parameter.
    sort : str
        Sort field. Currently only affects local ordering; Google Books does not
        support sorting by rating or year. Accepts 'relevance', 'title', 'author', 'year', 'rating'.
    page : int
        Page number (1-indexed).
    page_size : int
        Number of results per page. Google Books allows up to 40.

    Returns
    -------
    Tuple[List[Book], int]
        A tuple containing the list of books for the current page and the total
        number of items reported by Google.
    """
    # Build the query: base query plus subject filters
    query_terms = []
    if q:
        query_terms.append(q)
    if category:
        # Use subject filter; Google interprets this as a subject/category
        query_terms.append(f"subject:{category}")
    if tag:
        query_terms.append(f"subject:{tag}")
    query = "+".join(query_terms) if query_terms else ""
    # Ensure page_size does not exceed Google Books API limit (maxResults <= 40)
    max_results = min(max(page_size, 1), 40)
    start_index = max(0, (page - 1) * max_results)
    params = {
        "q": query,
        "startIndex": start_index,
        "maxResults": max_results,
        "printType": "books",
    }
    if language:
        params["langRestrict"] = language
    # Pass API key if provided via environment variable
    key = os.environ.get("GOOGLE_BOOKS_API_KEY")
    if key:
        params["key"] = key
    books: List[Book] = []
    total_items = 0
    try:
        resp = requests.get(
            "https://www.googleapis.com/books/v1/volumes", params=params, timeout=10
        )
        if resp.status_code != 200:
            # Non-200 response indicates failure; return empty results
            return [], 0
        data = resp.json()
        total_items = int(data.get("totalItems", 0))
        for item in data.get("items", []) or []:
            volume_id = item.get("id")
            info = item.get("volumeInfo", {}) or {}
            if not volume_id or not info:
                continue
            title = info.get("title") or ""
            authors = info.get("authors") or []
            author_str = ", ".join(authors) if authors else ""
            categories = info.get("categories") or []
            description = info.get("description") or ""
            lang = info.get("language") or ""
            published_date = info.get("publishedDate") or ""
            year = _parse_year(published_date)
            # Attempt to parse rating to float
            rating_raw = info.get("averageRating")
            try:
                rating = float(rating_raw) if rating_raw is not None else 0.0
            except Exception:
                rating = 0.0
            images = info.get("imageLinks") or {}
            cover_url = (
                images.get("thumbnail")
                or images.get("smallThumbnail")
                or images.get("medium")
                or images.get("large")
                or ""
            )
            tags = [c.lower() for c in categories]
            books.append(
                Book(
                    id=volume_id,
                    title=title,
                    author=author_str,
                    short_description=description,
                    cover_url=cover_url,
                    categories=categories,
                    tags=tags,
                    language=lang,
                    year=year,
                    rating=rating,
                )
            )
    except Exception:
        # On network or parsing errors, return empty list
        return [], 0
    # Note: sorting and filtering by category/tag are performed in router
    return books, total_items


def get_book_google(book_id: str) -> Optional[Book]:
    """Fetch a single book by its Google volume ID.

    Parameters
    ----------
    book_id : str
        The Google Books volume ID.

    Returns
    -------
    Optional[Book]
        A Book instance if the volume is found; otherwise ``None``.
    """
    params = {}
    key = os.environ.get("GOOGLE_BOOKS_API_KEY")
    if key:
        params["key"] = key
    try:
        resp = requests.get(
            f"https://www.googleapis.com/books/v1/volumes/{book_id}", params=params, timeout=10
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        info = data.get("volumeInfo", {}) or {}
        if not info:
            return None
        title = info.get("title") or ""
        authors = info.get("authors") or []
        author_str = ", ".join(authors) if authors else ""
        categories = info.get("categories") or []
        description = info.get("description") or ""
        lang = info.get("language") or ""
        published_date = info.get("publishedDate") or ""
        year = _parse_year(published_date)
        rating_raw = info.get("averageRating")
        try:
            rating = float(rating_raw) if rating_raw is not None else 0.0
        except Exception:
            rating = 0.0
        images = info.get("imageLinks") or {}
        cover_url = (
            images.get("thumbnail")
            or images.get("smallThumbnail")
            or images.get("medium")
            or images.get("large")
            or ""
        )
        tags = [c.lower() for c in categories]
        return Book(
            id=book_id,
            title=title,
            author=author_str,
            short_description=description,
            cover_url=cover_url,
            categories=categories,
            tags=tags,
            language=lang,
            year=year,
            rating=rating,
        )
    except Exception:
        return None