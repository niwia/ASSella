import math
import re
from typing import Any, Dict, List, Optional

_BLACKLIST_PATTERNS = [
    r"soundtracks?",
    r"sound tracks?",
    r"ost",
    r"original soundtrack",
    r"piano collections?",
    r"orchestras?",
    r"orchestral",
    r"world tour",
    r"concerts?",
    r"videos?",
    r"artbooks?",
    r"graphic novels?",
    r"dlcs?",
    r"demos?",
    r"dedicated server",
    r"servers?",
    r"tools?",
    r"sdks?",
    r"3d print model",
    r"wallpapers?",
    r"digital contents?",
    r"mod organizer",
    r"ultimate collections?",
    r"seekers edition",
    r"trailers?",
    r"shorts?",
    r"teasers?",
    r"the final hours",
    r"season pass(?:es)?",
    r"content packs?",
    r"free editions?",
    r"upgrades?",
    r"mini soundtrack",
    r"extra tracks?",
    r"trial versions?",
    r"beta(?:\s+test)?",
    r"benchmarks?",
]
_BLACKLIST_NAME_RE = re.compile(r"\b(?:" + "|".join(_BLACKLIST_PATTERNS) + r")\b", re.IGNORECASE)

_BLACKLIST_META_TOKENS = {
    "dlc",
    "demo",
    "soundtrack",
    "music",
    "video",
    "ost",
    "piano",
    "orchestra",
    "tool",
    "server",
    "sdk",
    "artbook",
    "graphic novel",
    "trailer",
    "short",
    "teaser",
    "season pass",
    "content pack",
    "free edition",
    "upgrade",
    "trial",
    "beta",
    "benchmark",
    "extra tracks",
}

_RESULT_NAME_KEYS = ("game_name", "name", "title")
_RESULT_ID_KEYS = ("game_id", "appid", "app_id", "id")
_RESULT_FLAG_KEYS = ("is_dlc", "is_demo", "is_tool", "is_server", "is_soundtrack")
_RESULT_META_KEYS = ("type", "app_type", "content_type", "category", "kind")
_RESULT_META_LIST_KEYS = ("tags", "genres", "categories", "types")

_LANGUAGE_VARIANT_RE = re.compile(
    r"\((english|french|german|russian|spanish|italian|japanese|korean|portuguese|polish|turkish|chinese)\)$",
    re.IGNORECASE,
)


def normalize_for_match(value: str) -> str:
    """Normalize string for token-based fuzzy matching."""
    lowered = (value or "").strip().lower()
    if not lowered:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def extract_game_name(game: Dict[str, Any]) -> str:
    """Extract game display name from a raw API result dictionary."""
    if not isinstance(game, dict):
        return ""
    for key in _RESULT_NAME_KEYS:
        value = game.get(key)
        if isinstance(value, str):
            name = value.strip()
            if name:
                return name
    return ""


def extract_app_id(game: Dict[str, Any]) -> str:
    """Extract AppID from a raw API result dictionary."""
    if not isinstance(game, dict):
        return ""
    for key in _RESULT_ID_KEYS:
        value = game.get(key)
        if value is None:
            continue
        app_id = str(value).strip()
        if app_id:
            return app_id
    return ""


def manifest_size(game: Dict[str, Any]) -> int:
    """Return manifest size as an integer, defaulting to 0."""
    if not isinstance(game, dict):
        return 0
    try:
        return int(game.get("manifest_size") or 0)
    except (TypeError, ValueError):
        return 0


def is_blacklisted(game_name: str) -> bool:
    """Checks if a game name contains blacklisted keywords."""
    return bool(_BLACKLIST_NAME_RE.search((game_name or "").lower()))


def is_blacklisted_result(game: Dict[str, Any]) -> bool:
    """Checks if a game result dictionary represents a blacklisted item (DLC, soundtrack, demo, etc.)."""
    if not isinstance(game, dict):
        return False

    for flag_key in _RESULT_FLAG_KEYS:
        if bool(game.get(flag_key)):
            return True

    meta_parts = []
    for key in _RESULT_META_KEYS:
        value = game.get(key)
        if isinstance(value, str) and value:
            meta_parts.append(value.lower())

    for key in _RESULT_META_LIST_KEYS:
        value = game.get(key)
        if isinstance(value, str):
            meta_parts.append(value.lower())
        elif isinstance(value, (list, tuple, set)):
            meta_parts.extend(
                str(item).lower() for item in value if item is not None
            )

    meta_text = " ".join(meta_parts)
    if meta_text and any(token in meta_text for token in _BLACKLIST_META_TOKENS):
        return True

    return is_blacklisted(extract_game_name(game))


def is_likely_media_variant(game: Dict[str, Any]) -> bool:
    """Checks if result is likely a small media trailer or language variant."""
    name = extract_game_name(game).lower()
    if not name:
        return False

    size = manifest_size(game)
    if size <= 0:
        return False

    if size <= 5000 and _LANGUAGE_VARIANT_RE.search(name):
        return True

    if size <= 2000 and any(
        token in name
        for token in ("trailer", "short", "teaser", "turrets", "behind the scenes")
    ):
        return True

    return False


def relevance_score(game: Dict[str, Any], query: str) -> int:
    """Computes a numeric relevance score for ranking game search results against a query."""
    name = extract_game_name(game)
    if not name:
        return -100000

    normalized_name = normalize_for_match(name)
    normalized_query = normalize_for_match(query)
    if not normalized_query:
        return 0

    score = 0
    if normalized_name == normalized_query:
        score += 5000
    if normalized_name.startswith(normalized_query):
        score += 2500
    if f" {normalized_query} " in f" {normalized_name} ":
        score += 1500

    name_tokens = normalized_name.split()
    query_tokens = normalized_query.split()
    token_hits = 0
    prefix_hits = 0
    for token in query_tokens:
        if token in name_tokens:
            token_hits += 1
        elif any(part.startswith(token) for part in name_tokens):
            prefix_hits += 1

    score += token_hits * 350
    score += prefix_hits * 120
    score -= min(700, max(0, len(normalized_name) - len(normalized_query)) * 5)

    size = manifest_size(game)
    if size > 0:
        score += min(120, int(math.log10(size + 1) * 20))

    return score


def relevance_sort_key(game: Dict[str, Any], query: str) -> tuple:
    """Generates a sort key (-score, lowercase name) for sorting search results by relevance."""
    score = relevance_score(game, query)
    name = extract_game_name(game).lower()
    return -score, name


def dedupe_results_by_name(games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate search result list keeping the first (highest-ranked) occurrence of each normalized name."""
    deduped = []
    seen_names = set()
    for game in games:
        normalized_name = normalize_for_match(extract_game_name(game))
        if not normalized_name or normalized_name in seen_names:
            continue
        seen_names.add(normalized_name)
        deduped.append(game)
    return deduped


def filter_and_rank_results(
    game_results: List[Dict[str, Any]],
    query: str,
    filter_blacklist: bool = False,
) -> Dict[str, Any]:
    """Filters, scores, ranks, and deduplicates raw search results."""
    if not isinstance(game_results, list):
        return {"results": [], "raw_total": 0}

    filtered = [
        g
        for g in game_results
        if isinstance(g, dict)
        and (not filter_blacklist or not is_blacklisted_result(g))
        and g.get("manifest_available", True) is not False
    ]

    ranked = sorted(filtered, key=lambda g: relevance_sort_key(g, query))
    deduped = dedupe_results_by_name(ranked)
    return {"results": deduped, "raw_total": len(game_results)}
