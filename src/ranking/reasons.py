from __future__ import annotations

from src.models import Paper


def build_reason(paper: Paper, signals: list[str]) -> str:
    useful_signals = [signal for signal in signals if signal]
    if useful_signals:
        return "; ".join(useful_signals[:4])
    if paper.related_to_seed and paper.relation_reason:
        return paper.relation_reason
    return "Matches the seed collection through title, abstract, venue, or citation metadata."


def reason_from_llm_or_local(paper: Paper, local_reason: str) -> str:
    if paper.why_recommended:
        return paper.why_recommended
    return local_reason
