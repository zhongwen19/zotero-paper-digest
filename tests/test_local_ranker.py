from src.models import Paper
from src.ranking.local_ranker import build_seed_profile, score_paper, select_shortlist


def test_score_paper_uses_multiple_relevance_signals() -> None:
    seeds = [
        Paper(
            title="Graph neural networks for molecular property prediction",
            abstract="message passing and molecular representation learning",
            year=2020,
            venue="NeurIPS",
            authors=["Ada Lovelace"],
            tags=["graph neural networks"],
        )
    ]
    profile = build_seed_profile(seeds)
    candidate = Paper(
        title="Graph neural networks improve molecular representation learning",
        abstract="message passing for molecule property prediction",
        year=2026,
        venue="NeurIPS",
        authors=["Ada Lovelace", "Grace Hopper"],
        doi="10.1/test",
        cited_by_count=12,
        related_to_seed=True,
        relation_reason="OpenAlex related work near seed",
    )

    score, reason = score_paper(candidate, profile, recent_days=90)

    assert score > 5
    assert "overlaps" in reason
    assert "NeurIPS" in reason


def test_select_shortlist_orders_by_score() -> None:
    seeds = [Paper(title="causal inference treatment effects", abstract="identification", year=2020)]
    weak = Paper(title="unrelated ecology note", abstract="plants", year=2026, doi="10.1/weak")
    strong = Paper(
        title="causal inference for treatment effect identification",
        abstract="causal identification and treatment effect estimation",
        year=2026,
        doi="10.1/strong",
        related_to_seed=True,
    )

    shortlist = select_shortlist([weak, strong], seeds, shortlist_size=1, recent_days=90)

    assert shortlist[0].title == strong.title
