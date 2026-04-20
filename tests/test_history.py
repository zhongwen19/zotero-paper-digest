import datetime as dt
import json

from src.history import (
    DEFAULT_HISTORY_COOLDOWN_DAYS,
    filter_previously_recommended,
    filter_to_previously_recommended,
    load_recommendation_history,
)
from src.models import Paper


def test_filter_previously_recommended_uses_doi_identity() -> None:
    candidates = [
        Paper(title="Already seen", doi="10.1/ABC", year=2025, abstract="useful abstract text for testing"),
        Paper(title="New paper", doi="10.2/NEW", year=2025, abstract="useful abstract text for testing"),
    ]

    filtered = filter_previously_recommended(candidates, {"doi:10.1/abc"})

    assert [paper.title for paper in filtered] == ["New paper"]


def test_load_recommendation_history_ignores_legacy_permanent_blacklist(tmp_path) -> None:
    history_file = tmp_path / "recommended_history.json"
    history_file.write_text(
        json.dumps(
            {
                "identity_keys": ["doi:10.1/abc", "doi:10.2/def"],
                "last_recommendations": [],
            }
        ),
        encoding="utf-8",
    )

    assert load_recommendation_history(history_file) == set()


def test_load_recommendation_history_respects_cooldown_days(tmp_path) -> None:
    today = dt.date.today()
    history_file = tmp_path / "recommended_history.json"
    history_file.write_text(
        json.dumps(
            {
                "history": [
                    {
                        "identity_key": "doi:10.1/recent",
                        "recommended_at": today.isoformat(),
                    },
                    {
                        "identity_key": "doi:10.1/old",
                        "recommended_at": (today - dt.timedelta(days=DEFAULT_HISTORY_COOLDOWN_DAYS + 1)).isoformat(),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    assert load_recommendation_history(history_file) == {"doi:10.1/recent"}


def test_filter_to_previously_recommended_keeps_only_seen_candidates() -> None:
    candidates = [
        Paper(title="Seen paper", doi="10.1/ABC", year=2025, abstract="useful abstract text for testing"),
        Paper(title="New paper", doi="10.2/NEW", year=2025, abstract="useful abstract text for testing"),
    ]

    filtered = filter_to_previously_recommended(candidates, {"doi:10.1/abc"})

    assert [paper.title for paper in filtered] == ["Seen paper"]
