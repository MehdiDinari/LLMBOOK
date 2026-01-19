"""
Route definitions for the catalogue API.

This module defines a small set of REST endpoints under the ``/api/catalog``
prefix. The endpoints allow clients to retrieve a paginated list of
books with optional text search and simple filters, as well as fetch
details for a single book by its identifier.

The implementation intentionally remains simple and stateless.
Complex business logic such as authentication, advanced search,
sorting by multiple fields or integration with a database can be
layered on later if needed.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from typing_extensions import Literal  # for Py3.8 compatibility

from .schemas import Book, PaginatedBooks
from .store import search_books_google, get_book_google


# Define allowed sort fields; you can extend this list in the future
SortField = Literal["relevance", "title", "author", "year", "rating"]


router = APIRouter(prefix="/api/catalog", tags=["catalog"])


def _normalize(s: Optional[str]) -> str:
    return (s or "").strip().lower()


@router.get("/books", response_model=PaginatedBooks)
def list_books(
    q: Optional[str] = Query(default=None, description="Recherche texte (titre/auteur)"),
    category: Optional[str] = Query(default=None, description="Filtrer par catégorie"),
    tag: Optional[str] = Query(default=None, description="Filtrer par tag"),
    language: Optional[str] = Query(default=None, description="Filtrer par langue"),
    sort: SortField = Query(default="relevance", description="Tri"),
    page: int = Query(default=1, ge=1, description="Page courante (1-indexée)"),
    page_size: int = Query(default=12, ge=4, le=100, description="Taille de page"),
) -> PaginatedBooks:
    """Return a paginated list of books matching optional filters.

    This implementation delegates the search to the Google Books API. The
    ``category`` and ``tag`` filters are translated into ``subject:`` queries
    when calling the API. Sorting and additional filtering is applied locally.
    """
    # Normalize filters for local comparison
    ncat = _normalize(category)
    ntag = _normalize(tag)
    # Fetch books from Google Books
    books, total_items = search_books_google(
        q=q,
        category=category,
        tag=tag,
        language=language,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    # Apply local category and tag filters in case Google API returns broader results
    if ncat:
        books = [b for b in books if any(_normalize(c) == ncat for c in b.categories)]
    if ntag:
        books = [b for b in books if any(_normalize(t) == ntag for t in b.tags)]
    # Apply local sorting
    if sort == "title":
        books.sort(key=lambda b: (_normalize(b.title), _normalize(b.author)))
    elif sort == "author":
        books.sort(key=lambda b: (_normalize(b.author), _normalize(b.title)))
    elif sort == "year":
        books.sort(key=lambda b: (b.year or 0), reverse=True)
    elif sort == "rating":
        books.sort(key=lambda b: b.rating, reverse=True)
    # Determine pagination metadata
    total = total_items if total_items else len(books)
    total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 1
    # Clamp page number within range
    if page > total_pages:
        page = total_pages
    return PaginatedBooks(
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        items=books,
    )


@router.get("/books/{book_id}", response_model=Book)
def get_book(book_id: str) -> Book:
    """Return a single book by its identifier.

    This endpoint fetches the book from the Google Books API. If the
    volume is not found, a 404 error is raised.
    """
    book = get_book_google(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    return book