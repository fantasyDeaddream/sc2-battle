#!/usr/bin/env bash
# =============================================================
# deploy_update.sh — 部署网页更新：把旧版本目录中的运行时数据
# （数据库、头像、replay 等）覆盖复制到新版本目录。
#
# 用法：
#   ./deploy_update.sh -o /srv/web/contest_old -n /srv/web/contest_new
#   ./deploy_update.sh -o /srv/web/contest_old -n /srv/web/contest_new -b
#   ./deploy_update.sh -o ... -n ... -i "contest.db,avatars,ReplaySave"
#   ./deploy_update.sh -o ... -n ... -d    # 预演，不实际复制
#
# 选项：
#   -o PATH  旧版本目录（必填）
#   -n PATH  新版本目录（必填）
#   -b       覆盖前把新目录同名项备份到 deploy_backup_时间戳
#   -i LIST  自定义清单（逗号/分号分隔的相对路径），为空用内置清单
#   -d       预演模式，只打印将执行的操作
#   -h       帮助
#
# 内置数据清单：
#   contest.db                数据库（含 -wal/-shm 附属文件）
#   ReplaySave                赛事 replay 存档
#   rank                      天梯 replay 归档
#   avatars                   用户头像、头像框与 frames.json 配置
#   payment                   支付静态资源
#   novel                     同人小说内容
#   update                    风暴对决更新补丁页
#   guide_assets/operation_guide.pdf  操作指南 PDF
#   static/shop_images        商城商品图片
#   server_data               桌面天梯数据（ladder_project 用）
#
# 建议执行前先停止服务器，避免复制中的 SQLite 数据不一致。
# =============================================================
set -u

usage() {
    grep '^# ' "$0" | sed 's/^# \{0,1\}//'
}

OLD_PATH=""
NEW_PATH=""
BACKUP=0
DRY_RUN=0
ITEMS=""

while getopts "o:n:bi:dh" opt; do
    case "$opt" in
        o) OLD_PATH="$OPTARG" ;;
        n) NEW_PATH="$OPTARG" ;;
        b) BACKUP=1 ;;
        i) ITEMS="$OPTARG" ;;
        d) DRY_RUN=1 ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done

if [ -z "$OLD_PATH" ] || [ -z "$NEW_PATH" ]; then
    echo "错误：必须同时提供 -o（旧目录）和 -n（新目录）。"
    usage
    exit 1
fi
if [ ! -d "$OLD_PATH" ]; then
    echo "错误：旧目录不存在：$OLD_PATH"
    exit 1
fi
if [ ! -d "$NEW_PATH" ]; then
    echo "错误：新目录不存在：$NEW_PATH"
    exit 1
fi

OLD_ROOT=$(cd "$OLD_PATH" && pwd)
NEW_ROOT=$(cd "$NEW_PATH" && pwd)
if [ "$OLD_ROOT" = "$NEW_ROOT" ]; then
    echo "错误：旧目录与新目录不能是同一个路径。"
    exit 1
fi

# ---- 服务器运行提醒 ----
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -qE ':(80|8010)[[:space:]]'; then
    echo "警告：检测到 80/8010 端口有服务在监听。建议先停止服务器再执行部署。"
elif command -v netstat >/dev/null 2>&1 && netstat -ltn 2>/dev/null | grep -qE ':(80|8010)[[:space:]]'; then
    echo "警告：检测到 80/8010 端口有服务在监听。建议先停止服务器再执行部署。"
fi

# ---- 清单解析 ----
DEFAULT_ITEMS=(contest.db ReplaySave rank avatars payment novel update \
               guide_assets/operation_guide.pdf static/shop_images server_data)

item_list=()
if [ -n "$ITEMS" ]; then
    # 逗号/分号分隔，去空白
    IFS=',;' read -r -a raw_items <<< "$ITEMS"
    for raw in "${raw_items[@]}"; do
        item="${raw// /}"
        [ -n "$item" ] && item_list+=("$item")
    done
else
    item_list=("${DEFAULT_ITEMS[@]}")
fi

timestamp=$(date +%Y%m%d_%H%M%S)
backup_root=""
[ "$BACKUP" = "1" ] && backup_root="$NEW_ROOT/deploy_backup_$timestamp"

# ---- 复制单个项（目录合并复制，避免嵌套） ----
copy_one() {
    local src="$1" dst="$2"
    if [ "$DRY_RUN" = "1" ]; then
        echo "  [预演] cp -a $src -> $dst"
        return 0
    fi
    if [ -d "$src" ]; then
        mkdir -p "$dst"
        cp -a "$src/." "$dst/"
    else
        mkdir -p "$(dirname "$dst")"
        cp -a "$src" "$dst"
    fi
}

copied=()
skipped=()

for item in "${item_list[@]}"; do
    src="$OLD_ROOT/$item"
    dst="$NEW_ROOT/$item"

    if [ ! -e "$src" ]; then
        echo "跳过（旧目录中不存在）：$item"
        skipped+=("$item")
        continue
    fi

    # 备份：把新目录中将被覆盖的项移入备份目录
    if [ "$BACKUP" = "1" ] && [ -e "$dst" ]; then
        bak="$backup_root/$item"
        if [ "$DRY_RUN" = "1" ]; then
            echo "  [预演] mv $dst -> $bak"
        else
            mkdir -p "$(dirname "$bak")"
            mv "$dst" "$bak"
        fi
    fi

    copy_one "$src" "$dst"
    copied+=("$item")

    # 数据库：一并复制 SQLite WAL 附属文件，避免丢数据
    if [ "$item" = "contest.db" ]; then
        for ext in -wal -shm; do
            if [ -e "$src$ext" ]; then
                copy_one "$src$ext" "$dst$ext"
                copied+=("$item$ext（SQLite WAL 附属）")
            fi
        done
    fi
done

# ---- 摘要 ----
echo ""
echo "== 部署数据覆盖完成 =="
echo "已复制 ${#copied[@]} 项："
for entry in "${copied[@]}"; do
    echo "  + $entry"
done
if [ "${#skipped[@]}" -gt 0 ]; then
    echo "跳过 ${#skipped[@]} 项（旧目录中不存在）："
    for entry in "${skipped[@]}"; do
        echo "  - $entry"
    done
fi
if [ "$BACKUP" = "1" ] && [ -n "$backup_root" ] && [ -d "$backup_root" ]; then
    echo "覆盖前的新目录原文件已备份到：$backup_root"
fi
echo "提示：请在新目录用相同方式启动服务器（如 venv 需重新创建/安装依赖）。"
