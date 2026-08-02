"""Authoritative resource boundary for backups, exports, and cloud upload."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from yancuo_win.data.models import Asset, NoteAsset


def formal_asset_relative_paths(session: Session) -> set[str]:
    """Return only durable user-facing resources; staging originals are excluded."""

    problem_figures = session.scalars(
        select(Asset.relative_path).where(
            Asset.role == "derived_figure",
            Asset.relative_path != "",
        )
    ).all()
    note_resources = session.scalars(
        select(NoteAsset.relative_path).where(
            NoteAsset.role != "original",
            NoteAsset.relative_path != "",
        )
    ).all()
    return {str(value) for value in [*problem_figures, *note_resources] if value}
