"""
Open Library integration for the catalogue.  This module replaces the
previous Google Books dependency with free, anonymous requests to
Open Library.  It exposes two primary functions:

* ``search_books_openlibrary()`` — search for books matching a query
  and optional filters such as category, tag, language, page and
  page_size.  Results are mapped into the ``Book`` schema.

* ``get_book_openlibrary()`` — retrieve detailed information about a
  single work by its Open Library identifier.

Both functions implement simple in-memory caches to avoid repeated
requests for the same queries or works.  Only the Python standard
library is used for HTTP requests.  If Open Library is unreachable or
returns no results, the calling code can fall back to local sample
data via ``_search_books_local`` in ``store.py``.

The code here is a lightly adapted version of the generic
``openlibrary_backend`` previously provided; it has been adjusted to
integrate with this FastAPI project and its schemas.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from .schemas import Book


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _http_get_json(url: str) -> Optional[dict]:
    """Perform an HTTP GET and return parsed JSON or ``None`` on failure.

    A custom User-Agent and Accept header are provided to avoid 403
    responses from Open Library.  Network errors are logged and
    ``None`` is returned.
    """
    try:
        request = urllib.request.Request(
            url,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/115.0 Safari/537.36'
                ),
                'Accept': 'application/json',
            },
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status != 200:
                logger.warning(
                    "Open Library request to %s returned status %s", url, response.status
                )
                return None
            data = response.read().decode('utf-8', errors='ignore')
            return json.loads(data)
    except Exception as exc:
        logger.error("Error fetching %s: %s", url, exc)
        return None


# Caches for search queries, works and authors
_search_cache: Dict[str, List[Book]] = {}
_work_cache: Dict[str, Book] = {}
_author_cache: Dict[str, str] = {}


def _convert_language(code: str) -> str:
    """Convert a three-letter ISO 639‑2 code to a two-letter code.

    If the provided code is unknown, the first two characters are
    returned.  When no code is provided, ``'fr'`` is returned.
    """
    if not code:
        return 'fr'
    code = code.lower()
    if len(code) == 2:
        return code
    mapping = {
        'eng': 'en', 'fre': 'fr', 'fra': 'fr', 'spa': 'es', 'ita': 'it',
        'por': 'pt', 'ger': 'de', 'deu': 'de', 'rus': 'ru', 'jpn': 'ja',
        'chi': 'zh', 'zho': 'zh', 'kor': 'ko', 'tur': 'tr', 'ara': 'ar',
        'hin': 'hi', 'urd': 'ur', 'per': 'fa', 'fas': 'fa', 'pes': 'fa',
        'dan': 'da', 'nor': 'no', 'nob': 'no', 'fin': 'fi', 'swe': 'sv',
        'nep': 'ne', 'bul': 'bg', 'rum': 'ro', 'ron': 'ro', 'ukr': 'uk',
        'vie': 'vi', 'cat': 'ca', 'lat': 'la', 'heb': 'he', 'gre': 'el',
        'ell': 'el', 'gla': 'gd', 'yid': 'yi', 'lit': 'lt', 'lav': 'lv',
        'hun': 'hu', 'ice': 'is', 'isl': 'is', 'hrv': 'hr', 'gle': 'ga',
        'afr': 'af', 'dut': 'nl', 'nld': 'nl', 'pol': 'pl', 'cze': 'cs',
        'ces': 'cs', 'alb': 'sq', 'ben': 'bn', 'tam': 'ta', 'tel': 'te',
        'mar': 'mr', 'tha': 'th', 'tib': 'bo', 'grc': 'el', 'kal': 'kl',
        'ltz': 'lb', 'wel': 'cy', 'cym': 'cy',
    }
    return mapping.get(code, code[:2])


def _generate_tags(title: str, subjects: List[str]) -> List[str]:
    """Create a list of tags from the title and subject list.

    Tags are lowercased, punctuation removed, and duplicates removed
    while preserving order.  Common stopwords are excluded.  This
    heuristic helps create useful tags for the front‑end.
    """
    import re as _re

    stopwords = {
        'the', 'and', 'of', 'a', 'an', 'for', 'in', 'on', 'to', 'from',
        'with', 'without', 'into', 'by', 'le', 'la', 'les', 'un', 'une',
        'des', 'et', 'dans', 'en', 'du', 'de', 'der', 'die', 'das', 'und',
        'el', 'los', 'las', 'y', 'del', 'por'
    }

    def tokenize(text: str) -> List[str]:
        cleaned = _re.sub(r'[^\w]+', ' ', text.lower())
        return [w for w in cleaned.split() if w and w not in stopwords]

    tokens: List[str] = []
    seen = set()
    # Title tokens
    for token in tokenize(title):
        if token not in seen:
            seen.add(token)
            tokens.append(token)
    # Subject tokens
    for subj in subjects:
        for token in tokenize(subj):
            if token not in seen:
                seen.add(token)
                tokens.append(token)
    return tokens


def _build_cover_url(cover_id: Optional[int], cover_edition_key: Optional[str]) -> Optional[str]:
    """Construct a cover URL from either a numeric ID or an edition key."""
    if cover_id:
        return f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
    if cover_edition_key:
        return f"https://covers.openlibrary.org/b/olid/{cover_edition_key}-L.jpg"
    return None


def _get_author_name(author_key: str) -> Optional[str]:
    """Resolve an author key to a display name using the Open Library API."""
    key = author_key.strip()
    if not key:
        return None
    if key.startswith('/'):
        parts = key.strip('/').split('/')
        if len(parts) == 2:
            key = parts[1]
    # Cache lookup
    if key in _author_cache:
        return _author_cache[key]
    url = f"https://openlibrary.org/authors/{urllib.parse.quote(key)}.json"
    data = _http_get_json(url)
    if data and 'name' in data:
        _author_cache[key] = data['name']
        return data['name']
    return None


def search_books_openlibrary(
    q: Optional[str] = None,
    category: Optional[str] = None,
    tag: Optional[str] = None,
    language: Optional[str] = None,
    sort: str = "relevance",
    page: int = 1,
    page_size: int = 12,
) -> List[Book]:
    """Search Open Library for books.

    Only the first ``page_size`` results for the specified page are
    returned.  Additional sorting and filtering is performed locally in
    ``store.py``.  When Open Library returns no results, an empty list
    is returned and callers can fall back to local data.
    """
    # Compose a cache key including all parameters
    cache_key = f"{q or ''}|{category or ''}|{tag or ''}|{language or ''}|{page}|{page_size}"
    if cache_key in _search_cache:
        return _search_cache[cache_key]
    # Build query string.  Subjects are specified via 'subject:' terms.
    terms: List[str] = []
    if q:
        terms.append(q)
    if category:
        terms.append(f"subject:{category}")
    if tag:
        terms.append(f"subject:{tag}")
    query = " ".join(terms)
    # Build URL with pagination
    params = {
        'q': query,
        'limit': max(1, int(page_size)),
        'page': max(1, int(page)),
    }
    if language:
        params['lang'] = language
    url = f"https://openlibrary.org/search.json?{urllib.parse.urlencode(params)}"
    data = _http_get_json(url)
    books: List[Book] = []
    if not data or 'docs' not in data:
        _search_cache[cache_key] = books
        return books
    docs = data.get('docs') or []
    for doc in docs:
        key = doc.get('key')
        if not key or not isinstance(key, str):
            continue
        work_id = key.split('/')[-1]
        title = doc.get('title') or doc.get('title_suggest') or ''
        authors = doc.get('author_name') or []
        author_str = ", ".join(authors) if authors else ''
        cover_id = doc.get('cover_i')
        cover_edition_key = doc.get('cover_edition_key')
        cover_url = _build_cover_url(cover_id, cover_edition_key) or ''
        # Subjects may be under 'subject' or 'subject_facet'
        subjects_raw = doc.get('subject') or doc.get('subject_facet') or []
        if isinstance(subjects_raw, str):
            subjects_list = [subjects_raw]
        else:
            subjects_list = [s for s in subjects_raw if isinstance(s, str)]
        tags = _generate_tags(title, subjects_list)
        # Language conversion
        lang = 'fr'
        codes = doc.get('language') or []
        if codes:
            lang = _convert_language(codes[0])
        # Year
        year_val = doc.get('first_publish_year')
        year = int(year_val) if isinstance(year_val, int) else None
        # Rating (if provided)
        rating = None
        if 'ratings_average' in doc and isinstance(doc['ratings_average'], (int, float)):
            rating = float(doc['ratings_average'])
        elif 'ratings_sortable' in doc and isinstance(doc['ratings_sortable'], (int, float)):
            rating = float(doc['ratings_sortable']) / 100 if doc['ratings_sortable'] else None
        ratings_count = 0
        if 'ratings_count' in doc and isinstance(doc['ratings_count'], int):
            ratings_count = doc['ratings_count']
        books.append(
            Book(
                id=work_id,
                title=title,
                author=author_str,
                short_description="",
                cover_url=cover_url,
                categories=subjects_list,
                tags=tags,
                language=lang,
                year=year,
                rating=rating,
                ratings_count=ratings_count,
                web_reader_link=None,
            )
        )
    # Cache and return
    _search_cache[cache_key] = books
    return books


def get_book_openlibrary(book_id: str) -> Optional[Book]:
    """Return detailed metadata for a work ID from Open Library."""
    if not book_id:
        return None
    work_id = book_id.strip()
    if '/' in work_id:
        work_id = work_id.split('/')[-1]
    if work_id in _work_cache:
        return _work_cache[work_id]
    url = f"https://openlibrary.org/works/{urllib.parse.quote(work_id)}.json"
    data = _http_get_json(url)
    if not data:
        return None
    title = data.get('title', '')
    # Resolve authors
    author_names: List[str] = []
    for entry in data.get('authors') or []:
        if isinstance(entry, dict):
            ainfo = entry.get('author')
            if ainfo and isinstance(ainfo, dict):
                key = ainfo.get('key')
                name = _get_author_name(key)
                if name:
                    author_names.append(name)
    author_str = ", ".join(author_names) if author_names else ''
    # Description or excerpt
    desc = data.get('description')
    short_description: Optional[str] = None
    if isinstance(desc, str):
        short_description = desc.strip()
    elif isinstance(desc, dict):
        val = desc.get('value')
        if isinstance(val, str):
            short_description = val.strip()
    if not short_description:
        for ex in data.get('excerpts') or []:
            if isinstance(ex, dict) and isinstance(ex.get('excerpt'), str):
                short_description = ex['excerpt'].strip()
                break
    # Cover
    cover_url = None
    covers = data.get('covers') or []
    if covers and isinstance(covers, list):
        cid = covers[0]
        if isinstance(cid, int):
            cover_url = _build_cover_url(cid, None)
    # Categories
    categories = [s for s in data.get('subjects') or [] if isinstance(s, str)]
    tags = _generate_tags(title, categories)
    # Language
    lang = 'fr'
    langs_data = data.get('languages') or []
    if langs_data:
        first_lang = langs_data[0]
        if isinstance(first_lang, dict):
            code = first_lang.get('key')
            if isinstance(code, str):
                lang = _convert_language(code.split('/')[-1])
    # Year
    year: Optional[int] = None
    if 'first_publish_date' in data and isinstance(data['first_publish_date'], str):
        m = re.match(r'\d{4}', data['first_publish_date'])
        if m:
            year = int(m.group())
    elif 'created' in data and isinstance(data['created'], dict):
        val = data['created'].get('value')
        if isinstance(val, str):
            m = re.match(r'\d{4}', val)
            if m:
                year = int(m.group())
    # Ratings
    rating = None
    if 'ratings_average' in data and isinstance(data['ratings_average'], (int, float)):
        rating = float(data['ratings_average'])
    elif 'ratings_sortable' in data and isinstance(data['ratings_sortable'], (int, float)):
        rating = float(data['ratings_sortable']) / 100 if data['ratings_sortable'] else None
    ratings_count = 0
    if 'ratings_count' in data and isinstance(data['ratings_count'], int):
        ratings_count = data['ratings_count']
    book = Book(
        id=work_id,
        title=title,
        author=author_str,
        short_description=short_description or "",
        cover_url=cover_url or "",
        categories=categories,
        tags=tags,
        language=lang,
        year=year,
        rating=rating,
        ratings_count=ratings_count,
        web_reader_link=None,
    )
    _work_cache[work_id] = book
    return book