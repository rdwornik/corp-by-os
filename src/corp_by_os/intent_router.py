"""Two-stage intent routing: keywords first, LLM fallback.

Stage 1: Match user input against trigger_phrases from workflows.yaml
         + task-specific patterns. Fast, free, deterministic.

Stage 2: If no match, call Gemini Flash with available workflows
         as context. Returns structured Intent.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

from corp_by_os.models import Workflow

logger = logging.getLogger(__name__)


@dataclass
class Intent:
    """Routing result from user input."""

    workflow_id: str | None = None  # None = chitchat/unclear
    parameters: dict = field(default_factory=dict)
    confidence: float = 0.0
    source: str = "none"  # "keyword" | "llm" | "none"
    response_text: str | None = None  # for chitchat/clarification


# --- Product aliases ---

PRODUCT_ALIASES: dict[str, str] = {
    "planning": "Planning",
    "wms": "WMS",
    "tms": "TMS",
    "catman": "CatMan",
    "network": "Network",
    "e2e": "E2E",
    "ibp": "Planning",
    "siop": "Planning",
    "flexis": "Flexis",
    "retail": "Retail",
    "migration": "Migration",
}

# --- Priority keywords ---

PRIORITY_KEYWORDS: dict[str, str] = {
    "pilne": "high",
    "urgent": "high",
    "pilny": "high",
    "asap": "high",
    "wazne": "medium",
    "important": "medium",
}

# --- Archive reason keywords ---

REASON_KEYWORDS: dict[str, str] = {
    "won": "won",
    "wygran": "won",
    "lost": "lost",
    "przegran": "lost",
    "cancelled": "cancelled",
    "anulowa": "cancelled",
    "on_hold": "on_hold",
    "wstrzyman": "on_hold",
    "zamknij": "lost",
}

# --- Polish day names for date resolution ---

_WEEKDAY_MAP: dict[str, int] = {
    "poniedzialek": 0,
    "poniedzialku": 0,
    "wtorek": 1,
    "wtorku": 1,
    "sroda": 2,
    "srode": 2,
    "srody": 2,
    "czwartek": 3,
    "czwartku": 3,
    "piatek": 4,
    "piatku": 4,
    "sobota": 5,
    "soboty": 5,
    "sobote": 5,
    "niedziela": 6,
    "niedzieli": 6,
    "niedziele": 6,
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_POLISH_MONTHS: dict[str, int] = {
    "stycznia": 1,
    "styczen": 1,
    "lutego": 2,
    "luty": 2,
    "marca": 3,
    "marzec": 3,
    "kwietnia": 4,
    "kwiecien": 4,
    "maja": 5,
    "maj": 5,
    "czerwca": 6,
    "czerwiec": 6,
    "lipca": 7,
    "lipiec": 7,
    "sierpnia": 8,
    "sierpien": 8,
    "wrzesnia": 9,
    "wrzesien": 9,
    "pazdziernika": 10,
    "pazdziernik": 10,
    "listopada": 11,
    "listopad": 11,
    "grudnia": 12,
    "grudzien": 12,
}


# --- Main routing ---


def route(
    user_input: str,
    workflows: dict[str, Workflow],
    context: dict | None = None,
    use_llm: bool = True,
) -> Intent:
    """Route user input to a workflow via keyword match or LLM fallback.

    Args:
        user_input: Raw user message.
        workflows: Loaded workflow definitions.
        context: Conversation history for LLM.
        use_llm: If False, skip LLM fallback (keyword-only mode).

    Returns:
        Intent with workflow_id, parameters, confidence, source.
    """
    intent = _keyword_match(user_input, workflows)
    if intent is not None:
        return intent

    if use_llm:
        try:
            from corp_by_os.llm_router import classify_intent

            return classify_intent(user_input, workflows, context)
        except Exception as e:
            logger.warning("LLM routing failed: %s", e)

    return Intent(
        source="none",
        response_text="Nie rozumiem. Spróbuj: 'nowe opportunity', 'co wymaga uwagi', 'moje taski'",
    )


# --- Keyword matching ---


def _keyword_match(
    user_input: str,
    workflows: dict[str, Workflow],
) -> Intent | None:
    """Match user input against workflow trigger phrases.

    Returns Intent if a match found, None otherwise.
    """
    normalized = _normalize(user_input)

    # Check task shortcuts FIRST — "muszę..." patterns should capture before
    # other workflows match on embedded keywords like "brief"
    task_intent = _check_task_shortcuts(normalized, user_input)
    if task_intent:
        return task_intent

    # Score each workflow by best trigger phrase match
    matches: list[tuple[int, str, Workflow]] = []

    for wf_id, wf in workflows.items():
        for phrase in wf.trigger_phrases:
            norm_phrase = _normalize(phrase)
            if _phrase_matches(norm_phrase, normalized):
                matches.append((len(norm_phrase), wf_id, wf))

    if not matches:
        return None

    # Pick longest match (most specific)
    matches.sort(key=lambda m: m[0], reverse=True)
    _, best_wf_id, best_wf = matches[0]

    # Extract parameters
    params = _extract_parameters(user_input, normalized, best_wf)

    return Intent(
        workflow_id=best_wf_id,
        parameters=params,
        confidence=0.9,
        source="keyword",
    )


def _check_task_shortcuts(normalized: str, raw: str) -> Intent | None:
    """Check for implicit task creation patterns."""
    task_triggers = ["musze", "need to", "zaplanuj", "przypomnij", "remind me"]
    for trigger in task_triggers:
        if trigger in normalized:
            # Extract title: everything after the trigger
            idx = normalized.index(trigger) + len(trigger)
            title_part = raw[idx:].strip().lstrip(",").strip()
            if not title_part:
                title_part = raw.strip()

            # Extract date/priority/project from the full input
            extracted_date = _extract_date(normalized)
            extracted_priority = _extract_priority(normalized)
            extracted_project = _extract_project_ref(normalized)

            # Strip date/deadline references from title
            title_part = _strip_date_references(title_part)
            title_part = title_part.strip().rstrip(",").strip()
            if not title_part:
                title_part = raw.strip()

            params: dict[str, str] = {"title": title_part}

            if extracted_date:
                params["deadline"] = extracted_date
            if extracted_priority:
                params["priority"] = extracted_priority
            if extracted_project:
                params["project"] = extracted_project

            return Intent(
                workflow_id="add_task",
                parameters=params,
                confidence=0.8,
                source="keyword",
            )
    return None


# --- Parameter extraction ---


def _extract_parameters(
    raw_input: str,
    normalized: str,
    workflow: Workflow,
) -> dict[str, str]:
    """Extract workflow parameters from user input."""
    params: dict[str, str] = {}

    # Extract by parameter type
    for param_name, _param_def in workflow.parameters.items():
        if param_name == "client":
            val = _extract_client(normalized, raw_input)
            if val:
                params["client"] = val
        elif param_name == "product":
            val = _extract_product(normalized)
            if val:
                params["product"] = val
        elif param_name == "project":
            val = _extract_project_ref(normalized)
            if val:
                params["project"] = val
        elif param_name == "topic":
            val = _extract_topic(normalized, raw_input)
            if val:
                params["topic"] = val
        elif param_name == "date":
            val = _extract_date(normalized)
            if val:
                params["date"] = val
        elif param_name == "priority":
            val = _extract_priority(normalized)
            if val:
                params["priority"] = val
        elif param_name == "reason":
            val = _extract_reason(normalized)
            if val:
                params["reason"] = val
        elif param_name == "title":
            val = _extract_title(normalized, raw_input)
            if val:
                params["title"] = val
        elif param_name == "contact":
            val = _extract_contact(raw_input)
            if val:
                params["contact"] = val

    return params


def _extract_client(normalized: str, raw: str) -> str | None:
    """Extract client name — check against known projects or capitalized words."""
    try:
        from corp_by_os.project_resolver import list_all_project_ids

        projects = list_all_project_ids()
        for proj in projects:
            client_slug = proj.split("_")[0].lower()
            if client_slug in normalized:
                return proj.split("_")[0]
    except Exception:
        pass

    # Fallback: look for capitalized words that aren't common Polish/English words
    skip_words = {
        "new",
        "nowe",
        "opportunity",
        "projekt",
        "project",
        "firma",
        "client",
        "klient",
        "kontakt",
        "contact",
        "product",
        "produkt",
        "mam",
        "need",
        "dla",
        "for",
        "przygotuj",
        "prepare",
        "na",
    }
    words = raw.split()
    for word in words:
        clean = word.strip(",.!?;:")
        if clean and clean[0].isupper() and clean.lower() not in skip_words:
            # Check it's not a product alias
            if clean.lower() not in PRODUCT_ALIASES:
                return clean

    return None


def _extract_product(normalized: str) -> str | None:
    """Extract product from known aliases."""
    for alias, product in PRODUCT_ALIASES.items():
        if alias in normalized.split():
            return product
    return None


def _extract_project_ref(normalized: str) -> str | None:
    """Extract project reference — fuzzy match against known projects."""
    try:
        from corp_by_os.project_resolver import list_all_project_ids, resolve_project

        projects = list_all_project_ids()
        for proj in projects:
            # Check both full name and client slug
            if proj.lower() in normalized:
                return proj.lower()
            client_slug = proj.split("_")[0].lower()
            if len(client_slug) >= 3 and client_slug in normalized:
                resolved = resolve_project(client_slug)
                if resolved:
                    return resolved.project_id
    except Exception:
        pass
    return None


def _strip_date_references(text: str) -> str:
    """Remove date/deadline phrases from text (for cleaning task titles).

    Handles: "do piątku", "do środy", "do 15 marca", "do jutra",
             "by Friday", "by tomorrow", "before 2026-03-15"
    Works on both raw text (with diacritics) and normalized text.
    """
    # Polish weekday forms — both with and without diacritics
    _PL_WEEKDAYS_ALL = [
        "poniedziałek",
        "poniedzialek",
        "poniedziałku",
        "poniedzialku",
        "wtorek",
        "wtorku",
        "środa",
        "sroda",
        "środę",
        "srode",
        "środy",
        "srody",
        "czwartek",
        "czwartku",
        "piątek",
        "piatek",
        "piątku",
        "piatku",
        "sobota",
        "soboty",
        "sobotę",
        "sobote",
        "niedziela",
        "niedzieli",
        "niedzielę",
        "niedziele",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    _PL_RELATIVE = ["dzisiaj", "jutro", "pojutrze", "today", "tomorrow"]
    _PL_MONTHS_ALL = list(_POLISH_MONTHS.keys())

    weekdays_pattern = "|".join(re.escape(w) for w in _PL_WEEKDAYS_ALL)
    relative_pattern = "|".join(_PL_RELATIVE)
    months_pattern = "|".join(_PL_MONTHS_ALL)

    patterns = [
        r"\s+do\s+(?:" + weekdays_pattern + r")\b",
        r"\s+do\s+(?:" + relative_pattern + r")\b",
        r"\s+do\s+\d{1,2}\s+(?:" + months_pattern + r")\b",
        r"\s+do\s+\d{4}-\d{2}-\d{2}\b",
        r"\s+(?:by|before)\s+(?:" + weekdays_pattern + r")\b",
        r"\s+(?:by|before)\s+(?:" + relative_pattern + r")\b",
        r"\s+(?:by|before)\s+\d{4}-\d{2}-\d{2}\b",
    ]
    result = text
    for pattern in patterns:
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    return result


def _extract_date(normalized: str) -> str | None:
    """Extract date from input — supports ISO, Polish relative dates, weekday names."""
    today = date.today()

    # ISO date: YYYY-MM-DD
    iso_match = re.search(r"\d{4}-\d{2}-\d{2}", normalized)
    if iso_match:
        return iso_match.group()

    # Polish day+month: "15 marca", "3 kwietnia"
    pl_date = re.search(r"(\d{1,2})\s+(" + "|".join(_POLISH_MONTHS.keys()) + r")", normalized)
    if pl_date:
        day = int(pl_date.group(1))
        month = _POLISH_MONTHS[pl_date.group(2)]
        year = today.year
        if month < today.month or (month == today.month and day < today.day):
            year += 1
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass

    # Relative dates
    relative_map: dict[str, date] = {
        "dzisiaj": today,
        "today": today,
        "jutro": today + timedelta(days=1),
        "tomorrow": today + timedelta(days=1),
        "pojutrze": today + timedelta(days=2),
    }
    for keyword, target_date in relative_map.items():
        if keyword in normalized:
            return target_date.isoformat()

    # Weekday names: "w piatek", "w poniedziałek", "friday", etc.
    for day_name, weekday_num in _WEEKDAY_MAP.items():
        if day_name in normalized:
            return _next_weekday(today, weekday_num).isoformat()

    return None


def _extract_priority(normalized: str) -> str | None:
    """Extract priority from keywords."""
    for keyword, priority in PRIORITY_KEYWORDS.items():
        if keyword in normalized:
            return priority
    return None


def _extract_reason(normalized: str) -> str | None:
    """Extract archive reason."""
    for keyword, reason in REASON_KEYWORDS.items():
        if keyword in normalized:
            return reason
    return None


def _extract_contact(raw: str) -> str | None:
    """Extract contact name — look for 'kontakt/contact' followed by a name."""
    patterns = [
        r"(?:kontakt|contact)\s+(.+?)(?:,|\.|$)",
        r"(?:kontakt|contact)\s+(.+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(",.!?")
    return None


def _extract_topic(normalized: str, raw: str) -> str | None:
    """Extract topic — words like 'demo', 'discovery', 'technical', etc."""
    topic_keywords = [
        "demo",
        "discovery",
        "technical",
        "deep dive",
        "workshop",
        "overview",
        "review",
        "kickoff",
        "planning",
        "assessment",
    ]
    for kw in topic_keywords:
        if kw in normalized:
            return kw.title()
    return None


def _extract_title(normalized: str, raw: str) -> str | None:
    """Extract task title from input."""
    # Remove common prefixes
    prefixes = [
        "dodaj task",
        "add task",
        "musze zrobic",
        "need to",
        "zaplanuj",
        "przypomnij",
        "remind me",
    ]
    text = raw.strip()
    for prefix in prefixes:
        idx = normalized.find(prefix)
        if idx >= 0:
            text = raw[idx + len(prefix) :].strip().lstrip(",").strip()
            break

    if text:
        # Clean up trailing date/priority references
        text = re.sub(r"\s+(do|by|before)\s+\S+$", "", text, flags=re.IGNORECASE)
        return text.strip() if text.strip() else None
    return None


# --- Text normalization ---


def _phrase_matches(phrase: str, text: str) -> bool:
    """Check if a trigger phrase matches the text.

    First tries exact substring match. If that fails, tries Polish
    stem prefix matching: each word in the phrase (≥4 chars) must be
    a prefix of some word in the text. This handles inflected forms
    like zrobic→zrobienia, przygotuj→przygotowac, etc.
    """
    # Exact substring match
    if phrase in text:
        return True

    # Stem prefix match — each phrase word must prefix-match an input word
    phrase_words = phrase.split()
    text_words = text.split()

    if not phrase_words:
        return False

    for pw in phrase_words:
        if len(pw) < 4:
            # Short words need exact match in text
            if pw not in text_words:
                return False
        else:
            # Longer words: check if pw is a prefix of any text word
            # Use minimum stem length of 4
            stem = pw[: max(4, len(pw) - 2)]
            if not any(tw.startswith(stem) for tw in text_words):
                return False

    return True


def _normalize(text: str) -> str:
    """Normalize text for matching: lowercase, strip diacritics, strip punctuation."""
    text = text.lower()
    # Strip diacritics: ą→a, ś→s, ł→l, etc.
    text = _strip_diacritics(text)
    # Strip punctuation
    text = re.sub(r"[^\w\s]", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _strip_diacritics(text: str) -> str:
    """Remove diacritical marks from Unicode text."""
    # Special Polish mappings that NFD doesn't handle well
    polish_map = {"ł": "l", "Ł": "L"}
    for char, replacement in polish_map.items():
        text = text.replace(char, replacement)

    # NFD decomposition then strip combining marks
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _next_weekday(from_date: date, weekday: int) -> date:
    """Get the next occurrence of a weekday (0=Monday)."""
    days_ahead = weekday - from_date.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return from_date + timedelta(days=days_ahead)
