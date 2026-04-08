from __future__ import annotations

import logging
import re
from typing import Any

import requests

from src.models import Paper
from src.retry import with_retries

LOGGER = logging.getLogger(__name__)


class ZoteroClient:
    def __init__(self, library_type: str, library_id: str, api_key: str) -> None:
        if library_type not in {"user", "group"}:
            raise ValueError("zotero.library_type must be 'user' or 'group'")
        library_path = "users" if library_type == "user" else "groups"
        self.base_url = f"https://api.zotero.org/{library_path}/{library_id}"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Zotero-API-Key": api_key,
                "Zotero-API-Version": "3",
                "User-Agent": "zotero-paper-digest/0.1",
            }
        )

    def fetch_seed_papers(self, collection_keys: list[str], max_seeds: int) -> list[Paper]:
        papers: list[Paper] = []
        for collection_key in collection_keys:
            if len(papers) >= max_seeds:
                break
            collection_papers = self._fetch_collection_items(collection_key, max_seeds - len(papers))
            LOGGER.info("Fetched %s seed papers from Zotero collection %s", len(collection_papers), collection_key)
            papers.extend(collection_papers)
        return papers[:max_seeds]

    def _fetch_collection_items(self, collection_key: str, remaining: int) -> list[Paper]:
        papers: list[Paper] = []
        start = 0
        limit = min(100, max(remaining, 1))
        while len(papers) < remaining:
            url = f"{self.base_url}/collections/{collection_key}/items/top"
            params = {"format": "json", "limit": limit, "start": start}

            def request() -> requests.Response:
                response = self.session.get(url, params=params, timeout=30)
                response.raise_for_status()
                return response

            response = with_retries(request, logger=LOGGER)
            items = response.json()
            if not items:
                break
            for item in items:
                paper = self._item_to_paper(item)
                if paper:
                    papers.append(paper)
                    if len(papers) >= remaining:
                        break
            start += len(items)
            if len(items) < limit:
                break
        return papers

    @staticmethod
    def _item_to_paper(item: dict[str, Any]) -> Paper | None:
        data = item.get("data", {})
        item_type = data.get("itemType", "")
        if item_type in {"attachment", "note", "annotation"}:
            return None
        title = str(data.get("title", "")).strip()
        if not title:
            return None
        venue = (
            data.get("publicationTitle")
            or data.get("conferenceName")
            or data.get("proceedingsTitle")
            or data.get("journalAbbreviation")
            or ""
        )
        authors = []
        for creator in data.get("creators", []):
            name = " ".join(
                part
                for part in [creator.get("firstName", ""), creator.get("lastName", "")]
                if part
            ).strip()
            if not name and creator.get("name"):
                name = creator["name"].strip()
            if name:
                authors.append(name)
        tags = [tag.get("tag", "") for tag in data.get("tags", []) if tag.get("tag")]
        return Paper(
            title=title,
            abstract=str(data.get("abstractNote", "") or ""),
            doi=str(data.get("DOI", "") or ""),
            year=_extract_year(str(data.get("date", "") or "")),
            venue=str(venue or ""),
            authors=authors,
            tags=tags,
            url=str(data.get("url", "") or ""),
            source="zotero",
            source_id=str(data.get("key", "") or item.get("key", "")),
        )


def _extract_year(date_value: str) -> int | None:
    match = re.search(r"(19|20)\d{2}", date_value)
    if not match:
        return None
    return int(match.group(0))
