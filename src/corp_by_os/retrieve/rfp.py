"""RFP answer generator.

Retrieves relevant knowledge and drafts an answer with inline
citations and confidence assessment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from corp_by_os.retrieve.engine import (
    RetrievalFilter,
    RetrievalResult,
    retrieve,
)

logger = logging.getLogger(__name__)


@dataclass
class RFPAnswer:
    """Generated RFP answer."""

    question: str
    answer_text: str
    confidence: str  # 'high' | 'medium' | 'low' | 'insufficient'
    source_count: int
    coverage_gaps: list[str]
    retrieval: RetrievalResult
    model: str
    cost: float


RFP_SYSTEM_PROMPT = """\
You are an RFP response writer for Blue Yonder, an enterprise supply chain \
software company.

Blue Yonder products include:
- Cognitive Demand Planning, Supply Planning, Allocation & Replenishment
- Warehouse Management System (WMS), Transportation Management (TMS)
- Blue Yonder Platform (SaaS, multi-tenant, Snowflake-based data cloud)
- Order Management, Network Optimization, Control Tower

Rules:
- Answer the RFP question directly and professionally.
- Use ONLY information from the provided knowledge notes. Do NOT invent capabilities.
- Cite sources using [Note Title] format after each claim.
- If the notes don't fully answer the question, say what IS covered and flag what's missing.
- Be specific: mention product names, feature names, version numbers when available.
- Write in third person ("Blue Yonder provides..." not "We provide...").
- Keep answers concise but complete — typical RFP answer length is 100-300 words.
- If you genuinely cannot answer from the provided notes, say so clearly.
"""

RFP_USER_PROMPT = """\
RFP Question: {question}

{client_context}

Here are the relevant knowledge notes:

{notes_context}

Draft an RFP answer. Structure:

**Answer:**
[Direct answer to the question, with [Note Title] citations]

**Key Capabilities:**
[Bullet points of specific relevant capabilities, each cited]

**Confidence Assessment:**
- HIGH: Answer is well-supported by multiple sources
- MEDIUM: Answer is partially supported, some gaps
- LOW: Limited relevant information found
- INSUFFICIENT: Cannot answer from available knowledge

**Gaps:**
[List specific aspects of the question we cannot answer from current knowledge]
"""


def answer_rfp(
    question: str,
    db_path: Path,
    vault_root: Path,
    client: str | None = None,
    product: str | None = None,
    model: str = "gemini-3-flash-preview",
) -> RFPAnswer:
    """Generate an RFP answer from the knowledge base.

    Steps:
    1. Retrieve relevant notes via corp retrieve (with optional filters)
    2. Build context with citations
    3. LLM synthesis into structured RFP answer
    4. Assess confidence based on retrieval quality
    """
    from corp_by_os.retrieve.prep import _call_llm, build_notes_context

    # --- Step 1: Retrieve ---
    filters = RetrievalFilter(
        client=client,
        products=[product] if product else None,
    )

    result = retrieve(
        query=question,
        db_path=db_path,
        vault_root=vault_root,
        filters=filters,
        top_n=15,
    )

    # If no results with filters, try without
    if not result.notes and (client or product):
        logger.info("No results with filters, trying broader search...")
        result = retrieve(
            query=question,
            db_path=db_path,
            vault_root=vault_root,
            top_n=15,
        )

    # --- Step 2: Build context ---
    notes_context = build_notes_context(result.notes)

    client_context = ""
    if client:
        client_context = (
            f"This RFP is for client: {client}. "
            "Tailor the answer to their context if relevant information is available."
        )

    # --- Step 3: LLM synthesis ---
    prompt = RFP_USER_PROMPT.format(
        question=question,
        client_context=client_context,
        notes_context=notes_context,
    )

    answer_text, cost = _call_llm(RFP_SYSTEM_PROMPT, prompt, model)

    # --- Step 4: Assess confidence ---
    if not result.notes:
        confidence = "insufficient"
    elif len(result.notes) >= 5 and result.sufficient:
        confidence = "high"
    elif len(result.notes) >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    return RFPAnswer(
        question=question,
        answer_text=answer_text,
        confidence=confidence,
        source_count=len(result.notes),
        coverage_gaps=result.coverage_gaps,
        retrieval=result,
        model=model,
        cost=cost,
    )
