"""领域包：身份、版本常量与规则。"""

from yancuo_win.domain.identity import (
    DATA_FORMAT_VERSION,
    SCHEMA_VERSION,
    LocalIdentity,
    load_or_create_identity,
)
from yancuo_win.domain.rules import (
    DomainError,
    assert_asset_writable,
    assert_transition,
    can_transition,
    validate_priority,
    validate_status,
)

__all__ = [
    "DATA_FORMAT_VERSION",
    "SCHEMA_VERSION",
    "LocalIdentity",
    "DomainError",
    "assert_asset_writable",
    "assert_transition",
    "can_transition",
    "load_or_create_identity",
    "validate_priority",
    "validate_status",
]
