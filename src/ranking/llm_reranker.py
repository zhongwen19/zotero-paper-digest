from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

from src.models import Paper
from src.retry import with_retries

LOGGER = logging.getLogger(__name__)


def rerank_with_deepseek(
    papers: list[Paper],
    seed_summary: str,
    *,
    max_items: int,
    enabled: bool,
) -> tuple[list[Paper], dict[str, Any]]:
    stats = {
        "llm_enabled": enabled,
        "llm_triggered": False,
        "llm_items": 0,
        "estimated_input_tokens": 0,
        "estimated_output_tokens": 0,
    }
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not enabled or not api_key or not papers:
        return papers, stats

    items = papers[:max_items]
    prompt = build_prompt(items, seed_summary)
    stats["llm_triggered"] = True
    stats["llm_items"] = len(items)
    stats["estimated_input_tokens"] = estimate_tokens(prompt)

    try:
        response_text = call_deepseek(prompt, api_key)
        stats["estimated_output_tokens"] = estimate_tokens(response_text)
        updates = parse_llm_response(response_text)
        by_index = {int(item["index"]): item for item in updates if "index" in item}
        for index, paper in enumerate(items):
            update = by_index.get(index)
            if not update:
                continue
            llm_score = float(update.get("relevance_score", paper.score))
            paper.score = round((paper.score + llm_score) / 2, 3)
            paper.why_recommended = str(update.get("reason", paper.why_recommended)).strip() or paper.why_recommended
            category = str(update.get("category", paper.category)).upper()
            if category in {"NEW", "CLASSIC"}:
                paper.category = category
        return sorted(papers, key=lambda paper: paper.score, reverse=True), stats
    except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError) as exc:
        LOGGER.warning("DeepSeek rerank failed; falling back to local ranking: %s", exc)
        stats["llm_error"] = str(exc)
        return papers, stats


def build_prompt(papers: list[Paper], seed_summary: str) -> str:
    compact_items = []
    for index, paper in enumerate(papers):
        compact_items.append(
            {
                "index": index,
                "title": paper.title[:220],
                "abstract_snippet": paper.abstract[:600],
                "venue": paper.venue[:120],
                "year": paper.year,
                "local_score": paper.score,
                "local_reason": paper.why_recommended[:280],
                "category": paper.category,
            }
        )
    return (
        "You rerank scholarly paper recommendations for one researcher's Zotero seed collection. "
        "Prioritize precision over recall. Do not invent metadata. Return only valid JSON.\n"
        f"Seed summary: {seed_summary[:1200]}\n"
        "For each item, return index, relevance_score from 0 to 10, novelty_score from 0 to 10, "
        "category NEW or CLASSIC, and a short reason under 35 words.\n"
        f"Items: {json.dumps(compact_items, ensure_ascii=True)}\n"
        'JSON schema: {"items":[{"index":0,"relevance_score":8.1,"novelty_score":7.0,"category":"NEW","reason":"..."}]}'
    )


def call_deepseek(prompt: str, api_key: str) -> str:
    payload = {
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": "You are a concise scholarly recommendation reranker."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def request() -> requests.Response:
        response = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        return response

    response = with_retries(request, logger=LOGGER)
    content = response.json()["choices"][0]["message"].get("content", "")
    if not content:
        raise ValueError("DeepSeek returned empty content")
    return content


def parse_llm_response(text: str) -> list[dict[str, Any]]:
    payload = json.loads(text)
    items = payload.get("items", [])
    if not isinstance(items, list):
        raise ValueError("DeepSeek JSON did not contain an items list")
    return items


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
