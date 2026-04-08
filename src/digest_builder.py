from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from src.config import AppConfig
from src.dedup import deduplicate_candidates
from src.discovery.crossref import CrossrefClient
from src.discovery.openalex import OpenAlexClient
from src.models import Digest, Paper
from src.ranking.llm_reranker import rerank_with_deepseek
from src.ranking.local_ranker import extract_keywords, is_classic, is_recent, score_candidates, select_shortlist
from src.zotero_client import ZoteroClient

LOGGER = logging.getLogger(__name__)
QUALITY_THRESHOLD = 2.0


def build_digest(config: AppConfig, zotero_api_key: str) -> Digest:
    zotero = ZoteroClient(config.zotero.library_type, config.zotero.library_id, zotero_api_key)
    seeds = zotero.fetch_seed_papers(config.zotero.collection_keys, config.zotero.max_seeds)
    if not seeds:
        raise RuntimeError("No seed papers found. Check Zotero collection keys and API permissions.")

    recent_candidates = discover_candidates(config, seeds, classics=False)
    recent_deduped = deduplicate_candidates(recent_candidates, seeds)
    recent_shortlist = select_shortlist(
        recent_deduped,
        seeds,
        config.ranking.shortlist_size,
        config.discovery.recent_days,
    )
    local_new_count = sum(
        1
        for paper in recent_shortlist
        if is_recent(paper, config.discovery.recent_days) and paper.score >= QUALITY_THRESHOLD
    )

    classic_candidates: list[Paper] = []
    classic_deduped: list[Paper] = []
    classic_shortlist: list[Paper] = []
    fallback_triggered = local_new_count < config.ranking.min_new_results_before_classics
    if fallback_triggered:
        classic_candidates = discover_candidates(config, seeds, classics=True)
        classic_deduped = deduplicate_candidates(classic_candidates, seeds + recent_shortlist)
        ranked_classics = score_candidates(classic_deduped, seeds, config.discovery.recent_days)
        classic_shortlist = [
            paper
            for paper in ranked_classics
            if is_classic(paper, config.classics.min_age_years) and paper.score >= QUALITY_THRESHOLD
        ][: max(config.ranking.min_new_results_before_classics - local_new_count, 3)]

    recent_slots = max(0, config.ranking.shortlist_size - len(classic_shortlist))
    combined = (recent_shortlist[:recent_slots] + classic_shortlist)[: config.ranking.shortlist_size]
    seed_summary = summarize_seeds(seeds)
    reranked, llm_stats = rerank_with_deepseek(
        combined,
        seed_summary,
        max_items=config.ranking.llm_max_items,
        enabled=config.ranking.llm_enabled,
    )

    new_papers = [
        paper
        for paper in reranked
        if paper.category == "NEW" and is_recent(paper, config.discovery.recent_days) and paper.score >= QUALITY_THRESHOLD
    ]
    classic_papers = [
        paper
        for paper in reranked
        if (paper.category == "CLASSIC" or is_classic(paper, config.classics.min_age_years))
        and paper.score >= QUALITY_THRESHOLD
    ]

    if len(new_papers) < config.ranking.min_new_results_before_classics:
        needed = config.ranking.min_new_results_before_classics - len(new_papers)
        existing_titles = {paper.title for paper in new_papers + classic_papers}
        extra_classics = [
            paper
            for paper in classic_shortlist
            if paper.title not in existing_titles and is_classic(paper, config.classics.min_age_years)
        ][:needed]
        classic_papers.extend(extra_classics)

    stats: dict[str, Any] = {
        "seed_count": len(seeds),
        "recent_candidate_count": len(recent_candidates),
        "recent_deduped_count": len(recent_deduped),
        "classic_candidate_count": len(classic_candidates),
        "classic_deduped_count": len(classic_deduped),
        "shortlist_count": len(combined),
        "fallback_classics_triggered": fallback_triggered,
        "new_result_count": len(new_papers),
        "classic_result_count": len(classic_papers),
    }
    stats.update(llm_stats)
    LOGGER.info("Digest stats: %s", stats)
    return Digest(new_papers=new_papers, classic_papers=classic_papers, stats=stats)


def discover_candidates(config: AppConfig, seeds: list[Paper], *, classics: bool) -> list[Paper]:
    candidates: list[Paper] = []
    max_per_source = config.discovery.max_candidates_per_source
    sources = {source.lower() for source in config.discovery.sources}
    mailto = config.email.to_email or config.email.from_email

    if "openalex" in sources:
        client = OpenAlexClient(mailto=mailto)
        discovered = (
            client.discover_classics(seeds, config.classics.min_age_years, max_per_source)
            if classics
            else client.discover_recent(seeds, config.discovery.recent_days, max_per_source)
        )
        LOGGER.info("OpenAlex discovered %s %s candidates", len(discovered), "classic" if classics else "recent")
        candidates.extend(discovered)

    if "crossref" in sources:
        client = CrossrefClient(mailto=mailto)
        discovered = (
            client.discover_classics(seeds, config.classics.min_age_years, max_per_source)
            if classics
            else client.discover_recent(seeds, config.discovery.recent_days, max_per_source)
        )
        LOGGER.info("Crossref discovered %s %s candidates", len(discovered), "classic" if classics else "recent")
        candidates.extend(discovered)

    return candidates


def summarize_seeds(seeds: list[Paper]) -> str:
    keywords: Counter[str] = Counter()
    venues: Counter[str] = Counter()
    authors: Counter[str] = Counter()
    for seed in seeds:
        keywords.update(extract_keywords(" ".join([seed.title, seed.abstract, " ".join(seed.tags)])))
        if seed.venue:
            venues[seed.venue] += 1
        for author in seed.authors[:3]:
            authors[author] += 1
    keyword_text = ", ".join(keyword for keyword, _ in keywords.most_common(18))
    venue_text = ", ".join(venue for venue, _ in venues.most_common(8))
    author_text = ", ".join(author for author, _ in authors.most_common(8))
    return f"Top keywords: {keyword_text}. Frequent venues: {venue_text}. Frequent authors: {author_text}."
