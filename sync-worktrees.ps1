param(
    [switch]$Force   # 子分支工作树里有未提交改动时，也强制覆盖
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

Write-Host '==> 1/3 拉取远端更新'
git -C $root fetch origin

Write-Host '==> 2/3 更新本地 main（快进到 origin/main）'
git -C $root merge --ff-only origin/main
if ($LASTEXITCODE -ne 0) {
    Write-Warning '本地 main 无法快进（可能 main 上有尚未推送的 AI 整合提交），继续用本地 main 作为同步基准。'
}

Write-Host '==> 3/3 把 main 同步到各子分支工作树'
$lines = git -C $root worktree list --porcelain
$wt = $null
foreach ($line in $lines) {
    if ($line -like 'worktree *') {
        $wt = ($line -replace '^worktree ', '').Trim()
    }
    elseif ($line -like 'branch refs/heads/*' -and $wt) {
        $branch = ($line -replace '^branch refs/heads/', '').Trim()
        if ($branch -eq 'main') { $wt = $null; continue }
        Write-Host "----> $branch ($wt)"
        $dirty = git -C $wt status --porcelain
        if ($dirty -and -not $Force) {
            Write-Warning '      跳过：工作树里有未提交/未跟踪改动（确认后加 -Force 可强制覆盖）'
            $wt = $null
            continue
        }
        # 先尝试安全快进：如果 main 已经包含子分支的提交（普通 merge），直接移动指针即可
        git -C $wt merge --ff-only main 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            # 快进失败说明 main 是 squash/改写方式整合的，子分支已无保留价值，直接覆盖为 main
            Write-Host '      无法快进（AI 可能用了 squash 整合），改为直接覆盖为 main'
            git -C $wt reset --hard main
        }
        $wt = $null
    }
}

$short = git -C $root rev-parse --short main
Write-Host "完成：所有子分支已同步到 main（$short）"
