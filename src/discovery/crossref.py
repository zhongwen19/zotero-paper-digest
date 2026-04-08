from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import requests

from src.models import Paper
from src.retry import with_retries

LOGGER = logging.getLogger(__name__)


class CrossrefClient:
    def __init__(self, mailto: str = "") -> None:
        self.mailto = mailto
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "zotero-paper-digest/0.1"})

    def discover_recent(self, seeds: list[Paper], recent_days: int, max_results: int) -> list[Paper]:
        from_date = (dt.date.today() - dt.timedelta(days=recent_days)).isoformat()
        return self._search_from_seeds(
            seeds,
            max_results,
            filters=f"from-pub-date:{from_date},type:journal-article",
            relation_reason="recent Crossref bibliographic match",
        )

    def discover_classics(self, seeds: list[Paper], min_age_years: int, max_results: int) -> list[Paper]:
        cutoff_year = dt.date.today().year - min_age_years
        return self._search_from_seeds(
            seeds,
            max_results,
            filters=f"until-pub-date:{cutoff_year}-12-31,type:journal-article",
            relation_reason="older Crossref bibliographic match",
        )

    def _search_from_seeds(
        self,
        seeds: list[Paper],
        max_results: int,
        *,
        filters: str,
        relation_reason: str,
    ) -> list[Paper]:
        candidates: list[Paper] = []
        queries = [seed.title for seed in seeds[:8] if seed.title]
        per_query = max(3, min(20, max_results // max(len(queries), 1) + 1))
        for query in queries:
            if len(candidates) >= max_results:
                break
            params: dict[str, Any] = {
                "query.bibliographic": query,
                "rows": per_query,
                "filter": filters,
                "select": "DOI,title,abstract,published-print,published-online,published,publisher,container-title,author,URL,is-referenced-by-count",
            }
            if self.mailto:
                params["mailto"] = self.mailto
            works = self._get_works(params)
            for work in works:
                paper = work_to_paper(work)
                if not paper:
                    continue
                paper.related_to_seed = True
                paper.relation_reason = relation_reason
                candidates.append(paper)
                if len(candidates) >= max_results:
                    break
        return candidates[:max_results]

    def _get_works(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        def request() -> requests.Response:
            response = self.session.get("https://api.crossref.org/works", params=params, timeout=30)
            response.raise_for_status()
            return response

        try:
            payload = with_retries(request, logger=LOGGER).json()
            return payload.get("message", {}).get("items", [])
        except requests.RequestException as exc:
            LOGGER.warning("Crossref search failed: %s", exc)
            return []


def work_to_paper(work: dict[str, Any]) -> Paper | None:
    title = " ".join(work.get("title") or []).strip()
    if not title:
        return None
    venue = " ".join(work.get("container-title") or []).strip()
    authors = []
    for author in work.get("author", [])[:12]:
        name = " ".join(part for part in [author.get("given", ""), author.get("family", "")] if part).strip()
        if name:
            authors.append(name)
    doi = work.get("DOI", "") or ""
    return Paper(
        title=title,
        abstract=strip_crossref_markup(work.get("abstract", "") or ""),
        doi=doi,
        year=extract_year(work),
        venue=venue,
        authors=authors,
        url=work.get("URL", "") or (f"https://doi.org/{doi}" if doi else ""),
        source="crossref",
        source_id=doi,
        external_ids={"doi": doi} if doi else {},
        cited_by_count=int(work.get("is-referenced-by-count") or 0),
    )


def extract_year(work: dict[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "published"):
        parts = work.get(key, {}).get("date-parts", [])
        if parts and parts[0]:
            return int(parts[0][0])
    return None


def strip_crossref_markup(text: str) -> str:
    return text.replace("<jats:p>", "").replace("</jats:p>", "").strip()
