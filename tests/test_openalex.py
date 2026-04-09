from src.discovery.openalex import openalex_source_api_url, source_impact_factor


def test_source_impact_factor_uses_two_year_mean_citedness() -> None:
    source = {"summary_stats": {"2yr_mean_citedness": 5.236}}

    assert source_impact_factor(source) == 5.24


def test_openalex_source_api_url_converts_source_ids() -> None:
    assert openalex_source_api_url("https://openalex.org/S123456789") == "https://api.openalex.org/sources/S123456789"
    assert openalex_source_api_url("S123456789") == "https://api.openalex.org/sources/S123456789"
