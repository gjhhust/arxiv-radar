"""
filter.py — Semantic similarity filter for arxiv-radar.

Pipeline:
  1. Fast keyword pre-filter (blocklist) - removes noise domain papers
  2. Sentence embedding similarity vs seed papers
  3. Domain assignment and threshold filtering
  4. Optional LLM judge for borderline papers

This is the most critical module. Threshold tuning is key.
"""

from __future__ import annotations
import re
import logging
import os
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ─────────────────────────── Noise Pre-filter ───────────────────────────

def noise_pre_filter(
    papers: list[dict],
    noise_keywords: list[str],
    strict: bool = True,
) -> tuple[list[dict], list[dict]]:
    """
    Fast keyword-based pre-filter. Removes papers from noise domains BEFORE
    expensive embedding computation.

    Returns:
        (kept_papers, rejected_papers)
    """
    kept, rejected = [], []

    # Compile patterns for fast matching
    patterns = [re.compile(r"\b" + re.escape(kw.lower()) + r"\b")
                for kw in noise_keywords]

    for paper in papers:
        text = (paper["title"] + " " + paper["abstract"]).lower()
        is_noise = any(p.search(text) for p in patterns)
        (rejected if is_noise else kept).append(paper)

    logger.info(
        f"Noise pre-filter: {len(papers)} → {len(kept)} kept, "
        f"{len(rejected)} rejected"
    )
    return kept, rejected


# ─────────────────────────── Embedding Engine ───────────────────────────

_model_cache: dict[str, Any] = {}


def _get_model(model_name: str):
    """Load and cache sentence-transformer model."""
    if model_name not in _model_cache:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers not installed. "
                "Run: pip install sentence-transformers"
            )
        logger.info(f"Loading embedding model: {model_name}")
        _model_cache[model_name] = SentenceTransformer(model_name)
    return _model_cache[model_name]


def embed_texts(texts: list[str], model_name: str = "all-MiniLM-L6-v2") -> np.ndarray:
    """Embed a list of texts. Returns (N, D) float32 array."""
    model = _get_model(model_name)
    embeddings = model.encode(texts, batch_size=64, show_progress_bar=False,
                               normalize_embeddings=True)
    return embeddings


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two normalized vectors."""
    # Both normalized (via normalize_embeddings=True), so dot product = cosine sim
    return float(np.dot(a, b))


# ─────────────────────────── Core Filter ───────────────────────────

def filter_papers(
    papers: list[dict],
    config: dict,
    domains: list[dict],
) -> dict:
    """
    Main entry point. Filter papers by semantic similarity to domain seeds.

    Args:
        papers: List of paper dicts (from crawler)
        config: Parsed config dict
        domains: List of domain dicts (each has name, seed_text, keywords)

    Returns:
        {
          "domain_0": [paper_dicts],   # papers assigned to domain 0
          "domain_1": [paper_dicts],   # papers assigned to domain 1
          ...
          "unmatched": [paper_dicts],  # below threshold for all domains
          "rejected_noise": [paper_dicts],
          "stats": {...}
        }
    """
    noise_keywords = config.get("noise_keywords", [])
    model_name = config.get("embedding_model", "all-MiniLM-L6-v2")
    threshold = float(config.get("similarity_threshold", 0.35))
    mode = config.get("threshold_mode", "adaptive")
    top_k = int(config.get("adaptive_top_k", 30))

    total_input = len(papers)

    # Step 1: Noise pre-filter
    if noise_keywords:
        papers, rejected_noise = noise_pre_filter(
            papers, noise_keywords,
            strict=config.get("noise_filter_strict", True)
        )
    else:
        rejected_noise = []

    if not papers:
        return {
            f"domain_{i}": [] for i in range(len(domains))
        } | {"unmatched": [], "rejected_noise": rejected_noise,
             "stats": {"total_input": total_input, "after_noise": 0}}

    # Step 2: Prepare seed embeddings
    logger.info("Building seed embeddings...")
    seed_embeddings = []
    for domain in domains:
        seed_text = domain.get("seed_text", "")
        if not seed_text:
            # Fall back to keywords as seed text
            seed_text = " ".join(domain.get("keywords", []))
        emb = embed_texts([seed_text], model_name)[0]
        seed_embeddings.append(emb)

    # Step 3: Embed all paper abstracts (batch for speed)
    logger.info(f"Embedding {len(papers)} papers...")
    paper_texts = [
        p["title"] + ". " + p["abstract"][:1000]  # truncate long abstracts
        for p in papers
    ]
    paper_embeddings = embed_texts(paper_texts, model_name)

    # Step 4: Compute similarity scores per domain
    for i, paper in enumerate(papers):
        scores = {}
        for j, (domain, seed_emb) in enumerate(zip(domains, seed_embeddings)):
            score = cosine_similarity(paper_embeddings[i], seed_emb)
            scores[domain["name"]] = round(float(score), 4)

        paper["similarity_scores"] = scores

        # Best domain
        if scores:
            best_domain = max(scores, key=scores.get)
            paper["best_domain"] = best_domain
            paper["best_score"] = scores[best_domain]
        else:
            paper["best_domain"] = None
            paper["best_score"] = 0.0

    # Step 5: Apply threshold and assign to domains
    result = {f"domain_{i}": [] for i in range(len(domains))}
    result["unmatched"] = []
    result["rejected_noise"] = rejected_noise

    domain_names = [d["name"] for d in domains]

    if mode == "fixed":
        _apply_fixed_threshold(papers, domains, threshold, result, domain_names)

    elif mode == "adaptive":
        _apply_adaptive_threshold(papers, domains, top_k, threshold, result, domain_names)

    elif mode == "hybrid":
        # Adaptive but with a minimum floor
        _apply_adaptive_threshold(papers, domains, top_k, threshold * 0.7, result, domain_names)

    else:
        # Default to adaptive
        _apply_adaptive_threshold(papers, domains, top_k, threshold, result, domain_names)

    # Step 6: Stats
    total_filtered = sum(len(result[f"domain_{i}"]) for i in range(len(domains)))
    result["stats"] = {
        "total_input": total_input,
        "after_noise_filter": len(papers),
        "noise_rejected": len(rejected_noise),
        "total_filtered": total_filtered,
        "unmatched": len(result["unmatched"]),
        "per_domain": {
            domain["name"]: len(result[f"domain_{i}"])
            for i, domain in enumerate(domains)
        },
        "threshold_mode": mode,
        "threshold": threshold,
    }

    logger.info(
        f"Filter complete: {total_input} → {total_filtered} relevant papers "
        f"({result['stats']['per_domain']})"
    )
    return result


def _apply_fixed_threshold(papers, domains, threshold, result, domain_names):
    """Assign papers to domains using a fixed threshold."""
    for paper in papers:
        assigned = False
        for i, domain in enumerate(domains):
            score = paper["similarity_scores"].get(domain["name"], 0.0)
            if score >= threshold:
                result[f"domain_{i}"].append(paper)
                assigned = True
                break  # assign to first matching domain (best score first)
        if not assigned:
            result["unmatched"].append(paper)


def _apply_adaptive_threshold(papers, domains, top_k, floor_threshold, result, domain_names):
    """
    Adaptive threshold: keep top-K papers per domain, but enforce a minimum floor.

    Each paper can only be assigned to one domain (best scoring one).
    """
    # Group by best domain
    domain_buckets: dict[int, list[dict]] = {i: [] for i in range(len(domains))}
    unmatched_candidates = []

    for paper in papers:
        best_score = paper.get("best_score", 0.0)
        best_domain_name = paper.get("best_domain")

        if best_domain_name and best_score >= floor_threshold:
            # Find domain index
            for i, domain in enumerate(domains):
                if domain["name"] == best_domain_name:
                    domain_buckets[i].append(paper)
                    break
        else:
            unmatched_candidates.append(paper)

    # For each domain, keep only top-K by score
    for i, domain in enumerate(domains):
        domain_name = domain["name"]
        bucket = sorted(
            domain_buckets[i],
            key=lambda p: p["similarity_scores"].get(domain_name, 0),
            reverse=True,
        )
        kept = bucket[:top_k]
        not_kept = bucket[top_k:]
        result[f"domain_{i}"] = kept
        unmatched_candidates.extend(not_kept)

    result["unmatched"] = sorted(
        unmatched_candidates,
        key=lambda p: p.get("best_score", 0),
        reverse=True,
    )


# ─────────────────────────── CLI / Standalone Test ───────────────────────────

if __name__ == "__main__":
    import json
    from pathlib import Path

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # ── Mock papers for testing ──
    MOCK_PAPERS = [
        # Domain 1 relevant (1D tokenizer / image generation)
        {"id": "test_001", "title": "Efficient Image Tokenization with 1D Latent Codes",
         "abstract": "We propose a novel 1D tokenizer for images using VQ-VAE that reduces the token sequence length to 16 tokens while maintaining reconstruction quality. Our codebook-based approach enables efficient autoregressive image generation.", "authors": ["Alice Wang"], "labels": [], "date": "2024-06-01"},
        {"id": "test_002", "title": "Discrete Visual Tokens for High-Quality Image Synthesis",
         "abstract": "We introduce a compact visual tokenizer leveraging region redundancy in natural images. By encoding images into a 1D sequence of discrete tokens, we achieve state-of-the-art performance on ImageNet generation benchmarks.", "authors": ["Bob Chen"], "labels": [], "date": "2024-06-01"},
        {"id": "test_003", "title": "VQVAE-based Image Reconstruction with Codebook Learning",
         "abstract": "This paper presents an improved VQVAE architecture for image reconstruction. We introduce a novel codebook learning strategy that reduces codebook collapse and improves reconstruction quality on ImageNet.", "authors": ["Carol Lee"], "labels": [], "date": "2024-06-01"},
        {"id": "test_004", "title": "Scalable Autoregressive Image Generation via Compact Tokens",
         "abstract": "We present a scalable autoregressive image generation model that operates on compact 1D token representations. By reducing image token sequences from 256 to 32, our model achieves 10x speedup with comparable generation quality.", "authors": ["David Kim"], "labels": [], "date": "2024-06-01"},
        {"id": "test_005", "title": "ViT-based Discrete Representation Learning for Vision",
         "abstract": "We propose a vision transformer architecture for learning discrete visual representations. The model learns a hierarchical codebook for efficient image encoding and demonstrates strong transfer learning performance.", "authors": ["Eva Martinez"], "labels": [], "date": "2024-06-01"},

        # Domain 2 relevant (unified understanding & generation)
        {"id": "test_006", "title": "Joint Training for Visual Understanding and Generation",
         "abstract": "We present a unified framework for simultaneous visual understanding and image generation. By jointly training on both tasks, the model develops emergent multimodal capabilities that surpass specialized models.", "authors": ["Kaiming He"], "labels": [], "date": "2024-06-01"},
        {"id": "test_007", "title": "Unified Multimodal Foundation Model for Vision-Language Tasks",
         "abstract": "This work introduces a unified multimodal model that handles text-to-image generation, image captioning, and visual question answering within a single architecture. Joint pretraining leads to strong performance across all tasks.", "authors": ["Saining Xie"], "labels": [], "date": "2024-06-01"},
        {"id": "test_008", "title": "Emerging Properties in Large-Scale Multimodal Pretraining",
         "abstract": "We study emergent capabilities in large multimodal models trained on diverse understanding and generation objectives. Our model demonstrates surprising compositional reasoning abilities not present in smaller models.", "authors": ["Frank Zhang"], "labels": [], "date": "2024-06-01"},
        {"id": "test_009", "title": "Diffusion-based Unified Visual Understanding and Generation",
         "abstract": "We propose a diffusion model that unifies visual understanding and image generation in a single framework. The model achieves state-of-the-art results on both text-to-image synthesis and visual recognition benchmarks.", "authors": ["Grace Liu"], "labels": [], "date": "2024-06-01"},
        {"id": "test_010", "title": "MoE Architecture for Efficient Multimodal Understanding",
         "abstract": "This paper presents a mixture-of-experts approach for efficient multimodal learning. By routing different input types to specialized experts, our model achieves better performance on multimodal benchmarks with lower computational cost.", "authors": ["Henry Park"], "labels": [], "date": "2024-06-01"},

        # Irrelevant (noise - should be filtered)
        {"id": "noise_001", "title": "Deep Learning for Medical Image Segmentation",
         "abstract": "We present a deep learning approach for medical image segmentation of tumors in CT scans. Our clinical model achieves state-of-the-art performance on the LIDC-IDRI dataset for lung cancer detection.", "authors": ["Iris Brown"], "labels": [], "date": "2024-06-01"},
        {"id": "noise_002", "title": "Predicting Stock Market Returns with Neural Networks",
         "abstract": "We propose a transformer model for financial market prediction using historical stock prices. Our model achieves superior portfolio returns with lower risk compared to traditional trading strategies.", "authors": ["Jack Wilson"], "labels": [], "date": "2024-06-01"},
        {"id": "noise_003", "title": "Protein Structure Prediction with Molecular Dynamics",
         "abstract": "We introduce a new approach for protein folding using molecular simulation. Our method integrates genome sequencing data with deep learning to predict 3D protein structures from DNA sequences.", "authors": ["Kate Davis"], "labels": [], "date": "2024-06-01"},
        {"id": "noise_004", "title": "GPT for Legal Document Understanding",
         "abstract": "We fine-tune large language models for legal contract analysis. Our model can identify clauses, obligations, and risks in legal documents, demonstrating strong performance on court cases and legal text.", "authors": ["Liam Taylor"], "labels": [], "date": "2024-06-01"},
        {"id": "noise_005", "title": "Sentiment Analysis for Financial News",
         "abstract": "This paper presents a system for analyzing financial news sentiment to predict stock trading signals. We use BERT-based models trained on financial text to generate portfolio recommendations.", "authors": ["Mia Johnson"], "labels": [], "date": "2024-06-01"},

        # Borderline (not clearly relevant or irrelevant)
        {"id": "border_001", "title": "Self-Supervised Learning for Visual Representations",
         "abstract": "We propose a new self-supervised learning method for learning visual representations from unlabeled images. Our contrastive approach learns semantically meaningful features that transfer well to downstream tasks.", "authors": ["Noah Williams"], "labels": [], "date": "2024-06-01"},
        {"id": "border_002", "title": "Efficient Transformers for Image Recognition",
         "abstract": "We present an efficient vision transformer that reduces computational complexity while maintaining accuracy. Our model uses sparse attention and token pruning to process high-resolution images efficiently.", "authors": ["Olivia Brown"], "labels": [], "date": "2024-06-01"},
        {"id": "border_003", "title": "Neural Architecture Search for Vision Models",
         "abstract": "We propose an automated neural architecture search method for finding efficient vision model architectures. Our search space includes modern components like attention mechanisms and convolutional layers.", "authors": ["Peter Chen"], "labels": [], "date": "2024-06-01"},
    ]

    # Load seed texts
    skill_dir = Path(__file__).parent.parent
    domains = [
        {
            "name": "1D Image Tokenizer",
            "seed_text": (skill_dir / "data/seeds/domain1_titok.txt").read_text(),
            "keywords": ["image tokenizer", "1D token", "VQVAE"],
        },
        {
            "name": "Unified Understanding & Generation",
            "seed_text": (skill_dir / "data/seeds/domain2_bagel.txt").read_text(),
            "keywords": ["unified model", "multimodal", "image generation"],
        },
    ]

    config = {
        "noise_keywords": [
            "medical", "clinical", "patient", "hospital", "drug", "cancer",
            "tumor", "financial", "stock", "trading", "cryptocurrency",
            "legal", "law", "contract", "court",
            "chemistry", "molecular", "protein", "genome", "DNA",
        ],
        "embedding_model": "all-MiniLM-L6-v2",
        "similarity_threshold": 0.30,
        "threshold_mode": "adaptive",
        "adaptive_top_k": 8,
        "noise_filter_strict": True,
    }

    print("=== Semantic Filter Standalone Test ===\n")
    result = filter_papers(MOCK_PAPERS, config, domains)

    print("\n📊 Results:")
    print(f"  Noise rejected: {len(result['rejected_noise'])}")
    print(f"  Domain 1 (1D Tokenizer): {len(result['domain_0'])} papers")
    print(f"  Domain 2 (Unified): {len(result['domain_1'])} papers")
    print(f"  Unmatched: {len(result['unmatched'])} papers")

    print("\n✅ Domain 1 papers (should be test_001-005):")
    for p in result["domain_0"]:
        score = p["similarity_scores"].get("1D Image Tokenizer", 0)
        print(f"  [{p['id']}] score={score:.3f} — {p['title'][:55]}...")

    print("\n✅ Domain 2 papers (should be test_006-010):")
    for p in result["domain_1"]:
        score = p["similarity_scores"].get("Unified Understanding & Generation", 0)
        print(f"  [{p['id']}] score={score:.3f} — {p['title'][:55]}...")

    print("\n❌ Noise rejected (should be noise_001-005):")
    for p in result["rejected_noise"]:
        print(f"  [{p['id']}] {p['title'][:55]}...")

    print("\n📊 Unmatched / borderline:")
    for p in result["unmatched"]:
        score = p.get("best_score", 0)
        print(f"  [{p['id']}] best_score={score:.3f} — {p['title'][:55]}...")

    # Threshold analysis
    print("\n\n=== THRESHOLD ANALYSIS ===")
    print("Testing different thresholds on clean papers (no noise):")
    clean_papers = [p for p in MOCK_PAPERS if not p["id"].startswith("noise")]
    all_scored = filter_papers(
        clean_papers,
        {**config, "threshold_mode": "fixed", "similarity_threshold": 0.0,
         "adaptive_top_k": 100},
        domains,
    )
    all_relevant = all_scored["domain_0"] + all_scored["domain_1"] + all_scored["unmatched"]

    for thresh in [0.20, 0.25, 0.30, 0.35, 0.40, 0.45]:
        kept = [p for p in all_relevant if p.get("best_score", 0) >= thresh]
        print(f"  threshold={thresh:.2f}: {len(kept)}/{len(all_relevant)} papers kept")

    print("\n✅ Filter module test complete!")
