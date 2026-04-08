from __future__ import annotations

import datetime as dt
import math
import re
from collections import Counter
from dataclasses import dataclass

from src.models import Paper
from src.ranking.reasons import build_reason

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "based",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "paper",
    "study",
    "the",
    "to",
    "using",
    "via",
    "with",
}


@dataclass
class SeedProfile:
    keywords: Counter[str]
    authors: set[str]
    venues: set[str]
    concepts: set[str]


def build_seed_profile(seeds: list[Paper]) -> SeedProfile:
    keywords: Counter[str] = Counter()
    authors: set[str] = set()
    venues: set[str] = set()
    concepts: set[str] = set()
    for seed in seeds:
        text = " ".join([seed.title, seed.abstract, " ".join(seed.tags), " ".join(seed.concepts)])
        keywords.update(extract_keywords(text))
        authors.update(normalize_name(author) for author in seed.authors if author)
        if seed.venue:
            venues.add(normalize_text(seed.venue))
        concepts.update(normalize_text(concept) for concept in seed.concepts if concept)
    return SeedProfile(keywords=keywords, authors=authors, venues=venues, concepts=concepts)


def score_candidates(candidates: list[Paper], seeds: list[Paper], recent_days: int) -> list[Paper]:
    profile = build_seed_profile(seeds)
    for candidate in candidates:
        candidate.score, candidate.why_recommended = score_paper(candidate, profile, recent_days)
        candidate.category = "NEW" if is_recent(candidate, recent_days) else "CLASSIC"
    return sorted(candidates, key=lambda paper: paper.score, reverse=True)


def score_paper(paper: Paper, profile: SeedProfile, recent_days: int) -> tuple[float, str]:
    signals: list[str] = []
    score = 0.0

    candidate_keywords = set(extract_keywords(" ".join([paper.title, paper.abstract, " ".join(paper.concepts)])))
    seed_top_keywords = {keyword for keyword, _ in profile.keywords.most_common(80)}
    keyword_overlap = candidate_keywords.intersection(seed_top_keywords)
    if keyword_overlap:
        overlap_score = min(4.0, len(keyword_overlap) * 0.4)
        score += overlap_score
        signals.append(f"overlaps on concepts/keywords: {', '.join(sorted(keyword_overlap)[:5])}")

    candidate_concepts = {normalize_text(concept) for concept in paper.concepts}
    concept_overlap = candidate_concepts.intersection(profile.concepts)
    if concept_overlap:
        score += min(2.0, len(concept_overlap) * 0.6)
        signals.append(f"shares OpenAlex concepts: {', '.join(sorted(concept_overlap)[:3])}")

    author_overlap = {normalize_name(author) for author in paper.authors}.intersection(profile.authors)
    if author_overlap:
        score += min(2.0, len(author_overlap) * 0.75)
        signals.append(f"shares author signal: {', '.join(sorted(author_overlap)[:3])}")

    venue = normalize_text(paper.venue)
    if venue and venue in profile.venues:
        score += 1.5
        signals.append(f"appears in a venue already represented in the seed collection: {paper.venue}")

    if paper.related_to_seed:
        score += 2.0
        signals.append(paper.relation_reason or "citation/related-work proximity to a seed paper")

    if is_recent(paper, recent_days):
        score += 1.25
        signals.append("recent publication within the configured window")

    if paper.cited_by_count:
        citation_score = min(2.0, math.log10(paper.cited_by_count + 1))
        score += citation_score
        if paper.cited_by_count >= 50:
            signals.append(f"strong citation signal ({paper.cited_by_count} citations)")

    if paper.doi:
        score += 0.3
    if paper.abstract:
        score += 0.4
    if paper.venue:
        score += 0.2

    return round(score, 3), build_reason(paper, signals)


def select_shortlist(candidates: list[Paper], seeds: list[Paper], shortlist_size: int, recent_days: int) -> list[Paper]:
    ranked = score_candidates(candidates, seeds, recent_days)
    return ranked[:shortlist_size]


def is_recent(paper: Paper, recent_days: int) -> bool:
    if not paper.year:
        return False
    current_year = dt.date.today().year
    if recent_days >= 365:
        return paper.year >= current_year - max(1, recent_days // 365)
    return paper.year >= current_year - 1


def is_classic(paper: Paper, min_age_years: int) -> bool:
    if not paper.year:
        return False
    return paper.year <= dt.date.today().year - min_age_years


def extract_keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", text.lower())
    return [word for word in words if word not in STOPWORDS and not word.isdigit()]


def normalize_name(name: str) -> str:
    return normalize_text(name)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())
