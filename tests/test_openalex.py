from src.discovery.openalex import merge_work_metadata, normalize_title_key, openalex_source_api_url, source_impact_factor
from src.models import Paper


def test_source_impact_factor_uses_two_year_mean_citedness() -> None:
    source = {"summary_stats": {"2yr_mean_citedness": 5.236}}

    assert source_impact_factor(source) == 5.24


def test_openalex_source_api_url_converts_source_ids() -> None:
    assert openalex_source_api_url("https://openalex.org/S123456789") == "https://api.openalex.org/sources/S123456789"
    assert openalex_source_api_url("S123456789") == "https://api.openalex.org/sources/S123456789"


def test_merge_work_metadata_backfills_missing_abstract() -> None:
    paper = Paper(title="Example paper")
    work = {
        "id": "https://openalex.org/W123",
        "abstract_inverted_index": {
            "Magnetic": [0],
            "resonance": [1],
            "imaging": [2],
            "reconstruction": [3],
            "uses": [4],
            "a": [5],
            "fast": [6],
            "method": [7],
            "today.": [8],
        },
    }

    merge_work_metadata(paper, work)

    assert "Magnetic resonance imaging reconstruction" in paper.abstract


def test_normalize_title_key_is_case_insensitive() -> None:
    assert normalize_title_key("The Visual Computer") == normalize_title_key("the visual computer")
