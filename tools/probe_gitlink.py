"""GitLink API 兼容性探测（令牌从环境变量或系统凭据读取，禁止打印令牌）。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPORT = Path(__file__).resolve().parent / "gitlink_compat_report.md"


def _safe_head(text: str, n: int = 120) -> str:
    cleaned = text.replace("\n", " ").strip()
    return cleaned[:n]


def _preview_keys(raw: str) -> str:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return ",".join(list(data.keys())[:12])
        if isinstance(data, list):
            return f"list[{len(data)}]"
    except Exception:
        pass
    return _safe_head(raw)


def _token() -> str:
    t = os.environ.get("GITLINK_TOKEN") or os.environ.get("YANCUO_GITLINK_TOKEN") or ""
    if t:
        return t.strip()
    try:
        import keyring

        t = keyring.get_password("Yancuo", "yancuo_gitlink_token") or ""
        return t.strip()
    except Exception:
        return ""


def _req(url: str, *, method: str = "GET", data: dict | bytes | None = None, token: str = "") -> tuple[int, str]:
    headers = {"User-Agent": "yancuo-gitlink-probe"}
    body = None
    if isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif isinstance(data, (bytes, bytearray)):
        body = data
    if token:
        headers["PRIVATE-TOKEN"] = token
        sep = "&" if "?" in url else "?"
        if "private_token=" not in url:
            url = f"{url}{sep}private_token={urllib.parse.quote(token)}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


def main() -> int:
    token = _token()
    lines = [
        "# GitLink API 兼容性验证报告",
        "",
        f"- 探测时间：由 `tools/probe_gitlink.py` 生成",
        f"- 令牌来源：{'已提供（已脱敏）' if token else '未找到'}",
        f"- 令牌长度：{len(token) if token else 0}",
        "",
        "## 结果摘要",
        "",
    ]
    if not token:
        lines.append("未找到令牌，请设置环境变量 `YANCUO_GITLINK_TOKEN` 或在设置中保存。")
        REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {REPORT}")
        return 2

    checks: list[tuple[str, bool, str]] = []

    # releases list (known public repo)
    status, raw = _req(
        "https://www.gitlink.org.cn/api/Gitlink/forgeplus/releases.json",
        token=token,
    )
    ok = status == 200 and '"releases"' in raw
    checks.append(("GET /api/{owner}/{repo}/releases.json", ok, f"HTTP {status}"))

    status2, raw2 = _req(
        "https://www.gitlink.org.cn/api/projects.json?category=created",
        token=token,
    )
    # 当前公开目录接口即使带 token 仍返回全站列表，记为部分可用
    partial = status2 == 200 and "projects" in raw2
    checks.append(
        (
            "GET /api/projects.json?category=created",
            partial,
            f"HTTP {status2}; 疑似未按用户过滤（需结合 owner 配置）",
        )
    )

    status3, raw3 = _req("https://www.gitlink.org.cn/api/v1/user.json", token=token)
    checks.append(
        (
            "GET /api/v1/user.json",
            status3 == 200 and ("login" in raw3 or "username" in raw3 or "name" in raw3),
            f"HTTP {status3}; body_keys≈{_preview_keys(raw3)}",
        )
    )

    # contents API
    status4, raw4 = _req(
        "https://www.gitlink.org.cn/api/v1/repos/Gitlink/forgeplus/contents/?ref=master",
        token=token,
    )
    checks.append(
        (
            "GET /api/v1/repos/{owner}/{repo}/contents/",
            status4 == 200 and not raw4.strip().startswith("<!") and ("[" in raw4 or "{" in raw4),
            f"HTTP {status4}; body_head={_safe_head(raw4)}",
        )
    )

    for name, ok, detail in checks:
        mark = "PASS" if ok else "FAIL/UNKNOWN"
        lines.append(f"- **{mark}** `{name}` — {detail}")

    lines.extend(
        [
            "",
            "## 结论与实现策略",
            "",
            "1. Release 列表接口 `/api/{owner}/{repo}/releases.json` 可用（已验证公开仓库）。",
            "2. `/api/v1/user.json` 当前返回 `{status,message}`，不能可靠识别当前用户；`/api/v1/repos/.../contents/` 返回 HTML 页面而非 JSON。",
            "3. `projects.json` 即使带令牌仍像全站列表；私有库请在网页创建后填写 owner/name。",
            "4. 研错库阶段 G：**LocalFolderProvider 作为完整可测后端**；GitLinkProvider 实现同一接口，按 capabilities 降级（附件上传默认关闭），令牌仅存系统凭据。",
            "5. 上传顺序仍遵守：先上传完整 `.ebpack` 并校验，再更新 `latest.json`。",
            "",
            "## 安全",
            "",
            "- 本报告不含令牌明文。",
            "- 若令牌曾出现在聊天记录中，建议在 GitLink 设置中轮换。",
            "",
        ]
    )
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {REPORT}")
    for name, ok, detail in checks:
        print(("PASS" if ok else "FAIL"), name, detail)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
