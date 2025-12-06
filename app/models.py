# app/models.py
from typing import List, Optional
from pydantic import BaseModel, Field


class CreateBookRequest(BaseModel):
    title: str
    author: str
    year: Optional[int] = None
    genre: Optional[str] = None
    description: Optional[str] = None
    sections: List[str] = Field(
        default_factory=list,
        description=(
            "Liste de résumés dérivés (par chapitre, thème, etc.). "
            "Pas de texte intégral protégé."
        ),
    )


class Book(BaseModel):
    id: int
    title: str
    author: str
    year: Optional[int] = None
    genre: Optional[str] = None
    description: Optional[str] = None


class QARequest(BaseModel):
    question: str
    book_id: Optional[int] = None
    title_hint: Optional[str] = None
    top_k: int = 3


class QAAnswer(BaseModel):
    answer: str
    book_id: Optional[int] = None
    book_title: Optional[str] = None
    confidence: float
    source_note: str = (
        "Réponse générée à partir de résumés et de métadonnées, "
        "sans reproduction du texte original protégé par le droit d'auteur."
    )
