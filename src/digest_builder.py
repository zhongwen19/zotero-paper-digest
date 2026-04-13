from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Callable

from src.config import AppConfig
from src.dedup import deduplicate_candidates, paper_identity_keys
from src.discovery.crossref import CrossrefClient
from src.discovery.openalex import OpenAlexClient
from src.history import filter_previously_recommended
from src.models import Digest, Paper
from src.ranking.llm_reranker import rerank_with_deepseek
from src.ranking.local_ranker import (
    extract_keywords,
    filter_by_required_domain,
    is_classic_in_window,
    is_recent,
    score_candidates,
    select_shortlist,
)
from src.zotero_client import ZoteroClient

LOGGER = logging.getLogger(__name__)
QUALITY_THRESHOLD = 2.0


def build_digest(config: AppConfig, zotero_api_key: str, recommendation_history: set[str] | None = None) -> Digest:
    recommendation_history = recommendation_history or set()
    zotero = ZoteroClient(config.zotero.library_type, config.zotero.library_id, zotero_api_key)
    seeds = zotero.fetch_seed_papers(config.zotero.collection_keys, config.zotero.max_seeds)
    if not seeds:
        raise RuntimeError("No seed papers found. Check Zotero collection keys and API permissions.")

    recent_candidates: list[Paper] = []
    recent_deduped: list[Paper] = []
    recent_shortlist: list[Paper] = []
    recent_windows_used: list[int] = []
    local_new_count = 0
    for recent_days in build_recent_candidate_windows(config):
        recent_windows_used.append(recent_days)
        recent_candidates.extend(discover_candidates(config, seeds, classics=False, recent_days=recent_days))
        recent_deduped = deduplicate_candidates(recent_candidates, seeds)
        recent_deduped = filter_previously_recommended(recent_deduped, recommendation_history)
        recent_deduped = filter_by_required_domain(recent_deduped, config.ranking.required_domain_terms)
        recent_shortlist = select_shortlist(
            recent_deduped,
            seeds,
            config.ranking.shortlist_size,
            config.discovery.recent_days,
        )
        local_new_count = sum(
            1
            for paper in recent_shortlist
            if is_within_new_backfill_window(paper, config) and paper.score >= QUALITY_THRESHOLD
        )
        if local_new_count >= config.ranking.target_new_results:
            break

    classic_candidates: list[Paper] = []
    classic_deduped: list[Paper] = []
    classic_shortlist: list[Paper] = []
    fallback_triggered = (
        local_new_count < config.ranking.min_new_results_before_classics
        or config.ranking.target_classic_results > 0
    )
    if fallback_triggered:
        classic_candidates = discover_candidates(config, seeds, classics=True)
        classic_deduped = deduplicate_candidates(classic_candidates, seeds + recent_shortlist)
        classic_deduped = filter_previously_recommended(classic_deduped, recommendation_history)
        classic_deduped = filter_by_required_domain(classic_deduped, config.ranking.required_domain_terms)
        ranked_classics = score_candidates(classic_deduped, seeds, config.discovery.recent_days)
        classic_shortlist = [
            paper
            for paper in ranked_classics
            if is_classic_in_window(paper, config.classics.min_age_years, config.classics.max_age_years)
            and paper.score >= QUALITY_THRESHOLD
        ][: config.ranking.target_classic_results]

    recent_slots = min(config.ranking.target_new_results, max(0, config.ranking.shortlist_size - len(classic_shortlist)))
    combined = (recent_shortlist[:recent_slots] + classic_shortlist)[: config.ranking.shortlist_size]
    seed_summary = summarize_seeds(seeds)
    reranked, llm_stats = rerank_with_deepseek(
        combined,
        seed_summary,
        max_items=config.ranking.llm_max_items,
        enabled=config.ranking.llm_enabled,
        required_domain_terms=config.ranking.required_domain_terms,
    )

    recent_shortlist_keys = build_identity_key_index(recent_shortlist)
    classic_shortlist_keys = build_identity_key_index(classic_shortlist)
    new_papers = select_ranked_papers(
        reranked,
        eligible_identity_keys=recent_shortlist_keys,
        predicate=lambda paper: is_within_new_backfill_window(paper, config) and paper.score >= QUALITY_THRESHOLD,
        limit=config.ranking.target_new_results,
        category="NEW",
    )
    classic_papers = select_ranked_papers(
        reranked,
        eligible_identity_keys=classic_shortlist_keys,
        predicate=lambda paper: is_classic_in_window(
            paper,
            config.classics.min_age_years,
            config.classics.max_age_years,
        )
        and paper.score >= QUALITY_THRESHOLD,
        limit=config.ranking.target_classic_results,
        category="CLASSIC",
    )

    if len(new_papers) < config.ranking.min_new_results_before_classics:
        needed = min(
            config.ranking.min_new_results_before_classics - len(new_papers),
            max(0, config.ranking.target_classic_results - len(classic_papers)),
        )
        existing_titles = {paper.title for paper in new_papers + classic_papers}
        extra_classics = [
            paper
            for paper in classic_shortlist
            if paper.title not in existing_titles
            and is_classic_in_window(paper, config.classics.min_age_years, config.classics.max_age_years)
        ][:needed]
        classic_papers.extend(extra_classics)

    enrich_selected_papers(new_papers + classic_papers, mailto=config.email.to_email or config.email.from_email)
    ensure_paper_summaries(new_papers + classic_papers)

    stats: dict[str, Any] = {
        "seed_count": len(seeds),
        "recent_candidate_count": len(recent_candidates),
        "recent_deduped_count": len(recent_deduped),
        "recent_windows_used": recent_windows_used,
        "recent_backfill_triggered": len(recent_windows_used) > 1,
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


def discover_candidates(
    config: AppConfig,
    seeds: list[Paper],
    *,
    classics: bool,
    recent_days: int | None = None,
) -> list[Paper]:
    candidates: list[Paper] = []
    max_per_source = config.discovery.max_candidates_per_source
    sources = {source.lower() for source in config.discovery.sources}
    mailto = config.email.to_email or config.email.from_email
    effective_recent_days = recent_days if recent_days is not None else config.discovery.recent_days

    if "openalex" in sources:
        client = OpenAlexClient(mailto=mailto)
        discovered = (
            client.discover_classics(seeds, config.classics.min_age_years, config.classics.max_age_years, max_per_source)
            if classics
            else client.discover_recent(seeds, effective_recent_days, max_per_source)
        )
        LOGGER.info("OpenAlex discovered %s %s candidates", len(discovered), "classic" if classics else "recent")
        candidates.extend(discovered)

    if "crossref" in sources:
        client = CrossrefClient(mailto=mailto)
        discovered = (
            client.discover_classics(seeds, config.classics.min_age_years, config.classics.max_age_years, max_per_source)
            if classics
            else client.discover_recent(seeds, effective_recent_days, max_per_source)
        )
        LOGGER.info("Crossref discovered %s %s candidates", len(discovered), "classic" if classics else "recent")
        candidates.extend(discovered)

    return candidates


def build_recent_candidate_windows(config: AppConfig) -> list[int]:
    windows = [config.discovery.recent_days]
    for years in sorted({year for year in config.discovery.recent_backfill_years if year > 0}):
        days = max(365, years * 365)
        if days not in windows:
            windows.append(days)
    return windows


def is_within_new_backfill_window(paper: Paper, config: AppConfig) -> bool:
    if is_recent(paper, config.discovery.recent_days):
        return True
    if not paper.year:
        return False
    positive_backfill_years = [year for year in config.discovery.recent_backfill_years if year > 0]
    if not positive_backfill_years:
        return False
    newest_allowed_year = max(positive_backfill_years)
    return paper.year >= datetime_year() - newest_allowed_year


def datetime_year() -> int:
    from datetime import date

    return date.today().year


def build_identity_key_index(papers: list[Paper]) -> set[str]:
    keys: set[str] = set()
    for paper in papers:
        keys.update(paper_identity_keys(paper))
    return keys


def select_ranked_papers(
    papers: list[Paper],
    *,
    eligible_identity_keys: set[str],
    predicate: Callable[[Paper], bool],
    limit: int,
    category: str,
) -> list[Paper]:
    selected: list[Paper] = []
    for paper in papers:
        if not eligible_identity_keys:
            break
        if not paper_identity_keys(paper).intersection(eligible_identity_keys):
            continue
        if not predicate(paper):
            continue
        paper.category = category
        selected.append(paper)
        if len(selected) >= limit:
            break
    return selected


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


def enrich_selected_papers(papers: list[Paper], *, mailto: str) -> None:
    if not papers:
        return
    unique_papers: list[Paper] = []
    seen_keys: set[str] = set()
    for paper in papers:
        key = paper.openalex_id or paper.doi or paper.title.casefold()
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique_papers.append(paper)
    OpenAlexClient(mailto=mailto).enrich_papers(unique_papers)


def ensure_paper_summaries(papers: list[Paper]) -> None:
    for paper in papers:
        if paper.summary:
            continue
        paper.summary = fallback_summary(paper)


def fallback_summary(paper: Paper) -> str:
    abstract = re.sub(r"\s+", " ", paper.abstract).strip()
    if abstract:
        sentences = re.split(r"(?<=[.!?])\s+", abstract)
        for sentence in sentences:
            cleaned = sentence.strip()
            if len(cleaned) >= 50:
                return cleaned[:197].rstrip(". ") + "."
    venue = paper.venue.strip()
    year = str(paper.year) if paper.year else "Unknown year"
    venue_text = f" in {venue}" if venue else ""
    return f"This paper presents {paper.title}{venue_text} ({year})."
