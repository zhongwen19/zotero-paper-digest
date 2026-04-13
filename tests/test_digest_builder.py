import datetime as dt

import src.digest_builder as digest_builder
from src.config import (
    AppConfig,
    ClassicsConfig,
    DiscoveryConfig,
    EmailConfig,
    OutputConfig,
    RankingConfig,
    ZoteroConfig,
)
from src.digest_builder import build_digest
from src.models import Paper


def make_config() -> AppConfig:
    return AppConfig(
        zotero=ZoteroConfig(
            library_type="user",
            library_id="123",
            collection_keys=["ABC123"],
            max_seeds=10,
        ),
        discovery=DiscoveryConfig(
            recent_days=90,
            recent_backfill_years=[2, 3],
            max_candidates_per_source=20,
            sources=["openalex"],
        ),
        ranking=RankingConfig(
            shortlist_size=10,
            llm_enabled=False,
            llm_max_items=10,
            prefer_new=True,
            min_new_results_before_classics=1,
            target_new_results=1,
            target_classic_results=1,
            required_domain_terms=["MRI", "magnetic resonance imaging"],
        ),
        classics=ClassicsConfig(
            min_age_years=2,
            max_age_years=8,
        ),
        email=EmailConfig(
            from_email="from@example.com",
            to_email="to@example.com",
            smtp_host="smtp.example.com",
            smtp_port=587,
        ),
        output=OutputConfig(
            save_json=True,
            save_html=True,
        ),
    )


def test_build_digest_backfills_new_papers_from_recent_years(monkeypatch) -> None:
    current_year = dt.date.today().year
    config = make_config()
    seeds = [
        Paper(
            title="Cardiac MRI reconstruction with priors",
            abstract="Magnetic resonance imaging reconstruction for cardiac MRI with diffusion priors.",
            year=current_year - 1,
            venue="MRM",
            authors=["Ada Lovelace"],
            tags=["cardiac MRI", "reconstruction"],
        )
    ]

    class FakeZoteroClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def fetch_seed_papers(self, _collection_keys, _max_seeds) -> list[Paper]:
            return seeds

    def fake_discover_candidates(_config, _seeds, *, classics: bool, recent_days: int | None = None) -> list[Paper]:
        if classics:
            return [
                Paper(
                    title="Foundational cardiac MRI reconstruction review",
                    abstract="Magnetic resonance imaging reconstruction review for cardiac MRI systems.",
                    year=current_year - 5,
                    venue="Radiology",
                    doi="10.1/classic",
                    cited_by_count=240,
                    related_to_seed=True,
                    relation_reason="older OpenAlex match",
                )
            ]
        if recent_days == 90:
            return []
        if recent_days == 365 * 2:
            return [
                Paper(
                    title="Cardiac MRI reconstruction using diffusion priors",
                    abstract="Magnetic resonance imaging reconstruction for cardiac MRI using diffusion priors.",
                    year=current_year - 2,
                    venue="MRM",
                    doi="10.1/backfill",
                    related_to_seed=True,
                    relation_reason="recent OpenAlex match",
                )
            ]
        return []

    monkeypatch.setattr(digest_builder, "ZoteroClient", FakeZoteroClient)
    monkeypatch.setattr(digest_builder, "discover_candidates", fake_discover_candidates)
    monkeypatch.setattr(
        digest_builder,
        "rerank_with_deepseek",
        lambda papers, *_args, **_kwargs: (
            papers,
            {
                "llm_enabled": False,
                "llm_triggered": False,
                "llm_items": 0,
                "estimated_input_tokens": 0,
                "estimated_output_tokens": 0,
            },
        ),
    )
    monkeypatch.setattr(digest_builder, "enrich_selected_papers", lambda *args, **kwargs: None)

    digest = build_digest(config, "test-api-key", recommendation_history=set())

    assert [paper.title for paper in digest.new_papers] == ["Cardiac MRI reconstruction using diffusion priors"]
    assert digest.new_papers[0].category == "NEW"
    assert [paper.title for paper in digest.classic_papers] == ["Foundational cardiac MRI reconstruction review"]
    assert digest.classic_papers[0].category == "CLASSIC"
    assert digest.stats["recent_windows_used"] == [90, 730]
    assert digest.stats["recent_backfill_triggered"] is True
