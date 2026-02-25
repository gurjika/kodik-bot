import json
import logging
from pathlib import Path
from config import get_settings

logger = logging.getLogger(__name__)

_ENTRIES: list[dict] = []


def _load_kb() -> None:
    global _ENTRIES
    path = Path(get_settings().KB_PATH)
    if not path.exists():
        logger.warning("Knowledge base file not found at %s", path)
        _ENTRIES = []
        return
    with path.open(encoding="utf-8") as f:
        _ENTRIES = json.load(f)
    logger.info("Knowledge base loaded: %d entries", len(_ENTRIES))


# Load at import time — shared read-only across all async workers (safe, no locks needed)
_load_kb()


def _score(entry: dict, tokens: set[str]) -> int:
    """
    Score an entry by counting how many query tokens appear anywhere
    in the section heading or documentation text.
    All comparisons are lowercase.
    """
    haystack = (
        entry.get("section", "").lower()
        + " "
        + entry.get("text", "").lower()
    )
    return sum(1 for t in tokens if t in haystack)


def search_kb(query: str, top_k: int = 3) -> str:
    """
    Search the knowledge base for entries relevant to *query*.
    Returns a formatted string with the top_k results, or a
    'not found' message if nothing scored above zero.

    This is intentionally synchronous — it's CPU-bound and
    completes in microseconds, so no async overhead needed.
    """
    if not _ENTRIES:
        return "Knowledge base is empty."

    tokens = {t.lower() for t in query.split() if len(t) > 2}
    if not tokens:
        return "Query too short to search."

    scored = [(entry, _score(entry, tokens)) for entry in _ENTRIES]
    scored.sort(key=lambda x: x[1], reverse=True)
    top = [(e, s) for e, s in scored[:top_k] if s > 0]

    if not top:
        return "No relevant information found in the knowledge base."

    parts = []
    for entry, _ in top:
        parts.append(f"**{entry['section']}**\n{entry['text']}")

    return "\n\n---\n\n".join(parts)
