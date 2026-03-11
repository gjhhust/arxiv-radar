"""
recommender.py — Sub-agent based paper recommendation for arxiv-radar.

For each domain, selects 1-3 must-read papers using:
  1. Scoring function (semantic similarity + label bonuses)
  2. Optional LLM call for "why_read" explanation
  3. Returns structured recommendations with reading rationale
"""

from __future__ import annotations
import logging
import os
import json
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────── Scoring ───────────────────────────

def score_paper(paper: dict, domain_name: str) -> float:
    """
    Score a paper for recommendation priority.

    Factors:
    - Semantic similarity to domain seed (base score)
    - VIP author bonus (+0.10)
    - Open-source bonus (+0.05)
    - Oral/spotlight bonus (+0.03)
    - From major lab bonus (+0.02)
    """
    base_score = paper.get("similarity_scores", {}).get(domain_name, 0.0)

    # If best domain matches, use best_score for extra boost
    if paper.get("best_domain") == domain_name:
        base_score = max(base_score, paper.get("best_score", 0.0))

    bonus = 0.0
    labels = paper.get("labels", [])

    # VIP bonus (extra weight for notable researchers)
    vip_count = sum(1 for l in labels if "VIP:" in l)
    bonus += 0.10 * min(vip_count, 2)  # cap at 2 VIPs

    # Open source bonus
    if any("open-source" in l for l in labels):
        bonus += 0.05

    # Quality bonus
    if any("oral" in l for l in labels):
        bonus += 0.03
    if any("spotlight" in l for l in labels):
        bonus += 0.02

    # Lab bonus (small)
    if any("🏢" in l for l in labels):
        bonus += 0.02

    return round(base_score + bonus, 4)


# ─────────────────────────── Why-Read Generator ───────────────────────────

def _generate_why_read_template(paper: dict, domain_name: str, rank: int) -> str:
    """
    Generate a "why read" explanation using templates (no LLM cost).
    Used as fallback or when LLM is not configured.
    """
    labels = paper.get("labels", [])
    score = paper.get("_recommend_score", 0)

    # Build descriptor phrases
    descriptors = []

    vip_names = [l.replace("⭐ VIP:", "") for l in labels if "VIP:" in l]
    if vip_names:
        descriptors.append(f"by {', '.join(vip_names[:2])}")

    if any("open-source" in l for l in labels):
        descriptors.append("with open-source code")

    if any("oral" in l for l in labels):
        descriptors.append("oral presentation")

    labs = [l.replace("🏢 ", "") for l in labels if "🏢" in l]
    if labs:
        descriptors.append(f"from {labs[0]}")

    descriptor_str = f" ({', '.join(descriptors)})" if descriptors else ""

    # Generate template-based rationale
    if rank == 0:
        prefix = f"Top pick for **{domain_name}**{descriptor_str}."
    elif rank == 1:
        prefix = f"Strong follow-up in **{domain_name}**{descriptor_str}."
    else:
        prefix = f"Notable work in **{domain_name}**{descriptor_str}."

    # Extract key phrases from abstract
    abstract = paper.get("abstract", "")
    first_sentence = abstract.split(". ")[0].rstrip(".") + "." if abstract else ""

    return f"{prefix} {first_sentence[:200]}"


def _generate_why_read_llm(paper: dict, domain_name: str, user_context: str) -> str | None:
    """
    Call a lightweight LLM to generate a why-read explanation.
    Returns None if LLM is not available or fails.
    """
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import urllib.request

        prompt = f"""You are helping a computer vision researcher (working on visual representation and unified representation) quickly evaluate if a paper is worth reading.

Paper: {paper['title']}
Abstract: {paper['abstract'][:500]}
Domain: {domain_name}
Labels: {', '.join(paper.get('labels', [])) or 'none'}

In 1-2 sentences, explain specifically why this paper matters for visual representation research. Be concrete and technical. No fluff."""

        # Try OpenAI-compatible API
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model = os.environ.get("ARXIV_RADAR_LLM_MODEL", "gpt-4o-mini")

        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 100,
            "temperature": 0.3,
        }).encode()

        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()

    except Exception as e:
        logger.debug(f"LLM why_read failed: {e}")
        return None


# ─────────────────────────── Main Recommender ───────────────────────────

def recommend(
    domain_papers: dict,
    config: dict,
    domains: list[dict] | None = None,
) -> dict:
    """
    Select top-K must-read papers per domain.

    Args:
        domain_papers: Output from filter_papers() — {"domain_0": [...], "domain_1": [...]}
        config: Parsed config dict
        domains: Optional domain definitions list

    Returns:
        {
          "domain_0": {
            "name": "1D Image Tokenizer",
            "recommendations": [
              {
                "rank": 1,
                "paper": {...},
                "score": 0.87,
                "why_read": "...",
              },
              ...
            ]
          },
          ...
        }
    """
    top_k = int(config.get("top_k_recommend", 2))
    use_llm = config.get("use_llm_why_read", False)
    user_context = config.get("research_background", "CV researcher in visual representation")

    result = {}

    # Collect domain names from domains list or config
    domain_names = {}
    if domains:
        for i, d in enumerate(domains):
            domain_names[f"domain_{i}"] = d["name"]
    else:
        # Try to infer from paper scores
        for key, papers in domain_papers.items():
            if key.startswith("domain_") and papers:
                domain_names[key] = papers[0].get("best_domain", key)

    for domain_key, papers in domain_papers.items():
        if not domain_key.startswith("domain_"):
            continue
        if not papers:
            domain_name = domain_names.get(domain_key, domain_key)
            result[domain_key] = {"name": domain_name, "recommendations": []}
            continue

        domain_name = domain_names.get(domain_key, domain_key)

        # Score and rank all papers in this domain
        scored = []
        for paper in papers:
            s = score_paper(paper, domain_name)
            paper["_recommend_score"] = s
            scored.append((s, paper))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_papers = scored[:top_k]

        recommendations = []
        for rank, (score, paper) in enumerate(top_papers):
            # Generate why_read
            why_read = None
            if use_llm:
                why_read = _generate_why_read_llm(paper, domain_name, user_context)

            if not why_read:
                why_read = _generate_why_read_template(paper, domain_name, rank)

            recommendations.append({
                "rank": rank + 1,
                "paper": paper,
                "score": score,
                "why_read": why_read,
            })

        result[domain_key] = {
            "name": domain_name,
            "recommendations": recommendations,
        }

        logger.info(
            f"Domain '{domain_name}': recommended {len(recommendations)} papers "
            f"from pool of {len(papers)}"
        )

    return result


# ─────────────────────────── CLI Test ───────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Mock domain_papers (as if from filter_papers)
    mock_domain_papers = {
        "domain_0": [
            {
                "id": "test_001", "title": "Efficient Image Tokenization with 1D Latent Codes",
                "abstract": "We propose a novel 1D tokenizer for images using VQ-VAE that reduces token length to 16 while maintaining reconstruction quality. Code available at github.com/example/1dtok",
                "authors": ["Kaiming He", "Alice Wang"],
                "labels": ["⭐ VIP:Kaiming He", "🔓 open-source"],
                "similarity_scores": {"1D Image Tokenizer": 0.82},
                "best_domain": "1D Image Tokenizer", "best_score": 0.82,
            },
            {
                "id": "test_002", "title": "Discrete Visual Tokens for High-Quality Image Synthesis",
                "abstract": "We introduce a compact visual tokenizer leveraging region redundancy in natural images for state-of-the-art generation.",
                "authors": ["Bob Chen"],
                "labels": [],
                "similarity_scores": {"1D Image Tokenizer": 0.75},
                "best_domain": "1D Image Tokenizer", "best_score": 0.75,
            },
            {
                "id": "test_003", "title": "VQVAE-based Image Reconstruction with Codebook Learning",
                "abstract": "An improved VQVAE architecture for image reconstruction with novel codebook learning strategy.",
                "authors": ["Carol Lee", "Saining Xie"],
                "labels": ["⭐ VIP:Saining Xie"],
                "similarity_scores": {"1D Image Tokenizer": 0.71},
                "best_domain": "1D Image Tokenizer", "best_score": 0.71,
            },
        ],
        "domain_1": [
            {
                "id": "test_006", "title": "Joint Training for Visual Understanding and Generation",
                "abstract": "A unified framework for simultaneous visual understanding and image generation with emergent multimodal capabilities.",
                "authors": ["Frank Zhang"],
                "labels": [],
                "similarity_scores": {"Unified Understanding & Generation": 0.79},
                "best_domain": "Unified Understanding & Generation", "best_score": 0.79,
            },
            {
                "id": "test_007", "title": "Unified Multimodal Foundation Model for Vision-Language Tasks",
                "abstract": "A unified multimodal model handling text-to-image, captioning, and VQA. Open-source at github.com/example/unified",
                "authors": ["Saining Xie"],
                "labels": ["⭐ VIP:Saining Xie", "🔓 open-source"],
                "similarity_scores": {"Unified Understanding & Generation": 0.84},
                "best_domain": "Unified Understanding & Generation", "best_score": 0.84,
            },
        ],
    }

    domains = [
        {"name": "1D Image Tokenizer"},
        {"name": "Unified Understanding & Generation"},
    ]

    config = {"top_k_recommend": 2, "use_llm_why_read": False}

    print("=== Recommender Standalone Test ===\n")
    recs = recommend(mock_domain_papers, config, domains)

    for domain_key, domain_result in recs.items():
        print(f"\n📚 {domain_result['name']}:")
        for r in domain_result["recommendations"]:
            p = r["paper"]
            print(f"  #{r['rank']} [{p['id']}] score={r['score']:.3f}")
            print(f"     Title: {p['title'][:60]}...")
            print(f"     Labels: {p['labels']}")
            print(f"     Why read: {r['why_read'][:150]}...")

    print("\n✅ Recommender test complete!")
