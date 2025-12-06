# app/main.py
from typing import List

from fastapi import FastAPI, HTTPException

from .models import CreateBookRequest, Book, QARequest, QAAnswer
from . import storage
from . import ai


app = FastAPI(
    title="Chatbot Livre IA (local LLM)",
    description=(
        "Microservice qui répond aux questions sur un livre en utilisant "
        "un LLM open-source (FLAN-T5) et des embeddings locaux, "
        "à partir de résumés dérivés uniquement."
    ),
    version="1.0.0",
)


# 🔹 Route de base pour tester rapidement
@app.get("/")
def health_check():
    return {"status": "ok", "message": "FastAPI + LLM live 🚀"}


@app.get("/books", response_model=List[Book])
def list_books_api():
    return storage.list_books()


@app.post("/books", response_model=Book)
def add_book_api(req: CreateBookRequest):
    try:
        return storage.add_book(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/qa", response_model=QAAnswer)
def qa_api(req: QARequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question vide.")

    target_book, sections = ai.retrieve_relevant_sections(req)
    if not target_book:
        raise HTTPException(status_code=404, detail="Aucun livre disponible.")
    if not sections:
        raise HTTPException(
            status_code=404,
            detail="Aucune section de résumé trouvée pour ce livre.",
        )

    prompt = ai.build_prompt(req.question, target_book.title, sections)
    answer_text = ai.generate_answer(prompt)

    # (pour l’instant, confidence fixe – tu pourras l’améliorer plus tard)
    return QAAnswer(
        answer=answer_text,
        book_id=target_book.id,
        book_title=target_book.title,
        confidence=0.8,
    )
