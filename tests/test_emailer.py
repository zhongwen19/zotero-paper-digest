from src.emailer import display_doi, display_url, render_html_digest, render_text_digest
from src.models import Digest, Paper


def test_display_doi_strips_doi_url_prefix() -> None:
    paper = Paper(title="x", doi="https://doi.org/10.1002/mrm.70250")

    assert display_doi(paper) == "10.1002/mrm.70250"


def test_display_url_keeps_duplicate_doi_url_as_clickable_link() -> None:
    paper = Paper(
        title="x",
        doi="https://doi.org/10.1002/mrm.70250",
        url="https://doi.org/10.1002/mrm.70250",
    )

    assert display_url(paper) == "https://doi.org/10.1002/mrm.70250"


def test_render_html_digest_shows_if_summary_and_classic_citations() -> None:
    digest = Digest(
        new_papers=[],
        classic_papers=[
            Paper(
                title="Time domain principal component analysis for real-time MRI",
                abstract="This study reconstructs real-time MRI using temporal principal component analysis and improves latency.",
                summary="The paper reconstructs real-time MRI with temporal PCA and reduces reconstruction latency in undersampled scans.",
                doi="10.1002/mp.15238",
                year=2021,
                venue="Medical Physics",
                url="https://doi.org/10.1002/mp.15238",
                category="CLASSIC",
                why_recommended="Matches the seed collection.",
                cited_by_count=128,
                journal_impact_factor=4.27,
            )
        ],
        stats={},
    )

    html = render_html_digest(digest)

    assert "CLASSIC" in html
    assert "IF: 4.27" in html
    assert "Times Cited: 128" in html
    assert "<strong>Summary:</strong>" in html
    assert "temporal PCA" in html


def test_render_text_digest_places_summary_after_abstract() -> None:
    digest = Digest(
        new_papers=[
            Paper(
                title="Fast cardiac MRI reconstruction",
                abstract="This paper reconstructs accelerated cardiac MRI with a low-rank method and improves temporal fidelity.",
                summary="The paper uses a low-rank reconstruction method for accelerated cardiac MRI and improves temporal fidelity.",
                year=2026,
                venue="Magnetic Resonance in Medicine",
                category="NEW",
                why_recommended="Strong overlap with the seed library.",
                journal_impact_factor=3.8,
            )
        ],
        classic_papers=[],
        stats={},
    )

    text = render_text_digest(digest)

    assert "Tags: NEW | IF:3.8" in text
    assert text.index("Abstract:") < text.index("Summary:") < text.index("Why:")
