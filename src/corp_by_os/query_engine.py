"""Query the cross-project index.

Three query modes:
1. Full-text search: FTS5 on facts
2. Structured: filter projects by product, topic, status, region
3. Analytics: aggregations, patterns, rankings
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path

from corp_by_os.index_builder import _connect, _ensure_schema
from corp_by_os.models import AnalyticsReport, FactResult, ProjectResult

logger = logging.getLogger(__name__)


def search_facts(
    query: str,
    project_filter: str | None = None,
    limit: int = 20,
    db_path: Path | None = None,
) -> list[FactResult]:
    """Full-text search across all facts using FTS5.

    Args:
        query: Search terms (FTS5 syntax supported).
        project_filter: Restrict to a single project.
        limit: Max results.

    Returns:
        List of FactResult sorted by relevance.
    """
    conn = _connect(db_path)
    try:
        _ensure_schema(conn)

        # Build FTS5 query — quote terms for safety
        fts_query = _sanitize_fts_query(query)
        if not fts_query:
            return []

        if project_filter:
            sql = """
                SELECT f.project_id, p.client, f.fact, f.source_title,
                       f.topics, rank
                FROM facts_fts fts
                JOIN facts f ON f.id = fts.rowid
                JOIN projects p ON p.project_id = f.project_id
                WHERE facts_fts MATCH ? AND f.project_id = ?
                ORDER BY rank
                LIMIT ?
            """
            rows = conn.execute(sql, (fts_query, project_filter.lower(), limit)).fetchall()
        else:
            sql = """
                SELECT f.project_id, p.client, f.fact, f.source_title,
                       f.topics, rank
                FROM facts_fts fts
                JOIN facts f ON f.id = fts.rowid
                JOIN projects p ON p.project_id = f.project_id
                WHERE facts_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """
            rows = conn.execute(sql, (fts_query, limit)).fetchall()

        results = []
        for row in rows:
            topics = _parse_json_list(row[4])
            results.append(
                FactResult(
                    project_id=row[0],
                    client=row[1],
                    fact=row[2],
                    source_title=row[3] or "",
                    topics=topics,
                    relevance_score=abs(row[5]) if row[5] else 0.0,
                )
            )

        # Also search notes_fts for matching vault notes
        remaining = limit - len(results)
        if remaining > 0:
            try:
                notes_results = _search_notes_fts(conn, fts_query, project_filter, remaining)
                results.extend(notes_results)
            except Exception:
                # notes table may not exist in older indexes
                logger.debug("notes_fts search skipped (table may not exist)")

        return results
    finally:
        conn.close()


def search_projects(
    products: list[str] | None = None,
    topics: list[str] | None = None,
    status: str | None = None,
    region: str | None = None,
    db_path: Path | None = None,
) -> list[ProjectResult]:
    """Structured project search with JSON array filters."""
    conn = _connect(db_path)
    try:
        _ensure_schema(conn)

        sql = (
            "SELECT project_id, client, status, products,"
            " topics, facts_count FROM projects WHERE 1=1"
        )
        params: list = []

        if status:
            sql += " AND status = ?"
            params.append(status)

        if region:
            sql += " AND region = ?"
            params.append(region)

        sql += " ORDER BY facts_count DESC, client ASC"
        rows = conn.execute(sql, params).fetchall()

        results = []
        for row in rows:
            proj_products = _parse_json_list(row[3])
            proj_topics = _parse_json_list(row[4])

            # Filter by products (any match)
            if products:
                if not any(p.lower() in [pp.lower() for pp in proj_products] for p in products):
                    continue

            # Filter by topics (any match, substring)
            if topics:
                topics_lower = [t.lower() for t in proj_topics]
                if not any(any(t.lower() in pt for pt in topics_lower) for t in topics):
                    continue

            results.append(
                ProjectResult(
                    project_id=row[0],
                    client=row[1],
                    status=row[2] or "",
                    products=proj_products,
                    topics=proj_topics,
                    facts_count=row[5] or 0,
                )
            )

        return results
    finally:
        conn.close()


def get_analytics(db_path: Path | None = None) -> AnalyticsReport:
    """Cross-project analytics: top topics, products, bundles, status breakdown."""
    conn = _connect(db_path)
    try:
        _ensure_schema(conn)

        # Total counts
        total_projects = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        total_facts = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]

        # Aggregate topics from facts
        topic_counter: Counter = Counter()
        rows = conn.execute("SELECT topics FROM facts WHERE topics != '[]'").fetchall()
        for (raw,) in rows:
            for t in _parse_json_list(raw):
                topic_counter[t] += 1
        top_topics = topic_counter.most_common(15)

        # Aggregate products from projects
        product_counter: Counter = Counter()
        rows = conn.execute("SELECT products FROM projects").fetchall()
        all_project_products: list[list[str]] = []
        for (raw,) in rows:
            prods = _parse_json_list(raw)
            all_project_products.append(prods)
            for p in prods:
                product_counter[p] += 1
        top_products = product_counter.most_common(10)

        # Aggregate domains from projects
        domain_counter: Counter = Counter()
        rows = conn.execute("SELECT domains FROM projects").fetchall()
        for (raw,) in rows:
            for d in _parse_json_list(raw):
                domain_counter[d] += 1
        top_domains = domain_counter.most_common(8)

        # Product bundles (pairs that appear together)
        bundle_counter: Counter = Counter()
        for prods in all_project_products:
            # Normalize product names, take unique
            unique = sorted(set(p for p in prods if p))
            for pair in combinations(unique, 2):
                bundle_counter[f"{pair[0]} + {pair[1]}"] += 1
        product_bundles = [
            (bundle, count) for bundle, count in bundle_counter.most_common(10) if count >= 2
        ]

        # Projects by status
        status_rows = conn.execute(
            "SELECT status, COUNT(*) FROM projects GROUP BY status ORDER BY COUNT(*) DESC",
        ).fetchall()
        projects_by_status = {s: c for s, c in status_rows}

        # Projects by region
        region_rows = conn.execute(
            "SELECT region, COUNT(*) FROM projects WHERE region IS NOT NULL GROUP BY region",
        ).fetchall()
        projects_by_region = {r: c for r, c in region_rows}

        # Avg facts per project (only those with facts)
        avg_row = conn.execute(
            "SELECT AVG(facts_count) FROM projects WHERE facts_count > 0",
        ).fetchone()
        avg_facts = avg_row[0] if avg_row[0] else 0.0

        # Stale projects (last_extracted > 30 days ago)
        cutoff = (date.today() - timedelta(days=30)).isoformat()
        stale_rows = conn.execute(
            "SELECT project_id FROM projects"
            " WHERE last_extracted IS NOT NULL"
            " AND last_extracted < ?",
            (cutoff,),
        ).fetchall()
        stale_projects = [r[0] for r in stale_rows]

        return AnalyticsReport(
            total_projects=total_projects,
            total_facts=total_facts,
            top_topics=top_topics,
            top_products=top_products,
            top_domains=top_domains,
            product_bundles=product_bundles,
            projects_by_status=projects_by_status,
            projects_by_region=projects_by_region,
            avg_facts_per_project=round(avg_facts, 1),
            stale_projects=stale_projects,
        )
    finally:
        conn.close()


# --- Helpers ---


def _search_notes_fts(
    conn: sqlite3.Connection,
    fts_query: str,
    project_filter: str | None,
    limit: int,
) -> list[FactResult]:
    """Search notes_fts and return results as FactResult for unified display."""
    if project_filter:
        sql = """
            SELECT n.project_id, n.client, n.title, n.products, n.topics, rank
            FROM notes_fts nf
            JOIN notes n ON n.id = nf.rowid
            WHERE notes_fts MATCH ? AND n.project_id = ?
            ORDER BY rank
            LIMIT ?
        """
        rows = conn.execute(sql, (fts_query, project_filter.lower(), limit)).fetchall()
    else:
        sql = """
            SELECT n.project_id, n.client, n.title, n.products, n.topics, rank
            FROM notes_fts nf
            JOIN notes n ON n.id = nf.rowid
            WHERE notes_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        rows = conn.execute(sql, (fts_query, limit)).fetchall()

    results = []
    for row in rows:
        results.append(
            FactResult(
                project_id=row[0],
                client=row[1] or "",
                fact=f"[note] {row[2]}",
                source_title=row[3] or "",
                topics=row[4].split(", ") if row[4] else [],
                relevance_score=abs(row[5]) if row[5] else 0.0,
            )
        )
    return results


def _sanitize_fts_query(query: str) -> str:
    """Clean up user query for FTS5 safety.

    Wraps each word in quotes to prevent FTS syntax errors.
    """
    words = query.strip().split()
    if not words:
        return ""
    # Quote each word to prevent FTS operator confusion
    return " ".join(f'"{w}"' for w in words if w)


def _parse_json_list(raw: str | None) -> list[str]:
    """Parse a JSON array string, returning empty list on failure."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(item) for item in data]
    except (json.JSONDecodeError, TypeError):
        pass
    return []
