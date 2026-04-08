from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ZoteroConfig:
    library_type: str
    library_id: str
    collection_keys: list[str]
    max_seeds: int


@dataclass
class DiscoveryConfig:
    recent_days: int
    max_candidates_per_source: int
    sources: list[str]


@dataclass
class RankingConfig:
    shortlist_size: int
    llm_enabled: bool
    llm_max_items: int
    prefer_new: bool
    min_new_results_before_classics: int


@dataclass
class ClassicsConfig:
    min_age_years: int


@dataclass
class EmailConfig:
    from_email: str
    to_email: str
    smtp_host: str
    smtp_port: int


@dataclass
class OutputConfig:
    save_json: bool
    save_html: bool


@dataclass
class AppConfig:
    zotero: ZoteroConfig
    discovery: DiscoveryConfig
    ranking: RankingConfig
    classics: ClassicsConfig
    email: EmailConfig
    output: OutputConfig


def _split_env_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config(path: str | Path) -> AppConfig:
    with Path(path).open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    zotero = raw.get("zotero", {})
    discovery = raw.get("discovery", {})
    ranking = raw.get("ranking", {})
    classics = raw.get("classics", {})
    email = raw.get("email", {})
    output = raw.get("output", {})

    collection_keys = _split_env_list(os.getenv("ZOTERO_COLLECTION_KEYS")) or list(
        zotero.get("collection_keys", [])
    )

    return AppConfig(
        zotero=ZoteroConfig(
            library_type=os.getenv("ZOTERO_LIBRARY_TYPE", zotero.get("library_type", "user")),
            library_id=os.getenv("ZOTERO_LIBRARY_ID", str(zotero.get("library_id", ""))),
            collection_keys=collection_keys,
            max_seeds=int(os.getenv("MAX_SEEDS", zotero.get("max_seeds", 30))),
        ),
        discovery=DiscoveryConfig(
            recent_days=int(os.getenv("RECENT_DAYS", discovery.get("recent_days", 90))),
            max_candidates_per_source=int(
                os.getenv("MAX_CANDIDATES_PER_SOURCE", discovery.get("max_candidates_per_source", 50))
            ),
            sources=_split_env_list(os.getenv("DISCOVERY_SOURCES"))
            or list(discovery.get("sources", ["openalex", "crossref"])),
        ),
        ranking=RankingConfig(
            shortlist_size=int(os.getenv("SHORTLIST_SIZE", ranking.get("shortlist_size", 25))),
            llm_enabled=_env_bool("LLM_ENABLED", bool(ranking.get("llm_enabled", True))),
            llm_max_items=int(os.getenv("LLM_MAX_ITEMS", ranking.get("llm_max_items", 25))),
            prefer_new=_env_bool("PREFER_NEW", bool(ranking.get("prefer_new", True))),
            min_new_results_before_classics=int(
                os.getenv(
                    "MIN_NEW_RESULTS_BEFORE_CLASSICS",
                    ranking.get("min_new_results_before_classics", 3),
                )
            ),
        ),
        classics=ClassicsConfig(
            min_age_years=int(os.getenv("CLASSIC_MIN_AGE_YEARS", classics.get("min_age_years", 5)))
        ),
        email=EmailConfig(
            from_email=os.getenv("EMAIL_FROM", email.get("from_email", "")),
            to_email=os.getenv("EMAIL_TO", email.get("to_email", "")),
            smtp_host=os.getenv("SMTP_HOST", email.get("smtp_host", "")),
            smtp_port=int(os.getenv("SMTP_PORT", email.get("smtp_port", 587))),
        ),
        output=OutputConfig(
            save_json=bool(output.get("save_json", True)),
            save_html=bool(output.get("save_html", True)),
        ),
    )


def validate_config(config: AppConfig, *, require_email: bool = True) -> None:
    missing: list[str] = []
    if not config.zotero.library_id:
        missing.append("ZOTERO_LIBRARY_ID")
    if not os.getenv("ZOTERO_API_KEY"):
        missing.append("ZOTERO_API_KEY")
    if not config.zotero.collection_keys:
        missing.append("ZOTERO_COLLECTION_KEYS")
    if require_email:
        if not config.email.smtp_host:
            missing.append("SMTP_HOST")
        if not os.getenv("SMTP_USER"):
            missing.append("SMTP_USER")
        if not os.getenv("SMTP_PASS"):
            missing.append("SMTP_PASS")
        if not config.email.from_email:
            missing.append("EMAIL_FROM")
        if not config.email.to_email:
            missing.append("EMAIL_TO")
    if config.ranking.llm_enabled and not os.getenv("DEEPSEEK_API_KEY"):
        missing.append("DEEPSEEK_API_KEY or set ranking.llm_enabled=false")
    if missing:
        raise ValueError(f"Missing required configuration: {', '.join(missing)}")
