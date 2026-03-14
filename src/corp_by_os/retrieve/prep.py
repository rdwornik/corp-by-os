"""Client preparation briefing generator.

Retrieves everything known about a client, synthesizes it via LLM
into a structured briefing with key facts, talking points, and gaps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from corp_by_os.retrieve.engine import (
    RetrievalFilter,
    RetrievalResult,
    RetrievedNote,
    retrieve,
)

logger = logging.getLogger(__name__)

try:
    from google import genai  # type: ignore[import-untyped]
    from google.genai import types as genai_types  # type: ignore[import-untyped]
except ImportError:
    genai = None  # type: ignore[assignment]
    genai_types = None  # type: ignore[assignment]


@dataclass
class PrepBriefing:
    """Generated client preparation briefing."""

    client: str
    generated_at: str
    retrieval: RetrievalResult
    briefing_text: str
    source_count: int
    coverage_gaps: list[str]
    model: str
    cost: float


PREP_SYSTEM_PROMPT = """\
You are a pre-sales preparation assistant for Blue Yonder, an enterprise \
supply chain software company.

You are preparing a briefing for a pre-sales engineer who has a meeting \
with a client. Your job is to synthesize all available knowledge about \
this client into a concise, actionable briefing.

Rules:
- Be specific. Reference actual facts from the notes, not generic statements.
- Cite your sources using [Note Title] format.
- Distinguish between CONFIRMED facts and INFERRED/ASSUMED information.
- Flag knowledge gaps explicitly — what we DON'T know is as important as what we do.
- Write in professional English, concise, scannable format.
- Focus on what helps the engineer in the meeting, not background noise.
"""

PREP_USER_PROMPT = """\
Prepare a client briefing for: {client}

Here are all the knowledge notes we have about this client:

{notes_context}

Generate a briefing with these sections:

## Client Overview
- Who they are, what they do, industry, size (if known)
- Our relationship with them (what stage, what products evaluated)

## Key Requirements & Pain Points
- What they need from us (specific requirements from RFP/meetings)
- Their current problems (why are they looking at Blue Yonder?)

## Technical Landscape
- Their current systems (ERP, WMS, planning tools — if known)
- Integration requirements
- Any technical constraints or preferences mentioned

## What We've Discussed So Far
- Timeline of interactions (discovery, RFP, demos, workshops)
- Key decisions made or pending
- Questions they've asked
- Concerns they've raised

## Competitive Situation
- Other vendors they're evaluating (if known)
- Our differentiators for this specific client

## Open Questions & Gaps
- What we DON'T know and should ask in the next meeting
- Areas where our knowledge is thin or outdated

## Suggested Talking Points
- 3-5 specific topics to raise in the meeting
- Based on what we know about their priorities

For each fact, cite the source note using [Note Title] format.
If information is not available in the notes, say "[NOT IN KNOWLEDGE BASE]" \
— do not invent.
"""


def generate_prep(
    client: str,
    db_path: Path,
    vault_root: Path,
    output_dir: Path | None = None,
    model: str = "gemini-2.0-flash",
) -> PrepBriefing:
    """Generate a client preparation briefing.

    Steps:
    1. Retrieve all notes related to this client
    2. Build context from note contents
    3. Send to LLM for synthesis
    4. Save briefing to file
    5. Return result
    """
    # --- Step 1: Retrieve ---
    result = retrieve(
        query=client,
        db_path=db_path,
        vault_root=vault_root,
        filters=RetrievalFilter(client=client),
        top_n=15,
    )

    if not result.notes:
        logger.info(
            "No notes with client=%s, trying broader search...", client,
        )
        result = retrieve(
            query=client,
            db_path=db_path,
            vault_root=vault_root,
            top_n=15,
        )

    # --- Step 2: Build context ---
    notes_context = build_notes_context(result.notes)

    # --- Step 3: Synthesize via LLM ---
    prompt = PREP_USER_PROMPT.format(
        client=client,
        notes_context=notes_context,
    )

    briefing_text, cost = _call_llm(PREP_SYSTEM_PROMPT, prompt, model)

    # --- Step 4: Save ---
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"prep_{client.replace(' ', '_')}_{timestamp}.md"
        output_path = output_dir / filename

        full_content = f"# Client Prep: {client}\n"
        full_content += f"*Generated: {datetime.now().isoformat()}*\n"
        full_content += f"*Sources: {len(result.notes)} notes*\n"
        if result.coverage_gaps:
            full_content += (
                f"*Gaps: {', '.join(result.coverage_gaps)}*\n"
            )
        full_content += f"\n---\n\n{briefing_text}"

        output_path.write_text(full_content, encoding="utf-8")
        logger.info("Briefing saved: %s", output_path)

    return PrepBriefing(
        client=client,
        generated_at=datetime.now().isoformat(),
        retrieval=result,
        briefing_text=briefing_text,
        source_count=len(result.notes),
        coverage_gaps=result.coverage_gaps,
        model=model,
        cost=cost,
    )


def build_notes_context(notes: list[RetrievedNote]) -> str:
    """Build LLM context from retrieved notes.

    Format each note with metadata header + content.
    Truncate individual notes if too long. Total context capped
    at ~50,000 chars (~12k tokens).
    """
    max_note_chars = 2000
    max_total_chars = 50000

    context_parts: list[str] = []
    total_chars = 0

    for i, note in enumerate(notes):
        header = (
            f"### [{note.title}]\n"
            f"Client: {note.client or 'N/A'} | "
            f"Type: {note.source_type or 'N/A'} | "
            f"Topics: {', '.join(note.topics[:5]) or 'N/A'} | "
            f"Products: {', '.join(note.products[:3]) or 'N/A'}\n\n"
        )

        content = note.content[:max_note_chars]
        if len(note.content) > max_note_chars:
            content += "\n[... truncated ...]"

        part = header + content + "\n\n---\n"

        if total_chars + len(part) > max_total_chars:
            remaining = len(notes) - len(context_parts)
            context_parts.append(
                f"\n[{remaining} more notes omitted due to context limit]",
            )
            break

        context_parts.append(part)
        total_chars += len(part)

    if not context_parts:
        return "[NO KNOWLEDGE AVAILABLE FOR THIS CLIENT]"

    return "\n".join(context_parts)


def _call_llm(
    system_prompt: str,
    user_prompt: str,
    model: str,
) -> tuple[str, float]:
    """Call Gemini for synthesis. Returns (text, estimated_cost).

    Graceful degradation: if LLM fails, returns message asking
    the user to review raw notes manually.
    """
    if genai is None:
        logger.warning("google-genai not installed — skipping LLM synthesis")
        return (
            "[LLM SYNTHESIS UNAVAILABLE: google-genai not installed]\n\n"
            "Raw notes were retrieved. Review them manually.",
            0.0,
        )

    try:
        client = genai.Client()
        response = client.models.generate_content(
            model=model,
            contents=user_prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.3,
                max_output_tokens=4096,
            ),
        )

        text = response.text or ""
        # Rough cost estimate for Flash
        input_tokens = len(system_prompt + user_prompt) / 4
        output_tokens = len(text) / 4
        cost = (input_tokens * 0.5 + output_tokens * 3.0) / 1_000_000

        return text, cost

    except Exception as exc:
        logger.error("LLM synthesis failed: %s", exc)
        return (
            f"[LLM SYNTHESIS FAILED: {exc}]\n\n"
            "Raw notes were retrieved successfully. Review them manually.",
            0.0,
        )
