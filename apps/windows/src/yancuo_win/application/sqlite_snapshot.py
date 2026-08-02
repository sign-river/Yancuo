"""Create consistent, portable SQLite snapshots."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterable

from yancuo_win.domain.rules import DomainError


def create_sqlite_snapshot(
    source: Path, destination: Path, *, drop_tables: Iterable[str] = ()
) -> None:
    """Copy committed main/WAL pages through SQLite's online backup API."""

    source = Path(source)
    destination = Path(destination)
    if source.is_symlink() or not source.is_file():
        raise DomainError("数据库不存在或不是普通文件，无法导出")
    try:
        with (
            closing(sqlite3.connect(source)) as source_connection,
            closing(sqlite3.connect(destination)) as destination_connection,
        ):
            source_connection.backup(destination_connection)
            for table in drop_tables:
                if not table.replace("_", "").isalnum():
                    raise ValueError(f"invalid SQLite table name: {table!r}")
                destination_connection.execute(f'DROP TABLE IF EXISTS "{table}"')
            destination_connection.commit()
    except (OSError, sqlite3.DatabaseError) as exc:
        destination.unlink(missing_ok=True)
        raise DomainError("数据库快照创建失败") from exc
