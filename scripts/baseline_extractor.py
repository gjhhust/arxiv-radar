"""
baseline_extractor.py — Extract comparison baselines from paper abstracts.

Approach (layered):
  1. Keyword/pattern matching (fast, no API cost)
  2. LLM extraction via claude CLI or OPENAI_API_KEY (optional, higher quality)

Output per paper: list of {"name": str, "canonical": str, "context": str}

Then builds COMPARES_WITH edges between papers sharing baselines,
and EXTENDS edges for papers explicitly building on another.
"""

from __future__ import annotations
import json
import logging
import os
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ─────────────── Canonical Method Aliases ───────────────

# Known CV method names and their canonical forms
CANONICAL_METHODS = {
    # Image tokenizers / codebooks
    "vq-vae": ["vqvae", "vq vae", "vector quantized vae", "van den oord"],
    "vqgan": ["vq-gan", "vq gan", "taming transformers"],
    "titok": ["titok", "1d tokenizer", "ti-tok", "an image is worth 32 tokens"],
    "llamagen": ["llamagen", "llama-gen", "autoregressive image generation llama"],
    "var": ["visual autoregressive", "var modeling", "visual ar"],
    "magvit": ["magvit", "magvit-v2", "magvit2"],
    "open-magvit2": ["open-magvit2", "open magvit"],
    # Diffusion models
    "ldm": ["ldm", "latent diffusion", "stable diffusion", "rombach"],
    "sdxl": ["sdxl", "stable diffusion xl"],
    "dit": ["dit", "diffusion transformer", "scalable diffusion transformers"],
    "flux": ["flux", "flow matching"],
    # Unified models
    "blip-3": ["blip-3", "blip3", "xgen-mm"],
    "llava": ["llava", "llava-1.5", "llava1.5", "visual instruction tuning"],
    "next-gpt": ["nextgpt", "next-gpt", "any-to-any"],
    "show-o": ["show-o", "showo"],
    "janus": ["janus", "janus-pro"],
    "bagel": ["bagel", "bootstrapping agents"],
    "emu3": ["emu3", "emu-3"],
    "chameleon": ["chameleon"],
    "anole": ["anole"],
    # Vision encoders
    "clip": ["clip", "contrastive language-image"],
    "dinov2": ["dinov2", "dino v2"],
    "mae": ["mae", "masked autoencoders", "he et al"],
    "rae": ["rae", "reconstruction autoencoder"],
    # Benchmarks
    "imagenet": ["imagenet", "ilsvrc"],
    "coco": ["coco", "ms-coco"],
    "vqav2": ["vqav2", "vqa v2"],
    "mmstar": ["mmstar"],
}

# Build reverse lookup: alias → canonical
_ALIAS_TO_CANONICAL: dict[str, str] = {}
for canonical, aliases in CANONICAL_METHODS.items():
    for alias in aliases:
        _ALIAS_TO_CANONICAL[alias.lower()] = canonical
    _ALIAS_TO_CANONICAL[canonical.lower()] = canonical

# Patterns that indicate comparison / building on
COMPARE_PATTERNS = [
    r"(?:compar(?:e|ed|ing)|evaluat(?:e|ed|ing)|benchmarked? against|outperform(?:s|ed)?|surpass(?:es|ed)?)\s+(?:[\w\s,-]+?(?:and\s+)?){0,3}(?:(?:[A-Z][\w-]+(?:\s*[v]?\d+\.?\d*)?){1,3})",
    r"(?:over|vs\.?|versus)\s+((?:[A-Z][\w-]+\s*){1,4})",
    r"baselines?\s+(?:include|:)\s+((?:[A-Z][\w-][\w\s,]+?)(?:\.|;|$))",
    r"we compare (?:with|against|to)\s+((?:[A-Z][\w\s,-]+?)(?:\.|,\s+and|\sand))",
    r"state.of.the.art (?:methods?|models?|approaches?|baselines?)[:\s]+([A-Z][\w\s,-]+?)(?:\.|;|$)",
]

EXTENDS_PATTERNS = [
    r"(?:build(?:ing)? (?:upon|on)|extend(?:s|ing|ed)|based on|follow(?:s|ing))\s+([A-Z][\w-]+(?:\s+\[\d+\])?)",
    r"(?:our|the) (?:work|method|approach|model|framework) (?:extends?|builds? on)\s+([A-Z][\w-]+)",
    r"(?:inspired by|following|adapting)\s+([A-Z][\w-]+(?:\s+\[\d+\])?)",
]


def normalize_method_name(raw_name: str) -> str:
    """Normalize a method name to its canonical form."""
    clean = raw_name.strip().lower()
    # Direct lookup
    if clean in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[clean]
    # Partial match
    for alias, canonical in _ALIAS_TO_CANONICAL.items():
        if alias in clean or clean in alias:
            return canonical
    # Return cleaned original
    return raw_name.strip()


def extract_baselines_keyword(paper: dict) -> list[dict]:
    """Fast keyword/pattern-based baseline extraction from abstract + title."""
    text = (paper.get("title", "") + ". " + paper.get("abstract", ""))

    found = {}  # name → {"canonical": ..., "context": ...}

    # 1. Known method scanning
    text_lower = text.lower()
    for alias, canonical in _ALIAS_TO_CANONICAL.items():
        if len(alias) < 3:
            continue
        if alias in text_lower:
            # Try to extract surrounding context
            idx = text_lower.find(alias)
            ctx_start = max(0, idx - 40)
            ctx_end = min(len(text), idx + len(alias) + 40)
            context = text[ctx_start:ctx_end].strip()
            if canonical not in found:
                found[canonical] = {"name": alias, "canonical": canonical, "context": context}

    # 2. Pattern-based extraction for less common names
    for pattern in COMPARE_PATTERNS:
        try:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                if m.lastindex is None or m.lastindex < 1:
                    continue
                raw = m.group(1).strip()
                # Split on comma/and to get individual names
                for part in re.split(r",\s*|\s+and\s+", raw):
                    name = part.strip().rstrip(".,;")
                    if len(name) >= 3 and name[0].isupper():
                        canonical = normalize_method_name(name)
                        if canonical not in found:
                            found[canonical] = {
                                "name": name,
                                "canonical": canonical,
                                "context": m.group(0)[:100],
                            }
        except (IndexError, re.error):
            continue

    return list(found.values())


def extract_extends_keyword(paper: dict) -> list[str]:
    """Extract papers this paper explicitly extends/builds upon."""
    text = paper.get("abstract", "") + " " + paper.get("title", "")
    extends = []
    for pattern in EXTENDS_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            name = m.group(1).strip().rstrip(".,;[]0123456789 ")
            if len(name) >= 3:
                extends.append(normalize_method_name(name))
    return list(set(extends))


def _llm_extract_baselines(papers: list[dict]) -> dict[str, list[dict]]:
    """Use LLM to extract baselines more accurately. Returns {paper_id: [baselines]}."""
    prompt_lines = ["Extract comparison baselines from these CV papers. Return JSON only:\n"
                    '{"results":[{"id":"...","baselines":["method1","method2"],"extends":["method3"]}]}']
    for p in papers:
        abstract = p.get("abstract", "")[:400]
        prompt_lines.append(f'\nID:{p["id"]}|TITLE:{p["title"][:80]}\nABS:{abstract}')
    prompt = "\n".join(prompt_lines)

    # Try claude CLI
    raw = None
    try:
        result = subprocess.run(
            ["claude", "--permission-mode", "bypassPermissions", "--print", "--output-format", "text", "-p", prompt],
            capture_output=True, text=True, timeout=60, cwd="/tmp"
        )
        if result.returncode == 0:
            raw = result.stdout.strip()
    except Exception:
        pass

    # Try OpenAI API
    if not raw:
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            try:
                payload = json.dumps({"model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1500, "temperature": 0}).encode()
                req = urllib.request.Request(
                    f"{os.environ.get('OPENAI_BASE_URL','https://api.openai.com/v1')}/chat/completions",
                    data=payload,
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
                    raw = data["choices"][0]["message"]["content"].strip()
            except Exception:
                pass

    if not raw:
        return {}

    # Parse
    try:
        text = raw
        if "```" in text:
            text = re.sub(r"```\w*\n?", "", text)
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            data = json.loads(m.group())
            results = {}
            for item in data.get("results", []):
                pid = item["id"]
                baselines = [{"name": b, "canonical": normalize_method_name(b), "context": "llm-extracted"}
                             for b in item.get("baselines", [])]
                extends = item.get("extends", [])
                results[pid] = {"baselines": baselines, "extends": extends}
            return results
    except Exception as e:
        logger.warning(f"LLM baseline parse failed: {e}")
    return {}


def extract_baselines_batch(papers: list[dict], use_llm: bool = False) -> dict[str, dict]:
    """
    Extract baselines for a batch of papers.

    Args:
        papers: list of paper dicts
        use_llm: whether to try LLM for higher quality (slower, costs API)

    Returns:
        dict: paper_id → {"baselines": [...], "extends": [...]}
    """
    results = {}

    # LLM extraction (optional)
    llm_results = {}
    if use_llm:
        llm_results = _llm_extract_baselines(papers)

    for paper in papers:
        pid = paper["id"]
        if pid in llm_results:
            results[pid] = llm_results[pid]
        else:
            baselines = extract_baselines_keyword(paper)
            extends = extract_extends_keyword(paper)
            results[pid] = {"baselines": baselines, "extends": extends}

    return results


def build_method_edges(paper_id: str, extends: list[str], all_papers: list[dict], db) -> int:
    """
    Build EXTENDS edges: paper_id EXTENDS papers with matching method names.
    Returns count of edges added.
    """
    from paper_db import EDGE_EXTENDS
    count = 0
    # Try to find any known paper in DB that matches the extended method name
    for method_name in extends:
        rows = db.search_papers(limit=200)
        for candidate in rows:
            if (method_name.lower() in candidate["title"].lower() or
                    (candidate.get("cn_oneliner") and method_name.lower() in candidate.get("cn_oneliner", "").lower())):
                if candidate["id"] != paper_id:
                    db.add_edge(paper_id, candidate["id"], EDGE_EXTENDS,
                                metadata={"method": method_name})
                    count += 1
                    break
    return count


def build_compares_with_edges(db) -> int:
    """
    For papers sharing the same baseline, build COMPARES_WITH edges.
    Returns count of edges added.
    """
    from paper_db import EDGE_COMPARES_WITH
    conn = db._connect()
    try:
        rows = conn.execute("""
            SELECT canonical_name, GROUP_CONCAT(paper_id) as paper_ids, COUNT(*) as cnt
            FROM baselines
            WHERE canonical_name IS NOT NULL AND canonical_name != ''
            GROUP BY canonical_name
            HAVING cnt > 1
        """).fetchall()

        edges_added = 0
        for row in rows:
            canonical = row[0]
            paper_ids = row[1].split(",")
            for i, pid_a in enumerate(paper_ids):
                for pid_b in paper_ids[i+1:]:
                    db.add_edge(pid_a, pid_b, EDGE_COMPARES_WITH,
                                weight=0.8, metadata={"shared_baseline": canonical})
                    db.add_edge(pid_b, pid_a, EDGE_COMPARES_WITH,
                                weight=0.8, metadata={"shared_baseline": canonical})
                    edges_added += 2
        return edges_added
    finally:
        conn.close()


def process_papers(papers: list[dict], db, use_llm: bool = False) -> dict:
    """
    Full P2 pipeline: extract baselines → write to DB → build COMPARES_WITH edges.

    Returns stats dict.
    """
    if not papers:
        return {"processed": 0, "baselines_total": 0, "compares_edges": 0, "extends_edges": 0}

    logger.info(f"Extracting baselines for {len(papers)} papers (llm={use_llm})...")
    results = extract_baselines_batch(papers, use_llm=use_llm)

    # Write baselines to DB
    total_baselines = 0
    for paper in papers:
        pid = paper["id"]
        data = results.get(pid, {})
        baselines = data.get("baselines", [])
        if baselines:
            db.add_baselines(pid, baselines)
            total_baselines += len(baselines)

    # Build COMPARES_WITH edges from shared baselines
    compares_edges = build_compares_with_edges(db)

    # Build EXTENDS edges
    extends_edges = 0
    for paper in papers:
        pid = paper["id"]
        data = results.get(pid, {})
        extends = data.get("extends", [])
        if extends:
            extends_edges += build_method_edges(pid, extends, papers, db)

    stats = {
        "processed": len(papers),
        "baselines_total": total_baselines,
        "compares_edges": compares_edges,
        "extends_edges": extends_edges,
    }
    logger.info(f"P2 done: {stats}")
    return stats


# ─────────────────────── CLI Test ───────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")

    test_papers = [
        {
            "id": "2603.03276v1",
            "title": "Beyond Language Modeling: An Exploration of Multimodal Pretraining",
            "abstract": "We compare against BLIP-3, LLaVA-1.5, and CLIP. Our model outperforms TiTok and VQGAN on reconstruction. We build upon MAE and RAE, extending the autoencoder paradigm to unified multimodal training.",
        },
        {
            "id": "2603.06449v1",
            "title": "CaTok: Taming Mean Flows for One-Dimensional Causal Image Tokenization",
            "abstract": "We compare against TiTok, LlamaGen, and VAR. Unlike prior tokenizers that use VQVAE or VQGAN codebooks, CaTok achieves better perplexity. We benchmark against ImageNet class-conditional generation.",
        },
        {
            "id": "2603.04980v1",
            "title": "Wallaroo: Unifying Understanding, Generation, and Editing",
            "abstract": "Building on LLaVA and following Chameleon's unified approach, Wallaroo surpasses Show-o and Janus in both understanding and generation. We compare against BLIP-3, LLaVA-1.5, and Emu3.",
        },
    ]

    print("=== Baseline Extractor Test ===\n")
    results = extract_baselines_batch(test_papers, use_llm=False)

    for paper in test_papers:
        pid = paper["id"]
        data = results[pid]
        print(f"[{pid}] {paper['title'][:60]}")
        print(f"  Baselines ({len(data['baselines'])}):")
        for b in data["baselines"][:5]:
            print(f"    - {b['canonical']} (raw: {b['name']})")
        print(f"  Extends: {data['extends']}")
        print()

    print("✅ Baseline extraction test complete!")
