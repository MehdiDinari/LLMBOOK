"""
Main application file for the book chatbot API.

This module defines a simple web service using FastAPI that exposes
endpoints for searching books, retrieving details about a single book
and chatting with an assistant about a chosen book. It relies on
Open Library's public APIs to fetch book data and does not require
authentication or API keys.

The service also serves a minimal HTML front-end that allows users to
search for a book and then ask questions in natural language. The
chatbot is intentionally simple: it looks for keywords in the user's
message and responds with information pulled from the book’s
metadata (title, description, authors, page count, etc.). If the
question cannot be answered, the bot falls back to the book
description or a generic apology message.

To run the application locally, install dependencies from
``requirements.txt`` and then execute:
This will start a development server on ``http://localhost:8000``. Open
the root URL in a browser to use the chat interface.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
import os
from pathlib import Path
from typing import Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

app = FastAPI(title="Book Chatbot API")

# Enable CORS so that the front-end can call the API from the same origin
# NOTE: for token-based sessions (WordPress/JS), allow_credentials can be False.
# If you use cookies later, you can switch it back to True + a specific origin list.
app.add_middleware(
    CORSMIDDLEWARE := CORSMiddleware,  # keep import usage explicit
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# Include the catalogue API
#
# The catalogue API lives in ``app/catalog`` and exposes endpoints under
# ``/api/catalog``. We import and register its router here so that the
# catalogue endpoints coexist alongside the chatbot endpoints. By wrapping
# the import in a try/except, we ensure that the chatbot still functions
# even if the catalogue package is missing or fails to import.
try:
    from .catalog import catalog_router  # type: ignore

    app.include_router(catalog_router)
except Exception as e:  # pragma: no cover - do not fail on import
    logger.warning("Catalogue API could not be loaded: %s", e)

# Determine the base path of the app directory. ``__file__`` points to
# this file (``main.py``); ``Path(__file__).resolve().parent`` returns
# the ``app`` directory. Static assets and templates live in
# ``app/static`` and ``app/templates`` relative to this location.
BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "data" / "sample_books.json"

# Mount static assets and templates
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ----------------------------
# TEST session store (in-memory)
# token -> {work_id: [ {role, content, ts} ]}
# ----------------------------
CHAT_STORE: Dict[str, Dict[str, List[Dict[str, object]]]] = {}

# Optional API key for external clients (e.g. WordPress integration).
# When set, the API key can be passed as the ``token`` parameter to
# the /history, /reset and /chat endpoints. If the provided token
# equals ``BOOKGPT_API_KEY``, a new chat session will be automatically
# created on demand. Leaving this unset (the default) means that any
# unknown token will result in a new per-user session as well.
API_KEY = os.environ.get("BOOKGPT_API_KEY")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Serve the main chat interface.

    Parameters
    ----------
    request: Request
        The request context needed by Jinja templates.

    Returns
    -------
    HTMLResponse
        Rendered HTML page for the chat interface.
    """
    return templates.TemplateResponse("index.html", {"request": request})


# ----------------------------
# Session endpoints (required by your JS)
# ----------------------------
@app.post("/session")
def create_session() -> Dict[str, str]:
    """Create a per-user session token (TEST)."""
    token = uuid.uuid4().hex
    CHAT_STORE[token] = {}
    return {"token": token}


@app.get("/history")
def get_history(work_id: str, token: str) -> Dict[str, object]:
    """Return the per-user chat history for a given book.

    Historically, this endpoint only accepted tokens previously issued via
    the `/session` endpoint. In practice, external clients such as a
    WordPress plugin may pass a single API key in lieu of a per-session
    token. To make the service more resilient, we no longer reject
    unknown tokens. If a token is not found in the in-memory store, a
    new session is created automatically. If the ``BOOKGPT_API_KEY``
    environment variable is set and the provided token matches it, a
    session is created on demand as well. Otherwise, the provided
    token becomes the key under which chat history is stored.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Invalid token")
    # If we don't know this token yet, create a new session mapping
    if token not in CHAT_STORE:
        # Only validate against API_KEY if it is set; otherwise accept any
        # token to bootstrap a session.
        if API_KEY and token != API_KEY:
            # Accept unknown tokens by initialising an empty history
            CHAT_STORE[token] = {}
        else:
            CHAT_STORE[token] = {}
    return {"work_id": work_id, "messages": CHAT_STORE[token].get(work_id, [])}


@app.post("/reset")
def reset_history(payload: Dict[str, str]) -> Dict[str, str]:
    """Reset the per-user chat history for a given book.

    When the supplied token is unknown, a new chat session is created on
    the fly rather than raising an Unauthorized error. This makes the
    endpoint compatible with stateless clients that do not first call
    ``/session`` to obtain a token. The ``work_id`` parameter is still
    required and a 400 error is returned if it is missing.
    """
    token = payload.get("token")
    work_id = payload.get("work_id")
    if not token:
        raise HTTPException(status_code=401, detail="Invalid token")
    if not work_id:
        raise HTTPException(status_code=400, detail="work_id required")
    # Create a new session if necessary
    if token not in CHAT_STORE:
        CHAT_STORE[token] = {}
    CHAT_STORE[token][work_id] = []
    return {"status": "ok"}


@app.get("/search")
def search_books(query: str, limit: int = 5) -> Dict[str, List[Dict[str, Optional[str]]]]:
    """Search for books using the Open Library search API.

    The endpoint accepts a search query and optional limit. It forwards
    the query to ``https://openlibrary.org/search.json`` with
    corresponding parameters. The Open Library API returns a JSON
    object containing a list of works in the ``docs`` field. For each
    work we extract a few pieces of information: the title, the first
    author's name and the internal work ID (e.g. ``OL27448W``). These
    values are returned to the caller.

    Parameters
    ----------
    query: str
        Free-text query string.
    limit: int, optional
        Maximum number of results to return (default 5).

    Returns
    -------
    Dict[str, List[Dict[str, Optional[str]]]]
        A dictionary with a ``results`` key mapping to a list of
        dictionaries with ``title``, ``author`` and ``work_id`` keys.
    """
    logger.info("Searching for books with query '%s' and limit %d", query, limit)
    url = "https://openlibrary.org/search.json"
    params = {"q": query, "limit": limit}

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        results: List[Dict[str, Optional[str]]] = []

        for doc in payload.get("docs", [])[:limit]:
            title = doc.get("title")
            authors = doc.get("author_name") or []
            work_key = doc.get("key", "")
            work_id = work_key.split("/")[-1] if work_key else None
            results.append(
                {
                    "title": title,
                    "author": authors[0] if authors else None,
                    "work_id": work_id,
                }
            )
        return {"results": results}

    except requests.RequestException as exc:
        # If the remote API is unreachable (e.g., due to network restrictions),
        # fall back to searching the local sample dataset.
        logger.warning("Remote search failed (%s). Falling back to sample data.", exc)
        sample_results: List[Dict[str, Optional[str]]] = []
        try:
            with DATA_FILE.open("r", encoding="utf-8") as f:
                books = json.load(f)
            query_lower = query.lower()
            for book in books:
                title = (book.get("title") or "")
                author = (book.get("author") or "")
                if (query_lower in title.lower()) or (query_lower in author.lower()):
                    sample_results.append(
                        {
                            "title": title,
                            "author": author or None,
                            "work_id": book.get("work_id"),
                        }
                    )
                    if len(sample_results) >= limit:
                        break
        except Exception as e:
            logger.error("Failed to read sample_books.json: %s", e)
        return {"results": sample_results}


def fetch_work(work_id: str) -> Dict:
    """Fetch information about a work from Open Library.

    Parameters
    ----------
    work_id: str
        The Open Library identifier for the work (e.g., ``OL27448W``).

    Returns
    -------
    Dict
        Parsed JSON response from the Open Library work endpoint.
    """
    url = f"https://openlibrary.org/works/{work_id}.json"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        # Fallback to local dataset when remote call fails
        logger.warning("Failed to fetch work %s remotely (%s); using local sample data.", work_id, exc)
        try:
            with DATA_FILE.open("r", encoding="utf-8") as f:
                books = json.load(f)
            for book in books:
                if book.get("work_id") == work_id:
                    # Convert sample data into a structure resembling Open Library's response.
                    # We also include page_count and characters so that chat_with_book can
                    # provide additional information without making further API calls.
                    authors_list = book.get("authors") or []
                    first_author = authors_list[0] if authors_list else ""
                    return {
                        "title": book.get("title"),
                        "description": book.get("description"),
                        "authors": [{"author": {"key": f"sample_author:{first_author}"}}],
                        "subjects": book.get("subjects", []) or [],
                        "page_count": book.get("page_count"),
                        "characters": book.get("characters", []),
                    }
        except Exception as e:
            logger.error("Failed to read sample_books.json: %s", e)
        raise HTTPException(status_code=503, detail="Failed to fetch book details")


# -----------------------------------------------------------------------------
# Local data helper
#
def get_local_book(work_id: str) -> Optional[Dict]:
    """Retrieve a book record from the local sample dataset if available.

    The sample dataset provides a fallback when network requests to Open Library
    fail or when additional metadata such as page count and characters is
    required. If the given work ID is found in the local data file,
    the corresponding dictionary is returned; otherwise ``None`` is returned.

    Parameters
    ----------
    work_id: str
        The Open Library work ID (e.g., ``OL0000001``).

    Returns
    -------
    Optional[Dict]
        A dictionary containing the book record, or ``None`` if not found.
    """
    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            books = json.load(f)
        for book in books:
            if book.get("work_id") == work_id:
                return book
    except Exception as exc:
        logger.error("Failed to load local books data: %s", exc)
    return None


def fetch_author_name(author_key: str) -> Optional[str]:
    """Fetch an author's name from Open Library using an author key.

    Parameters
    ----------
    author_key: str
        The author key returned from a work (e.g., ``/authors/OL26320A``).

    Returns
    -------
    Optional[str]
        The author's name if available.
    """
    # If the key is encoded by the fallback (sample_author:Name), extract the
    # name directly without performing a network request.
    if author_key.startswith("sample_author:"):
        return author_key.split(":", 1)[1]
    url = f"https://openlibrary.org{author_key}.json"
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        data = res.json()
        return data.get("name")
    except requests.RequestException as exc:
        logger.warning("Failed to fetch author %s: %s", author_key, exc)
        return None


@app.get("/book/{work_id}")
def get_book(work_id: str) -> Dict[str, object]:
    """Return details about a book given its work ID.

    This endpoint retrieves the work metadata from Open Library and
    extracts a few user-friendly fields: title, description,
    subjects and author IDs. If the description is nested as a
    dictionary (some works have ``{"value": "..."}``), the
    ``value`` field is returned.

    Parameters
    ----------
    work_id: str
        The Open Library work identifier.

    Returns
    -------
    Dict[str, Optional[str]]
        A dictionary with book metadata.
    """
    data = fetch_work(work_id)
    description = data.get("description")
    if isinstance(description, dict):
        description = description.get("value")

    authors_keys = []
    for author in data.get("authors", []):
        if isinstance(author, dict) and "author" in author:
            authors_keys.append(author["author"].get("key"))

    subjects = data.get("subjects") or []
    return {
        "title": data.get("title"),
        "description": description,
        "subjects": subjects,
        "authors": authors_keys,
        "page_count": data.get("page_count"),
        "characters": data.get("characters", []),
    }


@app.post("/chat")
def chat_with_book(payload: Dict[str, str]) -> Dict[str, object]:
    """Handle a chat message about a book.

    The caller must supply a JSON object with:
      - ``work_id``
      - ``message``
      - ``token``  (required for per-user history)

    Recognised categories include:
      - summary/description (FR/EN)
      - authors (FR/EN)
      - page count (FR/EN)
      - main characters (FR/EN)
      - themes/subjects (FR/EN)

    Returns
    -------
    Dict[str, object]
        { "answer": str, "messages": [...] }
    """
    token = payload.get("token")
    message = payload.get("message")
    work_id = payload.get("work_id")

    # Automatically create a session if the token is unknown but supplied.
    # This ensures that clients who do not call /session explicitly (e.g.
    # WordPress integrations) can still interact with the chatbot without
    # triggering an Unauthorized error.
    if not token:
        raise HTTPException(status_code=401, detail="Invalid token")
    if token not in CHAT_STORE:
        CHAT_STORE[token] = {}
    if not message or not work_id:
        raise HTTPException(status_code=400, detail="Both 'message' and 'work_id' are required.")

    # Init history for this token/work
    CHAT_STORE[token].setdefault(work_id, [])
    CHAT_STORE[token][work_id].append({"role": "user", "content": message, "ts": time.time()})

    # Load book data from remote or fallback
    data = fetch_work(work_id)
    title: str = data.get("title", "cet ouvrage")

    # Normalize description if it is a dict
    description: Optional[str] = data.get("description")
    if isinstance(description, dict):
        description = description.get("value")

    # Also load local metadata if available (page_count, characters, maybe better description)
    local_book = get_local_book(work_id)
    local_description: Optional[str] = None
    local_page_count: Optional[int] = None
    local_characters: List[str] = []
    local_subjects: List[str] = []

    if local_book:
        local_description = local_book.get("description")
        local_page_count = local_book.get("page_count")
        local_characters = local_book.get("characters", [])
        local_subjects = local_book.get("subjects", []) or []

    # Lowercase message for keyword matching
    lower_msg = message.lower()
    answer = ""

    # Simple language detection (FR vs EN)
    lang = "en" if any(w in lower_msg for w in ["summary", "author", "characters", "who", "what", "theme"]) else "fr"

    # Helpers
    def trim(text: str, max_len: int = 500) -> str:
        t = (text or "").strip()
        if len(t) <= max_len:
            return t
        t = t[:max_len]
        if " " in t:
            t = t.rsplit(" ", 1)[0]
        return t + "…"

    # Respond to simple greetings to make the chatbot feel more natural
    if any(term in lower_msg for term in ["bonjour", "salut", "coucou", "hey", "hello", "hi"]):
        if lang == "en":
            answer = "Hello! I'm your book assistant. Feel free to ask me anything about the book."
        else:
            answer = "Bonjour ! Je suis votre assistant spécialisé sur les livres. N'hésitez pas à me poser des questions sur l'ouvrage."

    # Acknowledge thanks
    elif any(term in lower_msg for term in ["merci", "thank you", "thanks"]):
        answer = "You're welcome!" if lang == "en" else "Avec plaisir !"

    # Introduce the assistant when asked
    elif any(term in lower_msg for term in ["qui es-tu", "qui êtes-vous", "who are you", "what are you"]):
        answer = (
            "I'm a virtual assistant designed to answer questions about books. Ask me about the author, characters, themes or summaries!"
            if lang == "en"
            else "Je suis un assistant virtuel conçu pour répondre à vos questions sur les livres. Demandez-moi des informations sur l'auteur, les personnages, les thèmes ou un résumé !"
        )

    # Explain capabilities when asked what the assistant can do
    elif any(term in lower_msg for term in ["que peux-tu faire", "qu'est-ce que tu fais", "what can you do", "what do you do"]):
        answer = (
            "I can help you explore details about a book's summary, authors, characters, themes and more."
            if lang == "en"
            else "Je peux vous aider à explorer les détails d'un livre : résumé, auteurs, personnages, thèmes et bien d'autres."
        )

    # Determine if user asks for publication date
    elif any(term in lower_msg for term in ["publié", "publication", "published", "publication date", "date de publication", "année de publication", "year"]):
        pub_date: Optional[str] = None
        # Try to get first publish date from the work data
        pub_date = data.get("first_publish_date") or data.get("created", {}).get("value")
        # Attempt to extract year if date is a full datetime string
        year: Optional[str] = None
        if isinstance(pub_date, str) and len(pub_date) >= 4:
            # The date may be in formats like '2001', '2001-05-04T00:00:00.000000'
            year = pub_date[:4]
        if year:
            answer = (
                f"The book {title} was first published in {year}."
                if lang == "en"
                else f"Le livre {title} a été publié pour la première fois en {year}."
            )
        else:
            answer = (
                f"Sorry, I don't have the publication date for {title}."
                if lang == "en"
                else f"Désolé, je n'ai pas la date de publication pour {title}."
            )

    # Determine if user asks for main characters
    elif any(term in lower_msg for term in ["personnage", "personnages", "character", "characters", "main character", "principaux", "principal"]):
        if local_characters:
            if lang == "en":
                answer = f"Main characters in {title}: {', '.join(local_characters)}"
            else:
                answer = f"Personnages principaux de {title} : {', '.join(local_characters)}"
        else:
            answer = f"Sorry, I don't have the main characters for {title}." if lang == "en" else f"Désolé, je n'ai pas les personnages principaux de {title}."

    # Determine if user asks for a summary/description
    elif any(term in lower_msg for term in ["résumé", "resumé", "summary", "description", "synopsis", "resume"]):
        desc = local_description or description
        if desc:
            trimmed = trim(desc, 500)
            answer = f"Summary of {title}: {trimmed}" if lang == "en" else f"Résumé de {title} : {trimmed}"
        else:
            answer = f"Sorry, I can't find a summary for {title}." if lang == "en" else f"Je suis désolé, je ne trouve pas de résumé pour {title}."

    # Determine if user asks about the author(s)
    elif any(term in lower_msg for term in ["auteur", "author", "écrivain", "writer", "written by"]):
        author_keys = [
            auth.get("author", {}).get("key")
            for auth in data.get("authors", [])
            if isinstance(auth, dict) and "author" in auth
        ]
        names: List[str] = []
        for key in author_keys:
            if key:
                name = fetch_author_name(key)
                if name:
                    names.append(name)

        # If no names found via Open Library, use local authors
        if not names and local_book:
            names = local_book.get("authors", [])

        if names:
            answer = f"Author(s) of {title}: {', '.join(names)}" if lang == "en" else f"Auteur(s) de {title} : {', '.join(names)}"
        else:
            answer = f"Sorry, I can't find the author of {title}." if lang == "en" else f"Je suis désolé, je ne trouve pas d'information sur l'auteur de {title}."

    # Determine if user asks about page count
    elif any(t in lower_msg for t in ["page", "pages", "nombre de pages", "how many pages"]):
        pages: Optional[int] = None

        # First try local data
        if local_page_count:
            pages = local_page_count

        # If not available, fetch edition info from remote API
        if pages is None:
            try:
                editions_resp = requests.get(
                    f"https://openlibrary.org/works/{work_id}/editions.json?limit=1",
                    timeout=10,
                )
                editions_resp.raise_for_status()
                editions_data = editions_resp.json()
                entries = editions_data.get("entries") or editions_data.get("docs") or []
                if entries:
                    edition = entries[0]
                    pages = edition.get("number_of_pages")
            except requests.RequestException:
                pages = None

        if pages:
            answer = f"{title} is about {pages} pages." if lang == "en" else f"Le livre {title} comporte environ {pages} pages."
        else:
            answer = f"Sorry, I don't have the page count for {title}." if lang == "en" else f"Désolé, je ne dispose pas du nombre de pages pour {title}."

    # Themes / subjects
    elif any(t in lower_msg for t in ["thème", "thèmes", "theme", "themes", "sujet", "sujets", "topics"]):
        subjects = (data.get("subjects") or []) or local_subjects
        if subjects:
            top = subjects[:8]
            answer = f"Main themes/topics in {title}: {', '.join(top)}" if lang == "en" else f"Thèmes/sujets principaux de {title} : {', '.join(top)}"
        else:
            answer = f"Sorry, I don't have themes for {title}." if lang == "en" else f"Désolé, je n'ai pas de thèmes pour {title}."

    else:
        # Generic fallback: use local or remote description, truncated
        desc = local_description or description
        if desc:
            answer = trim(desc, 500)
        else:
            answer = f"Sorry, I don't have enough information about {title}." if lang == "en" else f"Désolé, je n'ai pas suffisamment d'informations pour répondre à votre question sur {title}."

    # Save assistant message
    CHAT_STORE[token][work_id].append({"role": "assistant", "content": answer, "ts": time.time()})
    return {"answer": answer, "messages": CHAT_STORE[token][work_id]}
