"""
config_parser.py — Parse the markdown config file for arxiv-radar.

Supports:
- Parameter tables (| param | value | range | description |)
- Natural language sections (research background, interests)
- Domain definitions (name, seed text, keywords)
- VIP author list
- Noise filter keyword list
"""

from __future__ import annotations
import logging
import re
import os
from pathlib import Path

logger = logging.getLogger(__name__)
from typing import Any


# ─────────────────────────── Defaults ───────────────────────────

DEFAULTS: dict[str, Any] = {
    "similarity_threshold": 0.35,
    "threshold_mode": "adaptive",
    "adaptive_top_k": 30,
    "llm_judge_range": (0.25, 0.45),
    "top_k_recommend": 2,
    "arxiv_categories": ["cs.CV", "cs.LG", "cs.AI"],
    "embedding_model": "all-MiniLM-L6-v2",
    "max_papers_per_day": 500,
    "report_output": "file",
    "report_path": "~/arxiv-daily/",
    "noise_filter_strict": True,
}

DEFAULT_LLM_ANALYSE: dict[str, Any] = {
    "enabled": True,
    "default_model": "wq/minimaxm25",
    "fallback_model": "wq/glm5",
    "max_retries": 1,
    "timeout_seconds": 300,
    "prompt_template": "prompts/prompt_H3_template.txt",
}

DEFAULT_VIP_AUTHORS = [
    "Kaiming He", "Saining Xie", "Ross Girshick", "Piotr Dollar",
    "Jian Sun", "Yann LeCun", "Geoffrey Hinton", "Ilya Sutskever",
    "Andrej Karpathy", "Sergey Levine", "Yang Song", "Prafulla Dhariwal",
    "Aditya Ramesh", "Boyang Zheng", "Nanye Ma", "Shengbang Tong",
    "Yuxin Fang", "Zhuowen Tu", "Bolei Zhou",
]

DEFAULT_ORGS = [
    "Meta AI", "Meta AI Research", "FAIR",
    "Google Brain", "Google DeepMind", "DeepMind",
    "OpenAI", "ByteDance", "ByteDance Research",
    "Microsoft Research", "Apple Research",
]

DEFAULT_NOISE_KEYWORDS = [
    "medical", "clinical", "patient", "hospital", "drug", "cancer",
    "tumor", "tumour", "diabetes", "pathology", "radiology", "biopsy",
    "financial", "stock", "trading", "cryptocurrency", "portfolio",
    "hedge fund", "market prediction", "financial forecasting",
    "legal", "law", "contract", "court", "jurisdiction",
    "chemistry", "molecular", "protein", "genome", "DNA", "RNA",
    "drug discovery", "enzyme",
    "agriculture", "crop", "soil", "plant disease",
    "traffic prediction", "ride-sharing", "urban computing",
]


# ─────────────────────────── Parser ───────────────────────────

def parse_config(config_path: str | Path) -> dict[str, Any]:
    """Parse the markdown config file and return a structured config dict."""
    config_path = Path(config_path)
    if not config_path.exists():
        print(f"[config] Config not found at {config_path}, using defaults.")
        return _default_config()

    text = config_path.read_text(encoding="utf-8")
    config = _default_config()

    # Parse parameter table
    _parse_param_table(text, config)

    # Parse arxiv categories (may be comma-separated string)
    if isinstance(config.get("arxiv_categories"), str):
        config["arxiv_categories"] = [
            c.strip() for c in config["arxiv_categories"].split(",") if c.strip()
        ]

    # Parse llm_judge_range string like "0.25-0.45"
    if isinstance(config.get("llm_judge_range"), str):
        parts = config["llm_judge_range"].split("-")
        if len(parts) == 2:
            try:
                config["llm_judge_range"] = (float(parts[0]), float(parts[1]))
            except ValueError:
                pass

    # Parse VIP authors
    config["vip_authors"] = _parse_vip_authors(text) or DEFAULT_VIP_AUTHORS

    # Parse noise keywords
    config["noise_keywords"] = _parse_noise_keywords(text) or DEFAULT_NOISE_KEYWORDS

    # Parse org list
    config["orgs"] = DEFAULT_ORGS  # TODO: make configurable

    # Parse domains
    config["domains"] = _parse_domains(text, config_path.parent)

    # Parse natural language research background
    config["research_background"] = _parse_section(text, "研究背景")

    # Parse llm analyse block
    config["llm_analyse"] = _parse_llm_analyse(text)

    # Expand ~ in paths
    if "report_path" in config:
        config["report_path"] = str(Path(config["report_path"]).expanduser())

    return config


def _default_config() -> dict[str, Any]:
    cfg = DEFAULTS.copy()
    cfg["vip_authors"] = DEFAULT_VIP_AUTHORS.copy()
    cfg["noise_keywords"] = DEFAULT_NOISE_KEYWORDS.copy()
    cfg["orgs"] = DEFAULT_ORGS.copy()
    cfg["domains"] = []
    cfg["research_background"] = ""
    cfg["llm_analyse"] = DEFAULT_LLM_ANALYSE.copy()
    return cfg


def _parse_param_table(text: str, config: dict) -> None:
    """Extract key-value pairs from markdown tables."""
    # Match table rows: | `param` | value | ... |
    # Be flexible about backticks and whitespace
    pattern = re.compile(
        r"\|\s*`?(\w+)`?\s*\|\s*([^|\n]+?)\s*\|"
    )
    for match in pattern.finditer(text):
        key = match.group(1).strip()
        raw_val = match.group(2).strip().strip("`").strip("'").strip('"')

        if key in DEFAULTS:
            default_val = DEFAULTS[key]
            try:
                if isinstance(default_val, bool):
                    config[key] = raw_val.lower() in ("true", "1", "yes")
                elif isinstance(default_val, int):
                    config[key] = int(raw_val)
                elif isinstance(default_val, float):
                    config[key] = float(raw_val)
                else:
                    config[key] = raw_val
            except (ValueError, TypeError):
                config[key] = raw_val


def _parse_vip_authors(text: str) -> list[str]:
    """Extract VIP author list from the markdown config."""
    # Find the VIP authors section
    section = _parse_section(text, "VIP 作者列表")
    if not section:
        return []

    authors = []
    for line in section.split("\n"):
        line = line.strip()
        # Skip comments, empty lines, org entries, headers
        if (not line or line.startswith("#") or line.startswith("|") or
                line.startswith("```") or line.startswith("##") or
                line.startswith("以下") or line.startswith("添加")):
            continue
        # Clean up bullet points, etc.
        line = re.sub(r"^[-*•]\s*", "", line)
        if line and not any(c in line for c in ["[", "]", "(", ")"]):
            authors.append(line)

    return authors if authors else []


def _parse_noise_keywords(text: str) -> list[str]:
    """Extract noise filter keywords from config."""
    section = _parse_section(text, "噪声过滤关键词")
    if not section:
        return []

    keywords = []
    in_code_block = False
    for line in section.split("\n"):
        line = line.strip()
        if line.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            # Each line may have comma-separated keywords
            for kw in line.split(","):
                kw = kw.strip()
                if kw:
                    keywords.append(kw)

    return keywords if keywords else []


def _parse_domains(text: str, base_dir: Path) -> list[dict]:
    """Parse domain definitions from config."""
    domains = []

    # Find domain sections (### Domain N: ...)
    domain_pattern = re.compile(
        r"### Domain \d+:\s*(.+?)\n(.*?)(?=### Domain \d+:|---|\Z)",
        re.DOTALL
    )
    for m in domain_pattern.finditer(text):
        name = m.group(1).strip()
        body = m.group(2)

        domain = {"name": name, "seed_text": "", "keywords": []}

        # Extract seed paper reference
        seed_match = re.search(r"\*\*种子论文\*\*.*?arXiv:(\S+?)\)", body)
        if seed_match:
            domain["seed_arxiv"] = seed_match.group(1).rstrip(")")

        # Try to load seed text from data/seeds/
        # Naming convention: domain1_titok.txt, domain2_bagel.txt
        domain_idx = len(domains) + 1
        seed_candidates = list(base_dir.glob(f"data/seeds/domain{domain_idx}_*.txt"))
        if seed_candidates:
            domain["seed_text"] = seed_candidates[0].read_text(encoding="utf-8")
            domain["seed_file"] = str(seed_candidates[0])

        # Extract keywords
        kw_match = re.search(r"\*\*关键词\*\*:\s*(.+?)(?:\n|$)", body)
        if kw_match:
            domain["keywords"] = [
                k.strip() for k in kw_match.group(1).split(",") if k.strip()
            ]

        domains.append(domain)

    # Fallback: create default domains if none found
    if not domains:
        domains = _default_domains(base_dir)

    return domains


def _default_domains(base_dir: Path) -> list[dict]:
    """Return default domain configs with seed texts from data/seeds/."""
    domains = [
        {
            "name": "1D Image Tokenizer",
            "keywords": ["image tokenizer", "1D token", "VQVAE", "codebook",
                         "image generation", "reconstruction", "token efficiency"],
            "seed_text": "",
        },
        {
            "name": "Unified Understanding & Generation",
            "keywords": ["unified model", "multimodal", "image understanding",
                         "image generation", "unified pretraining", "joint training"],
            "seed_text": "",
        },
    ]

    for i, domain in enumerate(domains, 1):
        seed_files = list(base_dir.glob(f"data/seeds/domain{i}_*.txt"))
        if seed_files:
            domain["seed_text"] = seed_files[0].read_text(encoding="utf-8")
            domain["seed_file"] = str(seed_files[0])

    return domains


def _parse_section(text: str, section_name: str) -> str:
    """Extract text content of a markdown section by name."""
    # Match ## or ### section with given name (flexible matching)
    pattern = re.compile(
        r"#+\s*" + re.escape(section_name) + r".*?\n(.*?)(?=\n#+\s|\Z)",
        re.DOTALL | re.IGNORECASE
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def _parse_llm_analyse(text: str) -> dict[str, Any]:
    """Parse yaml-like llm_analyse block from markdown config."""
    cfg = DEFAULT_LLM_ANALYSE.copy()
    match = re.search(
        r"llm_analyse:\s*\n((?:[ \t]+[A-Za-z_][A-Za-z0-9_]*\s*:\s*.*\n?)*)",
        text,
        re.MULTILINE,
    )
    if not match:
        return cfg

    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip().strip("`").strip("'").strip('"')
        if key not in cfg:
            continue
        default_val = cfg[key]
        try:
            if isinstance(default_val, bool):
                cfg[key] = raw_value.lower() in ("true", "1", "yes")
            elif isinstance(default_val, int):
                cfg[key] = int(raw_value)
            else:
                cfg[key] = raw_value
        except (TypeError, ValueError):
            logger.warning("Invalid llm_analyse config for %s: %s", key, raw_value)
    return cfg


# ─────────────────────────── CLI test ───────────────────────────

if __name__ == "__main__":
    import json
    from pathlib import Path

    cfg_path = Path(__file__).parent.parent / "config.template.md"
    config = parse_config(cfg_path)

    print("=== Parsed Config ===")
    print(f"similarity_threshold: {config['similarity_threshold']}")
    print(f"threshold_mode: {config['threshold_mode']}")
    print(f"adaptive_top_k: {config['adaptive_top_k']}")
    print(f"arxiv_categories: {config['arxiv_categories']}")
    print(f"embedding_model: {config['embedding_model']}")
    print(f"max_papers_per_day: {config['max_papers_per_day']}")
    print(f"report_output: {config['report_output']}")
    print(f"\nVIP authors ({len(config['vip_authors'])}): {config['vip_authors'][:5]}...")
    print(f"Noise keywords ({len(config['noise_keywords'])}): {config['noise_keywords'][:5]}...")
    print(f"\nDomains ({len(config['domains'])}):")
    for d in config["domains"]:
        seed_len = len(d.get("seed_text", ""))
        print(f"  - {d['name']} | keywords: {d['keywords'][:3]} | seed_text: {seed_len} chars")
    print(f"\nResearch background: {config['research_background'][:200]}...")
    print(f"\nLLM analyse: {json.dumps(config['llm_analyse'], ensure_ascii=False)}")
