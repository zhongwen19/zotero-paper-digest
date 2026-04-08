from __future__ import annotations

import re
import unicodedata

from src.models import Paper


def normalize_doi(doi: str | None) -> str:
    if not doi:
        return ""
    normalized = doi.strip().lower()
    normalized = re.sub(r"^https?://(dx\.)?doi\.org/", "", normalized)
    normalized = re.sub(r"^doi:\s*", "", normalized)
    return normalized.strip(" .")


def normalize_title(title: str | None) -> str:
    if not title:
        return ""
    title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    title = title.lower()
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def paper_identity_keys(paper: Paper) -> set[str]:
    keys: set[str] = set()
    doi = normalize_doi(paper.doi)
    title = normalize_title(paper.title)
    if doi:
        keys.add(f"doi:{doi}")
    if title:
        keys.add(f"title:{title}")
    for key, value in paper.external_ids.items():
        if value:
            keys.add(f"{key.lower()}:{value.strip().lower()}")
    if paper.openalex_id:
        keys.add(f"openalex:{paper.openalex_id.strip().lower()}")
    return keys


def build_seen_keys(papers: list[Paper]) -> set[str]:
    seen: set[str] = set()
    for paper in papers:
        seen.update(paper_identity_keys(paper))
    return seen


def deduplicate_candidates(candidates: list[Paper], existing_papers: list[Paper]) -> list[Paper]:
    seen = build_seen_keys(existing_papers)
    deduped: list[Paper] = []
    for candidate in candidates:
        keys = paper_identity_keys(candidate)
        if not keys:
            continue
        if seen.intersection(keys):
            continue
        if not has_basic_metadata(candidate):
            continue
        seen.update(keys)
        deduped.append(candidate)
    return deduped


def has_basic_metadata(paper: Paper) -> bool:
    if not normalize_title(paper.title):
        return False
    if not paper.year:
        return False
    if not paper.abstract and not paper.doi and not paper.url:
        return False
    return True
