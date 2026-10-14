"""Paths, ISO weeks and JSON files of the weekly run."""

import datetime as dt
import json
import os
from typing import Any, List, Optional

from Config import Config


def data_dir() -> str:
    return getattr(Config, 'DATA_DIR', 'data')


def featured_dir() -> str:
    """Published weeks, served under /data/featured/."""
    return os.path.join(data_dir(), 'featured')


def week_dir(week: str) -> str:
    """Working files of one week; /data/ does not serve them (app.py)."""
    return os.path.join(data_dir(), 'trends', week)


def iso_week(day: Optional[dt.date] = None) -> str:
    year, week, _ = (day or dt.date.today()).isocalendar()
    return f"{year}-W{week:02d}"


def load_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path: str, obj: Any):
    """Writes atomically, so the web app never reads a half-written file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def recent_featured(before_week: str, weeks: int = 8) -> List[dict]:
    """Topics featured in the `weeks` published weeks before `before_week`, newest first."""
    folder = featured_dir()
    if not os.path.isdir(folder):
        return []
    files = sorted((f for f in os.listdir(folder) if f.endswith('.json') and f[:4].isdigit()), reverse=True)
    topics = []
    for name in [f for f in files if f[:-5] < before_week][:weeks]:
        topics.extend((load_json(os.path.join(folder, name), {}) or {}).get('topics', []))
    return topics
