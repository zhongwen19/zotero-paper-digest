from src.dedup import deduplicate_candidates, normalize_doi, normalize_title
from src.models import Paper


def test_normalize_doi_strips_common_prefixes() -> None:
    assert normalize_doi("https://doi.org/10.1000/ABC. ") == "10.1000/abc"
    assert normalize_doi("doi: 10.1000/ABC") == "10.1000/abc"


def test_normalize_title_removes_punctuation_and_case() -> None:
    assert normalize_title("Deep Learning: A Survey!") == "deep learning a survey"


def test_deduplicate_candidates_removes_existing_by_doi_and_title() -> None:
    existing = [Paper(title="A Good Paper", doi="10.1/ABC", year=2020, abstract="seed")]
    candidates = [
        Paper(title="Different title", doi="https://doi.org/10.1/abc", year=2025, abstract="dup"),
        Paper(title="A Good Paper", doi="", year=2025, abstract="dup"),
        Paper(title="A New Paper", doi="10.2/new", year=2025, abstract="fresh"),
    ]

    deduped = deduplicate_candidates(candidates, existing)

    assert [paper.title for paper in deduped] == ["A New Paper"]


def test_deduplicate_candidates_skips_low_quality_metadata() -> None:
    deduped = deduplicate_candidates([Paper(title="No year", doi="10.1/x", abstract="x")], [])

    assert deduped == []
