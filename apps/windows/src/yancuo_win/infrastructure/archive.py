"""安全处理 ZIP 归档。

所有来自外部的 ZIP 都必须经过这里的成员校验和流式解压。标准库的
``ZipFile.extractall`` 会按照归档内的路径写文件，调用方很容易因此
意外暴露路径穿越、符号链接和 ZIP 炸弹风险。

这个模块不依赖领域层；上层服务可以把 :class:`ArchiveSecurityError`
转换成自己的用户可见异常（当前 Windows 端使用 ``DomainError``）。
"""

from __future__ import annotations

import re
import shutil
import stat
import zipfile
from pathlib import Path
from collections.abc import Iterator


class ArchiveSecurityError(ValueError):
    """归档违反安全约束。"""


# 这些上限针对桌面端的备份/分享包，既能容纳正常数据库和图片，又能
# 避免恶意归档在校验阶段耗尽磁盘。调用方可以为受控场景传入更小的值。
DEFAULT_MAX_MEMBERS = 10_000
DEFAULT_MAX_MEMBER_SIZE = 256 * 1024 * 1024  # 256 MiB
DEFAULT_MAX_TOTAL_SIZE = 512 * 1024 * 1024  # 512 MiB
DEFAULT_MAX_COMPRESSION_RATIO = 1_000

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:($|/)")


def normalize_archive_name(name: str) -> str:
    """返回安全的 POSIX 相对路径，拒绝绝对路径和 ``..``。

    ZIP 规范通常使用 ``/``，但 Windows 创建的归档可能包含反斜杠；
    两者在校验前统一，避免 ``..\\target`` 绕过检查。冒号也被拒绝，
    以免在 Windows 上形成 NTFS alternate data stream 或驱动器路径。
    """

    if not isinstance(name, str):
        raise ArchiveSecurityError("ZIP 条目名称不是文本")
    if "\x00" in name:
        raise ArchiveSecurityError("ZIP 条目名称包含 NUL 字节")
    value = name.replace("\\", "/")
    if value.startswith("/") or _WINDOWS_DRIVE_RE.match(value):
        raise ArchiveSecurityError(f"ZIP 条目必须是相对路径：{name!r}")

    parts: list[str] = []
    for part in value.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ArchiveSecurityError(f"ZIP 条目包含路径穿越：{name!r}")
        if ":" in part:
            raise ArchiveSecurityError(f"ZIP 条目包含非法冒号：{name!r}")
        parts.append(part)
    if not parts:
        raise ArchiveSecurityError("ZIP 条目名称为空")
    return "/".join(parts)


def safe_relative_path(root: Path, relative_name: str) -> Path:
    """将归档内相对路径解析到 ``root``，并确认最终路径仍在 root 内。"""

    root = Path(root).resolve()
    normalized = normalize_archive_name(relative_name)
    candidate = (root / Path(*normalized.split("/"))).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:  # 防御性检查，理论上 normalize 已经拦截
        raise ArchiveSecurityError(f"路径超出归档根目录：{relative_name!r}") from exc
    return candidate


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    # Unix 将文件类型放在 external_attr 的高 16 位。不要依赖
    # ZipInfo.is_dir()，因为恶意归档可以把符号链接伪装成普通文件。
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def validate_zip_members(
    zf: zipfile.ZipFile,
    *,
    max_members: int = DEFAULT_MAX_MEMBERS,
    max_member_size: int = DEFAULT_MAX_MEMBER_SIZE,
    max_total_size: int = DEFAULT_MAX_TOTAL_SIZE,
    max_compression_ratio: float | None = DEFAULT_MAX_COMPRESSION_RATIO,
) -> list[zipfile.ZipInfo]:
    """校验 ZIP 成员名称、类型和大小，返回校验后的成员列表。

    ``ZipFile`` 已经在打开时检查了 central directory；这里额外拒绝
    重复路径、加密条目、符号链接以及超出解压预算的条目。重复路径很
    重要：即使路径安全，``extractall`` 也可能让后一个条目覆盖前一个。
    """

    if max_members <= 0 or max_member_size < 0 or max_total_size < 0:
        raise ValueError("ZIP 安全上限必须为非负值")
    infos = zf.infolist()
    if len(infos) > max_members:
        raise ArchiveSecurityError(
            f"ZIP 条目数过多（{len(infos)}，上限 {max_members}）"
        )

    seen: set[str] = set()
    total = 0
    for info in infos:
        normalized = normalize_archive_name(info.filename)
        if normalized in seen:
            raise ArchiveSecurityError(f"ZIP 包含重复条目：{normalized}")
        seen.add(normalized)

        if info.flag_bits & 0x1:
            raise ArchiveSecurityError(f"不支持加密 ZIP 条目：{normalized}")
        if _is_symlink(info):
            raise ArchiveSecurityError(f"不支持 ZIP 符号链接：{normalized}")
        # ZipInfo 中的大小来自 central directory，先做预算检查，再由
        # safe_extract_zip 在实际读取时再次限制写入量。
        size = int(info.file_size)
        compressed = int(info.compress_size)
        if size < 0 or compressed < 0:
            raise ArchiveSecurityError(f"ZIP 条目大小无效：{normalized}")
        if size > max_member_size:
            raise ArchiveSecurityError(
                f"ZIP 条目过大：{normalized}（{size} 字节）"
            )
        total += size
        if total > max_total_size:
            raise ArchiveSecurityError(
                f"ZIP 解压总大小过大（超过 {max_total_size} 字节）"
            )
        if (
            max_compression_ratio is not None
            and not info.is_dir()
            and size > 0
            and compressed == 0
        ):
            raise ArchiveSecurityError(f"ZIP 条目压缩大小无效：{normalized}")
        if (
            max_compression_ratio is not None
            and not info.is_dir()
            and compressed > 0
            and size / compressed > max_compression_ratio
        ):
            raise ArchiveSecurityError(f"ZIP 条目压缩比异常：{normalized}")
    return infos


def _ensure_no_symlink_components(root: Path, target: Path) -> None:
    """拒绝目标路径中已有的符号链接，避免解压时被重定向。"""

    root = root.resolve()
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ArchiveSecurityError(f"解压目标超出根目录：{target}") from exc
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ArchiveSecurityError(f"解压目标包含符号链接：{current}")


def safe_extract_zip(
    zf: zipfile.ZipFile,
    destination: Path,
    *,
    max_members: int = DEFAULT_MAX_MEMBERS,
    max_member_size: int = DEFAULT_MAX_MEMBER_SIZE,
    max_total_size: int = DEFAULT_MAX_TOTAL_SIZE,
    max_compression_ratio: float | None = DEFAULT_MAX_COMPRESSION_RATIO,
    overwrite: bool = False,
) -> list[Path]:
    """安全、流式地解压 ZIP 到 ``destination``。

    默认不覆盖已存在文件；调用方通常会先清空临时目录。即使
    ``overwrite=True``，也不会覆盖符号链接或穿越根目录的路径。
    返回实际创建的路径列表，便于调用方记录或测试。
    """

    infos = validate_zip_members(
        zf,
        max_members=max_members,
        max_member_size=max_member_size,
        max_total_size=max_total_size,
        max_compression_ratio=max_compression_ratio,
    )
    root = Path(destination)
    if root.is_symlink():
        raise ArchiveSecurityError("解压目标根目录不能是符号链接")
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    created: list[Path] = []
    written_total = 0

    for info in infos:
        normalized = normalize_archive_name(info.filename)
        target = safe_relative_path(root, normalized)
        _ensure_no_symlink_components(root, target.parent)
        if info.is_dir():
            if target.exists() and not target.is_dir():
                raise ArchiveSecurityError(f"目录与文件冲突：{normalized}")
            if target.is_symlink():
                raise ArchiveSecurityError(f"目录目标是符号链接：{normalized}")
            target.mkdir(parents=True, exist_ok=True)
            created.append(target)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        _ensure_no_symlink_components(root, target)
        if target.is_symlink():
            raise ArchiveSecurityError(f"文件目标是符号链接：{normalized}")
        if target.exists() and not overwrite:
            raise ArchiveSecurityError(f"解压目标已存在：{normalized}")

        # 使用独占创建避免一个恶意归档在遍历期间覆盖先前内容。对于
        # overwrite 场景，先删除普通文件；绝不删除目录或符号链接。
        if target.exists():
            if not target.is_file():
                raise ArchiveSecurityError(f"文件与目录冲突：{normalized}")
            target.unlink()
        written = 0
        try:
            with zf.open(info, "r") as source, target.open("xb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    written_total += len(chunk)
                    if written > max_member_size:
                        raise ArchiveSecurityError(
                            f"ZIP 条目实际解压大小超限：{normalized}"
                        )
                    if written_total > max_total_size:
                        raise ArchiveSecurityError("ZIP 实际解压总大小超限")
                    output.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        created.append(target)
    return created


def validate_relative_checksum_path(root: Path, relative_name: str) -> Path:
    """校验 checksum 清单中的路径并返回其绝对路径。"""

    return safe_relative_path(root, relative_name)


def iter_regular_files(root: Path) -> Iterator[Path]:
    """递归遍历普通文件，拒绝数据目录中的符号链接。

    备份导出面对的是本机可写目录，而不是不可信 ZIP；但如果对象目录
    中存在一个指向用户其他位置的链接，``rglob``/``copytree`` 可能把
    不在数据根内的内容意外打进备份。导出端宁可失败，也不静默漏数据。
    """

    root = Path(root)
    if root.is_symlink():
        raise ArchiveSecurityError(f"源目录不能是符号链接：{root}")
    if not root.is_dir():
        return
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if child.is_symlink():
            raise ArchiveSecurityError(f"源目录包含符号链接：{child}")
        if child.is_dir():
            yield from iter_regular_files(child)
        elif child.is_file():
            yield child


def copy_tree_no_symlinks(source: Path, destination: Path) -> None:
    """复制目录树并拒绝符号链接，供恢复 staging 和本地导出使用。"""

    source = Path(source)
    destination = Path(destination)
    if source.is_symlink():
        raise ArchiveSecurityError(f"源目录不能是符号链接：{source}")
    if not source.is_dir():
        raise ArchiveSecurityError(f"源目录不存在：{source}")
    destination.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir(), key=lambda item: item.name):
        target = destination / child.name
        if child.is_symlink():
            raise ArchiveSecurityError(f"源目录包含符号链接：{child}")
        if child.is_dir():
            copy_tree_no_symlinks(child, target)
        elif child.is_file():
            shutil.copy2(child, target)
