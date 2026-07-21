"""领域包：身份与版本常量。"""

from yancuo_win.domain.identity import (
    DATA_FORMAT_VERSION,
    SCHEMA_VERSION,
    LocalIdentity,
    load_or_create_identity,
)

__all__ = [
    "DATA_FORMAT_VERSION",
    "SCHEMA_VERSION",
    "LocalIdentity",
    "load_or_create_identity",
]
