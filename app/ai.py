# app/ai.py
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM, pipeline

from .models import QARequest
from .storage import get_book, get_sections_for_book, list_books


# === Chargement des modèles (à faire une seule fois) ===
embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

LLM_MODEL_NAME = "google/flan-t5-base"
tokenizer = AutoTokenizer.from_pretrained(LLM_MODEL_NAME)
model = AutoModelForSeq2SeqLM.from_pretrained(LLM_MODEL_NAME)
text2text_pipeline = pipeline("text2text-generation", model=model, tokenizer=tokenizer)


def _compute_embeddings_for_book(book_id: int) -> None:
    sections = get_sections_for_book(book_id)
    to_encode = [s for s in sections if s["embedding"] is None]
    if not to_encode:
        return

    texts = [s["text"] for s in to_encode]
    vectors = embedder.encode(texts, convert_to_numpy=True)
    for s, vec in zip(to_encode, vectors):
        s["embedding"] = vec


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def retrieve_relevant_sections(req: QARequest) -> Tuple[Optional[Any], List[Dict[str, Any]]]:
    """Retourne (livre ciblé, sections pertinentes)."""

    books = list_books()
    if not books:
        return None, []

    # choisir le livre
    target = None
    if req.book_id is not None:
        target = get_book(req.book_id)
    elif req.title_hint:
        hint_lower = req.title_hint.lower()
        for b in books:
            if hint_lower in b.title.lower():
                target = b
                break
    else:
        target = books[0]

    if not target:
        return None, []

    _compute_embeddings_for_book(target.id)

    sections = get_sections_for_book(target.id)
    valid_sections = [s for s in sections if s["embedding"] is not None]
    if not valid_sections:
        return target, []

    q_vec = embedder.encode([req.question], convert_to_numpy=True)[0]

    sims = [(s, _cosine_similarity(q_vec, s["embedding"])) for s in valid_sections]
    sims.sort(key=lambda x: x[1], reverse=True)

    k = max(1, min(req.top_k, len(sims)))
    selected = [item[0] for item in sims[:k]]
    return target, selected


def build_prompt(question: str, book_title: str, sections: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for s in sections:
        txt = s["text"]
        if len(txt) > 600:
            txt = txt[:600] + "..."
        parts.append(txt)

    context = "\n\n---\n\n".join(parts)

    return (
        "Tu es un assistant littéraire. Tu n'as accès qu'à des résumés dérivés "
        "d'un livre (pas au texte original). Tu dois respecter le droit d'auteur : "
        "ne copie jamais de longs passages textuels et ne tente pas de reconstituer "
        "le texte original.\n\n"
        f"Livre : {book_title}\n\n"
        f"Contexte (résumés dérivés) :\n{context}\n\n"
        f"Question : {question}\n\n"
        "Réponds en français, de façon claire et concise (5 à 10 lignes)."
    )


def generate_answer(prompt: str) -> str:
    result = text2text_pipeline(prompt, max_new_tokens=256)[0]["generated_text"]
    return result
