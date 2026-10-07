"""Load `data/sources.yaml` (file -> stable doc identity)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.ingest.models import DocMeta


def load_sources(path: Path) -> list[DocMeta]:
    """Parse the sources manifest; raises ValueError on duplicate ids or missing fields."""
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    effective = str(data.get("effective_date", ""))
    metas = [
        DocMeta(
            effective_date=str(d.get("effective_date", effective)),
            **{k: v for k, v in d.items() if k != "effective_date"},
        )
        for d in data["documents"]
    ]
    ids = [m.doc_id for m in metas]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate doc_id in {path}")
    return metas
