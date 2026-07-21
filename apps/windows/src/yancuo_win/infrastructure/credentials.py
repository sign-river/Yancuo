"""系统凭据读写（Windows Credential Manager / keyring）。禁止把密钥写入 TOML。"""

from __future__ import annotations

from yancuo_win.domain.rules import DomainError

SERVICE_NAME = "Yancuo"


def get_secret(credential_key: str) -> str | None:
    if not credential_key:
        return None
    try:
        import keyring
    except ImportError as exc:  # pragma: no cover
        raise DomainError("未安装 keyring，无法读取系统凭据") from exc
    value = keyring.get_password(SERVICE_NAME, credential_key)
    return value or None


def set_secret(credential_key: str, secret: str) -> None:
    if not credential_key:
        raise DomainError("credential_key 为空")
    if not secret.strip():
        raise DomainError("令牌不能为空")
    try:
        import keyring
    except ImportError as exc:  # pragma: no cover
        raise DomainError("未安装 keyring，无法保存系统凭据") from exc
    keyring.set_password(SERVICE_NAME, credential_key, secret.strip())


def delete_secret(credential_key: str) -> None:
    if not credential_key:
        return
    try:
        import keyring
    except ImportError:
        return
    try:
        keyring.delete_password(SERVICE_NAME, credential_key)
    except keyring.errors.PasswordDeleteError:
        pass


def mask_secret(secret: str | None) -> str:
    if not secret:
        return "（未配置）"
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}…{secret[-4:]}（长度 {len(secret)}）"
