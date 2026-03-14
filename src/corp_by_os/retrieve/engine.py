"""Unified retrieval engine for Corporate OS.

All workflows (prep, rfp, discovery) use this module.

Retrieval strategy:
1. Build FTS5 query with BM25 ranking
2. Apply metadata filters via SQL WHERE clauses
3. Load full note content from vault
4. Assess coverage and return ranked results with citations
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class RetrievalFilter:
    """Metadata filters for retrieval queries."""

    client: str | None = None
    project_id: str | None = None
    products: list[str] | None = None
    domains: list[str] | None = None
    topics: list[str] | None = None
    source_type: str | None = None
    type: str | None = None


@dataclass
class RetrievedNote:
    """A single retrieved note with content and metadata."""

    note_id: int
    title: str
    client: str
    project_id: str
    topics: list[str]
    products: list[str]
    domains: list[str]
    source_type: str
    note_type: str
    note_path: str
    content: str
    relevance_score: float
    citation: str
    confidence: str = "extracted"
    extracted_at: str = ""
    overlay_data: dict = field(default_factory=dict)


CONFIDENCE_BOOST: dict[str, float] = {
    "verified": 0,
    "extracted": 10,
    "generated": 50,
    "draft": 100,
}


def _apply_confidence_ranking(
    notes: list[RetrievedNote],
) -> list[RetrievedNote]:
    """Rerank by combining BM25 relevance with confidence trust level."""
    for note in notes:
        boost = CONFIDENCE_BOOST.get(note.confidence, 10)
        note.relevance_score = note.relevance_score + boost
    return sorted(notes, key=lambda n: n.relevance_score)


@dataclass
class RetrievalResult:
    """Complete result of a retrieval query."""

    query: str
    filters: RetrievalFilter
    notes: list[RetrievedNote]
    total_found: int
    sufficient: bool
    coverage_gaps: list[str]


def retrieve(
    query: str,
    db_path: Path,
    vault_root: Path,
    filters: RetrievalFilter | None = None,
    top_n: int = 15,
    min_results_for_sufficient: int = 3,
) -> RetrievalResult:
    """Retrieve relevant notes from the knowledge base.

    This is the ONLY retrieval function. All workflows call this.

    Strategy:
    1. Build FTS5 query from search terms
    2. Apply metadata filters via SQL WHERE clauses
    3. Rank by BM25
    4. Load full note content for top-N results
    5. Assess coverage sufficiency
    """
    if filters is None:
        filters = RetrievalFilter()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        fts_query = _build_fts_query(query)

        # Build metadata WHERE clauses
        where_clauses: list[str] = []
        params: list[str] = []

        if filters.client:
            where_clauses.append("n.client LIKE ?")
            params.append(f"%{filters.client}%")

        if filters.project_id:
            where_clauses.append("n.project_id LIKE ?")
            params.append(f"%{filters.project_id}%")

        if filters.products:
            product_clauses = []
            for p in filters.products:
                product_clauses.append("n.products LIKE ?")
                params.append(f"%{p}%")
            where_clauses.append(f"({' OR '.join(product_clauses)})")

        if filters.domains:
            domain_clauses = []
            for d in filters.domains:
                domain_clauses.append("n.domains LIKE ?")
                params.append(f"%{d}%")
            where_clauses.append(f"({' OR '.join(domain_clauses)})")

        if filters.topics:
            topic_clauses = []
            for t in filters.topics:
                topic_clauses.append("n.topics LIKE ?")
                params.append(f"%{t}%")
            where_clauses.append(f"({' OR '.join(topic_clauses)})")

        if filters.source_type:
            where_clauses.append("n.source_type = ?")
            params.append(filters.source_type)

        if filters.type:
            where_clauses.append("n.type = ?")
            params.append(filters.type)

        where_sql = (
            " AND " + " AND ".join(where_clauses) if where_clauses else ""
        )

        # FTS5 search with BM25 ranking
        sql = f"""
            SELECT
                n.id, n.title, n.client, n.project_id,
                n.topics, n.products, n.domains,
                n.source_type, n.type, n.note_path,
                n.confidence,
                rank
            FROM notes_fts f
            JOIN notes n ON f.rowid = n.id
            WHERE notes_fts MATCH ?
            {where_sql}
            ORDER BY rank
            LIMIT ?
        """

        fetch_limit = top_n * 3
        all_params = [fts_query] + params + [fetch_limit]

        try:
            rows = conn.execute(sql, all_params).fetchall()
        except sqlite3.OperationalError as exc:
            logger.warning("FTS5 query failed (%s), falling back", exc)
            rows = _fallback_search(conn, query, filters, fetch_limit)

        # Supplement with metadata-only results if client filter is set
        if filters.client and len(rows) < top_n:
            seen_ids = {r["id"] for r in rows}
            placeholders = ",".join(str(sid) for sid in seen_ids) or "0"
            meta_sql = f"""
                SELECT
                    n.id, n.title, n.client, n.project_id,
                    n.topics, n.products, n.domains,
                    n.source_type, n.type, n.note_path,
                    n.confidence,
                    999 as rank
                FROM notes n
                WHERE n.client LIKE ?
                AND n.id NOT IN ({placeholders})
                LIMIT ?
            """
            extra = conn.execute(
                meta_sql, [f"%{filters.client}%", top_n - len(rows)],
            ).fetchall()
            rows = list(rows) + list(extra)

        total_found = len(rows)

        # Build RetrievedNote objects with full content
        notes: list[RetrievedNote] = []
        seen_ids: set[int] = set()

        for row in rows[:top_n]:
            rid = row["id"]
            if rid in seen_ids:
                continue
            seen_ids.add(rid)

            note_path = Path(row["note_path"])
            content = _load_note_content(note_path)
            meta = _load_note_metadata(note_path)

            topics = _parse_json_field(row["topics"])
            products = _parse_json_field(row["products"])
            domains = _parse_json_field(row["domains"])

            # confidence: prefer DB column, fall back to frontmatter
            db_confidence = row["confidence"] if "confidence" in row.keys() else None
            confidence = db_confidence or meta.get("confidence", "extracted")

            citation = (
                f"[{row['title']}] "
                f"(client: {row['client'] or 'N/A'}, "
                f"source: {row['source_type'] or 'N/A'})"
            )

            notes.append(RetrievedNote(
                note_id=rid,
                title=row["title"],
                client=row["client"] or "",
                project_id=row["project_id"] or "",
                topics=topics,
                products=products,
                domains=domains,
                source_type=row["source_type"] or "",
                note_type=row["type"] or "",
                note_path=str(note_path),
                content=content,
                relevance_score=float(row["rank"]),
                citation=citation,
                confidence=confidence,
                extracted_at=meta.get("extracted_at", ""),
                overlay_data=meta.get("overlay_data", {}),
            ))

        notes = _apply_confidence_ranking(notes)
        sufficient = len(notes) >= min_results_for_sufficient
        coverage_gaps = _find_coverage_gaps(query, filters, notes)

        return RetrievalResult(
            query=query,
            filters=filters,
            notes=notes,
            total_found=total_found,
            sufficient=sufficient,
            coverage_gaps=coverage_gaps,
        )

    finally:
        conn.close()


# --- Internal helpers ---


def _build_fts_query(query: str) -> str:
    """Convert natural language query to FTS5 query syntax.

    Removes stopwords, cleans special chars, joins with OR for
    broad recall (BM25 handles ranking).
    """
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "of", "at", "by", "for", "with", "about", "between",
        "to", "from", "in", "on", "and", "or", "not", "but",
        "what", "which", "who", "whom", "this", "that", "these",
        "those", "i", "me", "my", "we", "our", "you", "your",
        "he", "him", "his", "she", "her", "it", "its", "they", "their",
    }

    words = query.lower().split()
    filtered = [w for w in words if w not in stopwords and len(w) > 1]
    if not filtered:
        filtered = words[:3]

    cleaned = []
    for word in filtered:
        clean = "".join(c for c in word if c.isalnum() or c == "_")
        if clean:
            cleaned.append(f'"{clean}"')

    if not cleaned:
        return f'"{query}"'

    return " OR ".join(cleaned)


def _load_note_content(note_path: Path) -> str:
    """Load full markdown content, stripping YAML frontmatter."""
    try:
        text = note_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            return text[end + 3:].strip()

    return text.strip()


def _load_note_metadata(note_path: Path) -> dict:
    """Load frontmatter metadata including overlay data."""
    try:
        text = note_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}

    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}

    try:
        import yaml

        fm = yaml.safe_load(text[3:end])
        if not isinstance(fm, dict):
            return {}
    except Exception:
        return {}

    overlay_data = {}
    for key in fm:
        if key.endswith("_overlay"):
            overlay_data[key] = fm[key]

    return {
        "confidence": fm.get("trust_level") or fm.get("confidence_level", "extracted"),
        "extracted_at": str(fm.get("extracted_at", "")),
        "extraction_version": fm.get("extraction_version", 1),
        "depth": fm.get("depth", "standard"),
        "doc_type": fm.get("doc_type", "general"),
        "key_facts": fm.get("key_facts", []),
        "overlay_data": overlay_data,
    }


def _parse_json_field(raw: str | None) -> list[str]:
    """Parse a JSON array string from the notes table."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(item) for item in data]
    except (json.JSONDecodeError, TypeError):
        pass
    # Fallback: comma-separated
    return [t.strip() for t in raw.split(",") if t.strip()]


def _fallback_search(
    conn: sqlite3.Connection,
    query: str,
    filters: RetrievalFilter,
    limit: int,
) -> list[sqlite3.Row]:
    """Fallback LIKE search when FTS5 query fails."""
    words = query.split()[:3]
    conditions = []
    params: list[str] = []

    for word in words:
        conditions.append("n.title LIKE ?")
        params.append(f"%{word}%")

    if filters.client:
        conditions.append("n.client LIKE ?")
        params.append(f"%{filters.client}%")

    where = " OR ".join(conditions) if conditions else "1=1"
    sql = f"""
        SELECT n.id, n.title, n.client, n.project_id,
               n.topics, n.products, n.domains,
               n.source_type, n.type, n.note_path,
               n.confidence,
               0 as rank
        FROM notes n
        WHERE {where}
        LIMIT ?
    """
    return conn.execute(sql, params + [str(limit)]).fetchall()


def _find_coverage_gaps(
    query: str,
    filters: RetrievalFilter,
    notes: list[RetrievedNote],
) -> list[str]:
    """Identify what the query asked about but results don't cover."""
    gaps: list[str] = []

    if filters.products:
        all_products = set()
        for note in notes:
            all_products.update(p.lower() for p in note.products)
        for p in filters.products:
            if not any(p.lower() in prod for prod in all_products):
                gaps.append(f"No results for product: {p}")

    if filters.topics:
        all_topics = set()
        for note in notes:
            all_topics.update(t.lower() for t in note.topics)
        for t in filters.topics:
            if not any(t.lower() in topic for topic in all_topics):
                gaps.append(f"No results for topic: {t}")

    if not notes:
        gaps.append("No results found for this query")

    return gaps
