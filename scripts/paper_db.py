"""
paper_db.py — SQLite-based paper knowledge network storage.

Schema:
  papers         — paper metadata (id, title, abstract, scores, etc.)
  paper_edges    — relationships between papers (CITES, COMPARES_WITH, etc.)
  baselines      — extracted baseline method names per paper
  methods        — canonical method name registry with aliases

Design choices:
  - arxiv ID (e.g. "2603.03276v1") as primary key for papers
  - Edge types: CITES, CITED_BY, COMPARES_WITH, EXTENDS, SIMILAR_TO
  - All writes are idempotent (INSERT OR REPLACE / INSERT OR IGNORE)
  - Thread-safe via per-call connections (no shared connection)
"""

from __future__ import annotations
import json
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default DB path: data/paper_network.db (relative to skill root)
DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "paper_network.db"

# ─────────────────────── Edge Types ───────────────────────

EDGE_CITES = "CITES"                  # A cites B (A → B)
EDGE_CITED_BY = "CITED_BY"            # A is cited by B (reverse of CITES)
EDGE_COMPARES_WITH = "COMPARES_WITH"  # A compares against same baseline as B
EDGE_EXTENDS = "EXTENDS"              # A explicitly extends/builds on B
EDGE_SIMILAR_TO = "SIMILAR_TO"        # High semantic similarity (embedding)

ALL_EDGE_TYPES = [EDGE_CITES, EDGE_CITED_BY, EDGE_COMPARES_WITH, EDGE_EXTENDS, EDGE_SIMILAR_TO]

# ─────────────────────── Schema ───────────────────────

SCHEMA_SQL = """
-- Core paper metadata
CREATE TABLE IF NOT EXISTS papers (
    id              TEXT PRIMARY KEY,      -- arxiv ID (e.g. "2603.03276v1")
    s2_id           TEXT,                  -- Semantic Scholar paper ID
    title           TEXT NOT NULL,
    abstract        TEXT,
    authors         TEXT,                  -- JSON array of author names
    author_ids      TEXT,                  -- JSON array of S2 author IDs
    date            TEXT,                  -- publication date YYYY-MM-DD
    year            INTEGER,               -- publication year
    arxiv_url       TEXT,
    arxiv_categories TEXT,                 -- JSON array of arxiv categories
    primary_category TEXT,                 -- primary arxiv category
    doi             TEXT,                  -- DOI
    venue           TEXT,                  -- conference/journal (e.g. "CVPR 2025")
    venue_short     TEXT,                  -- venue abbreviation (e.g. "CVPR")
    domain          TEXT,                  -- assigned domain name
    best_score      REAL DEFAULT 0,        -- best similarity score
    paper_type      TEXT DEFAULT '方法文',  -- 方法文/Benchmark/Survey
    labels          TEXT,                  -- JSON array of label strings
    cn_abstract     TEXT,                  -- Chinese abstract
    cn_oneliner     TEXT,                  -- One-line Chinese summary
    tldr            TEXT,                  -- S2 TLDR (single-sentence summary)
    s2_citation_count   INTEGER DEFAULT 0,
    s2_reference_count  INTEGER DEFAULT 0,
    s2_influential_citation_count INTEGER DEFAULT 0,
    s2_fields_of_study  TEXT,              -- JSON array of research fields
    s2_words        TEXT,                  -- JSON array of S2 keywords
    is_open_access  INTEGER DEFAULT 0,     -- 1 if open access
    open_access_pdf TEXT,                  -- PDF URL
    keywords        TEXT,                  -- JSON array of keywords (LLM or algorithm)
    tasks           TEXT,                  -- JSON array of task tags
    methods         TEXT,                  -- JSON array of method tags
    datasets        TEXT,                  -- JSON array of datasets
    method_variants TEXT,                  -- JSON array of method variants
    baselines_json  TEXT,                  -- JSON array of baseline papers
    motivation_sources TEXT,               -- JSON array of motivation source papers
    institutions    TEXT,                  -- JSON array of institutions
    code_url        TEXT,                  -- code repository URL
    github_stars    INTEGER DEFAULT 0,     -- GitHub stars count
    source          TEXT DEFAULT 'arxiv',   -- arxiv / s2_expansion / manual
    status          TEXT DEFAULT 'pending', -- pending / analyzed / failed
    analysis_status TEXT DEFAULT 'pending', -- pending / analyzing / completed / failed
    analysis_date   TEXT,                  -- analysis completion time
    analysis_model  TEXT,                  -- model used for analysis
    analysis_session_id TEXT,              -- OpenClaw session ID for traceability
    analysis_transcript TEXT,              -- transcript path for traceability
    analysis_result_path TEXT,             -- result JSON path
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- Relationship edges between papers
CREATE TABLE IF NOT EXISTS paper_edges (
    src_id      TEXT NOT NULL,           -- source paper ID
    dst_id      TEXT NOT NULL,           -- destination paper ID
    edge_type   TEXT NOT NULL,           -- CITES, CITED_BY, COMPARES_WITH, EXTENDS, SIMILAR_TO
    weight      REAL DEFAULT 1.0,       -- edge weight (similarity score, etc.)
    metadata    TEXT,                    -- optional JSON metadata
    created_at  TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (src_id, dst_id, edge_type)
);

-- Extracted baseline method names per paper
CREATE TABLE IF NOT EXISTS baselines (
    paper_id        TEXT NOT NULL,
    baseline_name   TEXT NOT NULL,        -- raw name from paper
    canonical_name  TEXT,                 -- normalized canonical name
    context         TEXT,                 -- how the baseline was mentioned
    PRIMARY KEY (paper_id, baseline_name),
    FOREIGN KEY (paper_id) REFERENCES papers(id)
);

-- Canonical method registry (for name normalization)
CREATE TABLE IF NOT EXISTS methods (
    canonical_name  TEXT PRIMARY KEY,     -- e.g. "TiTok"
    aliases         TEXT,                 -- JSON array: ["TiTok", "1D-Tokenizer", ...]
    description     TEXT,                 -- brief description
    first_paper_id  TEXT,                 -- original paper that introduced this method
    category        TEXT                  -- e.g. "tokenizer", "diffusion", "unified-model"
);

-- Indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_edges_src ON paper_edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON paper_edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON paper_edges(edge_type);
CREATE INDEX IF NOT EXISTS idx_papers_domain ON papers(domain);
CREATE INDEX IF NOT EXISTS idx_papers_date ON papers(date);
CREATE INDEX IF NOT EXISTS idx_papers_s2id ON papers(s2_id);
CREATE INDEX IF NOT EXISTS idx_baselines_name ON baselines(canonical_name);
"""


class PaperDB:
    """SQLite-based paper knowledge network storage."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        conn = self._connect()
        try:
            conn.executescript(SCHEMA_SQL)
            self._ensure_analysis_columns(conn)
            conn.commit()
            logger.info(f"PaperDB initialized: {self.db_path}")
        finally:
            conn.close()

    def _ensure_analysis_columns(self, conn: sqlite3.Connection) -> None:
        """Apply additive schema migrations for analysis tracking columns."""
        existing_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(papers)").fetchall()
        }
        required_columns = {
            "analysis_status": "TEXT DEFAULT 'pending'",
            "analysis_date": "TEXT",
            "analysis_model": "TEXT",
            "analysis_session_id": "TEXT",
            "analysis_transcript": "TEXT",
            "analysis_result_path": "TEXT",
        }
        for column_name, column_def in required_columns.items():
            if column_name in existing_columns:
                continue
            conn.execute(f"ALTER TABLE papers ADD COLUMN {column_name} {column_def}")
            logger.info("Added papers.%s column", column_name)

    # ─────────────── Paper CRUD ───────────────

    def upsert_paper(self, paper: dict) -> None:
        """Insert or update a paper record."""
        conn = self._connect()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO papers
                (id, s2_id, title, abstract, authors, date, arxiv_url,
                 domain, best_score, paper_type, labels,
                 cn_abstract, cn_oneliner,
                 s2_citation_count, s2_reference_count, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                paper["id"],
                paper.get("s2_id"),
                paper["title"],
                paper.get("abstract", ""),
                json.dumps(paper.get("authors", []), ensure_ascii=False),
                paper.get("date", ""),
                paper.get("arxiv_url", ""),
                paper.get("domain", ""),
                paper.get("best_score", 0),
                paper.get("paper_type", "方法文"),
                json.dumps(paper.get("labels", []), ensure_ascii=False),
                paper.get("cn_abstract", ""),
                paper.get("cn_oneliner", ""),
                paper.get("s2_citation_count", 0),
                paper.get("s2_reference_count", 0),
                paper.get("source", "arxiv"),
            ))
            conn.commit()
        finally:
            conn.close()

    def upsert_papers(self, papers: list[dict]) -> int:
        """Batch insert/update papers. Returns count."""
        conn = self._connect()
        try:
            for paper in papers:
                conn.execute("""
                    INSERT OR REPLACE INTO papers
                    (id, s2_id, title, abstract, authors, date, arxiv_url,
                     domain, best_score, paper_type, labels,
                     cn_abstract, cn_oneliner,
                     s2_citation_count, s2_reference_count, source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """, (
                    paper["id"],
                    paper.get("s2_id"),
                    paper["title"],
                    paper.get("abstract", ""),
                    json.dumps(paper.get("authors", []), ensure_ascii=False),
                    paper.get("date", ""),
                    paper.get("arxiv_url", ""),
                    paper.get("domain", ""),
                    paper.get("best_score", 0),
                    paper.get("paper_type", "方法文"),
                    json.dumps(paper.get("labels", []), ensure_ascii=False),
                    paper.get("cn_abstract", ""),
                    paper.get("cn_oneliner", ""),
                    paper.get("s2_citation_count", 0),
                    paper.get("s2_reference_count", 0),
                    paper.get("source", "arxiv"),
                ))
            conn.commit()
            return len(papers)
        finally:
            conn.close()

    def get_paper(self, paper_id: str) -> Optional[dict]:
        """Get a single paper by ID."""
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def update_analysis_status(
        self,
        paper_id: str,
        analysis_status: str,
        analysis_date: str | None = None,
        analysis_model: str | None = None,
        analysis_session_id: str | None = None,
        analysis_transcript: str | None = None,
        analysis_result_path: str | None = None,
    ) -> None:
        """Update per-paper analysis metadata without touching unrelated fields."""
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE papers
                SET analysis_status = ?,
                    analysis_date = COALESCE(?, analysis_date),
                    analysis_model = COALESCE(?, analysis_model),
                    analysis_session_id = COALESCE(?, analysis_session_id),
                    analysis_transcript = COALESCE(?, analysis_transcript),
                    analysis_result_path = COALESCE(?, analysis_result_path),
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    analysis_status,
                    analysis_date,
                    analysis_model,
                    analysis_session_id,
                    analysis_transcript,
                    analysis_result_path,
                    paper_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_analysis_status(self, paper_id: str) -> Optional[dict]:
        """Fetch analysis metadata for one paper."""
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT analysis_status, analysis_date, analysis_model,
                       analysis_session_id, analysis_transcript, analysis_result_path
                FROM papers
                WHERE id = ?
                """,
                (paper_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def search_papers(self, domain: str = None, min_score: float = 0,
                      date_from: str = None, date_to: str = None,
                      limit: int = 100) -> list[dict]:
        """Search papers with filters."""
        conn = self._connect()
        try:
            conditions = ["1=1"]
            params = []
            if domain:
                conditions.append("domain = ?")
                params.append(domain)
            if min_score > 0:
                conditions.append("best_score >= ?")
                params.append(min_score)
            if date_from:
                conditions.append("date >= ?")
                params.append(date_from)
            if date_to:
                conditions.append("date <= ?")
                params.append(date_to)
            params.append(limit)

            query = f"SELECT * FROM papers WHERE {' AND '.join(conditions)} ORDER BY best_score DESC LIMIT ?"
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def count_papers(self) -> int:
        conn = self._connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        finally:
            conn.close()

    # ─────────────── Edge Operations ───────────────

    def add_edge(self, src_id: str, dst_id: str, edge_type: str,
                 weight: float = 1.0, metadata: dict = None) -> None:
        """Add a relationship edge between two papers."""
        assert edge_type in ALL_EDGE_TYPES, f"Invalid edge type: {edge_type}"
        conn = self._connect()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO paper_edges (src_id, dst_id, edge_type, weight, metadata)
                VALUES (?, ?, ?, ?, ?)
            """, (src_id, dst_id, edge_type, weight,
                  json.dumps(metadata, ensure_ascii=False) if metadata else None))
            conn.commit()
        finally:
            conn.close()

    def add_edges_batch(self, edges: list[tuple]) -> int:
        """Batch add edges. Each tuple: (src_id, dst_id, edge_type, weight, metadata_dict)"""
        conn = self._connect()
        try:
            for edge in edges:
                src, dst, etype, weight = edge[0], edge[1], edge[2], edge[3] if len(edge) > 3 else 1.0
                meta = json.dumps(edge[4], ensure_ascii=False) if len(edge) > 4 and edge[4] else None
                conn.execute("""
                    INSERT OR IGNORE INTO paper_edges (src_id, dst_id, edge_type, weight, metadata)
                    VALUES (?, ?, ?, ?, ?)
                """, (src, dst, etype, weight, meta))
            conn.commit()
            return len(edges)
        finally:
            conn.close()

    def get_neighbors(self, paper_id: str, edge_type: str = None,
                      direction: str = "both") -> list[dict]:
        """Get connected papers. direction: 'out', 'in', 'both'."""
        conn = self._connect()
        try:
            results = []
            if direction in ("out", "both"):
                q = "SELECT dst_id as neighbor_id, edge_type, weight FROM paper_edges WHERE src_id = ?"
                params = [paper_id]
                if edge_type:
                    q += " AND edge_type = ?"
                    params.append(edge_type)
                results.extend([dict(r) for r in conn.execute(q, params).fetchall()])

            if direction in ("in", "both"):
                q = "SELECT src_id as neighbor_id, edge_type, weight FROM paper_edges WHERE dst_id = ?"
                params = [paper_id]
                if edge_type:
                    q += " AND edge_type = ?"
                    params.append(edge_type)
                results.extend([dict(r) for r in conn.execute(q, params).fetchall()])

            return results
        finally:
            conn.close()

    def get_citation_chain(self, paper_id: str, depth: int = 2) -> dict:
        """Get citation chain up to given depth. Returns {paper_id: [cited_ids]}."""
        chain = {}
        visited = set()
        queue = [(paper_id, 0)]

        while queue:
            pid, d = queue.pop(0)
            if pid in visited or d > depth:
                continue
            visited.add(pid)

            refs = self.get_neighbors(pid, EDGE_CITES, "out")
            chain[pid] = [r["neighbor_id"] for r in refs]
            if d < depth:
                for ref in refs:
                    queue.append((ref["neighbor_id"], d + 1))

        return chain

    def count_edges(self, edge_type: str = None) -> int:
        conn = self._connect()
        try:
            if edge_type:
                return conn.execute(
                    "SELECT COUNT(*) FROM paper_edges WHERE edge_type = ?", (edge_type,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM paper_edges").fetchone()[0]
        finally:
            conn.close()

    # ─────────────── Baseline Operations ───────────────

    def add_baselines(self, paper_id: str, baselines: list[dict]) -> None:
        """Add baseline entries for a paper.
        Each baseline: {"name": str, "canonical": str|None, "context": str|None}
        """
        conn = self._connect()
        try:
            for b in baselines:
                conn.execute("""
                    INSERT OR IGNORE INTO baselines (paper_id, baseline_name, canonical_name, context)
                    VALUES (?, ?, ?, ?)
                """, (paper_id, b["name"], b.get("canonical"), b.get("context")))
            conn.commit()
        finally:
            conn.close()

    def get_papers_sharing_baseline(self, baseline_name: str) -> list[str]:
        """Find all papers that compare against a given baseline."""
        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT DISTINCT paper_id FROM baselines
                WHERE baseline_name = ? OR canonical_name = ?
            """, (baseline_name, baseline_name)).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    # ─────────────── Method Registry ───────────────

    def register_method(self, canonical_name: str, aliases: list[str] = None,
                        description: str = None, first_paper_id: str = None,
                        category: str = None) -> None:
        conn = self._connect()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO methods (canonical_name, aliases, description, first_paper_id, category)
                VALUES (?, ?, ?, ?, ?)
            """, (canonical_name,
                  json.dumps(aliases or [], ensure_ascii=False),
                  description, first_paper_id, category))
            conn.commit()
        finally:
            conn.close()

    # ─────────────── Stats ───────────────

    def stats(self) -> dict:
        conn = self._connect()
        try:
            papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
            edges = conn.execute("SELECT COUNT(*) FROM paper_edges").fetchone()[0]
            baselines = conn.execute("SELECT COUNT(DISTINCT baseline_name) FROM baselines").fetchone()[0]
            methods = conn.execute("SELECT COUNT(*) FROM methods").fetchone()[0]

            edge_counts = {}
            for row in conn.execute("SELECT edge_type, COUNT(*) FROM paper_edges GROUP BY edge_type"):
                edge_counts[row[0]] = row[1]

            return {
                "papers": papers,
                "edges": edges,
                "edge_types": edge_counts,
                "baselines": baselines,
                "methods": methods,
                "db_path": str(self.db_path),
            }
        finally:
            conn.close()

    # ─────────────── Baselines Query ───────────────

    def get_baselines_for_paper(self, paper_id: str) -> list[str]:
        """Get canonical baseline names for a paper."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT canonical_name FROM baselines WHERE paper_id = ? AND canonical_name != ''",
                (paper_id,)
            ).fetchall()
            return [r[0] for r in rows if r[0]]
        finally:
            conn.close()

    # ─────────────── Method Variants ───────────────

    def ensure_method_variants_table(self):
        """Create method_variants table if not exists."""
        conn = self._connect()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS method_variants (
                    paper_id TEXT NOT NULL,
                    base_method TEXT NOT NULL,
                    variant_tag TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (paper_id, variant_tag)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mv_base ON method_variants(base_method)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mv_tag ON method_variants(variant_tag)")
            conn.commit()
        finally:
            conn.close()

    def store_method_variants(self, paper_id: str, variants: list[dict]) -> int:
        """Store method variant tags. Returns count stored."""
        self.ensure_method_variants_table()
        conn = self._connect()
        stored = 0
        try:
            for v in variants:
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO method_variants (paper_id, base_method, variant_tag, description) VALUES (?, ?, ?, ?)",
                        (paper_id, v.get("base_method", ""), v.get("variant_tag", ""), v.get("description", ""))
                    )
                    stored += 1
                except Exception as e:
                    logger.warning(f"Failed to store variant: {e}")
            conn.commit()
        finally:
            conn.close()
        return stored

    def get_exploration_branches(self, min_papers: int = 2) -> list[dict]:
        """Detect method exploration branches: base methods with multiple variant approaches."""
        self.ensure_method_variants_table()
        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT base_method, COUNT(DISTINCT variant_tag) as n_variants,
                       COUNT(DISTINCT paper_id) as n_papers
                FROM method_variants
                GROUP BY base_method
                HAVING n_papers >= ?
                ORDER BY n_papers DESC
            """, (min_papers,)).fetchall()

            branches = []
            for base, n_variants, n_papers in rows:
                papers = conn.execute("""
                    SELECT mv.paper_id, mv.variant_tag, mv.description, p.title, p.date
                    FROM method_variants mv
                    LEFT JOIN papers p ON p.id = mv.paper_id
                    WHERE mv.base_method = ?
                    ORDER BY p.date DESC
                """, (base,)).fetchall()

                branches.append({
                    "base_method": base,
                    "variant_count": n_variants,
                    "paper_count": n_papers,
                    "papers": [
                        {"paper_id": p[0], "variant_tag": p[1], "description": p[2],
                         "title": (p[3] or "")[:60], "date": p[4] or ""}
                        for p in papers
                    ],
                })
            return branches
        finally:
            conn.close()


# ─────────────────────── CLI Test ───────────────────────

if __name__ == "__main__":
    import tempfile
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")

    # Test with temp DB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = PaperDB(db_path)

    # Test paper insert
    db.upsert_paper({
        "id": "2603.03276v1",
        "title": "Beyond Language Modeling: An Exploration of Multimodal Pretraining",
        "abstract": "We provide empirical clarity...",
        "authors": ["Shengbang Tong", "Saining Xie", "Yann LeCun"],
        "date": "2026-03-03",
        "domain": "Unified Understanding & Generation",
        "best_score": 0.72,
    })
    db.upsert_paper({
        "id": "2406.07550v1",
        "title": "An Image is Worth 32 Tokens for Reconstruction and Generation",
        "abstract": "We propose TiTok...",
        "authors": ["Qihang Yu"],
        "date": "2024-06-11",
        "domain": "1D Image Tokenizer",
        "best_score": 0.90,
    })

    # Test edges
    db.add_edge("2603.03276v1", "2406.07550v1", EDGE_CITES)
    db.add_edge("2603.03276v1", "2406.07550v1", EDGE_COMPARES_WITH, weight=0.85)

    # Test baselines
    db.add_baselines("2603.03276v1", [
        {"name": "TiTok", "canonical": "TiTok", "context": "compared against"},
        {"name": "VQVAE", "canonical": "VQ-VAE"},
    ])

    # Test queries
    p = db.get_paper("2603.03276v1")
    print(f"Paper: {p['title'][:50]}... | score={p['best_score']}")

    neighbors = db.get_neighbors("2603.03276v1")
    print(f"Neighbors: {len(neighbors)}")
    for n in neighbors:
        print(f"  → {n['neighbor_id']} ({n['edge_type']}, w={n['weight']})")

    sharing = db.get_papers_sharing_baseline("TiTok")
    print(f"Papers sharing TiTok baseline: {sharing}")

    stats = db.stats()
    print(f"\nDB Stats: {json.dumps(stats, indent=2)}")

    # Cleanup
    Path(db_path).unlink()
    print("\n✅ All PaperDB tests passed!")
