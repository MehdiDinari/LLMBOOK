"""
Route definitions for the catalogue API.

Endpoints under /api/catalog:
- GET  /books            : list books with filters (Google + fallback local)
- GET  /books/{book_id}  : get one book (Google + fallback local)
- GET  /debug/local      : debug local fallback dataset
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Body
from typing_extensions import Literal  # Py3.8 compatibility

from .schemas import Book, PaginatedBooks
from .store import search_books_google, get_book_google, BOOKS

# Additional imports for favourites persistence
import json
from pathlib import Path
import threading
from typing import List, Dict

# Map of language aliases to support loose matching.
LANG_ALIASES = {
    "fr": {"fr", "fra", "fre", "fr-fr", "fr_ca", "fr-ca"},
    "en": {"en", "eng", "en-us", "en_us", "en-gb", "en_gb"},
    "ar": {"ar", "ara", "ar-sa", "ar_sa", "ar-ma", "ar_ma"},
}


def _normalize(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _lang_matches(book_lang: Optional[str], requested: Optional[str]) -> bool:
    if not requested:
        return True
    bl = (book_lang or "").strip().lower()
    if not bl:
        return True
    req = requested.strip().lower()
    aliases = LANG_ALIASES.get(req, {req})
    for alias in aliases:
        if bl == alias or bl.startswith(alias) or alias in bl:
            return True
    return False


SortField = Literal["relevance", "title", "author", "year", "rating"]

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

# ---------------------------------------------------------------------------
# Favourites management
#
# Favourites are stored per-user in a simple JSON file on disk.  The
# file ``app/data/favorites.json`` contains a mapping from user IDs
# (strings) to lists of book IDs.  To avoid race conditions in a
# concurrent environment, all read/write operations on this file are
# synchronised with a threading.Lock.  When the file does not exist or
# cannot be read, an empty dictionary is returned.  Clients interact
# with these favourites via the endpoints defined below.

# Path to the favourites data file
FAV_FILE = Path(__file__).resolve().parents[2] / "data" / "favorites.json"
# Lock to synchronise access to the favourites file
_fav_lock = threading.Lock()

def _load_favorites() -> Dict[str, List[str]]:
    """Load the favourites mapping from disk.

    Returns
    -------
    Dict[str, List[str]]
        A mapping of user IDs to lists of book IDs.  If the file is
        missing or malformed, an empty dict is returned.
    """
    try:
        if FAV_FILE.exists():
            with FAV_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {
                        str(k): [str(bid) for bid in v] if isinstance(v, list) else []
                        for k, v in data.items()
                    }
    except Exception:
        pass
    return {}

def _save_favorites(data: Dict[str, List[str]]) -> None:
    """Persist the favourites mapping to disk.

    Parameters
    ----------
    data : Dict[str, List[str]]
        The mapping of user IDs to lists of book IDs to write to the file.
    """
    # Ensure the parent directory exists
    FAV_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _fav_lock:
        try:
            with FAV_FILE.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            # Best-effort: ignore write errors
            pass


@router.get("/books", response_model=PaginatedBooks)
def list_books(
    q: Optional[str] = Query(default=None, description="Recherche texte (titre/auteur)"),
    category: Optional[str] = Query(default=None, description="Filtrer par catégorie"),
    tag: Optional[str] = Query(default=None, description="Filtrer par tag"),
    language: Optional[str] = Query(default=None, description="Filtrer par langue"),
    sort: SortField = Query(default="relevance", description="Tri"),
    page: int = Query(default=1, ge=1, description="Page courante (1-indexée)"),
    page_size: int = Query(default=12, ge=4, le=200, description="Taille de page"),
) -> PaginatedBooks:
    """
    Returns a paginated list of books.

    Important:
    - search_books_google() gère déjà Google + fallback local (si tu as appliqué le patch fallback).
    - On applique ensuite nos filtres locaux + tri.
    - Puis on recalcule total/total_pages après filtres.
    """

    ncat = _normalize(category)
    ntag = _normalize(tag)

    # 1) Fetch (Google OR fallback local)
    books, _ = search_books_google(
        q=q,
        category=category,
        tag=tag,
        language=language,
        sort=sort,
        page=page,
        page_size=page_size,
    )

    # 2) Local filters (category/tag/lang) to be safe
    if ncat:
        books = [b for b in books if any(_normalize(c) == ncat for c in (b.categories or []))]
    if ntag:
        books = [b for b in books if any(_normalize(t) == ntag for t in (b.tags or []))]
    if language:
        books = [b for b in books if _lang_matches(getattr(b, "language", None), language)]

    # 3) Local sorting
    if sort == "title":
        books.sort(key=lambda b: (_normalize(b.title), _normalize(b.author)))
    elif sort == "author":
        books.sort(key=lambda b: (_normalize(b.author), _normalize(b.title)))
    elif sort == "year":
        books.sort(key=lambda b: (b.year or 0), reverse=True)
    elif sort == "rating":
        books.sort(key=lambda b: (b.rating if b.rating is not None else 0.0), reverse=True)

    # 4) Pagination metadata AFTER filters
    total = len(books)
    total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 1
    page = min(max(1, page), total_pages)

    # 5) Slice items for the requested page (important if search_books_google fallback returns more)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = books[start:end]

    return PaginatedBooks(
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        items=page_items,
    )


@router.get("/books/{book_id}", response_model=Book)
def get_book(book_id: str) -> Book:
    book = get_book_google(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    return book


@router.get("/debug/local")
def debug_local():
    """
    Debug endpoint to verify local fallback dataset is loaded.
    Visit: http://127.0.0.1:8000/api/catalog/debug/local
    """
    return {
        "count": len(BOOKS),
        "sample": [
            {"id": b.id, "title": b.title, "language": b.language}
            for b in BOOKS[:5]
        ],
    }


# ---------------------------------------------------------------------------
# Favourite endpoints
#
# These endpoints allow clients to manage per-user favourites.  They
# delegate to ``_load_favorites()`` and ``_save_favorites()`` for
# persistence.  A favourite is simply a book ID stored under a user
# identifier.  The front-end should provide a unique ``user_id``
# derived from the logged-in account (e.g. email or user ID) when
# calling these endpoints.

@router.get("/favorites/{user_id}", response_model=List[Book])
def list_favorites(user_id: str) -> List[Book]:
    """Return the list of favourite books for a given user.

    Parameters
    ----------
    user_id : str
        The user identifier (must match whatever the front-end passes via
        ``window.HB_USER``).

    Returns
    -------
    List[Book]
        A list of books corresponding to the stored favourite IDs.
    """
    data = _load_favorites()
    ids = data.get(str(user_id), [])
    books: List[Book] = []
    for bid in ids:
        try:
            b = get_book_google(bid)
        except Exception:
            b = None
        if b is not None:
            books.append(b)
    return books


@router.post("/favorites/{user_id}")
def add_favorite(user_id: str, book_id: str = Body(..., embed=True)):
    """Add a book to the user's favourites list.

    Parameters
    ----------
    user_id : str
        The user identifier.
    book_id : str
        The identifier of the book to add.
    """
    data = _load_favorites()
    uid = str(user_id)
    ids = data.get(uid, [])
    bid = str(book_id)
    if bid not in ids:
        ids.append(bid)
    data[uid] = ids
    _save_favorites(data)
    return {"status": "ok"}


@router.delete("/favorites/{user_id}/{book_id}")
def remove_favorite(user_id: str, book_id: str):
    """Remove a book from the user's favourites list.

    Parameters
    ----------
    user_id : str
        The user identifier.
    book_id : str
        The identifier of the book to remove.
    """
    data = _load_favorites()
    uid = str(user_id)
    bid = str(book_id)
    ids = data.get(uid, [])
    if bid in ids:
        ids.remove(bid)
        data[uid] = ids
    _save_favorites(data)
    return {"status": "ok"}


@router.delete("/favorites/{user_id}")
def clear_favorites(user_id: str):
    """Clear all favourites for the given user.

    Parameters
    ----------
    user_id : str
        The user identifier.
    """
    data = _load_favorites()
    data[str(user_id)] = []
    _save_favorites(data)
    return {"status": "ok"}
