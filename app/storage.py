# app/storage.py
from typing import List, Dict, Any, Optional
from .models import Book, CreateBookRequest


BOOKS: List[Book] = []
SECTIONS: List[Dict[str, Any]] = []  # dict: id, book_id, text, embedding
_next_book_id = 1
_next_section_id = 1


def add_book(req: CreateBookRequest) -> Book:
    global _next_book_id, _next_section_id

    total_len = sum(len(s) for s in req.sections)
    if total_len > 20_000:
        raise ValueError(
            "Les sections sont trop longues. Fournis des résumés synthétiques, "
            "pas le texte complet du livre."
        )

    book = Book(
        id=_next_book_id,
        title=req.title,
        author=req.author,
        year=req.year,
        genre=req.genre,
        description=req.description,
    )
    BOOKS.append(book)

    for text in req.sections:
        SECTIONS.append(
            {
                "id": _next_section_id,
                "book_id": book.id,
                "text": text,
                "embedding": None,
            }
        )
        _next_section_id += 1

    _next_book_id += 1
    return book


def list_books() -> List[Book]:
    return BOOKS


def get_book(book_id: int) -> Optional[Book]:
    return next((b for b in BOOKS if b.id == book_id), None)


def get_sections_for_book(book_id: int) -> List[Dict[str, Any]]:
    return [s for s in SECTIONS if s["book_id"] == book_id]
