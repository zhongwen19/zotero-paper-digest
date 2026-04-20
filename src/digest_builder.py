from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Callable

from src.config import AppConfig
from src.dedup import deduplicate_candidates, paper_identity_keys
from src.discovery.crossref import CrossrefClient
from src.discovery.openalex import OpenAlexClient
from src.history import filter_previously_recommended, filter_to_previously_recommended
from src.models import Digest, Paper
from src.ranking.llm_reranker import rerank_with_deepseek
from src.ranking.local_ranker import (
    extract_keywords,
    filter_by_required_domain,
    is_classic_in_window,
    is_recent,
    score_candidates,
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
    recent_windows_used: list[int] = []
    for recent_days in build_recent_candidate_windows(config):
        recent_windows_used.append(recent_days)
        recent_candidates.extend(discover_candidates(config, seeds, classics=False, recent_days=recent_days))
    recent_deduped_all = deduplicate_candidates(recent_candidates, seeds)
    recent_deduped_all = filter_by_required_domain(recent_deduped_all, config.ranking.required_domain_terms)
    recent_deduped_fresh = filter_previously_recommended(recent_deduped_all, recommendation_history)
    recent_repeat_candidates = filter_to_previously_recommended(recent_deduped_all, recommendation_history)
    recent_ranked_fresh = score_candidates(recent_deduped_fresh, seeds, config.discovery.recent_days)
    recent_ranked_repeat = score_candidates(recent_repeat_candidates, seeds, config.discovery.recent_days)

    classic_candidates: list[Paper] = []
    classic_windows_used = build_classic_candidate_windows(config)
    for min_age, max_age in classic_windows_used:
        classic_candidates.extend(
            discover_candidates(
                config,
                seeds,
                classics=True,
                classic_min_age_years=min_age,
                classic_max_age_years=max_age,
            )
        )
    classic_deduped_all = deduplicate_candidates(classic_candidates, seeds + recent_deduped_all)
    classic_deduped_all = filter_by_required_domain(classic_deduped_all, config.ranking.required_domain_terms)
    classic_deduped_fresh = filter_previously_recommended(classic_deduped_all, recommendation_history)
    classic_repeat_candidates = filter_to_previously_recommended(classic_deduped_all, recommendation_history)
    classic_ranked_fresh = score_candidates(classic_deduped_fresh, seeds, config.discovery.recent_days)
    classic_ranked_repeat = score_candidates(classic_repeat_candidates, seeds, config.discovery.recent_days)

    combined = build_rerank_batch(
        recent_ranked_fresh,
        classic_ranked_fresh,
        recent_ranked_repeat,
        classic_ranked_repeat,
        config,
    )
    seed_summary = summarize_seeds(seeds)
    reranked, llm_stats = rerank_with_deepseek(
        combined,
        seed_summary,
        max_items=config.ranking.llm_max_items,
        enabled=config.ranking.llm_enabled,
        required_domain_terms=config.ranking.required_domain_terms,
    )

    new_papers, classic_papers = finalize_digest_papers(reranked, config)

    enrich_selected_papers(new_papers + classic_papers, mailto=config.email.to_email or config.email.from_email)
    ensure_paper_summaries(new_papers + classic_papers)

    stats: dict[str, Any] = {
        "seed_count": len(seeds),
        "recent_candidate_count": len(recent_candidates),
        "recent_deduped_count": len(recent_deduped_all),
        "recent_fresh_count": len(recent_deduped_fresh),
        "recent_repeat_count": len(recent_repeat_candidates),
        "recent_windows_used": recent_windows_used,
        "recent_backfill_triggered": len(recent_windows_used) > 1,
        "classic_candidate_count": len(classic_candidates),
        "classic_deduped_count": len(classic_deduped_all),
        "classic_fresh_count": len(classic_deduped_fresh),
        "classic_repeat_count": len(classic_repeat_candidates),
        "classic_windows_used": classic_windows_used,
        "classic_backfill_triggered": len(classic_windows_used) > 1,
        "shortlist_count": len(combined),
        "new_result_count": len(new_papers),
        "classic_result_count": len(classic_papers),
        "total_result_count": len(new_papers) + len(classic_papers),
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
    classic_min_age_years: int | None = None,
    classic_max_age_years: int | None = None,
) -> list[Paper]:
    candidates: list[Paper] = []
    max_per_source = config.discovery.max_candidates_per_source
    sources = {source.lower() for source in config.discovery.sources}
    mailto = config.email.to_email or config.email.from_email
    effective_recent_days = recent_days if recent_days is not None else config.discovery.recent_days
    effective_classic_min_age_years = (
        classic_min_age_years if classic_min_age_years is not None else config.classics.min_age_years
    )
    effective_classic_max_age_years = (
        classic_max_age_years if classic_max_age_years is not None else config.classics.max_age_years
    )

    if "openalex" in sources:
        client = OpenAlexClient(mailto=mailto)
        discovered = (
            client.discover_classics(
                seeds,
                effective_classic_min_age_years,
                effective_classic_max_age_years,
                max_per_source,
            )
            if classics
            else client.discover_recent(seeds, effective_recent_days, max_per_source)
        )
        LOGGER.info("OpenAlex discovered %s %s candidates", len(discovered), "classic" if classics else "recent")
        candidates.extend(discovered)

    if "crossref" in sources:
        client = CrossrefClient(mailto=mailto)
        discovered = (
            client.discover_classics(
                seeds,
                effective_classic_min_age_years,
                effective_classic_max_age_years,
                max_per_source,
            )
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


def build_classic_candidate_windows(config: AppConfig) -> list[tuple[int, int]]:
    windows = [(config.classics.min_age_years, config.classics.max_age_years)]
    for max_age_years in sorted({year for year in config.classics.backfill_max_age_years if year > 0}):
        candidate_window = (config.classics.min_age_years, max_age_years)
        if candidate_window not in windows:
            windows.append(candidate_window)
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


def is_within_classic_backfill_window(paper: Paper, config: AppConfig) -> bool:
    if not paper.year:
        return False
    positive_backfill_years = [year for year in config.classics.backfill_max_age_years if year > 0]
    max_age_years = max([config.classics.max_age_years] + positive_backfill_years)
    return is_classic_in_window(paper, config.classics.min_age_years, max_age_years)


def is_classic_candidate(paper: Paper, config: AppConfig) -> bool:
    return is_within_classic_backfill_window(paper, config) and not is_within_new_backfill_window(paper, config)


def datetime_year() -> int:
    from datetime import date

    return date.today().year


def build_identity_key_index(papers: list[Paper]) -> set[str]:
    keys: set[str] = set()
    for paper in papers:
        keys.update(paper_identity_keys(paper))
    return keys


def build_rerank_batch(
    recent_ranked_fresh: list[Paper],
    classic_ranked_fresh: list[Paper],
    recent_ranked_repeat: list[Paper],
    classic_ranked_repeat: list[Paper],
    config: AppConfig,
) -> list[Paper]:
    combined: list[Paper] = []
    seen_keys: set[str] = set()
    stage_order = [
        (
            recent_ranked_fresh,
            lambda paper: is_within_new_backfill_window(paper, config) and paper.score >= QUALITY_THRESHOLD,
            config.ranking.target_new_results,
        ),
        (
            classic_ranked_fresh,
            lambda paper: is_classic_candidate(paper, config) and paper.score >= QUALITY_THRESHOLD,
            config.ranking.target_classic_results,
        ),
        (
            recent_ranked_repeat,
            lambda paper: is_within_new_backfill_window(paper, config) and paper.score >= QUALITY_THRESHOLD,
            config.ranking.target_new_results,
        ),
        (
            classic_ranked_repeat,
            lambda paper: is_classic_candidate(paper, config) and paper.score >= QUALITY_THRESHOLD,
            config.ranking.target_classic_results,
        ),
        (
            recent_ranked_fresh + classic_ranked_fresh,
            lambda paper: (is_within_new_backfill_window(paper, config) or is_classic_candidate(paper, config))
            and paper.score >= QUALITY_THRESHOLD,
            config.ranking.shortlist_size,
        ),
        (
            recent_ranked_repeat + classic_ranked_repeat,
            lambda paper: (is_within_new_backfill_window(paper, config) or is_classic_candidate(paper, config))
            and paper.score >= QUALITY_THRESHOLD,
            config.ranking.shortlist_size,
        ),
        (
            recent_ranked_fresh + classic_ranked_fresh + recent_ranked_repeat + classic_ranked_repeat,
            lambda paper: is_within_new_backfill_window(paper, config) or is_classic_candidate(paper, config),
            config.ranking.shortlist_size,
        ),
    ]
    for pool, predicate, target_count in stage_order:
        append_ranked_papers(
            combined,
            pool,
            seen_keys=seen_keys,
            predicate=predicate,
            limit=target_count if target_count > 0 else config.ranking.shortlist_size,
            hard_cap=config.ranking.shortlist_size,
        )
        if len(combined) >= config.ranking.shortlist_size:
            break
    return combined[: config.ranking.shortlist_size]


def finalize_digest_papers(reranked: list[Paper], config: AppConfig) -> tuple[list[Paper], list[Paper]]:
    new_papers = select_ranked_papers(
        reranked,
        predicate=lambda paper: is_within_new_backfill_window(paper, config) and paper.score >= QUALITY_THRESHOLD,
        limit=config.ranking.target_new_results,
        category="NEW",
    )
    classic_papers = select_ranked_papers(
        reranked,
        predicate=lambda paper: is_classic_candidate(paper, config) and paper.score >= QUALITY_THRESHOLD,
        limit=config.ranking.target_classic_results,
        category="CLASSIC",
        existing_papers=new_papers,
    )
    if len(new_papers) < config.ranking.target_new_results:
        append_selected_papers(
            new_papers,
            reranked,
            predicate=lambda paper: is_within_new_backfill_window(paper, config),
            limit=config.ranking.target_new_results,
            category="NEW",
            existing_papers=new_papers + classic_papers,
        )
    if len(classic_papers) < config.ranking.target_classic_results:
        append_selected_papers(
            classic_papers,
            reranked,
            predicate=lambda paper: is_classic_candidate(paper, config),
            limit=config.ranking.target_classic_results,
            category="CLASSIC",
            existing_papers=new_papers + classic_papers,
        )
    total_target = config.ranking.target_new_results + config.ranking.target_classic_results
    if len(new_papers) + len(classic_papers) < total_target:
        append_selected_papers_until_total(
            new_papers,
            reranked,
            predicate=lambda paper: is_within_new_backfill_window(paper, config),
            total_target=total_target,
            category="NEW",
            selected_papers=new_papers + classic_papers,
        )
    if len(new_papers) + len(classic_papers) < total_target:
        append_selected_papers_until_total(
            classic_papers,
            reranked,
            predicate=lambda paper: is_classic_candidate(paper, config),
            total_target=total_target,
            category="CLASSIC",
            selected_papers=new_papers + classic_papers,
        )
    return new_papers, classic_papers


def select_ranked_papers(
    papers: list[Paper],
    *,
    predicate: Callable[[Paper], bool],
    limit: int,
    category: str,
    existing_papers: list[Paper] | None = None,
) -> list[Paper]:
    selected: list[Paper] = []
    seen_keys = build_identity_key_index(existing_papers or [])
    for paper in papers:
        paper_keys = paper_identity_keys(paper)
        if paper_keys.intersection(seen_keys):
            continue
        if not predicate(paper):
            continue
        paper.category = category
        selected.append(paper)
        seen_keys.update(paper_keys)
        if len(selected) >= limit:
            break
    return selected


def append_ranked_papers(
    target: list[Paper],
    pool: list[Paper],
    *,
    seen_keys: set[str],
    predicate: Callable[[Paper], bool],
    limit: int,
    hard_cap: int,
) -> None:
    start_count = len(target)
    for paper in pool:
        if len(target) >= hard_cap or len(target) - start_count >= limit:
            return
        paper_keys = paper_identity_keys(paper)
        if paper_keys.intersection(seen_keys):
            continue
        if not predicate(paper):
            continue
        target.append(paper)
        seen_keys.update(paper_keys)


def append_selected_papers(
    target: list[Paper],
    pool: list[Paper],
    *,
    predicate: Callable[[Paper], bool],
    limit: int,
    category: str,
    existing_papers: list[Paper],
) -> None:
    seen_keys = build_identity_key_index(existing_papers)
    while len(target) < limit:
        appended = False
        for paper in pool:
            paper_keys = paper_identity_keys(paper)
            if paper_keys.intersection(seen_keys):
                continue
            if not predicate(paper):
                continue
            paper.category = category
            target.append(paper)
            seen_keys.update(paper_keys)
            appended = True
            if len(target) >= limit:
                return
        if not appended:
            return


def append_selected_papers_until_total(
    target: list[Paper],
    pool: list[Paper],
    *,
    predicate: Callable[[Paper], bool],
    total_target: int,
    category: str,
    selected_papers: list[Paper],
) -> None:
    seen_keys = build_identity_key_index(selected_papers)
    while len(selected_papers) < total_target:
        appended = False
        for paper in pool:
            paper_keys = paper_identity_keys(paper)
            if paper_keys.intersection(seen_keys):
                continue
            if not predicate(paper):
                continue
            paper.category = category
            target.append(paper)
            selected_papers.append(paper)
            seen_keys.update(paper_keys)
            appended = True
            if len(selected_papers) >= total_target:
                return
        if not appended:
            return


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
