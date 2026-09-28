<#
.SYNOPSIS
  部署网页更新：把旧版本目录中的运行时数据（数据库、头像、replay 等）
  覆盖复制到新版本目录，使新版本继承全部用户数据。

.DESCRIPTION
  用法示例：
    .\deploy_update.ps1 -OldPath D:\web\contest_old -NewPath D:\web\contest_new
    .\deploy_update.ps1 -OldPath D:\web\contest_old -NewPath D:\web\contest_new -Backup
    .\deploy_update.ps1 -OldPath D:\web\contest_old -NewPath D:\web\contest_new -WhatIf

  内置数据清单（相对项目根目录）：
    contest.db                数据库（含 -wal/-shm）
    ReplaySave                赛事 replay 存档
    rank                      天梯 replay 归档
    avatars                   用户头像、头像框与 frames.json 配置
    payment                   支付静态资源
    novel                     同人小说内容
    update                    风暴对决更新补丁页
    guide_assets/operation_guide.pdf  操作指南 PDF
    static/shop_images        商城商品图片
    server_data               桌面天梯数据（ladder_project 用）

  -Items 可自定义清单（逗号/分号分隔的相对路径），为空则用内置清单。
  -Backup 开启后，新目录中将被覆盖的项会先移到 deploy_backup_时间戳 目录。
  建议执行前先停止服务器，避免复制中的 SQLite 数据不一致。
#>

[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory = $true)]
    [string]$OldPath,

    [Parameter(Mandatory = $true)]
    [string]$NewPath,

    [switch]$Backup,

    [string]$Items = ""
)

$ErrorActionPreference = "Stop"

$DefaultItems = @(
    "contest.db",
    "ReplaySave",
    "rank",
    "avatars",
    "payment",
    "novel",
    "update",
    "guide_assets/operation_guide.pdf",
    "static/shop_images",
    "server_data"
)

# ---- 校验路径 ----
if (-not (Test-Path $OldPath)) {
    throw "旧目录不存在：$OldPath"
}
if (-not (Test-Path $NewPath)) {
    throw "新目录不存在：$NewPath"
}

$oldRoot = (Resolve-Path $OldPath).Path
$newRoot = (Resolve-Path $NewPath).Path
if ($oldRoot -eq $newRoot) {
    throw "旧目录与新目录不能是同一个路径。"
}

# ---- 服务器运行提醒 ----
$listener = Get-NetTCPConnection -LocalPort 8010,80 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Write-Warning "检测到 80/8010 端口有服务在监听。建议先停止服务器再执行部署，避免数据库复制不一致。"
}

$itemList = if ($Items) {
    $Items -split "[,;]" | ForEach-Object { $_.Trim() } | Where-Object { $_ }
} else {
    $DefaultItems
}

$backupRoot = $null
if ($Backup) {
    $backupRoot = Join-Path $newRoot ("deploy_backup_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
}

$copied = @()
$skipped = @()
$extraWalFiles = @()

foreach ($item in $itemList) {
    $src = Join-Path $oldRoot $item
    $dst = Join-Path $newRoot $item

    if (-not (Test-Path $src)) {
        Write-Verbose "跳过（旧目录中不存在）：$item"
        $skipped += $item
        continue
    }

    $srcItem = Get-Item $src

    # 备份：把新目录中将被覆盖的项移入备份目录
    if ($Backup -and (Test-Path $dst)) {
        $bak = Join-Path $backupRoot $item
        $bakParent = Split-Path $bak -Parent
        if (-not (Test-Path $bakParent)) {
            New-Item -ItemType Directory -Force -Path $bakParent | Out-Null
        }
        Move-Item -Path $dst -Destination $bak -Force
        Write-Verbose "已备份：$item -> $bak"
    }

    if (-not $PSCmdlet.ShouldProcess($dst, "从 $src 复制")) {
        continue
    }

    $dstParent = Split-Path $dst -Parent
    if ($dstParent -and -not (Test-Path $dstParent)) {
        New-Item -ItemType Directory -Force -Path $dstParent | Out-Null
    }

    if ($srcItem.PSIsContainer) {
        # 目录：确保目标目录存在后用合并方式复制，避免 PowerShell 5.1 的嵌套怪癖
        New-Item -ItemType Directory -Force -Path $dst | Out-Null
        Copy-Item -Path (Join-Path $src "*") -Destination $dst -Recurse -Force
    } else {
        Copy-Item -Path $src -Destination $dst -Force
    }
    $copied += $item

    # 数据库：一并复制 SQLite WAL 附属文件，避免丢数据
    if ($item -eq "contest.db") {
        foreach ($ext in @("-wal", "-shm")) {
            $walSrc = "$src$ext"
            if (Test-Path $walSrc) {
                Copy-Item -Path $walSrc -Destination "$dst$ext" -Force
                $extraWalFiles += ($item + $ext)
            }
        }
    }
}

Write-Host ""
Write-Host "== 部署数据覆盖完成 ==" -ForegroundColor Green
Write-Host ("已复制 {0} 项：" -f $copied.Count) -ForegroundColor Green
foreach ($entry in $copied) {
    Write-Host ("  + " + $entry)
}
foreach ($entry in $extraWalFiles) {
    Write-Host ("  + " + $entry + "（SQLite WAL 附属）")
}
if ($skipped.Count -gt 0) {
    Write-Host ("跳过 {0} 项（旧目录中不存在）：" -f $skipped.Count) -ForegroundColor Yellow
    foreach ($entry in $skipped) {
        Write-Host ("  - " + $entry)
    }
}
if ($Backup -and $backupRoot -and (Test-Path $backupRoot)) {
    Write-Host ("覆盖前的新目录原文件已备份到：{0}" -f $backupRoot) -ForegroundColor Cyan
}
Write-Host "提示：请在新目录用相同方式启动服务器（如 .venv 需重新创建/安装依赖）。" -ForegroundColor DarkGray
