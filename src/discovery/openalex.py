from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import requests

from src.dedup import normalize_doi
from src.models import Paper
from src.retry import with_retries
from src.text_cleaning import clean_abstract, clean_text

LOGGER = logging.getLogger(__name__)


class OpenAlexClient:
    def __init__(self, mailto: str = "") -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "zotero-paper-digest/0.1"})
        self.mailto = mailto

    def discover_recent(self, seeds: list[Paper], recent_days: int, max_results: int) -> list[Paper]:
        from_date = (dt.date.today() - dt.timedelta(days=recent_days)).isoformat()
        params_extra = {"filter": f"from_publication_date:{from_date}"}
        return self._search_from_seeds(
            seeds,
            max_results,
            params_extra,
            "recent OpenAlex match",
            include_related=False,
        )

    def discover_classics(
        self,
        seeds: list[Paper],
        min_age_years: int,
        max_age_years: int,
        max_results: int,
    ) -> list[Paper]:
        cutoff_year = dt.date.today().year - min_age_years
        start_year = dt.date.today().year - max_age_years
        params_extra = {
            "filter": f"from_publication_date:{start_year}-01-01,to_publication_date:{cutoff_year}-12-31",
            "sort": "cited_by_count:desc",
        }
        return self._search_from_seeds(
            seeds,
            max_results,
            params_extra,
            "high-citation OpenAlex classic",
            include_related=True,
        )

    def _search_from_seeds(
        self,
        seeds: list[Paper],
        max_results: int,
        params_extra: dict[str, str],
        relation_reason: str,
        include_related: bool,
    ) -> list[Paper]:
        candidates: list[Paper] = []
        queries = self._seed_queries(seeds)
        per_query = max(5, min(25, max_results // max(len(queries), 1) + 1))
        for query in queries:
            if len(candidates) >= max_results:
                break
            params: dict[str, Any] = {
                "search": query,
                "per-page": per_query,
                "select": "id,doi,title,display_name,publication_year,publication_date,authorships,primary_location,abstract_inverted_index,concepts,cited_by_count,ids,related_works,referenced_works",
            }
            params.update(params_extra)
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
        if include_related:
            candidates.extend(self._related_works(seeds, max(0, max_results - len(candidates))))
        return candidates[:max_results]

    def _related_works(self, seeds: list[Paper], remaining: int) -> list[Paper]:
        if remaining <= 0:
            return []
        candidates: list[Paper] = []
        for seed in seeds[:8]:
            if not seed.doi:
                continue
            work = self._get_work_by_doi(seed.doi)
            if not work:
                continue
            related_ids = list(work.get("related_works", []) or [])[:2]
            for related_id in related_ids:
                if len(candidates) >= remaining:
                    return candidates
                related = self._get_work_by_id(related_id)
                paper = work_to_paper(related) if related else None
                if paper:
                    paper.related_to_seed = True
                    paper.relation_reason = f"OpenAlex related work near seed: {seed.title[:80]}"
                    candidates.append(paper)
        return candidates

    def _get_works(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        def request() -> requests.Response:
            response = self.session.get("https://api.openalex.org/works", params=params, timeout=30)
            response.raise_for_status()
            return response

        try:
            return with_retries(request, logger=LOGGER).json().get("results", [])
        except requests.RequestException as exc:
            LOGGER.warning("OpenAlex search failed: %s", exc)
            return []

    def _get_work_by_doi(self, doi: str) -> dict[str, Any] | None:
        clean_doi = normalize_doi(doi)
        if not clean_doi:
            return None
        return self._get_work_by_id(f"https://doi.org/{clean_doi}")

    def _get_work_by_id(self, work_id: str) -> dict[str, Any] | None:
        if not work_id:
            return None
        url = openalex_api_url(work_id)
        params = {"mailto": self.mailto} if self.mailto else {}

        def request() -> requests.Response:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response

        try:
            return with_retries(request, logger=LOGGER).json()
        except requests.RequestException as exc:
            LOGGER.debug("OpenAlex work lookup failed for %s: %s", work_id, exc)
            return None

    def _get_source_by_id(self, source_id: str) -> dict[str, Any] | None:
        if not source_id:
            return None
        url = openalex_source_api_url(source_id)
        params = {"mailto": self.mailto} if self.mailto else {}

        def request() -> requests.Response:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response

        try:
            return with_retries(request, logger=LOGGER).json()
        except requests.RequestException as exc:
            LOGGER.debug("OpenAlex source lookup failed for %s: %s", source_id, exc)
            return None

    def _search_sources(self, venue: str) -> list[dict[str, Any]]:
        clean_venue = clean_text(venue)
        if not clean_venue:
            return []
        params: dict[str, Any] = {
            "search": clean_venue,
            "per-page": 5,
            "select": "id,display_name,summary_stats",
        }
        if self.mailto:
            params["mailto"] = self.mailto

        def request() -> requests.Response:
            response = self.session.get("https://api.openalex.org/sources", params=params, timeout=30)
            response.raise_for_status()
            return response

        try:
            return with_retries(request, logger=LOGGER).json().get("results", [])
        except requests.RequestException as exc:
            LOGGER.debug("OpenAlex source search failed for %s: %s", venue, exc)
            return []

    def _get_source_by_venue(self, venue: str) -> dict[str, Any] | None:
        matches = self._search_sources(venue)
        if not matches:
            return None
        target = normalize_title_key(venue)
        for match in matches:
            if normalize_title_key(match.get("display_name", "")) == target:
                return match
        return matches[0]

    def _search_work_by_title(self, title: str) -> dict[str, Any] | None:
        clean_title = clean_text(title)
        if not clean_title:
            return None
        params: dict[str, Any] = {
            "search": clean_title,
            "per-page": 5,
            "select": "id,doi,title,display_name,publication_year,publication_date,authorships,primary_location,abstract_inverted_index,concepts,cited_by_count,ids,related_works,referenced_works",
        }
        if self.mailto:
            params["mailto"] = self.mailto
        works = self._get_works(params)
        target = normalize_title_key(clean_title)
        for work in works:
            work_title = work.get("title") or work.get("display_name") or ""
            if normalize_title_key(work_title) == target:
                return work
        return None

    def enrich_papers(self, papers: list[Paper]) -> None:
        source_cache: dict[str, dict[str, Any] | None] = {}
        for paper in papers:
            work = self._lookup_work_for_paper(paper)
            source_payload: dict[str, Any] | None = None
            if work:
                merge_work_metadata(paper, work)
                source = ((work.get("primary_location") or {}).get("source") or {})
                source_id = source.get("id", "")
                if source_id:
                    if source_id not in source_cache:
                        source_cache[source_id] = self._get_source_by_id(source_id)
                    source_payload = source_cache[source_id]
            if source_payload is None and paper.venue:
                venue_key = f"venue:{normalize_title_key(paper.venue)}"
                if venue_key not in source_cache:
                    source_cache[venue_key] = self._get_source_by_venue(paper.venue)
                source_payload = source_cache[venue_key]
            impact_factor = source_impact_factor(source_payload or {})
            if impact_factor is not None:
                paper.journal_impact_factor = impact_factor

    def _lookup_work_for_paper(self, paper: Paper) -> dict[str, Any] | None:
        if paper.openalex_id:
            return self._get_work_by_id(paper.openalex_id)
        openalex_external_id = paper.external_ids.get("openalex", "")
        if openalex_external_id:
            return self._get_work_by_id(openalex_external_id)
        if paper.source == "openalex" and paper.source_id:
            return self._get_work_by_id(paper.source_id)
        if paper.doi:
            work = self._get_work_by_doi(paper.doi)
            if work:
                return work
        return self._search_work_by_title(paper.title)

    @staticmethod
    def _seed_queries(seeds: list[Paper]) -> list[str]:
        queries: list[str] = []
        for seed in seeds[:10]:
            terms = " ".join(seed.tags[:4]) if seed.tags else seed.title
            query = " ".join(terms.split()[:12]).strip()
            if query and query not in queries:
                queries.append(query)
        return queries or ["machine learning research"]


def work_to_paper(work: dict[str, Any]) -> Paper | None:
    title = clean_text(work.get("title") or work.get("display_name") or "")
    if not title:
        return None
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    ids = work.get("ids") or {}
    doi = work.get("doi") or ids.get("doi") or ""
    url = primary_location.get("landing_page_url") or ids.get("openalex") or ids.get("doi") or ""
    authors = []
    for authorship in work.get("authorships", [])[:12]:
        author = authorship.get("author") or {}
        if author.get("display_name"):
            authors.append(author["display_name"])
    concepts = [
        concept.get("display_name", "")
        for concept in work.get("concepts", [])[:10]
        if concept.get("display_name")
    ]
    return Paper(
        title=title,
        abstract=clean_abstract(abstract_from_inverted_index(work.get("abstract_inverted_index") or {})),
        doi=doi,
        year=work.get("publication_year"),
        venue=source.get("display_name", "") if source else "",
        authors=authors,
        url=url,
        source="openalex",
        source_id=work.get("id", ""),
        openalex_id=work.get("id", ""),
        external_ids={key: str(value) for key, value in ids.items() if value},
        concepts=concepts,
        cited_by_count=int(work.get("cited_by_count") or 0),
    )


def merge_work_metadata(paper: Paper, work: dict[str, Any]) -> None:
    paper.openalex_id = clean_text(work.get("id") or paper.openalex_id)
    ids = work.get("ids") or {}
    if ids:
        paper.external_ids.update({key: str(value) for key, value in ids.items() if value})
    if not paper.abstract:
        paper.abstract = clean_abstract(abstract_from_inverted_index(work.get("abstract_inverted_index") or {}))
    cited_by_count = work.get("cited_by_count")
    if cited_by_count is not None:
        paper.cited_by_count = int(cited_by_count)
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    if source.get("display_name"):
        paper.venue = clean_text(source["display_name"])
    landing_page_url = primary_location.get("landing_page_url")
    if landing_page_url and not paper.url:
        paper.url = clean_text(landing_page_url)


def source_impact_factor(source: dict[str, Any]) -> float | None:
    summary_stats = source.get("summary_stats") or {}
    value = summary_stats.get("2yr_mean_citedness")
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def abstract_from_inverted_index(index: dict[str, list[int]]) -> str:
    if not index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, word_positions in index.items():
        for position in word_positions:
            positions.append((int(position), word))
    positions.sort()
    return " ".join(word for _, word in positions)


def openalex_api_url(work_id: str) -> str:
    if work_id.startswith("https://api.openalex.org/works/"):
        return work_id
    if work_id.startswith("https://openalex.org/"):
        return work_id.replace("https://openalex.org/", "https://api.openalex.org/works/")
    if work_id.startswith("https://doi.org/"):
        return f"https://api.openalex.org/works/{work_id}"
    if work_id.startswith("W"):
        return f"https://api.openalex.org/works/{work_id}"
    return work_id


def openalex_source_api_url(source_id: str) -> str:
    if source_id.startswith("https://api.openalex.org/sources/"):
        return source_id
    if source_id.startswith("https://openalex.org/"):
        return source_id.replace("https://openalex.org/", "https://api.openalex.org/sources/")
    if source_id.startswith("S"):
        return f"https://api.openalex.org/sources/{source_id}"
    return source_id


def normalize_title_key(value: object) -> str:
    return clean_text(value).casefold()
