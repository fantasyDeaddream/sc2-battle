# config.py — 赛事平台全部常量与路径配置
# 从 app.py 拆分，供所有模块导入

import os
from pathlib import Path

# ---- 路径常量 ----
BASE_DIR = Path(__file__).resolve().parent


def _load_env_file(path: Path) -> None:
    """极简 .env 加载器（无需额外依赖）。已存在的环境变量优先。"""
    if not path.exists():
        return
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


_load_env_file(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "contest.db"
REPLAY_SAVE_DIR = BASE_DIR / "ReplaySave"
RANK_SAVE_DIR = BASE_DIR / "rank"
LADDER_BOUNTY_ROOT_DIR = BASE_DIR
GUIDE_ASSETS_DIR = BASE_DIR / "guide_assets"
OPERATION_GUIDE_PDF_PATH = GUIDE_ASSETS_DIR / "operation_guide.pdf"
COMMANDER_ASSET_DIR = BASE_DIR / "static" / "commander_assets"
COMMANDER_ASSET_THUMB_DIR = COMMANDER_ASSET_DIR / "thumbs"
COMMANDER_ASSET_FULL_DIR = COMMANDER_ASSET_DIR / "full"
COMMANDER_ASSET_MANIFEST_PATH = COMMANDER_ASSET_DIR / "manifest.json"

# ---- 会话/认证常量 ----
SESSION_COOKIE = "contest_session"
SESSION_TTL_DAYS = 30
SESSION_TTL_HOURS = 12
TZ_LABEL = "UTC+8"
SITE_NAME = "SC2 Battle"
SUPERADMIN_USERNAME = os.getenv("SUPERADMIN_USERNAME", "sudo")
SUPERADMIN_PASSWORD = os.getenv("SUPERADMIN_PASSWORD", "change-me-in-production")
STREAMER_USERNAME = os.getenv("STREAMER_USERNAME", "streamer")
STREAMER_PASSWORD = os.getenv("STREAMER_PASSWORD", "change-me-in-production")
TOURNAMENT_TIME_FORMAT = "%Y-%m-%d %H:%M"

# ---- BP 常量（从 bp_modes 导入后赋值） ----
BP_SYNC_POLL_MS = 2000
LADDER_SYNC_POLL_MS = 2000
LADDER_CONFIRM_SECONDS = 40
LADDER_ONLINE_TTL_SECONDS = 10
LADDER_CHAT_HISTORY_LIMIT = 200

# ---- 悬赏常量 ----
LADDER_BOUNTY_STATUS_ACTIVE = "active"
LADDER_BOUNTY_STATUS_REVOKED = "revoked"
LADDER_BOUNTY_SUBMISSION_PENDING = "pending"
LADDER_BOUNTY_SUBMISSION_VALID = "valid"
LADDER_BOUNTY_SUBMISSION_INVALID = "invalid"

# ---- 循环赛默认值 ----
DEFAULT_ROUND_ROBIN_CYCLES = 1
DEFAULT_ROUND_ROBIN_WIN_POINTS = 3
DEFAULT_ROUND_ROBIN_DRAW_POINTS = 1
DEFAULT_ROUND_ROBIN_LOSS_POINTS = 0
DEFAULT_ROUND_ROBIN_BEST_OF = 3
DEFAULT_SWISS_REGULAR_BEST_OF = 3
DEFAULT_SWISS_DECIDER_BEST_OF = 3

# ---- 赛事 BP 步进时间 ----
from bp_modes import BP_STEP_SECONDS
DEFAULT_TOURNAMENT_BP_STEP_SECONDS = BP_STEP_SECONDS
TOURNAMENT_BP_STEP_SECONDS_MIN = 10
TOURNAMENT_BP_STEP_SECONDS_MAX = 600

LADDER_REPLAY_RETENTION_DAYS = 30
LADDER_BP_SECONDS = BP_STEP_SECONDS  # 复用同一默认值
LADDER_BP_SECONDS_MIN = 10
LADDER_BP_SECONDS_MAX = 600
SYSTEM_SETTING_LADDER_BP_SECONDS = "ladder_bp_seconds"

# ---- 格式/选项标签 ----
FORMAT_LABELS = {
    "single_elimination": "单败淘汰赛",
    "double_elimination": "双败淘汰赛",
    "swiss": "瑞士轮",
    "round_robin": "循环赛",
}

STAGE_LABELS = {
    "single_stage": "单阶段赛",
    "double_stage": "双阶段赛",
}
MATCH_ODD_BEST_OF_OPTIONS = (1, 3, 5, 7, 9)
MATCH_ALL_BEST_OF_OPTIONS = (1, 2, 3, 4, 5, 6, 7, 8, 9)

BRACKET_TYPE_LABELS = {
    "winners": "Upper Bracket",
    "losers": "Lower Bracket",
    "finals": "Grand Final",
    "finals_reset": "Grand Final Reset",
    "swiss": "瑞士轮",
    "round_robin": "循环赛",
}

# ---- 比赛槽位/天梯状态常量 ----
MATCH_SLOT_PLAYER = "player"
MATCH_SLOT_TBD = "tbd"
MATCH_SLOT_BYE = "bye"
LADDER_MODE_1V1 = "1v1"
LADDER_MODE_2V2 = "2v2"
LADDER_MATCH_STATE_PENDING_ACCEPT = "pending_accept"
LADDER_MATCH_STATE_BP_BAN = "bp_ban"
LADDER_MATCH_STATE_BP_PICK = "bp_pick"
LADDER_MATCH_STATE_TEAM_BAN = "team_ban"
LADDER_MATCH_STATE_TEAM_PICK = "team_pick"
LADDER_MATCH_STATE_READY = "ready"
LADDER_MATCH_STATE_ADMIN_REVIEW = "admin_review"
LADDER_MATCH_STATE_RESOLVED = "resolved"
