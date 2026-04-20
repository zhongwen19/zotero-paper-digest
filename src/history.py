from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from src.dedup import paper_identity_keys
from src.models import Digest, Paper

DEFAULT_HISTORY_COOLDOWN_DAYS = 30
MAX_STORED_HISTORY_DAYS = 365


def load_recommendation_history(path: str | Path, cooldown_days: int = DEFAULT_HISTORY_COOLDOWN_DAYS) -> set[str]:
    history_path = Path(path)
    payload = _read_history_payload(history_path)
    if not payload:
        return set()
    history_entries = payload.get("history", [])
    if not isinstance(history_entries, list):
        return set()
    active_keys = {
        entry["identity_key"]
        for entry in history_entries
        if isinstance(entry, dict) and is_history_entry_active(entry, cooldown_days)
    }
    return {str(key) for key in active_keys if key}


def filter_previously_recommended(candidates: list[Paper], history_keys: set[str]) -> list[Paper]:
    if not history_keys:
        return candidates
    return [
        paper
        for paper in candidates
        if not paper_identity_keys(paper).intersection(history_keys)
    ]


def filter_to_previously_recommended(candidates: list[Paper], history_keys: set[str]) -> list[Paper]:
    if not history_keys:
        return []
    return [
        paper
        for paper in candidates
        if paper_identity_keys(paper).intersection(history_keys)
    ]


def save_recommendation_history(path: str | Path, digest: Digest, existing_keys: set[str]) -> None:
    history_path = Path(path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _read_history_payload(history_path)
    cutoff_date = dt.date.today() - dt.timedelta(days=MAX_STORED_HISTORY_DAYS)
    retained_history = [
        entry
        for entry in payload.get("history", [])
        if isinstance(entry, dict) and parse_history_date(entry.get("recommended_at")) >= cutoff_date
    ]
    latest_by_key: dict[str, dict[str, Any]] = {}
    for entry in retained_history:
        key = str(entry.get("identity_key", "")).strip()
        if key:
            latest_by_key[key] = entry

    entries: list[dict[str, Any]] = []
    recommended_at = dt.date.today().isoformat()
    for paper in digest.new_papers + digest.classic_papers:
        keys = sorted(paper_identity_keys(paper))
        entries.append(
            {
                "title": paper.title,
                "doi": paper.doi,
                "year": paper.year,
                "category": paper.category,
                "identity_keys": keys,
            }
        )
        for key in keys:
            latest_by_key[key] = {
                "identity_key": key,
                "title": paper.title,
                "doi": paper.doi,
                "year": paper.year,
                "category": paper.category,
                "recommended_at": recommended_at,
            }

    all_keys = set(existing_keys)
    all_keys.update(latest_by_key.keys())
    payload = {
        "identity_keys": sorted(all_keys),
        "history": sorted(
            latest_by_key.values(),
            key=lambda entry: (str(entry.get("recommended_at", "")), str(entry.get("identity_key", ""))),
            reverse=True,
        ),
        "last_recommendations": entries,
    }
    history_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def is_history_entry_active(entry: dict[str, Any], cooldown_days: int) -> bool:
    key = str(entry.get("identity_key", "")).strip()
    if not key:
        return False
    if cooldown_days <= 0:
        return True
    recommended_at = parse_history_date(entry.get("recommended_at"))
    if recommended_at == dt.date.min:
        return False
    cutoff_date = dt.date.today() - dt.timedelta(days=cooldown_days)
    return recommended_at >= cutoff_date


def parse_history_date(value: object) -> dt.date:
    if not value:
        return dt.date.min
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        return dt.date.min


def _read_history_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
