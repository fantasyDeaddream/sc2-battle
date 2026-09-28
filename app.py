from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import shutil
import sqlite3
from collections import defaultdict
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
import random
import tarfile
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bp_modes import (
    COMMANDER_POOL_SEPARATE,
    BP_MODE_COMMANDER_CONQUEST,
    BP_MODE_COMMANDER_CONQUEST_2V2,
    BP_MODE_COMMANDER_DRAFT,
    BP_MODE_EVO,
    BP_MODE_LADDER_1V1,
    BP_MODE_LABELS,
    BP_MODE_MAP_POOL,
    BP_MODE_NONE,
    BP_STEP_SECONDS,
    COMMANDER_BAN_GLOBAL,
    COMMANDER_BAN_MODE_LABELS,
    COMMANDER_BAN_OPPONENT_ONLY,
    COMMANDER_POOL_MODE_LABELS,
    COMMANDER_POOL_SHARED,
    EVO_CHOICE_BOTH,
    EVO_CHOICE_LABELS,
    auto_complete_commander_action,
    apply_commander_action,
    apply_map_bp_action,
    build_commander_game_state,
    build_map_bp_state,
    commander_action_options,
    commander_catalog,
    commander_option_label,
    commander_pick_total,
    commander_score,
    confirm_commander_action,
    current_commander_step,
    current_map_bp_step,
    evo_choice_options,
    finalize_map_bp_if_needed,
    map_bp_required_map_count,
    normalize_bp_mode,
    normalize_commander_ban_mode,
    normalize_commander_pool_mode,
    normalize_evo_choice,
    pending_commander_action,
    remaining_commander_pool,
    set_pending_commander_action,
    side_assignments_for_game,
)
from replay_parser import (
    build_strict_team_match_verdict,
    inspect_replay,
    normalize_game_nickname,
    replay_datetime_value,
    replay_map_match_key,
    validate_game_nickname,
)
from ladder_project.shared.constants import (
    BP_SECONDS as LADDER_BP_SECONDS,
    COMMANDER_POOL as LADDER_COMMANDER_POOL,
    MAP_POOL_1V1 as LADDER_MAP_POOL_1V1,
    MAP_POOL_2V2 as LADDER_MAP_POOL_2V2,
    MATCH_BASE_WINDOW as LADDER_MATCH_BASE_WINDOW,
    MATCH_EXPAND_SECONDS as LADDER_MATCH_EXPAND_SECONDS,
    MATCH_EXPAND_STEP as LADDER_MATCH_EXPAND_STEP,
    MATCH_MAX_WINDOW as LADDER_MATCH_MAX_WINDOW,
)
from ladder_project.shared.rating import RatingValue as LadderRatingValue, update_elo as ladder_update_elo

# ---- 从拆分模块导入常量与数据库函数 ----
from config import *  # noqa: F401, F403 — 所有路径/配置常量
from database import (  # noqa: F401 — 数据库连接与迁移
    get_connection, init_db, now_text,
    table_info, table_columns, ensure_column,
    ensure_registrations_schema, backfill_registration_seed_order,
)

# ---- 仅保留 app.py 特有的状态 ----
# BASE_DIR 在 config.py 中已定义，此处仅保留以确保本文件内引用一致
# （实际使用 config.BASE_DIR 或直接用 from config import BASE_DIR 即可）


REPLAY_SAVE_DIR = BASE_DIR / "ReplaySave"
RANK_SAVE_DIR = BASE_DIR / "rank"
LADDER_BOUNTY_ROOT_DIR = BASE_DIR
GUIDE_ASSETS_DIR = BASE_DIR / "guide_assets"
OPERATION_GUIDE_PDF_PATH = GUIDE_ASSETS_DIR / "operation_guide.pdf"
COMMANDER_ASSET_DIR = BASE_DIR / "static" / "commander_assets"
COMMANDER_ASSET_THUMB_DIR = COMMANDER_ASSET_DIR / "thumbs"
COMMANDER_ASSET_FULL_DIR = COMMANDER_ASSET_DIR / "full"
COMMANDER_ASSET_MANIFEST_PATH = COMMANDER_ASSET_DIR / "manifest.json"
SESSION_COOKIE = "contest_session"
SESSION_TTL_DAYS = 30
SESSION_TTL_HOURS = 12
TZ_LABEL = "UTC+8"
SITE_NAME = "SC2 Battle"
# 认证口令统一取自 config.py（支持 .env / 环境变量覆盖，见 .env.example）
TOURNAMENT_TIME_FORMAT = "%Y-%m-%d %H:%M"
BP_SYNC_POLL_MS = 2000
LADDER_SYNC_POLL_MS = 2000
LADDER_CONFIRM_SECONDS = 40
LADDER_ONLINE_TTL_SECONDS = 10
LADDER_CHAT_HISTORY_LIMIT = 200
LADDER_BOUNTY_STATUS_ACTIVE = "active"
LADDER_BOUNTY_STATUS_REVOKED = "revoked"
LADDER_BOUNTY_SUBMISSION_PENDING = "pending"
LADDER_BOUNTY_SUBMISSION_VALID = "valid"
LADDER_BOUNTY_SUBMISSION_INVALID = "invalid"
DEFAULT_ROUND_ROBIN_CYCLES = 1
DEFAULT_ROUND_ROBIN_WIN_POINTS = 3
DEFAULT_ROUND_ROBIN_DRAW_POINTS = 1
DEFAULT_ROUND_ROBIN_LOSS_POINTS = 0
DEFAULT_ROUND_ROBIN_BEST_OF = 3
DEFAULT_SWISS_REGULAR_BEST_OF = 3
DEFAULT_SWISS_DECIDER_BEST_OF = 3
DEFAULT_TOURNAMENT_BP_STEP_SECONDS = BP_STEP_SECONDS
TOURNAMENT_BP_STEP_SECONDS_MIN = 10
TOURNAMENT_BP_STEP_SECONDS_MAX = 600
LADDER_REPLAY_RETENTION_DAYS = 30
LADDER_BP_SECONDS_MIN = 10
LADDER_BP_SECONDS_MAX = 600
SYSTEM_SETTING_LADDER_BP_SECONDS = "ladder_bp_seconds"

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


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title=SITE_NAME, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/payment", StaticFiles(directory=BASE_DIR / "payment"), name="payment")
app.mount("/novel-assets", StaticFiles(directory=BASE_DIR / "novel"), name="novel_assets")
app.mount("/background/normal", StaticFiles(directory=BASE_DIR / "background" / "normal"), name="bg_normal")
app.mount("/background/square", StaticFiles(directory=BASE_DIR / "background" / "square"), name="bg_square")
app.mount("/background/rectangle", StaticFiles(directory=BASE_DIR / "background" / "rectangle"), name="bg_rectangle")
app.mount("/avatars", StaticFiles(directory=BASE_DIR / "avatars"), name="avatars")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["format_labels"] = FORMAT_LABELS
templates.env.globals["stage_labels"] = STAGE_LABELS
templates.env.globals["bp_mode_labels"] = BP_MODE_LABELS
templates.env.globals["commander_pool_mode_labels"] = COMMANDER_POOL_MODE_LABELS
templates.env.globals["commander_ban_mode_labels"] = COMMANDER_BAN_MODE_LABELS
templates.env.globals["tz_label"] = TZ_LABEL
templates.env.globals["to_input_dt"] = lambda value: value.replace(" ", "T") if value else ""
templates.env.globals["site_name"] = SITE_NAME
templates.env.globals["match_best_of_options"] = MATCH_ODD_BEST_OF_OPTIONS
templates.env.globals["best_of_options_for_bracket"] = (
    lambda bracket_type: MATCH_ALL_BEST_OF_OPTIONS if bracket_type == "round_robin" else MATCH_ODD_BEST_OF_OPTIONS
)


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def table_info(connection: sqlite3.Connection, table_name: str) -> list[sqlite3.Row]:
    return connection.execute(f"PRAGMA table_info({table_name})").fetchall()


def table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row["name"] for row in table_info(connection, table_name)}


def ensure_column(connection: sqlite3.Connection, table_name: str, definition: str) -> None:
    column_name = definition.split()[0]
    if column_name not in table_columns(connection, table_name):
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {definition}")


def ensure_registrations_schema(connection: sqlite3.Connection) -> None:
    info = {row["name"]: row for row in table_info(connection, "registrations")}
    user_id_is_notnull = bool(info.get("user_id") and info["user_id"]["notnull"])
    needs_rebuild = user_id_is_notnull or "is_temporary" not in info or "seed_order" not in info
    if not needs_rebuild:
        return

    source_columns = table_columns(connection, "registrations")
    checked_in_expr = "checked_in" if "checked_in" in source_columns else "0"
    checked_in_at_expr = "checked_in_at" if "checked_in_at" in source_columns else "NULL"

    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("DROP TABLE IF EXISTS registrations_new")
    connection.execute(
        """
        CREATE TABLE registrations_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            user_id INTEGER,
            nickname TEXT NOT NULL,
            is_temporary INTEGER NOT NULL DEFAULT 0,
            checked_in INTEGER NOT NULL DEFAULT 0,
            checked_in_at TEXT,
            seed_order INTEGER NOT NULL DEFAULT 0,
            joined_at TEXT NOT NULL,
            UNIQUE (tournament_id, user_id),
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        f"""
        INSERT INTO registrations_new (
            id, tournament_id, user_id, nickname, is_temporary,
            checked_in, checked_in_at, seed_order, joined_at
        )
        SELECT id, tournament_id, user_id, nickname, 0,
               {checked_in_expr}, {checked_in_at_expr}, 0, joined_at
        FROM registrations
        ORDER BY tournament_id ASC, joined_at ASC, id ASC
        """
    )
    connection.execute("DROP TABLE registrations")
    connection.execute("ALTER TABLE registrations_new RENAME TO registrations")
    connection.commit()
    connection.execute("PRAGMA foreign_keys = ON")


def backfill_registration_seed_order(connection: sqlite3.Connection) -> None:
    tournament_ids = connection.execute(
        "SELECT DISTINCT tournament_id FROM registrations ORDER BY tournament_id ASC"
    ).fetchall()
    for tournament_row in tournament_ids:
        registration_rows = connection.execute(
            """
            SELECT id
            FROM registrations
            WHERE tournament_id = ?
            ORDER BY seed_order ASC, joined_at ASC, id ASC
            """,
            (tournament_row["tournament_id"],),
        ).fetchall()
        for index, registration_row in enumerate(registration_rows, start=1):
            connection.execute(
                "UPDATE registrations SET seed_order = ? WHERE id = ?",
                (index, registration_row["id"]),
            )


def init_db() -> None:
    schema = """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        game_nickname TEXT,
        password_hash TEXT NOT NULL,
        is_superadmin INTEGER NOT NULL DEFAULT 0,
        is_ladder_admin INTEGER NOT NULL DEFAULT 0,
        is_tournament_admin INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tournaments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        creator_id INTEGER NOT NULL,
        start_time TEXT NOT NULL,
        checkin_time TEXT NOT NULL,
        format_type TEXT NOT NULL,
        stage2_format_type TEXT,
        stage_mode TEXT NOT NULL,
        stage1_group_count INTEGER NOT NULL DEFAULT 1,
        round_robin_cycles INTEGER NOT NULL DEFAULT 1,
        round_robin_win_points INTEGER NOT NULL DEFAULT 3,
        round_robin_draw_points INTEGER NOT NULL DEFAULT 1,
        round_robin_loss_points INTEGER NOT NULL DEFAULT 0,
        round_robin_best_of INTEGER NOT NULL DEFAULT 3,
        swiss_regular_best_of INTEGER NOT NULL DEFAULT 3,
        swiss_decider_best_of INTEGER NOT NULL DEFAULT 3,
        bp_step_seconds INTEGER NOT NULL DEFAULT 40,
        bp_mode TEXT NOT NULL DEFAULT 'none',
        replay_result_enabled INTEGER NOT NULL DEFAULT 0,
        commander_shared_pool INTEGER NOT NULL DEFAULT 1,
        commander_ban_mode TEXT NOT NULL DEFAULT 'global',
        description TEXT NOT NULL,
        prize_info TEXT NOT NULL,
        map_pool TEXT NOT NULL,
        hide_bracket_until_publish INTEGER NOT NULL DEFAULT 0,
        bracket_published INTEGER NOT NULL DEFAULT 0,
        started_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (creator_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS tournament_admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        UNIQUE (tournament_id, user_id),
        FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS registrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER NOT NULL,
        user_id INTEGER,
        nickname TEXT NOT NULL,
        is_temporary INTEGER NOT NULL DEFAULT 0,
        checked_in INTEGER NOT NULL DEFAULT 0,
        checked_in_at TEXT,
        seed_order INTEGER NOT NULL DEFAULT 0,
        group_number INTEGER NOT NULL DEFAULT 1,
        joined_at TEXT NOT NULL,
        UNIQUE (tournament_id, user_id),
        FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS tournament_matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER NOT NULL,
        bracket_type TEXT NOT NULL,
        round_number INTEGER NOT NULL,
        match_number INTEGER NOT NULL,
        best_of INTEGER NOT NULL DEFAULT 3,
        player1_registration_id INTEGER,
        player1_slot_type TEXT NOT NULL DEFAULT 'player',
        player2_registration_id INTEGER,
        player2_slot_type TEXT NOT NULL DEFAULT 'player',
        winner_registration_id INTEGER,
        loser_registration_id INTEGER,
        status TEXT NOT NULL DEFAULT 'pending',
        score1 INTEGER NOT NULL DEFAULT 0,
        score2 INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (tournament_id, bracket_type, round_number, match_number),
        FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
        FOREIGN KEY (player1_registration_id) REFERENCES registrations(id) ON DELETE SET NULL,
        FOREIGN KEY (player2_registration_id) REFERENCES registrations(id) ON DELETE SET NULL,
        FOREIGN KEY (winner_registration_id) REFERENCES registrations(id) ON DELETE SET NULL,
        FOREIGN KEY (loser_registration_id) REFERENCES registrations(id) ON DELETE SET NULL
    );

    CREATE TABLE IF NOT EXISTS tournament_round_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER NOT NULL,
        stage_index INTEGER NOT NULL DEFAULT 1,
        group_number INTEGER NOT NULL DEFAULT 1,
        bracket_type TEXT NOT NULL,
        round_number INTEGER NOT NULL,
        best_of INTEGER NOT NULL,
        UNIQUE (tournament_id, stage_index, group_number, bracket_type, round_number),
        FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS tournament_stage_groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER NOT NULL,
        stage_index INTEGER NOT NULL DEFAULT 1,
        group_number INTEGER NOT NULL,
        advance_count INTEGER NOT NULL DEFAULT 1,
        UNIQUE (tournament_id, stage_index, group_number),
        FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS tournament_match_preparations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER NOT NULL,
        bracket_type TEXT NOT NULL,
        round_number INTEGER NOT NULL,
        match_number INTEGER NOT NULL,
        bp_mode TEXT NOT NULL DEFAULT 'none',
        state_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (tournament_id, bracket_type, round_number, match_number),
        FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS ladder_pair_ratings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user1_id INTEGER NOT NULL,
        user2_id INTEGER NOT NULL,
        rating REAL NOT NULL DEFAULT 1500,
        games INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (user1_id, user2_id),
        FOREIGN KEY (user1_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (user2_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS ladder_queue_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mode TEXT NOT NULL,
        captain_user_id INTEGER NOT NULL,
        teammate_user_id INTEGER,
        commanders_json TEXT NOT NULL DEFAULT '[]',
        rating REAL NOT NULL DEFAULT 1500,
        joined_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (captain_user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (teammate_user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS ladder_matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mode TEXT NOT NULL,
        state TEXT NOT NULL,
        map_name TEXT NOT NULL,
        team_a_captain_user_id INTEGER NOT NULL,
        team_a_user1_id INTEGER NOT NULL,
        team_a_user2_id INTEGER,
        team_b_captain_user_id INTEGER NOT NULL,
        team_b_user1_id INTEGER NOT NULL,
        team_b_user2_id INTEGER,
        winner_side TEXT,
        replay_filename TEXT,
        replay_saved_path TEXT,
        uploaded_at TEXT,
        resolved_at TEXT,
        deadline_at TEXT,
        state_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (team_a_captain_user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (team_a_user1_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (team_a_user2_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (team_b_captain_user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (team_b_user1_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (team_b_user2_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS ladder_chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        message_text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (match_id) REFERENCES ladder_matches(id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS ladder_lobby_chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        message_text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS ladder_online_presence (
        user_id INTEGER PRIMARY KEY,
        last_seen_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS ladder_bounties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        slot_count INTEGER NOT NULL,
        amount TEXT NOT NULL,
        description TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        created_by_user_id INTEGER NOT NULL,
        revoked_by_user_id INTEGER,
        revoked_reason TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        revoked_at TEXT,
        FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (revoked_by_user_id) REFERENCES users(id) ON DELETE SET NULL
    );

    CREATE TABLE IF NOT EXISTS ladder_bounty_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bounty_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        invalid_reason TEXT,
        selected_files_json TEXT NOT NULL DEFAULT '[]',
        copied_dir TEXT NOT NULL,
        reviewed_by_user_id INTEGER,
        reviewed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (bounty_id, user_id),
        FOREIGN KEY (bounty_id) REFERENCES ladder_bounties(id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (reviewed_by_user_id) REFERENCES users(id) ON DELETE SET NULL
    );
    """
    with closing(get_connection()) as connection:
        connection.executescript(schema)
        connection.commit()
        # 积分商城表
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS shop_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                points INTEGER NOT NULL DEFAULT 0,
                image_path TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shop_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                recipient_name TEXT,
                phone TEXT,
                address TEXT,
                notes TEXT,
                downloaded INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (item_id) REFERENCES shop_items(id) ON DELETE CASCADE
            );
        """)
        connection.commit()
        ensure_registrations_schema(connection)
        connection.commit()
        with connection:
            ensure_column(connection, "tournaments", "started_at TEXT")
            ensure_column(connection, "tournaments", "stage2_format_type TEXT")
            ensure_column(connection, "tournaments", "stage1_group_count INTEGER NOT NULL DEFAULT 1")
            ensure_column(connection, "tournaments", "round_robin_cycles INTEGER NOT NULL DEFAULT 1")
            ensure_column(connection, "tournaments", "round_robin_win_points INTEGER NOT NULL DEFAULT 3")
            ensure_column(connection, "tournaments", "round_robin_draw_points INTEGER NOT NULL DEFAULT 1")
            ensure_column(connection, "tournaments", "round_robin_loss_points INTEGER NOT NULL DEFAULT 0")
            ensure_column(connection, "tournaments", "round_robin_best_of INTEGER NOT NULL DEFAULT 3")
            ensure_column(connection, "tournaments", "swiss_regular_best_of INTEGER NOT NULL DEFAULT 3")
            ensure_column(connection, "tournaments", "swiss_decider_best_of INTEGER NOT NULL DEFAULT 3")
            ensure_column(connection, "tournaments", f"bp_step_seconds INTEGER NOT NULL DEFAULT {DEFAULT_TOURNAMENT_BP_STEP_SECONDS}")
            ensure_column(connection, "tournaments", "bp_mode TEXT NOT NULL DEFAULT 'none'")
            ensure_column(connection, "tournaments", "replay_result_enabled INTEGER NOT NULL DEFAULT 0")
            ensure_column(connection, "tournaments", "commander_shared_pool INTEGER NOT NULL DEFAULT 1")
            ensure_column(connection, "tournaments", "commander_ban_mode TEXT NOT NULL DEFAULT 'global'")
            ensure_column(connection, "users", "game_nickname TEXT")
            ensure_column(connection, "users", "is_superadmin INTEGER NOT NULL DEFAULT 0")
            ensure_column(connection, "users", "is_ladder_admin INTEGER NOT NULL DEFAULT 0")
            ensure_column(connection, "users", "is_tournament_admin INTEGER NOT NULL DEFAULT 0")
            ensure_column(connection, "users", "ladder_rating_1v1 REAL NOT NULL DEFAULT 1500")
            ensure_column(connection, "users", "ladder_games_1v1 INTEGER NOT NULL DEFAULT 0")
            ensure_column(connection, "users", "tournament_points INTEGER NOT NULL DEFAULT 0")
            ensure_column(connection, "users", "exchange_points INTEGER NOT NULL DEFAULT 0")
            ensure_column(connection, "users", "is_shop_admin INTEGER NOT NULL DEFAULT 0")
            ensure_column(connection, "users", "avatar_path TEXT")
            ensure_column(connection, "users", "avatar_frame TEXT")
            ensure_column(connection, "users", "nickname_frame TEXT")
            ensure_column(connection, "users", "nickname_color TEXT")
            ensure_column(connection, "users", "is_sponsor INTEGER NOT NULL DEFAULT 0")
            ensure_column(connection, "tournaments", "is_points_event INTEGER NOT NULL DEFAULT 0")
            ensure_column(connection, "tournaments", "points_for_champion INTEGER NOT NULL DEFAULT 0")
            ensure_column(connection, "tournaments", "is_private INTEGER NOT NULL DEFAULT 0")
            ensure_column(connection, "tournaments", "invite_code TEXT")
            ensure_column(connection, "registrations", "checked_in INTEGER NOT NULL DEFAULT 0")
            ensure_column(connection, "registrations", "checked_in_at TEXT")
            ensure_column(connection, "registrations", "is_temporary INTEGER NOT NULL DEFAULT 0")
            ensure_column(connection, "registrations", "seed_order INTEGER NOT NULL DEFAULT 0")
            ensure_column(connection, "registrations", "group_number INTEGER NOT NULL DEFAULT 1")
            ensure_column(connection, "tournament_matches", "best_of INTEGER NOT NULL DEFAULT 3")
            ensure_column(connection, "tournament_matches", "player1_slot_type TEXT NOT NULL DEFAULT 'player'")
            ensure_column(connection, "tournament_matches", "player2_slot_type TEXT NOT NULL DEFAULT 'player'")
            connection.execute("UPDATE users SET is_superadmin = 0 WHERE is_superadmin IS NULL")
            connection.execute("UPDATE users SET is_ladder_admin = 0 WHERE is_ladder_admin IS NULL")
            connection.execute("UPDATE users SET is_tournament_admin = 0 WHERE is_tournament_admin IS NULL")
            connection.execute("UPDATE users SET ladder_rating_1v1 = 1500 WHERE ladder_rating_1v1 IS NULL")
            connection.execute("UPDATE users SET ladder_games_1v1 = 0 WHERE ladder_games_1v1 IS NULL")
            connection.execute("UPDATE users SET tournament_points = 0 WHERE tournament_points IS NULL")
            connection.execute("UPDATE users SET exchange_points = tournament_points WHERE exchange_points IS NULL")
            connection.execute("UPDATE users SET is_shop_admin = 0 WHERE is_shop_admin IS NULL")
            connection.execute("UPDATE tournaments SET is_points_event = 0 WHERE is_points_event IS NULL")
            connection.execute("UPDATE tournaments SET points_for_champion = 0 WHERE points_for_champion IS NULL")
            connection.execute("UPDATE tournaments SET is_private = 0 WHERE is_private IS NULL")
            connection.execute("UPDATE tournaments SET stage1_group_count = 1 WHERE stage1_group_count IS NULL OR stage1_group_count < 1")
            connection.execute(
                "UPDATE tournaments SET round_robin_cycles = 1 WHERE round_robin_cycles IS NULL OR round_robin_cycles < 1"
            )
            connection.execute(
                "UPDATE tournaments SET round_robin_win_points = 3 WHERE round_robin_win_points IS NULL"
            )
            connection.execute(
                "UPDATE tournaments SET round_robin_draw_points = 1 WHERE round_robin_draw_points IS NULL"
            )
            connection.execute(
                "UPDATE tournaments SET round_robin_loss_points = 0 WHERE round_robin_loss_points IS NULL"
            )
            connection.execute(
                "UPDATE tournaments SET round_robin_best_of = 3 WHERE round_robin_best_of IS NULL"
            )
            connection.execute(
                "UPDATE tournaments SET swiss_regular_best_of = 3 WHERE swiss_regular_best_of IS NULL"
            )
            connection.execute(
                "UPDATE tournaments SET swiss_decider_best_of = 3 WHERE swiss_decider_best_of IS NULL"
            )
            connection.execute(
                "UPDATE tournaments SET bp_step_seconds = ? WHERE bp_step_seconds IS NULL OR bp_step_seconds < ? OR bp_step_seconds > ?",
                (
                    DEFAULT_TOURNAMENT_BP_STEP_SECONDS,
                    TOURNAMENT_BP_STEP_SECONDS_MIN,
                    TOURNAMENT_BP_STEP_SECONDS_MAX,
                ),
            )
            connection.execute("UPDATE tournaments SET bp_mode = 'none' WHERE bp_mode IS NULL OR bp_mode = ''")
            connection.execute(
                "UPDATE tournaments SET replay_result_enabled = 0 WHERE replay_result_enabled IS NULL"
            )
            connection.execute(
                "UPDATE tournaments SET commander_shared_pool = 1 WHERE commander_shared_pool IS NULL"
            )
            connection.execute(
                "UPDATE tournaments SET commander_ban_mode = 'global' WHERE commander_ban_mode IS NULL OR commander_ban_mode NOT IN ('global', 'opponent_only')"
            )
            connection.execute("UPDATE registrations SET checked_in = 0 WHERE checked_in IS NULL")
            connection.execute("UPDATE registrations SET is_temporary = 0 WHERE is_temporary IS NULL")
            connection.execute("UPDATE registrations SET seed_order = 0 WHERE seed_order IS NULL")
            connection.execute("UPDATE registrations SET group_number = 1 WHERE group_number IS NULL OR group_number < 1")
            connection.execute("UPDATE tournament_matches SET best_of = 3 WHERE best_of IS NULL")
            connection.execute(
                "UPDATE tournament_matches SET player1_slot_type = 'player' WHERE player1_slot_type IS NULL OR player1_slot_type = ''"
            )
            connection.execute(
                "UPDATE tournament_matches SET player2_slot_type = 'player' WHERE player2_slot_type IS NULL OR player2_slot_type = ''"
            )
            backfill_registration_seed_order(connection)
            ensure_superadmin(connection)
            ensure_streamer_account(connection)
            connection.execute(
                "INSERT OR IGNORE INTO system_settings (key, value, updated_at) VALUES (?, ?, ?)",
                (SYSTEM_SETTING_LADDER_BP_SECONDS, str(LADDER_BP_SECONDS), now_text()),
            )
    COMMANDER_ASSET_THUMB_DIR.mkdir(parents=True, exist_ok=True)
    COMMANDER_ASSET_FULL_DIR.mkdir(parents=True, exist_ok=True)
    GUIDE_ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def now_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def current_utc8_datetime() -> datetime:
    return datetime.utcnow() + timedelta(hours=8)


def current_utc8_text() -> str:
    return current_utc8_datetime().strftime(TOURNAMENT_TIME_FORMAT)


def current_utc8_timestamp_text() -> str:
    return f"{current_utc8_datetime().strftime('%Y-%m-%d %H:%M:%S')} UTC+8"


def parse_tournament_datetime(value: str) -> datetime:
    return datetime.strptime(value, TOURNAMENT_TIME_FORMAT)


def normalize_datetime_input(value: str) -> str:
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    return parsed.strftime(TOURNAMENT_TIME_FORMAT)


def password_hash(raw_password: str, salt: str | None = None) -> str:
    actual_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        raw_password.encode("utf-8"),
        actual_salt.encode("utf-8"),
        100_000,
    ).hex()
    return f"{actual_salt}${digest}"


def password_matches(raw_password: str, stored_hash: str) -> bool:
    salt, existing = stored_hash.split("$", 1)
    return password_hash(raw_password, salt).split("$", 1)[1] == existing


def ensure_superadmin(connection: sqlite3.Connection) -> None:
    existing = connection.execute(
        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
        (SUPERADMIN_USERNAME,),
    ).fetchone()
    admin_hash = password_hash(SUPERADMIN_PASSWORD)
    if existing is None:
        connection.execute(
            "INSERT INTO users (username, password_hash, is_superadmin, created_at) VALUES (?, ?, 1, ?)",
            (SUPERADMIN_USERNAME, admin_hash, now_text()),
        )
        return

    connection.execute(
        "UPDATE users SET password_hash = ?, is_superadmin = 1 WHERE id = ?",
        (admin_hash, existing["id"]),
    )


def ensure_streamer_account(connection: sqlite3.Connection) -> None:
    existing = connection.execute(
        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
        (STREAMER_USERNAME,),
    ).fetchone()
    streamer_hash = password_hash(STREAMER_PASSWORD)
    if existing is None:
        connection.execute(
            "INSERT INTO users (username, game_nickname, password_hash, is_superadmin, created_at) VALUES (?, ?, ?, 0, ?)",
            (STREAMER_USERNAME, "Streamer", streamer_hash, now_text()),
        )
        return

    connection.execute(
        "UPDATE users SET game_nickname = ?, password_hash = ?, is_superadmin = 0 WHERE id = ?",
        ("Streamer", streamer_hash, existing["id"]),
    )


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def trim_username(username: str) -> str:
    return username.strip()


def commander_base_name(name: str | None) -> str:
    cleaned_name = str(name or "").strip()
    return re.sub(r"\s*\([^)]*\)$", "", cleaned_name)


def default_commander_asset_manifest() -> dict[str, dict[str, str]]:
    return {
        commander_name: {"thumbnail": "", "full": ""}
        for commander_name in commander_catalog_names()
    }


def normalize_commander_asset_relative_path(raw_value: str | None) -> str:
    cleaned = str(raw_value or "").strip().replace("\\", "/")
    if not cleaned:
        return ""
    while cleaned.startswith("/"):
        cleaned = cleaned[1:]
    parts = [part for part in cleaned.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        return ""
    if parts[:2] == ["static", "commander_assets"]:
        parts = parts[2:]
    elif parts[:1] == ["commander_assets"]:
        parts = parts[1:]
    return "/".join(parts)


def load_commander_asset_manifest() -> dict[str, dict[str, str]]:
    manifest = default_commander_asset_manifest()
    if not COMMANDER_ASSET_MANIFEST_PATH.exists():
        return manifest
    try:
        raw_payload = json.loads(COMMANDER_ASSET_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return manifest
    if not isinstance(raw_payload, dict):
        return manifest

    for raw_name, raw_entry in raw_payload.items():
        base_name = commander_base_name(str(raw_name))
        if base_name not in manifest:
            continue
        if isinstance(raw_entry, str):
            normalized_path = normalize_commander_asset_relative_path(raw_entry)
            manifest[base_name] = {"thumbnail": normalized_path, "full": normalized_path}
            continue
        if not isinstance(raw_entry, dict):
            continue
        thumbnail_path = normalize_commander_asset_relative_path(raw_entry.get("thumbnail") or raw_entry.get("thumb"))
        full_path = normalize_commander_asset_relative_path(raw_entry.get("full") or raw_entry.get("detail") or thumbnail_path)
        manifest[base_name] = {"thumbnail": thumbnail_path, "full": full_path}
    return manifest


def commander_asset_payload(name: str | None, variant: str = "thumb") -> dict[str, str | bool]:
    base_name = commander_base_name(name)
    manifest = load_commander_asset_manifest()
    raw_entry = manifest.get(base_name, {"thumbnail": "", "full": ""})
    thumbnail_path = str(raw_entry.get("thumbnail") or "")
    full_path = str(raw_entry.get("full") or thumbnail_path or "")

    def build_url(relative_path: str) -> str:
        if not relative_path:
            return ""
        return f"/static/commander_assets/{quote(relative_path, safe='/')}"

    thumb_url = build_url(thumbnail_path)
    full_url = build_url(full_path)
    image_url = full_url if variant == "full" and full_url else thumb_url or full_url
    return {
        "name": base_name,
        "thumb_url": thumb_url,
        "full_url": full_url,
        "image_url": image_url,
        "has_image": bool(image_url),
    }


def validate_username(username: str) -> str | None:
    candidate = trim_username(username)
    if len(candidate) < 3 or len(candidate) > 24:
        return "账号长度需在 3 到 24 个字符之间。"
    if any(char.isspace() for char in candidate):
        return "账号不能包含空白字符。"
    return None


def validate_password(password: str) -> str | None:
    if len(password) < 6:
        return "密码长度至少需要 6 位。"
    return None


def current_user_game_nickname(user: sqlite3.Row | None) -> str | None:
    if user is None or "game_nickname" not in user.keys():
        return None
    return normalize_game_nickname(user["game_nickname"])


def user_display_name(user_row: sqlite3.Row | None) -> str:
    """返回用户对外展示名：优先游戏昵称，其次用户名。"""
    if user_row is None:
        return "未知用户"
    game_nick = current_user_game_nickname(user_row)
    if game_nick:
        return game_nick
    return str(user_row.get("username") or "未知用户")


def safe_redirect_path(candidate: str | None, default: str = "/dashboard") -> str:
    cleaned = (candidate or "").strip()
    if not cleaned.startswith("/") or cleaned.startswith("//"):
        return default
    return cleaned


def append_redirect_query_value(path: str, key: str, value: str) -> str:
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}{key}={quote(value)}"


def append_redirect_error(path: str, error_message: str) -> str:
    return append_redirect_query_value(path, "bind_game_nickname_error", error_message)


def sanitize_path_component(value: str | None, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (value or "").strip()).strip(". ")
    return cleaned or fallback


def slugify(name: str) -> str:
    raw = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return raw[:40] or "tournament"


def generate_invite_code() -> str:
    """生成 6 位随机邀请码（数字+大写字母）。"""
    chars = [c for c in "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"]
    return "".join(random.choice(chars) for _ in range(6))


def next_available_slug(connection: sqlite3.Connection, name: str) -> str:
    root = slugify(name)
    candidate = root
    suffix = 2
    while connection.execute(
        "SELECT 1 FROM tournaments WHERE slug = ?",
        (candidate,),
    ).fetchone():
        tail = f"-{suffix}"
        candidate = f"{root[: max(1, 40 - len(tail))]}{tail}"
        suffix += 1
    return candidate


def set_session_cookie(response: RedirectResponse, user_id: int, remember_login: bool) -> None:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + (
        timedelta(days=SESSION_TTL_DAYS) if remember_login else timedelta(hours=SESSION_TTL_HOURS)
    )

    with closing(get_connection()) as connection, connection:
        connection.execute(
            "INSERT INTO sessions (user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (user_id, token_digest(token), expires_at.strftime("%Y-%m-%d %H:%M:%S"), now_text()),
        )

    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_DAYS * 24 * 60 * 60 if remember_login else None,
    )


def clear_session_cookie(request: Request, response: RedirectResponse) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with closing(get_connection()) as connection, connection:
            connection.execute(
                "DELETE FROM sessions WHERE token_hash = ?",
                (token_digest(token),),
            )
    response.delete_cookie(SESSION_COOKIE)


def get_current_user(request: Request) -> sqlite3.Row | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None

    with closing(get_connection()) as connection, connection:
        current = connection.execute(
            """
            SELECT users.*
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ?
            """,
            (token_digest(token), now_text()),
        ).fetchone()
        if current is None:
            connection.execute(
                "DELETE FROM sessions WHERE token_hash = ? OR expires_at <= ?",
                (token_digest(token), now_text()),
            )
        return current


def render(
    request: Request,
    template_name: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    current_user = context.get("current_user") if context and "current_user" in context else get_current_user(request)
    bound_game_nickname = current_user_game_nickname(current_user)
    needs_avatar = bool(current_user and not (current_user["avatar_path"] if isinstance(current_user, dict) else (dict(current_user).get("avatar_path"))))
    payload = {
        "request": request,
        "current_user": current_user,
        "is_superadmin": bool(current_user and current_user["is_superadmin"]),
        "is_streamer": is_streamer_user(current_user),
        "is_ladder_admin": bool(current_user and current_user["is_ladder_admin"]),
        "is_tournament_platform_admin": bool(current_user and current_user["is_tournament_admin"]),
        "current_user_game_nickname": bound_game_nickname,
        "needs_game_nickname_binding": bool(current_user and not bound_game_nickname),
        "needs_avatar_setup": needs_avatar,
        "game_nickname_bind_error": request.query_params.get("bind_game_nickname_error"),
        "page_title": SITE_NAME,
        "site_name": SITE_NAME,
        "commander_asset": commander_asset_payload,
        "commander_asset_manifest_path": COMMANDER_ASSET_MANIFEST_PATH.relative_to(BASE_DIR).as_posix(),
    }
    if context:
        payload.update(context)
    if current_user is not None and not request.url.path.startswith("/ladder"):
        with closing(get_connection()) as connection, connection:
            ladder_clear_presence(connection, int(current_user["id"]))
    return templates.TemplateResponse(template_name, payload, status_code=status_code)


def require_user(request: Request) -> sqlite3.Row | None:
    return get_current_user(request)


def normalize_admin_panel(value: str | None) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in {"replay", "ladder_reviews", "users", "tournaments"} else "replay"


def admin_panel_redirect_path(panel: str | None) -> str:
    return f"/admin?panel={quote(normalize_admin_panel(panel))}"


def is_superadmin_user(user: sqlite3.Row | None) -> bool:
    return bool(user and user["is_superadmin"])


def is_streamer_user(user: sqlite3.Row | None) -> bool:
    return bool(user and str(user["username"]).lower() == STREAMER_USERNAME)


def is_ladder_admin_user(user: sqlite3.Row | None) -> bool:
    return bool(user and user["is_ladder_admin"])


def is_tournament_platform_admin_user(user: sqlite3.Row | None) -> bool:
    return bool(user and user["is_tournament_admin"])


def has_ladder_admin_access(user: sqlite3.Row | None) -> bool:
    return is_superadmin_user(user) or is_ladder_admin_user(user)


def has_bp_replay_access(user: sqlite3.Row | None) -> bool:
    return is_superadmin_user(user) or is_streamer_user(user)


def system_setting_text(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row is not None else None


def set_system_setting(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO system_settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, now_text()),
    )


def system_setting_int(
    connection: sqlite3.Connection,
    key: str,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw_value = system_setting_text(connection, key)
    try:
        parsed = int(raw_value or default)
    except (TypeError, ValueError):
        parsed = int(default)
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def ladder_bp_seconds_setting(connection: sqlite3.Connection) -> int:
    return system_setting_int(
        connection,
        SYSTEM_SETTING_LADDER_BP_SECONDS,
        LADDER_BP_SECONDS,
        minimum=LADDER_BP_SECONDS_MIN,
        maximum=LADDER_BP_SECONDS_MAX,
    )


def excerpt(text: str, limit: int = 140) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}..."


def parse_shared_admins(raw_value: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for part in raw_value.split(","):
        username = trim_username(part)
        if username and username.lower() not in seen:
            result.append(username)
            seen.add(username.lower())
    return result


def resolve_shared_admin_user_ids(
    connection: sqlite3.Connection,
    owner_user_id: int,
    raw_value: str,
) -> tuple[list[int], list[str]]:
    missing_admins: list[str] = []
    resolved_admin_ids: list[int] = []
    for username_item in parse_shared_admins(raw_value):
        user = connection.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
            (username_item,),
        ).fetchone()
        if user is None:
            missing_admins.append(username_item)
            continue
        user_id = int(user["id"])
        if user_id != int(owner_user_id):
            resolved_admin_ids.append(user_id)
    return resolved_admin_ids, missing_admins


def parse_map_pool(raw_value: str) -> list[str]:
    normalized = raw_value.replace("，", ",").replace("\r\n", "\n").replace("\r", "\n")
    result: list[str] = []
    for part in re.split(r",|\n", normalized):
        map_name = part.strip()
        if map_name and map_name != "待公布":
            result.append(map_name)
    return result


def normalize_map_pool(raw_value: str) -> str:
    maps = parse_map_pool(raw_value)
    return ", ".join(maps) if maps else "待公布"


def is_tournament_manager(
    connection: sqlite3.Connection,
    tournament_id: int,
    user_id: int,
    creator_id: int,
) -> bool:
    user = connection.execute(
        "SELECT is_superadmin, is_tournament_admin FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if user and (user["is_superadmin"] or user["is_tournament_admin"]):
        return True
    if user_id == creator_id:
        return True
    return (
        connection.execute(
            "SELECT 1 FROM tournament_admins WHERE tournament_id = ? AND user_id = ?",
            (tournament_id, user_id),
        ).fetchone()
        is not None
    )


def load_tournament_bundle(
    tournament_id: int,
    current_user_id: int,
) -> tuple[sqlite3.Row | None, sqlite3.Row | None, list[sqlite3.Row], list[sqlite3.Row], bool]:
    with closing(get_connection()) as connection:
        tournament = connection.execute(
            """
            SELECT tournaments.*, COALESCE(users.game_nickname, users.username) AS creator_name
            FROM tournaments
            JOIN users ON users.id = tournaments.creator_id
            WHERE tournaments.id = ?
            """,
            (tournament_id,),
        ).fetchone()
        if tournament is None:
            return None, None, [], [], False

        registration = connection.execute(
            "SELECT * FROM registrations WHERE tournament_id = ? AND user_id = ?",
            (tournament_id, current_user_id),
        ).fetchone()

        admins = connection.execute(
            """
            SELECT users.username
            FROM tournament_admins
            JOIN users ON users.id = tournament_admins.user_id
            WHERE tournament_admins.tournament_id = ?
            ORDER BY users.username COLLATE NOCASE
            """,
            (tournament_id,),
        ).fetchall()

        participants = connection.execute(
            """
            SELECT registrations.nickname, registrations.joined_at, registrations.is_temporary,
                   COALESCE(users.game_nickname, users.username) AS account_name,
                   users.game_nickname AS account_game_nickname
            FROM registrations
            LEFT JOIN users ON users.id = registrations.user_id
            WHERE registrations.tournament_id = ?
            ORDER BY registrations.seed_order ASC, registrations.joined_at ASC, registrations.id ASC
            """,
            (tournament_id,),
        ).fetchall()

        manager = is_tournament_manager(connection, tournament_id, current_user_id, tournament["creator_id"])
        return tournament, registration, admins, participants, manager


def can_view_bracket(tournament: sqlite3.Row, is_registered: bool, is_manager: bool) -> bool:
    if is_manager:
        return True
    if not is_registered:
        return False
    if tournament["hide_bracket_until_publish"] and not tournament["bracket_published"]:
        return False
    return True


def build_bracket_preview(tournament: sqlite3.Row, participants: list[sqlite3.Row]) -> dict[str, Any]:
    participant_names = [row["nickname"] for row in participants]
    format_type = tournament["format_type"]

    if not participant_names:
        return {
            "mode": "empty",
            "summary": "当前还没有报名选手，对阵表将在有选手后显示。",
        }

    if format_type in {"single_elimination", "double_elimination"}:
        matches = []
        for index in range(0, len(participant_names), 2):
            left = participant_names[index]
            right = participant_names[index + 1] if index + 1 < len(participant_names) else "轮空"
            matches.append(
                {
                    "label": f"第 1 轮 / 场次 {len(matches) + 1}",
                    "left": left,
                    "right": right,
                }
            )
        return {
            "mode": "matches",
            "summary": "当前根据已确认参赛选手生成首轮对阵。",
            "matches": matches,
        }

    if format_type == "swiss":
        return {
            "mode": "list",
            "summary": "当前根据选手积分与轮次信息展示瑞士轮状态。",
            "entries": [f"Seed {index + 1}: {name}" for index, name in enumerate(participant_names)],
        }

    return {
        "mode": "list",
        "summary": "当前展示循环赛赛程与积分状态。",
        "entries": [f"{index + 1}. {name}" for index, name in enumerate(participant_names)],
    }


def load_tournament_row(connection: sqlite3.Connection, tournament_id: int) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT tournaments.*, COALESCE(users.game_nickname, users.username) AS creator_name
        FROM tournaments
        JOIN users ON users.id = tournaments.creator_id
        WHERE tournaments.id = ?
        """,
        (tournament_id,),
    ).fetchone()


def tournament_started(tournament: sqlite3.Row) -> bool:
    return bool(tournament["started_at"])


def tournament_phase(tournament: sqlite3.Row) -> str:
    if tournament_started(tournament):
        return "started"

    current_time = current_utc8_datetime()
    checkin_time = parse_tournament_datetime(tournament["checkin_time"])
    start_time = parse_tournament_datetime(tournament["start_time"])
    if current_time < checkin_time:
        return "before_checkin"
    if current_time < start_time:
        return "checkin_open"
    return "awaiting_start"


def tournament_phase_label(phase: str) -> str:
    labels = {
        "before_checkin": "未到签到时间",
        "checkin_open": "签到进行中",
        "awaiting_start": "等待管理员开始",
        "started": "比赛已开始",
    }
    return labels.get(phase, "赛事进行中")


def can_register_for_tournament(tournament: sqlite3.Row) -> bool:
    return not tournament_started(tournament)


def checkin_is_open(tournament: sqlite3.Row) -> bool:
    return tournament_phase(tournament) in {"checkin_open", "awaiting_start"}


def has_completed_matches(connection: sqlite3.Connection, tournament_id: int) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM tournament_matches WHERE tournament_id = ? AND status = 'completed' LIMIT 1",
            (tournament_id,),
        ).fetchone()
        is not None
    )


def tournament_is_finished(connection: sqlite3.Connection, tournament: sqlite3.Row) -> bool:
    if not tournament_started(tournament):
        return False

    ensure_tournament_matches(connection, tournament)
    has_any_matches = connection.execute(
        "SELECT 1 FROM tournament_matches WHERE tournament_id = ? LIMIT 1",
        (tournament["id"],),
    ).fetchone()
    if has_any_matches is None:
        return False

    unfinished_match = connection.execute(
        "SELECT 1 FROM tournament_matches WHERE tournament_id = ? AND status != 'completed' LIMIT 1",
        (tournament["id"],),
    ).fetchone()
    return unfinished_match is None


def reset_unfinished_bracket(connection: sqlite3.Connection, tournament_id: int) -> None:
    tournament = load_tournament_row(connection, tournament_id)
    if tournament is not None and not tournament_started(tournament):
        connection.execute(
            "DELETE FROM tournament_matches WHERE tournament_id = ?",
            (tournament_id,),
        )


def load_registrations(connection: sqlite3.Connection, tournament_id: int) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT registrations.*, COALESCE(users.game_nickname, users.username) AS account_name,
               users.game_nickname AS account_game_nickname,
               users.avatar_path, users.avatar_frame, users.nickname_frame, users.nickname_color
        FROM registrations
        LEFT JOIN users ON users.id = registrations.user_id
        WHERE registrations.tournament_id = ?
        ORDER BY registrations.seed_order ASC, registrations.joined_at ASC, registrations.id ASC
        """,
        (tournament_id,),
    ).fetchall()


def load_registration_for_user(
    connection: sqlite3.Connection,
    tournament_id: int,
    user_id: int,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM registrations WHERE tournament_id = ? AND user_id = ?",
        (tournament_id, user_id),
    ).fetchone()


def prune_no_shows(connection: sqlite3.Connection, tournament_id: int) -> int:
    tournament = load_tournament_row(connection, tournament_id)
    if tournament is None or not tournament_started(tournament):
        return 0

    pending_rows = connection.execute(
        "SELECT id FROM registrations WHERE tournament_id = ? AND checked_in = 0",
        (tournament_id,),
    ).fetchall()
    if not pending_rows:
        return 0

    connection.execute(
        "DELETE FROM registrations WHERE tournament_id = ? AND checked_in = 0",
        (tournament_id,),
    )
    reset_unfinished_bracket(connection, tournament_id)
    return len(pending_rows)


def cleanup_all_tournaments(connection: sqlite3.Connection) -> None:
    tournament_ids = connection.execute("SELECT id FROM tournaments").fetchall()
    for row in tournament_ids:
        prune_no_shows(connection, row["id"])


def active_registration_rows(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
) -> list[sqlite3.Row]:
    if tournament_started(tournament):
        return connection.execute(
            """
            SELECT registrations.*, COALESCE(users.game_nickname, users.username) AS account_name,
                   users.game_nickname AS account_game_nickname
            FROM registrations
            LEFT JOIN users ON users.id = registrations.user_id
            WHERE registrations.tournament_id = ? AND registrations.checked_in = 1
            ORDER BY registrations.seed_order ASC, registrations.joined_at ASC, registrations.id ASC
            """,
            (tournament["id"],),
        ).fetchall()
    return load_registrations(connection, tournament["id"])


def next_registration_seed_order(connection: sqlite3.Connection, tournament_id: int) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(seed_order), 0) + 1 AS next_seed_order FROM registrations WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone()
    return int(row["next_seed_order"])


def normalize_group_number(group_number: int, group_count: int) -> int:
    return min(max(1, int(group_number)), max(1, int(group_count)))


def ensure_stage_group_settings(
    connection: sqlite3.Connection,
    tournament_id: int,
    group_count: int,
    stage_index: int = 1,
) -> None:
    normalized_group_count = max(1, int(group_count))
    existing = {
        row["group_number"]: row
        for row in connection.execute(
            "SELECT * FROM tournament_stage_groups WHERE tournament_id = ? AND stage_index = ?",
            (tournament_id, stage_index),
        ).fetchall()
    }
    for group_number in range(1, normalized_group_count + 1):
        if group_number not in existing:
            connection.execute(
                "INSERT INTO tournament_stage_groups (tournament_id, stage_index, group_number, advance_count) VALUES (?, ?, ?, 1)",
                (tournament_id, stage_index, group_number),
            )
    connection.execute(
        "DELETE FROM tournament_stage_groups WHERE tournament_id = ? AND stage_index = ? AND group_number > ?",
        (tournament_id, stage_index, normalized_group_count),
    )


def load_stage_group_settings(
    connection: sqlite3.Connection,
    tournament_id: int,
    group_count: int,
    stage_index: int = 1,
) -> list[dict[str, Any]]:
    ensure_stage_group_settings(connection, tournament_id, group_count, stage_index)
    rows = connection.execute(
        """
        SELECT *
        FROM tournament_stage_groups
        WHERE tournament_id = ? AND stage_index = ?
        ORDER BY group_number ASC
        """,
        (tournament_id, stage_index),
    ).fetchall()
    return [
        {
            "group_number": row["group_number"],
            "advance_count": row["advance_count"],
        }
        for row in rows
    ]


def registrations_by_group(
    registrations: list[sqlite3.Row],
    group_count: int,
) -> dict[int, list[sqlite3.Row]]:
    grouped: dict[int, list[sqlite3.Row]] = {group_number: [] for group_number in range(1, max(1, group_count) + 1)}
    for row in registrations:
        grouped.setdefault(normalize_group_number(row["group_number"], group_count), []).append(row)
    return grouped


def swap_registration_seed_order(
    connection: sqlite3.Connection,
    tournament_id: int,
    registration_id: int,
    direction: str,
) -> None:
    current_row = connection.execute(
        "SELECT id, seed_order FROM registrations WHERE id = ? AND tournament_id = ?",
        (registration_id, tournament_id),
    ).fetchone()
    if current_row is None:
        raise HTTPException(status_code=404, detail="参赛选手不存在。")

    if direction == "up":
        neighbor = connection.execute(
            """
            SELECT id, seed_order
            FROM registrations
            WHERE tournament_id = ? AND seed_order < ?
            ORDER BY seed_order DESC, id DESC
            LIMIT 1
            """,
            (tournament_id, current_row["seed_order"]),
        ).fetchone()
    elif direction == "down":
        neighbor = connection.execute(
            """
            SELECT id, seed_order
            FROM registrations
            WHERE tournament_id = ? AND seed_order > ?
            ORDER BY seed_order ASC, id ASC
            LIMIT 1
            """,
            (tournament_id, current_row["seed_order"]),
        ).fetchone()
    else:
        raise HTTPException(status_code=400, detail="调整方向无效。")

    if neighbor is None:
        return

    connection.execute(
        "UPDATE registrations SET seed_order = ? WHERE id = ?",
        (-1, current_row["id"]),
    )
    connection.execute(
        "UPDATE registrations SET seed_order = ? WHERE id = ?",
        (current_row["seed_order"], neighbor["id"]),
    )
    connection.execute(
        "UPDATE registrations SET seed_order = ? WHERE id = ?",
        (neighbor["seed_order"], current_row["id"]),
    )


def schedule_needs_rebuild(
    connection: sqlite3.Connection,
    tournament_id: int,
    active_registration_ids: list[int],
) -> bool:
    matches = connection.execute(
        "SELECT * FROM tournament_matches WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchall()
    if not matches:
        return True
    if any(row["status"] == "completed" for row in matches):
        return False

    match_participants = {
        registration_id
        for row in matches
        for registration_id in (row["player1_registration_id"], row["player2_registration_id"])
        if registration_id is not None
    }
    return match_participants != set(active_registration_ids)


def next_round_number(
    connection: sqlite3.Connection,
    tournament_id: int,
    bracket_type: str,
    stage_index: int = 1,
    group_number: int = 1,
) -> int:
    stored_bracket_type = bracket_storage_key(bracket_type, stage_index, group_number)
    row = connection.execute(
        "SELECT COALESCE(MAX(round_number), 0) + 1 AS next_round FROM tournament_matches WHERE tournament_id = ? AND bracket_type = ?",
        (tournament_id, stored_bracket_type),
    ).fetchone()
    return int(row["next_round"])


def bracket_sort_weight(bracket_type: str) -> int:
    _, _, base_bracket_type = parse_bracket_key(bracket_type)
    return {
        "winners": 1,
        "losers": 2,
        "finals": 3,
        "finals_reset": 4,
        "swiss": 1,
        "round_robin": 1,
    }.get(base_bracket_type, 99)


def bracket_storage_key(base_bracket_type: str, stage_index: int = 1, group_number: int = 1) -> str:
    return f"s{stage_index}g{group_number}:{base_bracket_type}"


def parse_bracket_key(bracket_type: str) -> tuple[int, int, str]:
    match = re.match(r"^s(\d+)g(\d+):(.*)$", bracket_type)
    if match is None:
        return 1, 1, bracket_type
    return int(match.group(1)), int(match.group(2)), match.group(3)


def bracket_allows_even_best_of(bracket_type: str) -> bool:
    return parse_bracket_key(bracket_type)[2] == "round_robin"


def default_best_of_for_bracket(bracket_type: str) -> int:
    return 3


def default_best_of_for_tournament_row(tournament: sqlite3.Row | None, bracket_type: str) -> int:
    if tournament is not None and normalize_bp_mode(tournament["bp_mode"]) in {BP_MODE_COMMANDER_CONQUEST, BP_MODE_COMMANDER_CONQUEST_2V2}:
        return 5
    return default_best_of_for_bracket(bracket_type)


def load_round_best_of(
    connection: sqlite3.Connection,
    tournament_id: int,
    bracket_type: str,
    round_number: int,
    stage_index: int = 1,
    group_number: int = 1,
) -> int:
    row = connection.execute(
        """
        SELECT best_of
        FROM tournament_round_settings
        WHERE tournament_id = ?
          AND stage_index = ?
          AND group_number = ?
          AND bracket_type = ?
          AND round_number = ?
        """,
        (tournament_id, stage_index, group_number, bracket_type, round_number),
    ).fetchone()
    if row is not None:
        return int(row["best_of"])
    tournament_row = connection.execute(
        "SELECT bp_mode FROM tournaments WHERE id = ?",
        (tournament_id,),
    ).fetchone()
    return default_best_of_for_tournament_row(tournament_row, bracket_type)


def upsert_round_best_of(
    connection: sqlite3.Connection,
    tournament_id: int,
    bracket_type: str,
    round_number: int,
    best_of: int,
    stage_index: int = 1,
    group_number: int = 1,
) -> None:
    validate_best_of(best_of, allow_even=bracket_allows_even_best_of(bracket_type))
    connection.execute(
        """
        INSERT INTO tournament_round_settings (
            tournament_id, stage_index, group_number, bracket_type, round_number, best_of
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(tournament_id, stage_index, group_number, bracket_type, round_number)
        DO UPDATE SET best_of = excluded.best_of
        """,
        (tournament_id, stage_index, group_number, bracket_type, round_number, best_of),
    )


def best_of_required_wins(best_of: int) -> int:
    return best_of // 2 + 1


def validate_best_of(best_of: int, allow_even: bool = False) -> None:
    if best_of < 1 or best_of > 9:
        raise HTTPException(status_code=400, detail="BO 局数只支持 1 到 9。")
    if not allow_even and best_of % 2 == 0:
        raise HTTPException(status_code=400, detail="当前赛制只支持奇数 BO。")


def completed_match_score_fits_best_of(match_row: sqlite3.Row, best_of: int) -> bool:
    if match_row["status"] != "completed":
        return True
    if match_row["player1_slot_type"] == MATCH_SLOT_BYE or match_row["player2_slot_type"] == MATCH_SLOT_BYE:
        return True

    score1 = int(match_row["score1"] or 0)
    score2 = int(match_row["score2"] or 0)
    if score1 < 0 or score2 < 0:
        return False

    if bracket_allows_even_best_of(match_row["bracket_type"]) and best_of % 2 == 0:
        return score1 == score2 == best_of // 2 or (
            score1 == best_of_required_wins(best_of) and score2 < best_of_required_wins(best_of)
        ) or (
            score2 == best_of_required_wins(best_of) and score1 < best_of_required_wins(best_of)
        )

    required_wins = best_of_required_wins(best_of)
    return (score1 == required_wins and score2 < required_wins) or (
        score2 == required_wins and score1 < required_wins
    )


def resolve_match_result_from_scores(
    match_row: sqlite3.Row,
    best_of: int,
    score1: int,
    score2: int,
) -> tuple[int | None, int | None]:
    allow_even = bracket_allows_even_best_of(match_row["bracket_type"])
    validate_best_of(best_of, allow_even=allow_even)
    if score1 < 0 or score2 < 0:
        raise HTTPException(status_code=400, detail="比分不能为负数。")

    player1_id = match_row["player1_registration_id"]
    player2_id = match_row["player2_registration_id"]
    if player1_id is None or player2_id is None:
        raise HTTPException(status_code=400, detail="当前对局选手不完整，不能录入比分。")

    required_wins = best_of_required_wins(best_of)
    if score1 > required_wins or score2 > required_wins:
        raise HTTPException(status_code=400, detail="比分超过了当前 BO 允许的最大胜场。")
    if score1 == score2:
        if allow_even and best_of % 2 == 0 and score1 == best_of // 2:
            return None, None
        raise HTTPException(status_code=400, detail="系列赛不能以平局结束。")

    if score1 == required_wins and score2 < required_wins:
        return player1_id, player2_id
    if score2 == required_wins and score1 < required_wins:
        return player2_id, player1_id

    raise HTTPException(status_code=400, detail=f"请输入合法的 BO{best_of} 完赛比分。")


def match_round_title(bracket_type: str, round_number: int) -> str:
    _, _, base_bracket_type = parse_bracket_key(bracket_type)
    if base_bracket_type in {"swiss", "round_robin"}:
        return f"第 {round_number} 轮"
    return f"{BRACKET_TYPE_LABELS.get(base_bracket_type, base_bracket_type)} 第 {round_number} 轮"


def make_match_slot(registration_id: int | None, slot_type: str = MATCH_SLOT_PLAYER) -> tuple[int | None, str]:
    if registration_id is None and slot_type == MATCH_SLOT_PLAYER:
        slot_type = MATCH_SLOT_TBD
    return (registration_id, slot_type)


def normalize_match_slot(slot: tuple[int | None, str] | int | None) -> tuple[int | None, str]:
    if isinstance(slot, tuple):
        return slot
    if slot is None:
        return make_match_slot(None, MATCH_SLOT_TBD)
    return make_match_slot(slot, MATCH_SLOT_PLAYER)


def pair_sequential(
    registration_ids: list[int | None],
    none_slot_type: str = MATCH_SLOT_TBD,
) -> list[tuple[tuple[int | None, str], tuple[int | None, str]]]:
    pairs: list[tuple[tuple[int | None, str], tuple[int | None, str]]] = []
    for index in range(0, len(registration_ids), 2):
        left = make_match_slot(registration_ids[index], MATCH_SLOT_PLAYER if registration_ids[index] is not None else none_slot_type)
        right_id = registration_ids[index + 1] if index + 1 < len(registration_ids) else None
        right = make_match_slot(right_id, MATCH_SLOT_PLAYER if right_id is not None else none_slot_type)
        pairs.append((left, right))
    return pairs


def recursive_seed_order(bracket_size: int) -> list[int]:
    order = [1]
    current_size = 1
    while current_size < bracket_size:
        next_size = current_size * 2
        expanded: list[int] = []
        for seed in order:
            expanded.extend([seed, next_size + 1 - seed])
        order = expanded
        current_size = next_size
    return order


def seeded_opening_pairs(registration_ids: list[int]) -> list[tuple[tuple[int | None, str], tuple[int | None, str]]]:
    bracket_size = next_power_of_two(len(registration_ids))
    arranged_slots = []
    for seed in recursive_seed_order(bracket_size):
        if seed <= len(registration_ids):
            arranged_slots.append(make_match_slot(registration_ids[seed - 1], MATCH_SLOT_PLAYER))
        else:
            arranged_slots.append(make_match_slot(None, MATCH_SLOT_BYE))

    pairs: list[tuple[tuple[int | None, str], tuple[int | None, str]]] = []
    for index in range(0, len(arranged_slots), 2):
        pairs.append((arranged_slots[index], arranged_slots[index + 1]))
    return pairs


def slot_display_name(registration: sqlite3.Row | None, slot_type: str) -> str:
    if registration is not None:
        # 优先使用报名时填写的队伍名称，其次使用绑定的游戏昵称，最后用登录名
        name = str(registration["nickname"] or "").strip()
        if name:
            return name
        if "account_game_nickname" in registration.keys():
            name = str(registration["account_game_nickname"] or "").strip()
            if name:
                return name
        if "account_name" in registration.keys():
            name = str(registration["account_name"] or "").strip()
            if name:
                return name
        # 最后回退到 registration 自带的 nickname（可能为空）
        return str(registration["nickname"] or "").strip() or "未命名"
    if slot_type == MATCH_SLOT_BYE:
        return "轮空"
    return "待定"


def match_view(
    match_row: sqlite3.Row,
    registration_map: dict[int, sqlite3.Row],
    manager: bool,
    started: bool,
) -> dict[str, Any]:
    stage_index, group_number, base_bracket_type = parse_bracket_key(match_row["bracket_type"])
    player1 = registration_map.get(match_row["player1_registration_id"])
    player2 = registration_map.get(match_row["player2_registration_id"])
    winner = registration_map.get(match_row["winner_registration_id"])
    best_of = int(match_row["best_of"] or 3)
    player1_slot_type = match_row["player1_slot_type"] or MATCH_SLOT_PLAYER
    player2_slot_type = match_row["player2_slot_type"] or MATCH_SLOT_PLAYER
    has_temporary_player = bool(
        (player1 and player1["is_temporary"]) or (player2 and player2["is_temporary"])
    )
    return {
        "id": match_row["id"],
        "player1_id": match_row["player1_registration_id"],
        "player2_id": match_row["player2_registration_id"],
        "winner_id": match_row["winner_registration_id"],
        "player1_name": slot_display_name(player1, player1_slot_type),
        "player2_name": slot_display_name(player2, player2_slot_type),
        "winner_name": slot_display_name(winner, MATCH_SLOT_PLAYER) if winner else "",
        "status": match_row["status"],
        "score1": match_row["score1"],
        "score2": match_row["score2"],
        "score_text": f"{match_row['score1']} : {match_row['score2']}",
        "best_of": best_of,
        "series_label": f"BO{best_of}",
        "round_label": match_round_title(base_bracket_type, match_row["round_number"]),
        "bracket_label": BRACKET_TYPE_LABELS.get(base_bracket_type, base_bracket_type),
        "stage_index": stage_index,
        "group_number": group_number,
        "base_bracket_type": base_bracket_type,
        "player1_slot_type": player1_slot_type,
        "player2_slot_type": player2_slot_type,
        "is_bye": player1_slot_type == MATCH_SLOT_BYE or player2_slot_type == MATCH_SLOT_BYE,
        "player1_is_temporary": bool(player1 and player1["is_temporary"]),
        "player2_is_temporary": bool(player2 and player2["is_temporary"]),
        "has_temporary_player": has_temporary_player,
        "editable": manager
        and started
        and match_row["player1_registration_id"] is not None
        and match_row["player2_registration_id"] is not None,
    }


def match_preparation_identity(match_row: sqlite3.Row) -> tuple[int, str, int, int]:
    return (
        int(match_row["tournament_id"]),
        str(match_row["bracket_type"]),
        int(match_row["round_number"]),
        int(match_row["match_number"]),
    )


def default_match_preparation_state(tournament: sqlite3.Row) -> dict[str, Any]:
    return {
        "bp_mode": normalize_bp_mode(tournament["bp_mode"]),
        "status": "awaiting_ready",
        "ready_registration_ids": [],
        "advance_ready_ids": [],
        "current_game_number": 1,
        "current_game_ready_at": None,
        "step_started_at": None,
        "step_deadline_seconds": tournament_bp_step_seconds(tournament),
        "map_bp": None,
        "commander_games": [],
        "conquest_series": None,
        "ladder_games": [],
        "evo_choices": [],
        "game_results": [],
        "pending_manual_replay_judgment": None,
        "pending_admin_replay_judgment": None,
    }


def round_robin_cycles_for_tournament(tournament: sqlite3.Row) -> int:
    return max(1, int(tournament["round_robin_cycles"] or DEFAULT_ROUND_ROBIN_CYCLES))


def round_robin_points_for_tournament(tournament: sqlite3.Row) -> tuple[int, int, int]:
    return (
        int(tournament["round_robin_win_points"] if tournament["round_robin_win_points"] is not None else DEFAULT_ROUND_ROBIN_WIN_POINTS),
        int(tournament["round_robin_draw_points"] if tournament["round_robin_draw_points"] is not None else DEFAULT_ROUND_ROBIN_DRAW_POINTS),
        int(tournament["round_robin_loss_points"] if tournament["round_robin_loss_points"] is not None else DEFAULT_ROUND_ROBIN_LOSS_POINTS),
    )


def round_robin_best_of_for_tournament(tournament: sqlite3.Row) -> int:
    return int(tournament["round_robin_best_of"] or DEFAULT_ROUND_ROBIN_BEST_OF)


def swiss_best_of_settings_for_tournament(tournament: sqlite3.Row) -> tuple[int, int]:
    return (
        int(tournament["swiss_regular_best_of"] or DEFAULT_SWISS_REGULAR_BEST_OF),
        int(tournament["swiss_decider_best_of"] or DEFAULT_SWISS_DECIDER_BEST_OF),
    )


def replay_result_enabled_for_tournament(tournament: sqlite3.Row) -> bool:
    return bool(tournament["replay_result_enabled"])


def tournament_bp_step_seconds(tournament: sqlite3.Row) -> int:
    raw_value = tournament["bp_step_seconds"] if "bp_step_seconds" in tournament.keys() else None
    try:
        parsed_value = int(raw_value or DEFAULT_TOURNAMENT_BP_STEP_SECONDS)
    except (TypeError, ValueError):
        parsed_value = DEFAULT_TOURNAMENT_BP_STEP_SECONDS
    return min(max(parsed_value, TOURNAMENT_BP_STEP_SECONDS_MIN), TOURNAMENT_BP_STEP_SECONDS_MAX)


def match_game_result_by_number(state: dict[str, Any], game_number: int) -> dict[str, Any] | None:
    target_game_number = int(game_number)
    for entry in reversed(state.get("game_results") or []):
        if int(entry.get("game_number") or 0) == target_game_number:
            return entry
    return None


def evo_choice_for_game(state: dict[str, Any], game_number: int) -> dict[str, Any] | None:
    target_game_number = int(game_number)
    for entry in state.get("evo_choices") or []:
        if int(entry.get("game_number") or 0) == target_game_number:
            return entry
    return None


def winner_loser_registration_ids_for_game(
    match_row: sqlite3.Row,
    game_result: dict[str, Any] | None,
) -> tuple[int | None, int | None]:
    if game_result is None:
        return None, None

    winner_registration_id = game_result.get("winner_registration_id")
    player1_id = match_row["player1_registration_id"]
    player2_id = match_row["player2_registration_id"]
    if winner_registration_id == player1_id and player2_id is not None:
        return int(player1_id), int(player2_id)
    if winner_registration_id == player2_id and player1_id is not None:
        return int(player2_id), int(player1_id)
    return None, None


def current_evo_choice_state(
    match_row: sqlite3.Row,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    current_game_number = max(1, int(state.get("current_game_number", 1) or 1))
    if current_game_number <= 1:
        return None

    stored_entry = evo_choice_for_game(state, current_game_number)
    if stored_entry is not None:
        normalized_value = str(stored_entry.get("choice_value") or "").strip()
        if normalized_value:
            stored_entry["choice_value"] = normalize_evo_choice(normalized_value)
            stored_entry["choice_label"] = EVO_CHOICE_LABELS[stored_entry["choice_value"]]
        else:
            stored_entry["choice_label"] = ""
        return stored_entry

    previous_result = match_game_result_by_number(state, current_game_number - 1)
    winner_registration_id, loser_registration_id = winner_loser_registration_ids_for_game(match_row, previous_result)
    if winner_registration_id is None or loser_registration_id is None:
        return None

    return {
        "game_number": current_game_number,
        "winner_registration_id": winner_registration_id,
        "loser_registration_id": loser_registration_id,
        "choice_value": "",
        "choice_label": "",
    }


def upsert_evo_choice(state: dict[str, Any], choice_entry: dict[str, Any]) -> dict[str, Any]:
    normalized_entry = dict(choice_entry)
    normalized_entry["game_number"] = int(choice_entry.get("game_number") or 0)
    raw_value = str(choice_entry.get("choice_value") or "").strip()
    normalized_entry["choice_value"] = normalize_evo_choice(raw_value) if raw_value else ""
    normalized_entry["choice_label"] = EVO_CHOICE_LABELS.get(normalized_entry["choice_value"], "")

    preserved_entries = [
        entry
        for entry in state.get("evo_choices") or []
        if int(entry.get("game_number") or 0) != normalized_entry["game_number"]
    ]
    preserved_entries.append(normalized_entry)
    state["evo_choices"] = sorted(preserved_entries, key=lambda item: int(item.get("game_number") or 0))
    return normalized_entry


def swiss_match_is_decider(
    threshold: int,
    stats: dict[int, dict[str, Any]],
    player1_id: int | None,
    player2_id: int | None,
) -> bool:
    player_ids = [registration_id for registration_id in (player1_id, player2_id) if registration_id is not None]
    if len(player_ids) < 2:
        return False
    return any(
        stats.get(registration_id, {}).get("wins") == threshold - 1
        or stats.get(registration_id, {}).get("losses") == threshold - 1
        for registration_id in player_ids
    )


def swiss_match_best_of(
    tournament: sqlite3.Row,
    threshold: int,
    stats: dict[int, dict[str, Any]],
    player1_id: int | None,
    player2_id: int | None,
) -> int:
    regular_best_of, decider_best_of = swiss_best_of_settings_for_tournament(tournament)
    return decider_best_of if swiss_match_is_decider(threshold, stats, player1_id, player2_id) else regular_best_of


def swiss_match_best_of_assignments(
    tournament: sqlite3.Row,
    threshold: int,
    stats: dict[int, dict[str, Any]],
    pairs: list[tuple[tuple[int | None, str] | int | None, tuple[int | None, str] | int | None]],
) -> list[int]:
    regular_best_of, _ = swiss_best_of_settings_for_tournament(tournament)
    assignments: list[int] = []
    for raw_pair in pairs:
        player1_id, player1_slot_type = normalize_match_slot(raw_pair[0])
        player2_id, player2_slot_type = normalize_match_slot(raw_pair[1])
        if player1_slot_type != MATCH_SLOT_PLAYER or player2_slot_type != MATCH_SLOT_PLAYER:
            assignments.append(regular_best_of)
            continue
        assignments.append(swiss_match_best_of(tournament, threshold, stats, player1_id, player2_id))
    return assignments


def swiss_match_modes_by_id(
    registrations: list[sqlite3.Row],
    swiss_match_rows: list[sqlite3.Row],
) -> dict[int, str]:
    if not registrations:
        return {}

    threshold = swiss_advancement_threshold(len(registrations))
    modes: dict[int, str] = {}
    processed_rows: list[sqlite3.Row] = []
    grouped_rows: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in swiss_match_rows:
        grouped_rows[int(row["round_number"])].append(row)

    for round_number in sorted(grouped_rows):
        progress = swiss_group_progress(registrations, processed_rows)
        stats = progress["stats"]
        for row in grouped_rows[round_number]:
            modes[int(row["id"])] = (
                "swiss_decider"
                if swiss_match_is_decider(
                    threshold,
                    stats,
                    row["player1_registration_id"],
                    row["player2_registration_id"],
                )
                else "swiss_regular"
            )
        processed_rows.extend(grouped_rows[round_number])
    return modes


def commander_draft_settings_for_tournament(tournament: sqlite3.Row) -> dict[str, str]:
    shared_pool_value = tournament["commander_shared_pool"]
    shared_pool_enabled = True if shared_pool_value is None else bool(shared_pool_value)
    return {
        "pool_mode": normalize_commander_pool_mode(
            COMMANDER_POOL_SHARED if shared_pool_enabled else COMMANDER_POOL_SEPARATE
        ),
        "ban_mode": normalize_commander_ban_mode(tournament["commander_ban_mode"]),
    }


def load_match_preparation_row(
    connection: sqlite3.Connection,
    match_row: sqlite3.Row,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT *
        FROM tournament_match_preparations
        WHERE tournament_id = ? AND bracket_type = ? AND round_number = ? AND match_number = ?
        """,
        match_preparation_identity(match_row),
    ).fetchone()


def load_match_preparation_state(
    connection: sqlite3.Connection,
    match_row: sqlite3.Row,
) -> dict[str, Any] | None:
    row = load_match_preparation_row(connection, match_row)
    if row is None:
        return None

    try:
        state = json.loads(row["state_json"])
    except json.JSONDecodeError:
        return None
    if not isinstance(state, dict):
        return None

    state["bp_mode"] = normalize_bp_mode(row["bp_mode"])
    state.setdefault("status", "awaiting_ready")
    state.setdefault("ready_registration_ids", [])
    state.setdefault("advance_ready_ids", [])
    state.setdefault("current_game_number", 1)
    state.setdefault("current_game_ready_at", None)
    state.setdefault("step_started_at", None)
    state.setdefault("map_bp", None)
    state.setdefault("commander_games", [])
    state.setdefault("conquest_series", None)
    state.setdefault("ladder_games", [])
    state.setdefault("evo_choices", [])
    state.setdefault("game_results", [])
    state.setdefault("pending_manual_replay_judgment", None)
    state.setdefault("pending_admin_replay_judgment", None)
    return state


def registration_game_nickname(
    registration_row: sqlite3.Row | None,
    connection: sqlite3.Connection | None = None,
) -> str | None:
    if registration_row is None:
        return None
    user_id = registration_row["user_id"] if "user_id" in registration_row.keys() else None
    if connection is not None and user_id is not None:
        user_row = load_user_row(connection, int(user_id))
        nickname = current_user_game_nickname(user_row)
        if nickname is not None:
            return nickname
    if "account_game_nickname" in registration_row.keys():
        return normalize_game_nickname(registration_row["account_game_nickname"])
    if "game_nickname" in registration_row.keys():
        return normalize_game_nickname(registration_row["game_nickname"])
    return None


def replay_match_validation_error(
    connection: sqlite3.Connection,
    match_row: sqlite3.Row,
    registration_map: dict[int, sqlite3.Row],
) -> str | None:
    player_ids = match_preparation_player_ids(match_row)
    if len(player_ids) != 2:
        return "当前对局尚未确定两名正式选手，暂时不能上传 replay。"

    participants = [registration_map.get(registration_id) for registration_id in player_ids]
    if any(participant is None or participant["is_temporary"] for participant in participants):
        return "当前对局存在临时选手，暂时不能上传 replay。"
    return None


def replay_storage_path(
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
    registration_map: dict[int, sqlite3.Row],
    game_number: int,
) -> Path:
    player1 = registration_map.get(match_row["player1_registration_id"])
    player2 = registration_map.get(match_row["player2_registration_id"])
    player1_name = sanitize_path_component(slot_display_name(player1, MATCH_SLOT_PLAYER), "选手1")
    player2_name = sanitize_path_component(slot_display_name(player2, MATCH_SLOT_PLAYER), "选手2")
    tournament_dir = sanitize_path_component(tournament["name"], f"tournament-{tournament['id']}")
    matchup_dir = f"{player1_name} vs {player2_name}"
    return (
        REPLAY_SAVE_DIR
        / tournament_dir
        / f"第{int(match_row['round_number'])}轮"
        / matchup_dir
        / f"第{int(game_number)}场.SC2Replay"
    )


def replay_match_directory(
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
    registration_map: dict[int, sqlite3.Row],
) -> Path:
    player1 = registration_map.get(match_row["player1_registration_id"])
    player2 = registration_map.get(match_row["player2_registration_id"])
    player1_name = sanitize_path_component(slot_display_name(player1, MATCH_SLOT_PLAYER), "选手1")
    player2_name = sanitize_path_component(slot_display_name(player2, MATCH_SLOT_PLAYER), "选手2")
    tournament_dir = sanitize_path_component(tournament["name"], f"tournament-{tournament['id']}")
    matchup_dir = f"{player1_name} vs {player2_name}"
    return REPLAY_SAVE_DIR / tournament_dir / f"第{int(match_row['round_number'])}轮" / matchup_dir


def tournament_replay_directory(tournament: sqlite3.Row) -> Path:
    tournament_dir = sanitize_path_component(tournament["name"], f"tournament-{tournament['id']}")
    return REPLAY_SAVE_DIR / tournament_dir


def tournament_replay_archive_path(tournament: sqlite3.Row) -> Path:
    tournament_dir = sanitize_path_component(tournament["name"], f"tournament-{tournament['id']}")
    return REPLAY_SAVE_DIR / f"{tournament_dir}.tar.gz"


def tournament_replay_files(tournament: sqlite3.Row) -> list[Path]:
    replay_directory = tournament_replay_directory(tournament)
    if not replay_directory.exists():
        return []
    return sorted(
        [path for path in replay_directory.rglob("*.SC2Replay") if path.is_file()],
        key=lambda path: str(path.relative_to(replay_directory)).replace("\\", "/"),
    )


def tournament_replays_ready_for_download(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> bool:
    tournament = load_tournament_row(connection, tournament_id)
    if tournament is None:
        return False
    return tournament_is_finished(connection, tournament)


def delete_tournament_record(connection: sqlite3.Connection, tournament: sqlite3.Row) -> None:
    replay_archive_path = tournament_replay_archive_path(tournament)
    replay_directory = tournament_replay_directory(tournament)
    if replay_archive_path.exists():
        replay_archive_path.unlink()
    if replay_directory.exists():
        shutil.rmtree(replay_directory)
    connection.execute("DELETE FROM tournaments WHERE id = ?", (tournament["id"],))


LADDER_MODE_LABELS = {
    LADDER_MODE_1V1: "1v1",
    LADDER_MODE_2V2: "2v2",
}

LADDER_STATE_LABELS = {
    LADDER_MATCH_STATE_PENDING_ACCEPT: "等待确认",
    LADDER_MATCH_STATE_BP_BAN: "Ban 阶段",
    LADDER_MATCH_STATE_BP_PICK: "Pick 阶段",
    LADDER_MATCH_STATE_TEAM_BAN: "团队 Ban 阶段",
    LADDER_MATCH_STATE_TEAM_PICK: "团队 Pick 阶段",
    LADDER_MATCH_STATE_READY: "可开始比赛",
    LADDER_MATCH_STATE_ADMIN_REVIEW: "等待 sudo 裁定",
    LADDER_MATCH_STATE_RESOLVED: "已结算",
}


def parse_timestamp_text(value: str | None) -> datetime | None:
    candidate = (value or "").strip()
    if not candidate:
        return None
    if candidate.endswith("UTC+8"):
        candidate = candidate[: -len("UTC+8")].strip()
    try:
        return datetime.strptime(candidate, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def format_utc_storage_timestamp_as_utc8(value: str | None) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return ""
    if candidate.endswith("UTC+8"):
        return candidate
    parsed = parse_timestamp_text(candidate)
    if parsed is None:
        return candidate
    return f"{(parsed + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')} UTC+8"


def timestamp_text_after(seconds: int) -> str:
    return (datetime.utcnow() + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


def ladder_countdown_seconds(deadline_at: str | None) -> int:
    parsed = parse_timestamp_text(deadline_at)
    if parsed is None:
        return 0
    return max(0, int((parsed - datetime.utcnow()).total_seconds()))


def normalize_ladder_mode(value: str | None) -> str:
    return value if value in {LADDER_MODE_1V1, LADDER_MODE_2V2} else LADDER_MODE_1V1


def normalize_ladder_chat_text(value: Any) -> str:
    candidate = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    candidate = re.sub(r"\s+", " ", candidate)
    return candidate[:200]


def load_user_row(connection: sqlite3.Connection, user_id: int | None) -> sqlite3.Row | None:
    if user_id is None:
        return None
    return connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def ladder_pair_user_ids(user1_id: int, user2_id: int) -> tuple[int, int]:
    return tuple(sorted((int(user1_id), int(user2_id))))


def ladder_user_display_name(user_row: sqlite3.Row | None) -> str:
    if user_row is None:
        return "待定"
    game_nick = current_user_game_nickname(user_row)
    if game_nick:
        return game_nick
    return str(user_row["username"])


def ladder_touch_presence(connection: sqlite3.Connection, user_id: int) -> None:
    current_time = now_text()
    connection.execute(
        """
        INSERT INTO ladder_online_presence (user_id, last_seen_at, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET last_seen_at = excluded.last_seen_at, updated_at = excluded.updated_at
        """,
        (user_id, current_time, current_time, current_time),
    )


def ladder_clear_presence(connection: sqlite3.Connection, user_id: int) -> None:
    connection.execute("DELETE FROM ladder_online_presence WHERE user_id = ?", (user_id,))


def ladder_cleanup_presence(connection: sqlite3.Connection) -> None:
    cutoff = (datetime.utcnow() - timedelta(seconds=LADDER_ONLINE_TTL_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")
    connection.execute("DELETE FROM ladder_online_presence WHERE last_seen_at < ?", (cutoff,))


def ladder_online_user_cards(connection: sqlite3.Connection, limit: int = 24) -> list[dict[str, Any]]:
    queue_rows = connection.execute("SELECT * FROM ladder_queue_entries ORDER BY joined_at ASC, id ASC").fetchall()
    placeholders = ", ".join("?" for _ in ladder_active_play_states())
    active_match_rows = connection.execute(
        f"SELECT * FROM ladder_matches WHERE state IN ({placeholders}) ORDER BY updated_at DESC, id DESC",
        ladder_active_play_states(),
    ).fetchall()
    queue_by_user_id: dict[int, sqlite3.Row] = {}
    match_by_user_id: dict[int, sqlite3.Row] = {}

    for queue_row in queue_rows:
        for user_id in ladder_queue_user_ids(queue_row):
            queue_by_user_id[int(user_id)] = queue_row

    for match_row in active_match_rows:
        for user_id in ladder_match_user_ids(match_row):
            match_by_user_id[int(user_id)] = match_row

    online_rows = connection.execute(
        """
        SELECT ladder_online_presence.user_id, ladder_online_presence.last_seen_at, users.username, users.game_nickname
        FROM ladder_online_presence
        JOIN users ON users.id = ladder_online_presence.user_id
        ORDER BY ladder_online_presence.last_seen_at DESC, users.username COLLATE NOCASE ASC
        LIMIT ?
        """,
        (max(1, limit),),
    ).fetchall()

    cards: list[dict[str, Any]] = []
    for row in online_rows:
        user_id = int(row["user_id"])
        match_row = match_by_user_id.get(user_id)
        queue_row = queue_by_user_id.get(user_id)
        if match_row is not None:
            status_label = f"比赛中 · {LADDER_MODE_LABELS.get(match_row['mode'], match_row['mode'])} #{int(match_row['id'])}"
        elif queue_row is not None:
            status_label = f"匹配中 · {LADDER_MODE_LABELS.get(queue_row['mode'], queue_row['mode'])}"
        else:
            status_label = "大厅中"
        cards.append(
            {
                "username": str(row["username"]),
                "game_nickname": str(row["game_nickname"] or "").strip(),
                "status_label": status_label,
                "last_seen_at": format_utc_storage_timestamp_as_utc8(str(row["last_seen_at"])),
            }
        )
    return cards


def ladder_lobby_stats(connection: sqlite3.Connection) -> dict[str, Any]:
    ladder_cleanup_presence(connection)
    queue_row = connection.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN mode = ? THEN CASE WHEN teammate_user_id IS NULL THEN 1 ELSE 2 END ELSE 0 END), 0) AS queue_1v1_players,
            COALESCE(SUM(CASE WHEN mode = ? THEN CASE WHEN teammate_user_id IS NULL THEN 1 ELSE 2 END ELSE 0 END), 0) AS queue_2v2_players
        FROM ladder_queue_entries
        """,
        (LADDER_MODE_1V1, LADDER_MODE_2V2),
    ).fetchone()
    placeholders = ", ".join("?" for _ in ladder_active_play_states())
    active_matches = connection.execute(
        f"SELECT COUNT(*) AS total FROM ladder_matches WHERE state IN ({placeholders})",
        ladder_active_play_states(),
    ).fetchone()
    online_count = connection.execute("SELECT COUNT(*) AS total FROM ladder_online_presence").fetchone()
    return {
        "online_count": int((online_count["total"] if online_count is not None else 0) or 0),
        "queue_1v1_players": int((queue_row["queue_1v1_players"] if queue_row is not None else 0) or 0),
        "queue_2v2_players": int((queue_row["queue_2v2_players"] if queue_row is not None else 0) or 0),
        "active_matches": int((active_matches["total"] if active_matches is not None else 0) or 0),
        "online_users": ladder_online_user_cards(connection),
    }


def ladder_chat_message_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "time": format_utc_storage_timestamp_as_utc8(str(row["created_at"])),
        "sender": str(row["game_nickname"] or row["username"]),
        "text": str(row["message_text"]),
    }


def ladder_chat_messages(connection: sqlite3.Connection, match_id: int, limit: int = LADDER_CHAT_HISTORY_LIMIT) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT ladder_chat_messages.id, ladder_chat_messages.created_at, ladder_chat_messages.message_text, users.username, users.game_nickname
        FROM ladder_chat_messages
        JOIN users ON users.id = ladder_chat_messages.user_id
        WHERE ladder_chat_messages.match_id = ?
        ORDER BY ladder_chat_messages.id DESC
        LIMIT ?
        """,
        (match_id, max(1, limit)),
    ).fetchall()
    return [ladder_chat_message_payload(row) for row in reversed(rows)]


def ladder_lobby_chat_messages(connection: sqlite3.Connection, limit: int = LADDER_CHAT_HISTORY_LIMIT) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT ladder_lobby_chat_messages.id, ladder_lobby_chat_messages.created_at, ladder_lobby_chat_messages.message_text, users.username, users.game_nickname
        FROM ladder_lobby_chat_messages
        JOIN users ON users.id = ladder_lobby_chat_messages.user_id
        ORDER BY ladder_lobby_chat_messages.id DESC
        LIMIT ?
        """,
        (max(1, limit),),
    ).fetchall()
    return [ladder_chat_message_payload(row) for row in reversed(rows)]


def ladder_append_chat_message(connection: sqlite3.Connection, match_id: int, user_id: int, message_text: str) -> dict[str, Any]:
    current_time = now_text()
    cursor = connection.execute(
        """
        INSERT INTO ladder_chat_messages (match_id, user_id, message_text, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (match_id, user_id, message_text, current_time, current_time),
    )
    connection.execute(
        """
        DELETE FROM ladder_chat_messages
        WHERE match_id = ?
          AND id NOT IN (
              SELECT id
              FROM ladder_chat_messages
              WHERE match_id = ?
              ORDER BY id DESC
              LIMIT ?
          )
        """,
        (match_id, match_id, LADDER_CHAT_HISTORY_LIMIT),
    )
    row = connection.execute(
        """
        SELECT ladder_chat_messages.id, ladder_chat_messages.created_at, ladder_chat_messages.message_text, users.username
        FROM ladder_chat_messages
        JOIN users ON users.id = ladder_chat_messages.user_id
        WHERE ladder_chat_messages.id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()
    if row is None:
        raise RuntimeError("聊天消息保存失败。")
    return ladder_chat_message_payload(row)


def ladder_append_lobby_chat_message(connection: sqlite3.Connection, user_id: int, message_text: str) -> dict[str, Any]:
    current_time = now_text()
    cursor = connection.execute(
        """
        INSERT INTO ladder_lobby_chat_messages (user_id, message_text, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, message_text, current_time, current_time),
    )
    connection.execute(
        """
        DELETE FROM ladder_lobby_chat_messages
        WHERE id NOT IN (
            SELECT id
            FROM ladder_lobby_chat_messages
            ORDER BY id DESC
            LIMIT ?
        )
        """,
        (LADDER_CHAT_HISTORY_LIMIT,),
    )
    row = connection.execute(
        """
        SELECT ladder_lobby_chat_messages.id, ladder_lobby_chat_messages.created_at, ladder_lobby_chat_messages.message_text, users.username
        FROM ladder_lobby_chat_messages
        JOIN users ON users.id = ladder_lobby_chat_messages.user_id
        WHERE ladder_lobby_chat_messages.id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()
    if row is None:
        raise RuntimeError("大厅聊天消息保存失败。")
    return ladder_chat_message_payload(row)


def ladder_team_display_name(user_rows: list[sqlite3.Row | None]) -> str:
    names = [ladder_user_display_name(row) for row in user_rows if row is not None]
    return " / ".join(names) if names else "待定"


def normalize_ladder_commanders(values: Any, limit: int) -> list[str]:
    if isinstance(values, str):
        raw_values = [values]
    elif isinstance(values, (list, tuple, set)):
        raw_values = [str(item) for item in values]
    else:
        raw_values = []

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        candidate = str(item).strip()
        if candidate not in LADDER_COMMANDER_POOL:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        cleaned.append(candidate)
        if len(cleaned) >= limit:
            break
    return cleaned


def ladder_queue_user_ids(queue_row: sqlite3.Row) -> list[int]:
    user_ids = [int(queue_row["captain_user_id"])]
    teammate_user_id = queue_row["teammate_user_id"]
    if teammate_user_id is not None:
        user_ids.append(int(teammate_user_id))
    return user_ids


def ladder_active_play_states() -> tuple[str, ...]:
    return (
        LADDER_MATCH_STATE_PENDING_ACCEPT,
        LADDER_MATCH_STATE_BP_BAN,
        LADDER_MATCH_STATE_BP_PICK,
        LADDER_MATCH_STATE_TEAM_BAN,
        LADDER_MATCH_STATE_TEAM_PICK,
        LADDER_MATCH_STATE_READY,
    )


def ladder_reviewable_match_states() -> tuple[str, ...]:
    return (*ladder_active_play_states(), LADDER_MATCH_STATE_ADMIN_REVIEW)


def load_ladder_queue_entry_for_user(connection: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT *
        FROM ladder_queue_entries
        WHERE captain_user_id = ? OR teammate_user_id = ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (user_id, user_id),
    ).fetchone()


def load_ladder_queue_entries(connection: sqlite3.Connection, mode: str) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM ladder_queue_entries WHERE mode = ? ORDER BY joined_at ASC, id ASC",
        (mode,),
    ).fetchall()


def load_active_ladder_match_for_user(connection: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    placeholders = ", ".join("?" for _ in ladder_active_play_states())
    return connection.execute(
        f"""
        SELECT *
        FROM ladder_matches
        WHERE state IN ({placeholders})
          AND (
                team_a_user1_id = ? OR team_a_user2_id = ?
             OR team_b_user1_id = ? OR team_b_user2_id = ?
          )
        ORDER BY id DESC
        LIMIT 1
        """,
        (*ladder_active_play_states(), user_id, user_id, user_id, user_id),
    ).fetchone()


def ladder_user_is_busy(connection: sqlite3.Connection, user_id: int) -> bool:
    return load_ladder_queue_entry_for_user(connection, user_id) is not None or load_active_ladder_match_for_user(connection, user_id) is not None


def ladder_match_user_ids(match_row: sqlite3.Row) -> list[int]:
    values = [
        match_row["team_a_user1_id"],
        match_row["team_a_user2_id"],
        match_row["team_b_user1_id"],
        match_row["team_b_user2_id"],
    ]
    return [int(value) for value in values if value is not None]


def ladder_user_side(match_row: sqlite3.Row, user_id: int) -> str:
    if int(match_row["team_a_user1_id"]) == user_id or (match_row["team_a_user2_id"] is not None and int(match_row["team_a_user2_id"]) == user_id):
        return "A"
    if int(match_row["team_b_user1_id"]) == user_id or (match_row["team_b_user2_id"] is not None and int(match_row["team_b_user2_id"]) == user_id):
        return "B"
    return ""


def ladder_match_state(match_row: sqlite3.Row) -> dict[str, Any]:
    try:
        state = json.loads(match_row["state_json"])
    except (TypeError, json.JSONDecodeError):
        state = {}
    state.setdefault("bp", {"ban": {"A": None, "B": None}, "pick": {"A": None, "B": None}, "available": {"A": [], "B": []}})
    state.setdefault("teams", {})
    state.setdefault("acceptance", {"confirmed_user_ids": [], "rejected_by_user_id": None, "rejected_at": None, "confirmed_at": None})
    state.setdefault("ready_at", None)
    state.setdefault("admin_review", None)
    state.setdefault("rating_result", None)
    return state


def save_ladder_match_state(
    connection: sqlite3.Connection,
    match_row: sqlite3.Row,
    state: dict[str, Any],
    row_state: str,
    deadline_at: str | None,
    winner_side: str | None = None,
    replay_filename: str | None = None,
    replay_saved_path: str | None = None,
    uploaded_at: str | None = None,
    resolved_at: str | None = None,
) -> sqlite3.Row:
    updated_at = now_text()
    connection.execute(
        """
        UPDATE ladder_matches
        SET state = ?,
            deadline_at = ?,
            winner_side = ?,
            replay_filename = ?,
            replay_saved_path = ?,
            uploaded_at = ?,
            resolved_at = ?,
            state_json = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            row_state,
            deadline_at,
            winner_side,
            replay_filename,
            replay_saved_path,
            uploaded_at,
            resolved_at,
            json.dumps(state, ensure_ascii=False),
            updated_at,
            match_row["id"],
        ),
    )
    refreshed = connection.execute("SELECT * FROM ladder_matches WHERE id = ?", (match_row["id"],)).fetchone()
    return refreshed or match_row


def ladder_get_1v1_rating(user_row: sqlite3.Row) -> LadderRatingValue:
    return LadderRatingValue(
        rating=float(user_row["ladder_rating_1v1"] or 1500.0),
        games=int(user_row["ladder_games_1v1"] or 0),
    )


def ladder_set_1v1_rating(connection: sqlite3.Connection, user_id: int, rating_value: LadderRatingValue) -> None:
    connection.execute(
        "UPDATE users SET ladder_rating_1v1 = ?, ladder_games_1v1 = ? WHERE id = ?",
        (round(float(rating_value.rating), 2), int(rating_value.games), user_id),
    )


def ladder_get_pair_rating(connection: sqlite3.Connection, user1_id: int, user2_id: int) -> LadderRatingValue:
    left_id, right_id = ladder_pair_user_ids(user1_id, user2_id)
    row = connection.execute(
        "SELECT * FROM ladder_pair_ratings WHERE user1_id = ? AND user2_id = ?",
        (left_id, right_id),
    ).fetchone()
    if row is None:
        return LadderRatingValue(rating=1500.0, games=0)
    return LadderRatingValue(rating=float(row["rating"] or 1500.0), games=int(row["games"] or 0))


def ladder_set_pair_rating(connection: sqlite3.Connection, user1_id: int, user2_id: int, rating_value: LadderRatingValue) -> None:
    left_id, right_id = ladder_pair_user_ids(user1_id, user2_id)
    current_time = now_text()
    connection.execute(
        """
        INSERT INTO ladder_pair_ratings (user1_id, user2_id, rating, games, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user1_id, user2_id) DO UPDATE SET
            rating = excluded.rating,
            games = excluded.games,
            updated_at = excluded.updated_at
        """,
        (left_id, right_id, round(float(rating_value.rating), 2), int(rating_value.games), current_time, current_time),
    )


def ladder_1v1_leaderboard(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT username, game_nickname, ladder_rating_1v1, ladder_games_1v1 FROM users ORDER BY ladder_rating_1v1 DESC, ladder_games_1v1 DESC, username COLLATE NOCASE ASC LIMIT 50"
    ).fetchall()
    return [
        {
            "username": row["username"],
            "game_nickname": row["game_nickname"],
            "rating": round(float(row["ladder_rating_1v1"] or 1500.0), 2),
            "games": int(row["ladder_games_1v1"] or 0),
        }
        for row in rows
    ]


def ladder_2v2_leaderboard(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT ladder_pair_ratings.*, u1.username AS username1, u2.username AS username2,
               u1.game_nickname AS game_nickname1, u2.game_nickname AS game_nickname2
        FROM ladder_pair_ratings
        JOIN users AS u1 ON u1.id = ladder_pair_ratings.user1_id
        JOIN users AS u2 ON u2.id = ladder_pair_ratings.user2_id
        ORDER BY ladder_pair_ratings.rating DESC, ladder_pair_ratings.games DESC, username1 COLLATE NOCASE ASC, username2 COLLATE NOCASE ASC
        LIMIT 50
        """
    ).fetchall()
    return [
        {
            "pair_name": f"{row['game_nickname1'] or row['username1']} / {row['game_nickname2'] or row['username2']}",
            "rating": round(float(row["rating"] or 1500.0), 2),
            "games": int(row["games"] or 0),
        }
        for row in rows
    ]


def ladder_current_window(waited_seconds: float) -> int:
    steps = int(max(0.0, waited_seconds) // LADDER_MATCH_EXPAND_SECONDS)
    return min(LADDER_MATCH_BASE_WINDOW + steps * LADDER_MATCH_EXPAND_STEP, LADDER_MATCH_MAX_WINDOW)


def ladder_queue_pair_cost(left: sqlite3.Row, right: sqlite3.Row, now_value: datetime) -> float:
    left_joined = parse_timestamp_text(left["joined_at"]) or now_value
    right_joined = parse_timestamp_text(right["joined_at"]) or now_value
    wait_left = max(0.0, (now_value - left_joined).total_seconds())
    wait_right = max(0.0, (now_value - right_joined).total_seconds())
    rating_diff = abs(float(left["rating"] or 1500.0) - float(right["rating"] or 1500.0))
    wait_penalty = abs(wait_left - wait_right) * 0.2
    long_wait_bonus = min(wait_left + wait_right, 120.0) * 0.1
    return rating_diff + wait_penalty - long_wait_bonus


def ladder_pick_best_pair(entries: list[sqlite3.Row]) -> tuple[sqlite3.Row, sqlite3.Row] | None:
    if len(entries) < 2:
        return None
    now_value = datetime.utcnow()
    best_pair = None
    best_cost = float("inf")
    for index, left in enumerate(entries):
        left_joined = parse_timestamp_text(left["joined_at"]) or now_value
        wait_left = max(0.0, (now_value - left_joined).total_seconds())
        for other in entries[index + 1 :]:
            right_joined = parse_timestamp_text(other["joined_at"]) or now_value
            wait_right = max(0.0, (now_value - right_joined).total_seconds())
            rating_diff = abs(float(left["rating"] or 1500.0) - float(other["rating"] or 1500.0))
            if rating_diff > max(ladder_current_window(wait_left), ladder_current_window(wait_right)):
                continue
            cost = ladder_queue_pair_cost(left, other, now_value)
            if cost < best_cost:
                best_cost = cost
                best_pair = (left, other)
    return best_pair


def ladder_queue_commanders(queue_row: sqlite3.Row) -> list[str]:
    try:
        values = json.loads(queue_row["commanders_json"])
    except (TypeError, json.JSONDecodeError):
        values = []
    return normalize_ladder_commanders(values, 3)


def build_ladder_match_state(left: sqlite3.Row, right: sqlite3.Row) -> tuple[str, str, dict[str, Any]]:
    mode = normalize_ladder_mode(left["mode"])
    left_players = ladder_queue_user_ids(left)
    right_players = ladder_queue_user_ids(right)
    acceptance = {
        "confirmed_user_ids": [],
        "rejected_by_user_id": None,
        "rejected_at": None,
        "confirmed_at": None,
    }
    if mode == LADDER_MODE_1V1:
        return (
            LADDER_MATCH_STATE_PENDING_ACCEPT,
            random.choice(LADDER_MAP_POOL_1V1),
            {
                "teams": {
                    "A": {
                        "captain_user_id": int(left["captain_user_id"]),
                        "players": left_players,
                        "commanders": ladder_queue_commanders(left) or LADDER_COMMANDER_POOL[:3],
                    },
                    "B": {
                        "captain_user_id": int(right["captain_user_id"]),
                        "players": right_players,
                        "commanders": ladder_queue_commanders(right) or LADDER_COMMANDER_POOL[:3],
                    },
                },
                "acceptance": acceptance,
                "bp": {
                    "ban": {"A": None, "B": None},
                    "pick": {"A": None, "B": None},
                    "available": {"A": [], "B": []},
                },
                "ready_at": None,
                "rating_result": None,
            },
        )

    return (
        LADDER_MATCH_STATE_PENDING_ACCEPT,
        random.choice(LADDER_MAP_POOL_2V2),
        {
            "teams": {
                "A": {
                    "captain_user_id": int(left["captain_user_id"]),
                    "players": left_players,
                    "commanders": LADDER_COMMANDER_POOL[:],
                },
                "B": {
                    "captain_user_id": int(right["captain_user_id"]),
                    "players": right_players,
                    "commanders": LADDER_COMMANDER_POOL[:],
                },
            },
            "acceptance": acceptance,
            "bp": {
                "ban": {"A": None, "B": None},
                "pick": {"A": None, "B": None},
                "available": {"A": [], "B": []},
            },
            "ready_at": None,
            "rating_result": None,
        },
    )


def create_ladder_match(connection: sqlite3.Connection, left: sqlite3.Row, right: sqlite3.Row) -> sqlite3.Row:
    match_state, map_name, state_json = build_ladder_match_state(left, right)
    created_at = now_text()
    deadline_at = timestamp_text_after(LADDER_CONFIRM_SECONDS)
    left_players = ladder_queue_user_ids(left)
    right_players = ladder_queue_user_ids(right)
    cursor = connection.execute(
        """
        INSERT INTO ladder_matches (
            mode, state, map_name,
            team_a_captain_user_id, team_a_user1_id, team_a_user2_id,
            team_b_captain_user_id, team_b_user1_id, team_b_user2_id,
            deadline_at, state_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            normalize_ladder_mode(left["mode"]),
            match_state,
            map_name,
            int(left["captain_user_id"]),
            int(left_players[0]),
            int(left_players[1]) if len(left_players) > 1 else None,
            int(right["captain_user_id"]),
            int(right_players[0]),
            int(right_players[1]) if len(right_players) > 1 else None,
            deadline_at,
            json.dumps(state_json, ensure_ascii=False),
            created_at,
            created_at,
        ),
    )
    return connection.execute("SELECT * FROM ladder_matches WHERE id = ?", (cursor.lastrowid,)).fetchone()


def ladder_transition_to_bp_start(
    match_row: sqlite3.Row,
    state: dict[str, Any],
    bp_seconds: int = LADDER_BP_SECONDS,
) -> tuple[str, str | None, dict[str, Any]]:
    acceptance = state.setdefault("acceptance", {})
    acceptance["confirmed_at"] = now_text()
    if match_row["mode"] == LADDER_MODE_1V1:
        return LADDER_MATCH_STATE_BP_BAN, timestamp_text_after(bp_seconds), state
    return LADDER_MATCH_STATE_TEAM_BAN, timestamp_text_after(bp_seconds), state


def ladder_cancel_proposed_match(connection: sqlite3.Connection, match_row: sqlite3.Row, rejected_by_user_id: int | None = None) -> None:
    state = ladder_match_state(match_row)
    acceptance = state.setdefault("acceptance", {})
    acceptance["rejected_by_user_id"] = rejected_by_user_id
    acceptance["rejected_at"] = now_text() if rejected_by_user_id is not None else acceptance.get("rejected_at")
    connection.execute("DELETE FROM ladder_matches WHERE id = ?", (match_row["id"],))


def ladder_transition_to_pick(
    match_row: sqlite3.Row,
    state: dict[str, Any],
    bp_seconds: int = LADDER_BP_SECONDS,
) -> tuple[str, str | None, dict[str, Any]]:
    if match_row["mode"] == LADDER_MODE_1V1:
        ban_a = str(state["bp"]["ban"].get("A") or "")
        ban_b = str(state["bp"]["ban"].get("B") or "")
        pool_a = list(state["teams"]["A"].get("commanders") or LADDER_COMMANDER_POOL[:3])
        pool_b = list(state["teams"]["B"].get("commanders") or LADDER_COMMANDER_POOL[:3])
        state["bp"]["available"]["A"] = [item for item in pool_a if item != ban_b] or pool_a
        state["bp"]["available"]["B"] = [item for item in pool_b if item != ban_a] or pool_b
        return LADDER_MATCH_STATE_BP_PICK, timestamp_text_after(bp_seconds), state

    ban_a = list(state["bp"]["ban"].get("A") or [])
    ban_b = list(state["bp"]["ban"].get("B") or [])
    pool_a = list(state["teams"]["A"].get("commanders") or LADDER_COMMANDER_POOL)
    pool_b = list(state["teams"]["B"].get("commanders") or LADDER_COMMANDER_POOL)
    state["bp"]["available"]["A"] = [item for item in pool_a if item not in ban_b] or pool_a
    state["bp"]["available"]["B"] = [item for item in pool_b if item not in ban_a] or pool_b
    return LADDER_MATCH_STATE_TEAM_PICK, timestamp_text_after(bp_seconds), state


def ladder_transition_to_ready(match_row: sqlite3.Row, state: dict[str, Any], timeout: bool) -> tuple[str, str | None, dict[str, Any]]:
    if match_row["mode"] == LADDER_MODE_1V1:
        available_a = list(state["bp"]["available"].get("A") or state["teams"]["A"].get("commanders") or LADDER_COMMANDER_POOL[:3])
        available_b = list(state["bp"]["available"].get("B") or state["teams"]["B"].get("commanders") or LADDER_COMMANDER_POOL[:3])
        if not state["bp"]["pick"].get("A"):
            state["bp"]["pick"]["A"] = random.choice(available_a)
        if not state["bp"]["pick"].get("B"):
            state["bp"]["pick"]["B"] = random.choice(available_b)
    else:
        available_a = list(state["bp"]["available"].get("A") or state["teams"]["A"].get("commanders") or LADDER_COMMANDER_POOL)
        available_b = list(state["bp"]["available"].get("B") or state["teams"]["B"].get("commanders") or LADDER_COMMANDER_POOL)
        if not isinstance(state["bp"]["pick"].get("A"), list) or len(state["bp"]["pick"]["A"]) != 2:
            state["bp"]["pick"]["A"] = available_a[:2] if len(available_a) >= 2 else LADDER_COMMANDER_POOL[:2]
        if not isinstance(state["bp"]["pick"].get("B"), list) or len(state["bp"]["pick"]["B"]) != 2:
            state["bp"]["pick"]["B"] = available_b[:2] if len(available_b) >= 2 else LADDER_COMMANDER_POOL[:2]
    state["ready_at"] = current_utc8_timestamp_text()
    return LADDER_MATCH_STATE_READY, None, state


def sync_ladder_timeouts(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT * FROM ladder_matches WHERE state IN (?, ?, ?, ?, ?) AND deadline_at IS NOT NULL ORDER BY id ASC",
        (
            LADDER_MATCH_STATE_PENDING_ACCEPT,
            LADDER_MATCH_STATE_BP_BAN,
            LADDER_MATCH_STATE_TEAM_BAN,
            LADDER_MATCH_STATE_BP_PICK,
            LADDER_MATCH_STATE_TEAM_PICK,
        ),
    ).fetchall()
    now_value = datetime.utcnow()
    current_bp_seconds = ladder_bp_seconds_setting(connection)
    for match_row in rows:
        deadline = parse_timestamp_text(match_row["deadline_at"])
        if deadline is None or deadline > now_value:
            continue
        state = ladder_match_state(match_row)
        if match_row["state"] == LADDER_MATCH_STATE_PENDING_ACCEPT:
            ladder_cancel_proposed_match(connection, match_row)
        elif match_row["state"] == LADDER_MATCH_STATE_BP_BAN:
            state["bp"]["ban"]["A"] = state["bp"]["ban"].get("A") or ""
            state["bp"]["ban"]["B"] = state["bp"]["ban"].get("B") or ""
            new_state, new_deadline, state = ladder_transition_to_pick(match_row, state, current_bp_seconds)
            save_ladder_match_state(connection, match_row, state, new_state, new_deadline)
        elif match_row["state"] == LADDER_MATCH_STATE_TEAM_BAN:
            state["bp"]["ban"]["A"] = state["bp"]["ban"].get("A") or []
            state["bp"]["ban"]["B"] = state["bp"]["ban"].get("B") or []
            new_state, new_deadline, state = ladder_transition_to_pick(match_row, state, current_bp_seconds)
            save_ladder_match_state(connection, match_row, state, new_state, new_deadline)
        elif match_row["state"] in {LADDER_MATCH_STATE_BP_PICK, LADDER_MATCH_STATE_TEAM_PICK}:
            new_state, new_deadline, state = ladder_transition_to_ready(match_row, state, timeout=True)
            save_ladder_match_state(connection, match_row, state, new_state, new_deadline)


def sync_ladder_matchmaking(connection: sqlite3.Connection) -> None:
    for mode in (LADDER_MODE_1V1, LADDER_MODE_2V2):
        while True:
            queue_rows = load_ladder_queue_entries(connection, mode)
            pair = ladder_pick_best_pair(queue_rows)
            if pair is None:
                break
            left, right = pair
            create_ladder_match(connection, left, right)
            connection.execute("DELETE FROM ladder_queue_entries WHERE id IN (?, ?)", (left["id"], right["id"]))


def sync_ladder_state(connection: sqlite3.Connection) -> None:
    sync_ladder_timeouts(connection)
    sync_ladder_matchmaking(connection)


def ladder_match_sync_key(connection: sqlite3.Connection, user_id: int) -> str:
    queue_row = load_ladder_queue_entry_for_user(connection, user_id)
    match_row = load_active_ladder_match_for_user(connection, user_id)
    payload = {
        "queue": {
            "id": int(queue_row["id"]),
            "mode": queue_row["mode"],
            "updated_at": queue_row["updated_at"],
        }
        if queue_row is not None
        else None,
        "match": {
            "id": int(match_row["id"]),
            "state": match_row["state"],
            "updated_at": match_row["updated_at"],
            "winner_side": match_row["winner_side"],
            "state_json": match_row["state_json"],
        }
        if match_row is not None
        else None,
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def cleanup_expired_ladder_replays() -> None:
    if not RANK_SAVE_DIR.exists():
        return

    cutoff_time = datetime.utcnow() - timedelta(days=LADDER_REPLAY_RETENTION_DAYS)
    for candidate in sorted(RANK_SAVE_DIR.rglob("*"), key=lambda path: len(path.parts), reverse=True):
        if candidate.is_file() and candidate.suffix.lower() == ".sc2replay":
            modified_at = datetime.utcfromtimestamp(candidate.stat().st_mtime)
            if modified_at < cutoff_time:
                candidate.unlink(missing_ok=True)
                continue
        if candidate.is_dir() and not any(candidate.iterdir()):
            candidate.rmdir()


def ladder_replay_participant_name(user_row: sqlite3.Row | None) -> str:
    if user_row is None:
        return "未知玩家"
    return current_user_game_nickname(user_row) or str(user_row["username"])


def ladder_replay_participant_rows(connection: sqlite3.Connection, match_row: sqlite3.Row) -> list[sqlite3.Row]:
    user_ids = [
        int(match_row["team_a_user1_id"]),
        int(match_row["team_b_user1_id"]),
    ]
    if match_row["team_a_user2_id"] is not None:
        user_ids.append(int(match_row["team_a_user2_id"]))
    if match_row["team_b_user2_id"] is not None:
        user_ids.append(int(match_row["team_b_user2_id"]))

    rows: list[sqlite3.Row] = []
    seen: set[int] = set()
    for user_id in user_ids:
        if user_id in seen:
            continue
        seen.add(user_id)
        user_row = load_user_row(connection, user_id)
        if user_row is not None:
            rows.append(user_row)
    return rows


def ladder_replay_user_directory(user_row: sqlite3.Row) -> Path:
    return RANK_SAVE_DIR / sanitize_path_component(str(user_row["username"]), f"user-{int(user_row['id'])}")


def ladder_replay_commander_label(value: Any) -> str:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return " / ".join(items)
    return str(value or "").strip()


def ladder_replay_side_descriptor(user_rows: list[sqlite3.Row | None], picked_value: Any) -> str:
    names = [ladder_replay_participant_name(row) for row in user_rows if row is not None]
    side_name = " + ".join(names) if names else "未知队伍"
    commander_label = ladder_replay_commander_label(picked_value)
    if commander_label:
        return f"{side_name}({commander_label})"
    return side_name


def ladder_replay_winner_label(team_a_rows: list[sqlite3.Row | None], team_b_rows: list[sqlite3.Row | None], winner_side: str) -> str:
    winner_rows = team_a_rows if winner_side == "A" else team_b_rows
    names = [ladder_replay_participant_name(row) for row in winner_rows if row is not None]
    return " + ".join(names) if names else "未知胜者"


def ladder_replay_timestamp_label(replay_result: dict[str, Any]) -> str:
    replay_time = replay_datetime_value(replay_result.get("end_time_dt") or replay_result.get("end_time"))
    if replay_time is None:
        replay_time = replay_datetime_value(replay_result.get("start_time_dt") or replay_result.get("start_time"))
    if replay_time is None:
        replay_time = current_utc8_datetime()
    return f"UTC+8 {replay_time.strftime('%Y-%m-%d %H-%M-%S')}"


def ladder_rank_replay_filename(
    match_row: sqlite3.Row,
    state: dict[str, Any],
    replay_result: dict[str, Any],
    winner_side: str,
    team_a_rows: list[sqlite3.Row | None],
    team_b_rows: list[sqlite3.Row | None],
    original_filename: str,
) -> str:
    suffix = Path(original_filename).suffix or ".SC2Replay"
    team_a_label = ladder_replay_side_descriptor(team_a_rows, state.get("bp", {}).get("pick", {}).get("A"))
    team_b_label = ladder_replay_side_descriptor(team_b_rows, state.get("bp", {}).get("pick", {}).get("B"))
    winner_label = ladder_replay_winner_label(team_a_rows, team_b_rows, winner_side)
    timestamp_label = ladder_replay_timestamp_label(replay_result)
    return (
        f"{sanitize_path_component(team_a_label, 'teamA')} vs "
        f"{sanitize_path_component(team_b_label, 'teamB')} - "
        f"{sanitize_path_component(winner_label, 'winner')} win - "
        f"{sanitize_path_component(timestamp_label, 'UTC+8')}{suffix}"
    )


def ladder_rank_replay_paths(
    connection: sqlite3.Connection,
    match_row: sqlite3.Row,
    state: dict[str, Any],
    replay_result: dict[str, Any],
    winner_side: str,
    team_a_rows: list[sqlite3.Row | None],
    team_b_rows: list[sqlite3.Row | None],
    original_filename: str,
) -> list[Path]:
    filename = ladder_rank_replay_filename(match_row, state, replay_result, winner_side, team_a_rows, team_b_rows, original_filename)
    return [ladder_replay_user_directory(user_row) / filename for user_row in ladder_replay_participant_rows(connection, match_row)]


def ladder_apply_rating_for_match(connection: sqlite3.Connection, match_row: sqlite3.Row, winner_side: str) -> dict[str, Any]:
    if match_row["mode"] == LADDER_MODE_1V1:
        user_a = load_user_row(connection, int(match_row["team_a_user1_id"]))
        user_b = load_user_row(connection, int(match_row["team_b_user1_id"]))
        if user_a is None or user_b is None:
            return {}
        rating_a = ladder_get_1v1_rating(user_a)
        rating_b = ladder_get_1v1_rating(user_b)
        score_a = 1.0 if winner_side == "A" else 0.0
        delta_a, delta_b = ladder_update_elo(rating_a, rating_b, score_a)
        ladder_set_1v1_rating(connection, int(user_a["id"]), rating_a)
        ladder_set_1v1_rating(connection, int(user_b["id"]), rating_b)
        return {
            "mode": LADDER_MODE_1V1,
            "A": {"label": user_a["username"], "delta": round(delta_a, 2), "new_rating": round(rating_a.rating, 2)},
            "B": {"label": user_b["username"], "delta": round(delta_b, 2), "new_rating": round(rating_b.rating, 2)},
        }

    pair_a = ladder_pair_user_ids(int(match_row["team_a_user1_id"]), int(match_row["team_a_user2_id"]))
    pair_b = ladder_pair_user_ids(int(match_row["team_b_user1_id"]), int(match_row["team_b_user2_id"]))
    rating_a = ladder_get_pair_rating(connection, pair_a[0], pair_a[1])
    rating_b = ladder_get_pair_rating(connection, pair_b[0], pair_b[1])
    score_a = 1.0 if winner_side == "A" else 0.0
    delta_a, delta_b = ladder_update_elo(rating_a, rating_b, score_a)
    ladder_set_pair_rating(connection, pair_a[0], pair_a[1], rating_a)
    ladder_set_pair_rating(connection, pair_b[0], pair_b[1], rating_b)
    user_a_1 = load_user_row(connection, pair_a[0])
    user_a_2 = load_user_row(connection, pair_a[1])
    user_b_1 = load_user_row(connection, pair_b[0])
    user_b_2 = load_user_row(connection, pair_b[1])
    return {
        "mode": LADDER_MODE_2V2,
        "A": {"label": ladder_team_display_name([user_a_1, user_a_2]), "delta": round(delta_a, 2), "new_rating": round(rating_a.rating, 2)},
        "B": {"label": ladder_team_display_name([user_b_1, user_b_2]), "delta": round(delta_b, 2), "new_rating": round(rating_b.rating, 2)},
    }


def ladder_resolve_match(
    connection: sqlite3.Connection,
    match_row: sqlite3.Row,
    winner_side: str,
    replay_filename: str | None,
    replay_saved_path: str | None,
    uploaded_at: str,
) -> sqlite3.Row:
    state = ladder_match_state(match_row)
    state["rating_result"] = ladder_apply_rating_for_match(connection, match_row, winner_side)
    admin_review = state.get("admin_review")
    if isinstance(admin_review, dict):
        admin_review["resolved_by_admin"] = True
        admin_review["resolved_at"] = uploaded_at
    return save_ladder_match_state(
        connection,
        match_row,
        state,
        LADDER_MATCH_STATE_RESOLVED,
        None,
        winner_side=winner_side,
        replay_filename=replay_filename,
        replay_saved_path=replay_saved_path,
        uploaded_at=uploaded_at,
        resolved_at=uploaded_at,
    )


def load_ladder_admin_review_matches(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM ladder_matches WHERE state = ? ORDER BY updated_at DESC, id DESC",
        (LADDER_MATCH_STATE_ADMIN_REVIEW,),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        state = ladder_match_state(row)
        team_a_rows = [load_user_row(connection, int(row["team_a_user1_id"])), load_user_row(connection, int(row["team_a_user2_id"])) if row["team_a_user2_id"] is not None else None]
        team_b_rows = [load_user_row(connection, int(row["team_b_user1_id"])), load_user_row(connection, int(row["team_b_user2_id"])) if row["team_b_user2_id"] is not None else None]
        admin_review = state.get("admin_review") if isinstance(state.get("admin_review"), dict) else {}
        items.append(
            {
                "id": int(row["id"]),
                "mode_label": LADDER_MODE_LABELS.get(row["mode"], row["mode"]),
                "map_name": str(row["map_name"]),
                "team_a_label": ladder_team_display_name(team_a_rows),
                "team_b_label": ladder_team_display_name(team_b_rows),
                "requested_by": str(admin_review.get("requested_by_username") or "未知用户"),
                "requested_at": format_utc_storage_timestamp_as_utc8(str(admin_review.get("requested_at") or "")),
                "reason": str(admin_review.get("reason") or "玩家申请管理员介入。"),
                "replay_filename": str(row["replay_filename"] or ""),
                "replay_saved_path": str(row["replay_saved_path"] or ""),
            }
        )
    return items


def ladder_match_view(connection: sqlite3.Connection, match_row: sqlite3.Row, viewer: sqlite3.Row) -> dict[str, Any]:
    state = ladder_match_state(match_row)
    my_side = ladder_user_side(match_row, int(viewer["id"]))
    team_a_rows = [load_user_row(connection, int(match_row["team_a_user1_id"])), load_user_row(connection, int(match_row["team_a_user2_id"])) if match_row["team_a_user2_id"] is not None else None]
    team_b_rows = [load_user_row(connection, int(match_row["team_b_user1_id"])), load_user_row(connection, int(match_row["team_b_user2_id"])) if match_row["team_b_user2_id"] is not None else None]
    can_submit = False
    bp_choices: list[dict[str, Any]] = []
    bp_selection_mode = "single"
    bp_action_label = "提交"
    bp_help = "当前无可操作项。"
    acceptance = state.get("acceptance") if isinstance(state.get("acceptance"), dict) else {}
    confirmed_user_ids = {int(value) for value in acceptance.get("confirmed_user_ids") or [] if str(value).isdigit()}
    participant_rows = [row for row in [*team_a_rows, *team_b_rows] if row is not None]
    participant_ids = [int(row["id"]) for row in participant_rows]
    my_confirmed = int(viewer["id"]) in confirmed_user_ids

    if match_row["state"] == LADDER_MATCH_STATE_PENDING_ACCEPT:
        bp_help = "匹配成功后，需要所有参赛者先确认比赛，确认完成后才会进入 BP。"
    elif match_row["state"] == LADDER_MATCH_STATE_ADMIN_REVIEW:
        bp_help = "本局已提交到 sudo 后台裁定，当前不会再阻塞你的下一次匹配。"
    elif match_row["mode"] == LADDER_MODE_1V1 and my_side:
        if match_row["state"] == LADDER_MATCH_STATE_BP_BAN and state["bp"]["ban"].get(my_side) is None:
            opponent_side = "B" if my_side == "A" else "A"
            bp_choices = [{"value": "", "label": "不禁用"}] + [
                {"value": commander, "label": commander}
                for commander in state["teams"][opponent_side].get("commanders") or []
            ]
            can_submit = True
            bp_help = "请选择要禁用的对方指挥官，可留空表示不禁用。"
            bp_action_label = "提交 Ban"
        elif match_row["state"] == LADDER_MATCH_STATE_BP_PICK and state["bp"]["pick"].get(my_side) is None:
            available = list(state["bp"]["available"].get(my_side) or [])
            bp_choices = [{"value": commander, "label": commander} for commander in available]
            bp_choices.append({"value": "random", "label": "随机"})
            can_submit = True
            bp_help = "请选择本局出战指挥官。"
            bp_action_label = "提交 Pick"
    elif match_row["mode"] == LADDER_MODE_2V2 and my_side:
        captain_user_id = int(state["teams"][my_side].get("captain_user_id") or 0)
        if captain_user_id == int(viewer["id"]):
            if match_row["state"] == LADDER_MATCH_STATE_TEAM_BAN and state["bp"]["ban"].get(my_side) is None:
                opponent_side = "B" if my_side == "A" else "A"
                bp_selection_mode = "multi"
                bp_choices = [{"value": commander, "label": commander} for commander in state["teams"][opponent_side].get("commanders") or []]
                can_submit = True
                bp_help = "最多禁用 3 位对方指挥官。"
                bp_action_label = "提交团队 Ban"
            elif match_row["state"] == LADDER_MATCH_STATE_TEAM_PICK and state["bp"]["pick"].get(my_side) is None:
                bp_selection_mode = "multi"
                bp_choices = [{"value": commander, "label": commander} for commander in state["bp"]["available"].get(my_side) or []]
                can_submit = True
                bp_help = "请选择 2 位己方指挥官。"
                bp_action_label = "提交团队 Pick"
        else:
            bp_help = f"当前为 {ladder_user_display_name(load_user_row(connection, captain_user_id))} 的队长操作阶段。"

    return {
        "id": int(match_row["id"]),
        "serial_number": int(match_row["id"]),
        "mode": match_row["mode"],
        "mode_label": LADDER_MODE_LABELS.get(match_row["mode"], match_row["mode"]),
        "state": match_row["state"],
        "state_label": LADDER_STATE_LABELS.get(match_row["state"], match_row["state"]),
        "countdown_seconds": ladder_countdown_seconds(match_row["deadline_at"]),
        "map_name": match_row["map_name"],
        "my_side": my_side,
        "team_a": {"label": ladder_team_display_name(team_a_rows), "players": [ladder_user_display_name(row) for row in team_a_rows if row is not None]},
        "team_b": {"label": ladder_team_display_name(team_b_rows), "players": [ladder_user_display_name(row) for row in team_b_rows if row is not None]},
        "bp": state["bp"],
        "bp_choices": bp_choices,
        "bp_selection_mode": bp_selection_mode,
        "bp_action_label": bp_action_label,
        "bp_help": bp_help,
        "can_submit_bp": can_submit,
        "ready_to_upload": match_row["state"] == LADDER_MATCH_STATE_READY,
        "can_request_admin_review": match_row["state"] == LADDER_MATCH_STATE_READY,
        "acceptance": {
            "confirmed_count": len(confirmed_user_ids),
            "total_count": len(participant_ids),
            "confirmed_players": [ladder_user_display_name(row) for row in participant_rows if int(row["id"]) in confirmed_user_ids],
            "pending_players": [ladder_user_display_name(row) for row in participant_rows if int(row["id"]) not in confirmed_user_ids],
            "my_confirmed": my_confirmed,
            "can_accept": match_row["state"] == LADDER_MATCH_STATE_PENDING_ACCEPT and not my_confirmed,
            "can_reject": match_row["state"] == LADDER_MATCH_STATE_PENDING_ACCEPT,
        },
    }


def ladder_queue_view(connection: sqlite3.Connection, queue_row: sqlite3.Row | None) -> dict[str, Any]:
    if queue_row is None:
        return {"queued": False, "mode": None, "status_text": "当前未在匹配中。", "teammate_name": "", "commanders": []}
    teammate_row = load_user_row(connection, int(queue_row["teammate_user_id"])) if queue_row["teammate_user_id"] is not None else None
    return {
        "queued": True,
        "mode": queue_row["mode"],
        "mode_label": LADDER_MODE_LABELS.get(queue_row["mode"], queue_row["mode"]),
        "status_text": f"已进入 {LADDER_MODE_LABELS.get(queue_row['mode'], queue_row['mode'])} 匹配队列。",
        "teammate_name": ladder_user_display_name(teammate_row) if teammate_row is not None else "",
        "commanders": ladder_queue_commanders(queue_row),
    }


def build_ladder_sync_view(connection: sqlite3.Connection, user_id: int) -> dict[str, Any] | None:
    return {
        "url": "/ladder/sync",
        "key": ladder_match_sync_key(connection, user_id),
        "interval_ms": LADDER_SYNC_POLL_MS,
    }


def build_ladder_live_state(connection: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    active_match_row = load_active_ladder_match_for_user(connection, user_id)
    match_chat_messages = ladder_chat_messages(connection, int(active_match_row["id"])) if active_match_row is not None else []
    return {
        "lobby": ladder_lobby_stats(connection),
        "lobby_chat_messages": ladder_lobby_chat_messages(connection),
        "current_match_id": int(active_match_row["id"]) if active_match_row is not None else None,
        "match_chat_messages": match_chat_messages,
        "chat_messages": match_chat_messages,
    }


def build_ladder_page_context(connection: sqlite3.Connection, current_user: sqlite3.Row, mode: str) -> dict[str, Any]:
    ladder_touch_presence(connection, int(current_user["id"]))
    sync_ladder_state(connection)
    fresh_user = load_user_row(connection, int(current_user["id"])) or current_user
    current_ladder_bp_seconds = ladder_bp_seconds_setting(connection)
    queue_row = load_ladder_queue_entry_for_user(connection, int(current_user["id"]))
    active_match_row = load_active_ladder_match_for_user(connection, int(current_user["id"]))
    live_state = build_ladder_live_state(connection, int(current_user["id"]))
    teammate_pair_rows = connection.execute(
        """
        SELECT ladder_pair_ratings.*, u1.username AS username1, u2.username AS username2
        FROM ladder_pair_ratings
        JOIN users AS u1 ON u1.id = ladder_pair_ratings.user1_id
        JOIN users AS u2 ON u2.id = ladder_pair_ratings.user2_id
        WHERE ladder_pair_ratings.user1_id = ? OR ladder_pair_ratings.user2_id = ?
        ORDER BY ladder_pair_ratings.rating DESC, ladder_pair_ratings.games DESC
        LIMIT 10
        """,
        (fresh_user["id"], fresh_user["id"]),
    ).fetchall()
    pair_cards = [
        {
            "pair_name": f"{row['username1']} / {row['username2']}",
            "rating": round(float(row["rating"] or 1500.0), 2),
            "games": int(row["games"] or 0),
        }
        for row in teammate_pair_rows
    ]
    return {
        "page_title": "天梯匹配",
        "current_user": fresh_user,
        "ladder_mode": normalize_ladder_mode(mode),
        "ladder_commander_pool": LADDER_COMMANDER_POOL,
        "ladder_queue": ladder_queue_view(connection, queue_row),
        "ladder_match": ladder_match_view(connection, active_match_row, fresh_user) if active_match_row is not None else None,
        "ladder_sync": build_ladder_sync_view(connection, int(fresh_user["id"])),
        "ladder_lobby": live_state["lobby"],
        "ladder_lobby_chat": {
            "messages": live_state["lobby_chat_messages"],
        },
        "ladder_chat": {
            "match_id": live_state["current_match_id"],
            "messages": live_state["match_chat_messages"],
        }
        if live_state["current_match_id"] is not None
        else None,
        "ladder_profile": {
            "rating_1v1": round(float(fresh_user["ladder_rating_1v1"] or 1500.0), 2),
            "games_1v1": int(fresh_user["ladder_games_1v1"] or 0),
            "pair_cards": pair_cards,
        },
        "ladder_bp_seconds": current_ladder_bp_seconds,
        "ladder_bp_seconds_min": LADDER_BP_SECONDS_MIN,
        "ladder_bp_seconds_max": LADDER_BP_SECONDS_MAX,
        "ladder_leaderboard_1v1": ladder_1v1_leaderboard(connection),
        "ladder_leaderboard_2v2": ladder_2v2_leaderboard(connection),
    }


def load_ladder_bounty_row(connection: sqlite3.Connection, bounty_id: int) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM ladder_bounties WHERE id = ?", (bounty_id,)).fetchone()


def load_ladder_bounty_submission_row(connection: sqlite3.Connection, submission_id: int) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM ladder_bounty_submissions WHERE id = ?", (submission_id,)).fetchone()


def ladder_bounty_root_path(bounty_name: str) -> Path:
    return LADDER_BOUNTY_ROOT_DIR / sanitize_path_component(bounty_name, "悬赏")


def ladder_bounty_submission_path(bounty_name: str, username: str) -> Path:
    return ladder_bounty_root_path(bounty_name) / sanitize_path_component(f"{username} - 上交悬赏", f"{username}-上交悬赏")


def ladder_bounty_submission_archive_path(bounty_name: str, username: str) -> Path:
    return ladder_bounty_root_path(bounty_name) / sanitize_path_component(f"{username} - 上交悬赏.tar.gz", f"{username}-上交悬赏.tar.gz")


def ladder_bounty_status_label(status: str, is_full: bool) -> str:
    if status == LADDER_BOUNTY_STATUS_REVOKED:
        return "已撤回"
    if is_full:
        return "已满额"
    return "进行中"


def ladder_bounty_submission_status_label(status: str) -> str:
    return {
        LADDER_BOUNTY_SUBMISSION_PENDING: "待审核",
        LADDER_BOUNTY_SUBMISSION_VALID: "已通过",
        LADDER_BOUNTY_SUBMISSION_INVALID: "未通过",
    }.get(status, status)


def ladder_user_replay_file_map(user_row: sqlite3.Row | None) -> dict[str, Path]:
    if user_row is None:
        return {}
    replay_directory = ladder_replay_user_directory(user_row)
    if not replay_directory.exists():
        return {}
    return {path.name: path for path in replay_directory.glob("*.SC2Replay") if path.is_file()}


def ladder_bounty_available_replays(user_row: sqlite3.Row | None) -> list[dict[str, Any]]:
    replay_map = ladder_user_replay_file_map(user_row)
    items: list[dict[str, Any]] = []
    for path in sorted(replay_map.values(), key=lambda candidate: candidate.stat().st_mtime, reverse=True):
        modified_at = datetime.utcfromtimestamp(path.stat().st_mtime) + timedelta(hours=8)
        items.append(
            {
                "name": path.name,
                "size_kb": max(1, math.ceil(path.stat().st_size / 1024)),
                "modified_at": f"{modified_at.strftime('%Y-%m-%d %H:%M:%S')} UTC+8",
            }
        )
    return items


def ladder_bounty_valid_submission_count(connection: sqlite3.Connection, bounty_id: int) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS count FROM ladder_bounty_submissions WHERE bounty_id = ? AND status = ?",
        (bounty_id, LADDER_BOUNTY_SUBMISSION_VALID),
    ).fetchone()
    return int(row["count"] or 0) if row is not None else 0


def parse_ladder_bounty_selected_files(raw_value: str | None) -> list[str]:
    try:
        parsed = json.loads(raw_value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        name = str(item or "").strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def refresh_ladder_bounty_submission_archive(bounty_name: str, username: str) -> Path | None:
    submission_dir = ladder_bounty_submission_path(bounty_name, username)
    archive_path = ladder_bounty_submission_archive_path(bounty_name, username)
    replay_files = sorted(submission_dir.glob("*.SC2Replay")) if submission_dir.exists() else []
    if not replay_files:
        if archive_path.exists():
            archive_path.unlink()
        return None

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as archive:
        for replay_file in replay_files:
            archive.add(replay_file, arcname=f"{submission_dir.name}/{replay_file.name}")
    return archive_path


def build_ladder_bounties_context(connection: sqlite3.Connection, current_user: sqlite3.Row) -> dict[str, Any]:
    fresh_user = load_user_row(connection, int(current_user["id"])) or current_user
    can_manage_bounties = has_ladder_admin_access(fresh_user)
    available_replays = ladder_bounty_available_replays(fresh_user)
    bounty_rows = connection.execute(
        """
        SELECT ladder_bounties.*, users.username AS created_by_username
        FROM ladder_bounties
        JOIN users ON users.id = ladder_bounties.created_by_user_id
        ORDER BY CASE ladder_bounties.status WHEN 'active' THEN 0 ELSE 1 END, ladder_bounties.created_at DESC, ladder_bounties.id DESC
        """
    ).fetchall()

    items: list[dict[str, Any]] = []
    for bounty in bounty_rows:
        valid_count = ladder_bounty_valid_submission_count(connection, int(bounty["id"]))
        is_full = valid_count >= int(bounty["slot_count"] or 0)
        submission_rows = connection.execute(
            """
            SELECT ladder_bounty_submissions.*, users.username AS username, users.game_nickname AS game_nickname,
                   reviewers.username AS reviewer_username
            FROM ladder_bounty_submissions
            JOIN users ON users.id = ladder_bounty_submissions.user_id
            LEFT JOIN users AS reviewers ON reviewers.id = ladder_bounty_submissions.reviewed_by_user_id
            WHERE ladder_bounty_submissions.bounty_id = ?
            ORDER BY CASE ladder_bounty_submissions.status
                         WHEN 'pending' THEN 0
                         WHEN 'valid' THEN 1
                         ELSE 2
                     END,
                     ladder_bounty_submissions.updated_at DESC,
                     ladder_bounty_submissions.id DESC
            """,
            (bounty["id"],),
        ).fetchall()
        current_submission_row = next(
            (row for row in submission_rows if int(row["user_id"]) == int(fresh_user["id"])),
            None,
        )
        current_submission = None
        if current_submission_row is not None:
            current_submission = {
                "id": int(current_submission_row["id"]),
                "status": current_submission_row["status"],
                "status_label": ladder_bounty_submission_status_label(str(current_submission_row["status"])),
                "invalid_reason": str(current_submission_row["invalid_reason"] or ""),
                "selected_files": parse_ladder_bounty_selected_files(current_submission_row["selected_files_json"]),
                "updated_at": format_utc_storage_timestamp_as_utc8(str(current_submission_row["updated_at"] or "")),
            }
        submission_cards = [
            {
                "id": int(row["id"]),
                "username": str(row["username"]),
                "game_nickname": current_user_game_nickname(row),
                "status": row["status"],
                "status_label": ladder_bounty_submission_status_label(str(row["status"])),
                "invalid_reason": str(row["invalid_reason"] or ""),
                "selected_files": parse_ladder_bounty_selected_files(row["selected_files_json"]),
                "reviewed_by": str(row["reviewer_username"] or ""),
                "reviewed_at": format_utc_storage_timestamp_as_utc8(str(row["reviewed_at"] or "")),
                "updated_at": format_utc_storage_timestamp_as_utc8(str(row["updated_at"] or "")),
            }
            for row in submission_rows
        ]
        can_submit = (
            bounty["status"] == LADDER_BOUNTY_STATUS_ACTIVE
            and not is_full
            and bool(available_replays)
            and (current_submission_row is None or current_submission_row["status"] == LADDER_BOUNTY_SUBMISSION_INVALID)
        )
        items.append(
            {
                "id": int(bounty["id"]),
                "name": str(bounty["name"]),
                "slot_count": int(bounty["slot_count"] or 0),
                "valid_count": valid_count,
                "amount": str(bounty["amount"]),
                "description": str(bounty["description"]),
                "status": str(bounty["status"]),
                "status_label": ladder_bounty_status_label(str(bounty["status"]), is_full),
                "is_full": is_full,
                "created_by_username": str(bounty["created_by_username"]),
                "created_at": format_utc_storage_timestamp_as_utc8(str(bounty["created_at"] or "")),
                "revoked_reason": str(bounty["revoked_reason"] or ""),
                "can_submit": can_submit,
                "current_submission": current_submission,
                "submissions": submission_cards,
            }
        )

    return {
        "page_title": "天梯悬赏",
        "current_user": fresh_user,
        "can_manage_bounties": can_manage_bounties,
        "available_replays": available_replays,
        "bounties": items,
        "bounty_error": "",
        "bounty_success": "",
    }


def refresh_tournament_replay_archive(tournament: sqlite3.Row) -> Path | None:
    replay_files = tournament_replay_files(tournament)
    archive_path = tournament_replay_archive_path(tournament)
    if not replay_files:
        if archive_path.exists():
            archive_path.unlink()
        return None

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as archive:
        for replay_file in replay_files:
            try:
                arcname = replay_file.relative_to(REPLAY_SAVE_DIR)
            except ValueError:
                arcname = Path(replay_file.name)
            archive.add(replay_file, arcname=str(arcname).replace("\\", "/"))
    return archive_path


def refresh_tournament_replay_archive_for_tournament(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> Path | None:
    tournament = load_tournament_row(connection, tournament_id)
    if tournament is None:
        return None
    return refresh_tournament_replay_archive(tournament)


def tournament_replay_archive_download_view(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    manager: bool,
) -> dict[str, Any] | None:
    if not manager or not tournament_started(tournament):
        return None
    if not tournament_replays_ready_for_download(connection, tournament["id"]):
        return None
    replay_files = tournament_replay_files(tournament)
    if not replay_files:
        return None
    archive_path = tournament_replay_archive_path(tournament)
    return {
        "url": f"/tournaments/{tournament['id']}/replay-archive",
        "filename": archive_path.name,
        "game_count": len(replay_files),
    }


def replay_saved_path_text(path: Path) -> str:
    try:
        return str(path.relative_to(BASE_DIR))
    except ValueError:
        return str(path)


def replay_saved_paths_text(paths: list[Path]) -> str:
    return " | ".join(replay_saved_path_text(path) for path in paths)


def match_game_results(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in (state.get("game_results") or []) if isinstance(entry, dict)]


def pending_manual_replay_judgment(state: dict[str, Any]) -> dict[str, Any] | None:
    entry = state.get("pending_manual_replay_judgment")
    return entry if isinstance(entry, dict) else None


def pending_admin_replay_judgment(state: dict[str, Any]) -> dict[str, Any] | None:
    entry = state.get("pending_admin_replay_judgment")
    return entry if isinstance(entry, dict) else None


def consumed_replay_game_count(state: dict[str, Any]) -> int:
    total = len(match_game_results(state))
    if pending_manual_replay_judgment(state) is not None:
        total += 1
    if pending_admin_replay_judgment(state) is not None:
        total += 1
    return total


def clear_current_game_ready_at(state: dict[str, Any]) -> None:
    state["current_game_ready_at"] = None


def ensure_current_game_ready_at(state: dict[str, Any]) -> None:
    if not state.get("current_game_ready_at"):
        state["current_game_ready_at"] = current_utc8_timestamp_text()


def current_match_expected_map_name(
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
    state: dict[str, Any],
) -> str | None:
    current_game_number = max(1, int(state.get("current_game_number", 1) or 1))
    bp_mode = normalize_bp_mode(state.get("bp_mode") or tournament["bp_mode"])
    candidate = None

    if bp_mode in {BP_MODE_MAP_POOL, BP_MODE_EVO}:
        map_state = state.get("map_bp") or {}
        for game in map_state.get("games") or []:
            if int(game.get("game_number") or 0) == current_game_number:
                candidate = game.get("map_name")
                break
    elif bp_mode == BP_MODE_COMMANDER_DRAFT:
        for game in state.get("commander_games") or []:
            if int(game.get("game_number") or 0) == current_game_number:
                candidate = game.get("map_name")
                break
    elif bp_mode in {BP_MODE_COMMANDER_CONQUEST, BP_MODE_COMMANDER_CONQUEST_2V2}:
        for game in (state.get("conquest_series") or {}).get("games") or []:
            if int(game.get("game_number") or 0) == current_game_number:
                candidate = game.get("map_name")
                break
    elif bp_mode == BP_MODE_LADDER_1V1:
        for game in state.get("ladder_games") or []:
            if int(game.get("game_number") or 0) == current_game_number:
                candidate = game.get("map_name")
                break

    cleaned_candidate = str(candidate or "").strip()
    if not cleaned_candidate or cleaned_candidate in {"待确定", "待生成", "待公布"}:
        return None
    return cleaned_candidate


def replay_match_context_validation_error(
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
    state: dict[str, Any],
    replay_result: dict[str, Any],
) -> str | None:
    expected_map_name = current_match_expected_map_name(tournament, match_row, state)
    replay_map_name = str(replay_result.get("map_name") or "").strip() or None
    if expected_map_name is not None:
        if replay_map_match_key(replay_map_name) != replay_map_match_key(expected_map_name):
            return f"当前小局比赛地图应为 {expected_map_name}，但 replay 中识别到的地图为 {replay_map_name or '未识别'}。"

    ready_at_text = state.get("current_game_ready_at")
    if ready_at_text:
        replay_start_time = replay_datetime_value(replay_result.get("start_time_dt") or replay_result.get("start_time"))
        ready_at_time = replay_datetime_value(ready_at_text)
        if replay_start_time is None:
            return "当前 replay 未识别到可用的开始时间，无法验证其是否在本局准备/BP 完成之后开始。"
        if ready_at_time is not None and replay_start_time < ready_at_time:
            return f"当前 replay 开始时间 {replay_result.get('start_time') or '未识别'} 早于本局准备/BP 完成时间 {ready_at_text}。"

    return None


def match_has_recorded_or_pending_game(state: dict[str, Any], game_number: int) -> bool:
    if any(int(entry.get("game_number") or 0) == int(game_number) for entry in match_game_results(state)):
        return True
    # 新投票/仲裁流程
    if state.get("status") == "awaiting_vote" and int(state.get("current_game_number", 1) or 1) == int(game_number):
        return True
    pending_entry = pending_manual_replay_judgment(state)
    if pending_entry and int(pending_entry.get("game_number") or 0) == int(game_number):
        return True
    admin_entry = pending_admin_replay_judgment(state)
    return bool(admin_entry and int(admin_entry.get("game_number") or 0) == int(game_number))


def match_series_score(state: dict[str, Any], match_row: sqlite3.Row) -> tuple[int, int]:
    player1_id = match_row["player1_registration_id"]
    player2_id = match_row["player2_registration_id"]
    score1 = 0
    score2 = 0
    for result in match_game_results(state):
        winner_registration_id = result.get("winner_registration_id")
        if winner_registration_id == player1_id:
            score1 += 1
        elif winner_registration_id == player2_id:
            score2 += 1
    return score1, score2


def match_series_completion(
    state: dict[str, Any],
    match_row: sqlite3.Row,
) -> tuple[bool, int | None, int, int]:
    score1, score2 = match_series_score(state, match_row)
    if pending_admin_replay_judgment(state) is not None:
        return False, None, score1, score2
    best_of = int(match_row["best_of"] or default_best_of_for_bracket(match_row["bracket_type"]))
    player1_id = match_row["player1_registration_id"]
    player2_id = match_row["player2_registration_id"]
    total_games = len(match_game_results(state))

    if bracket_allows_even_best_of(match_row["bracket_type"]) and best_of % 2 == 0:
        threshold = (best_of // 2) + 1
        if score1 >= threshold:
            return True, player1_id, score1, score2
        if score2 >= threshold:
            return True, player2_id, score1, score2
        if total_games >= best_of:
            if score1 > score2:
                return True, player1_id, score1, score2
            if score2 > score1:
                return True, player2_id, score1, score2
            return True, None, score1, score2
        return False, None, score1, score2

    threshold = (best_of // 2) + 1
    if score1 >= threshold:
        return True, player1_id, score1, score2
    if score2 >= threshold:
        return True, player2_id, score1, score2
    return False, None, score1, score2


def advance_replay_series_state(
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
    state: dict[str, Any],
) -> dict[str, Any]:
    bp_mode = normalize_bp_mode(tournament["bp_mode"])
    best_of = int(match_row["best_of"] or default_best_of_for_bracket(match_row["bracket_type"]))
    current_game_number = int(state.get("current_game_number", 1) or 1)
    next_game_number = current_game_number + 1
    if next_game_number > best_of:
        raise ValueError("当前 BO 已达到最大局数，不能继续下一局。")

    state["advance_ready_ids"] = []
    state["pending_manual_replay_judgment"] = None
    clear_current_game_ready_at(state)
    stop_preparation_turn_clock(state, tournament_bp_step_seconds(tournament))

    if bp_mode == BP_MODE_COMMANDER_DRAFT:
        state["current_game_number"] = next_game_number
        state["ready_registration_ids"] = []
        state["status"] = "awaiting_ready"
    elif bp_mode in {BP_MODE_COMMANDER_CONQUEST, BP_MODE_COMMANDER_CONQUEST_2V2}:
        state["current_game_number"] = next_game_number
        state["ready_registration_ids"] = []
        state["status"] = "awaiting_ready"
    elif bp_mode == BP_MODE_LADDER_1V1:
        state["current_game_number"] = next_game_number
        state["ready_registration_ids"] = []
        next_game = build_tournament_ladder_bp_game_state(
            next_game_number,
            best_of,
            match_row["player1_registration_id"],
            match_row["player2_registration_id"],
        )
        preserved_games = [
            game
            for game in state.get("ladder_games") or []
            if int(game.get("game_number") or 0) < next_game_number
        ]
        state["ladder_games"] = [*preserved_games, next_game]
        finalize_tournament_ladder_bp_state(state, next_game, tournament_bp_step_seconds(tournament))
    elif bp_mode == BP_MODE_EVO:
        state["current_game_number"] = next_game_number
        state["status"] = "bp_in_progress"
        start_preparation_turn_clock(state, tournament_bp_step_seconds(tournament))
    else:
        state["current_game_number"] = next_game_number
        state["status"] = "ready_to_play"
    return state


def wait_for_admin_replay_judgment_state(state: dict[str, Any]) -> dict[str, Any]:
    state["advance_ready_ids"] = []
    state["pending_manual_replay_judgment"] = None
    state["status"] = "awaiting_admin_judgment"
    clear_current_game_ready_at(state)
    state["step_started_at"] = None
    return state


def finalize_match_replay_game(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
    state: dict[str, Any],
    winner_registration_id: int,
    uploader_registration_id: int,
    filename: str,
    saved_path: str,
    uploaded_at: str,
    game_number: int,
) -> str:
    state.setdefault("game_results", []).append(
        {
            "game_number": int(game_number),
            "winner_registration_id": winner_registration_id,
            "uploader_registration_id": uploader_registration_id,
            "filename": filename,
            "saved_path": saved_path,
            "uploaded_at": uploaded_at,
        }
    )
    state["pending_manual_replay_judgment"] = None

    series_completed, series_winner_id, score1, score2 = match_series_completion(state, match_row)
    actual_best_of = int(match_row["best_of"] or default_best_of_for_bracket(match_row["bracket_type"]))
    if series_completed:
        complete_match_internal(connection, match_row, series_winner_id, score1, score2, best_of=actual_best_of)
        refreshed_match = connection.execute(
            "SELECT * FROM tournament_matches WHERE id = ? AND tournament_id = ?",
            (match_row["id"], match_row["tournament_id"]),
        ).fetchone()
        state["status"] = "complete"
        state["advance_ready_ids"] = []
        stop_preparation_turn_clock(state, tournament_bp_step_seconds(tournament))
        save_match_preparation_state(connection, refreshed_match or match_row, tournament["bp_mode"], state)
        ensure_tournament_matches(connection, tournament)
        return f"已记录第 {int(game_number)} 局 replay，并完成整场比分 {score1}:{score2}。"

    if pending_admin_replay_judgment(state) is not None and consumed_replay_game_count(state) >= actual_best_of:
        state = wait_for_admin_replay_judgment_state(state)
        save_match_preparation_state(connection, match_row, tournament["bp_mode"], state)
        pending_entry = pending_admin_replay_judgment(state)
        return (
            f"已记录第 {int(game_number)} 局 replay。"
            f"当前 BO 已达到局数上限，等待赛事管理员裁定第 {int((pending_entry or {}).get('game_number') or 0)} 局。"
        )

    state = advance_replay_series_state(tournament, match_row, state)
    save_match_preparation_state(connection, match_row, tournament["bp_mode"], state)
    return f"已记录第 {int(game_number)} 局 replay，已自动进入下一局。"


def save_match_preparation_state(
    connection: sqlite3.Connection,
    match_row: sqlite3.Row,
    bp_mode: str,
    state: dict[str, Any],
) -> None:
    normalized_mode = normalize_bp_mode(bp_mode)
    state["bp_mode"] = normalized_mode
    state_json = json.dumps(state, ensure_ascii=False)
    current_time = now_text()
    connection.execute(
        """
        INSERT INTO tournament_match_preparations (
            tournament_id, bracket_type, round_number, match_number,
            bp_mode, state_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tournament_id, bracket_type, round_number, match_number)
        DO UPDATE SET
            bp_mode = excluded.bp_mode,
            state_json = excluded.state_json,
            updated_at = excluded.updated_at
        """,
        (*match_preparation_identity(match_row), normalized_mode, state_json, current_time, current_time),
    )


def match_preparation_sync_key(
    connection: sqlite3.Connection,
    match_row: sqlite3.Row,
) -> str:
    preparation_row = load_match_preparation_row(connection, match_row)
    payload = {
        "match": {
            "id": int(match_row["id"]),
            "status": match_row["status"],
            "score1": int(match_row["score1"]),
            "score2": int(match_row["score2"]),
            "winner_registration_id": match_row["winner_registration_id"],
            "updated_at": match_row["updated_at"],
        },
        "preparation": {
            "updated_at": preparation_row["updated_at"],
            "bp_mode": preparation_row["bp_mode"],
            "state_json": preparation_row["state_json"],
        }
        if preparation_row is not None
        else None,
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def tournament_preparation_sync_key(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> str:
    match_rows = connection.execute(
        """
        SELECT id, bracket_type, round_number, match_number, status, score1, score2,
               winner_registration_id, updated_at
        FROM tournament_matches
        WHERE tournament_id = ?
        ORDER BY bracket_type ASC, round_number ASC, match_number ASC, id ASC
        """,
        (tournament_id,),
    ).fetchall()
    preparation_rows = connection.execute(
        """
        SELECT bracket_type, round_number, match_number, bp_mode, state_json, updated_at
        FROM tournament_match_preparations
        WHERE tournament_id = ?
        ORDER BY bracket_type ASC, round_number ASC, match_number ASC, id ASC
        """,
        (tournament_id,),
    ).fetchall()
    payload = {
        "matches": [
            {
                "id": int(row["id"]),
                "bracket_type": row["bracket_type"],
                "round_number": int(row["round_number"]),
                "match_number": int(row["match_number"]),
                "status": row["status"],
                "score1": int(row["score1"]),
                "score2": int(row["score2"]),
                "winner_registration_id": row["winner_registration_id"],
                "updated_at": row["updated_at"],
            }
            for row in match_rows
        ],
        "preparations": [
            {
                "bracket_type": row["bracket_type"],
                "round_number": int(row["round_number"]),
                "match_number": int(row["match_number"]),
                "bp_mode": row["bp_mode"],
                "updated_at": row["updated_at"],
                "state_json": row["state_json"],
            }
            for row in preparation_rows
        ],
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def match_preparation_player_ids(match_row: sqlite3.Row) -> list[int]:
    return [
        registration_id
        for registration_id in (match_row["player1_registration_id"], match_row["player2_registration_id"])
        if registration_id is not None
    ]


def preparation_participant_name(
    registration_map: dict[int, sqlite3.Row],
    registration_id: int | None,
    fallback: str,
) -> str:
    if registration_id is None:
        return fallback
    participant = registration_map.get(registration_id)
    if participant is None:
        return fallback
    # 优先使用 nickname（报名时填写的展示名），其次使用绑定的游戏昵称，最后用用户名
    name = str(participant["nickname"] or "").strip()
    if not name:
        if "account_game_nickname" in participant.keys():
            name = str(participant["account_game_nickname"] or "").strip()
    if not name:
        if "account_name" in participant.keys():
            name = str(participant["account_name"] or "").strip()
    return name if name else fallback


def start_preparation_turn_clock(state: dict[str, Any], duration_seconds: int = DEFAULT_TOURNAMENT_BP_STEP_SECONDS) -> None:
    state["step_started_at"] = now_text()
    state["step_deadline_seconds"] = max(1, int(duration_seconds or DEFAULT_TOURNAMENT_BP_STEP_SECONDS))


def stop_preparation_turn_clock(state: dict[str, Any], duration_seconds: int = DEFAULT_TOURNAMENT_BP_STEP_SECONDS) -> None:
    state["step_started_at"] = None
    state["step_deadline_seconds"] = max(1, int(duration_seconds or DEFAULT_TOURNAMENT_BP_STEP_SECONDS))


def update_commander_preparation_status(
    state: dict[str, Any],
    commander_game: dict[str, Any],
    duration_seconds: int = DEFAULT_TOURNAMENT_BP_STEP_SECONDS,
) -> None:
    state["status"] = "ready_to_play" if commander_game.get("completed") else "bp_in_progress"
    if state["status"] == "bp_in_progress":
        clear_current_game_ready_at(state)
        start_preparation_turn_clock(state, duration_seconds)
    else:
        ensure_current_game_ready_at(state)
        stop_preparation_turn_clock(state, duration_seconds)


def preparation_turn_clock_view(
    state: dict[str, Any],
    default_duration_seconds: int = DEFAULT_TOURNAMENT_BP_STEP_SECONDS,
) -> dict[str, Any]:
    duration = int(state.get("step_deadline_seconds") or default_duration_seconds)
    started_at = state.get("step_started_at")
    if state.get("status") != "bp_in_progress" or not started_at:
        return {
            "active": False,
            "remaining_seconds": duration,
            "duration_seconds": duration,
            "expired": False,
        }

    try:
        started_dt = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return {
            "active": False,
            "remaining_seconds": duration,
            "duration_seconds": duration,
            "expired": False,
        }

    elapsed = max(0, int((datetime.utcnow() - started_dt).total_seconds()))
    remaining = max(0, duration - elapsed)
    return {
        "active": True,
        "remaining_seconds": remaining,
        "duration_seconds": duration,
        "expired": remaining == 0,
    }


def sync_match_preparation_state(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
) -> dict[str, Any] | None:
    state = load_match_preparation_state(connection, match_row)
    if state is None:
        return None

    state.setdefault("step_started_at", None)
    state["step_deadline_seconds"] = int(state.get("step_deadline_seconds") or tournament_bp_step_seconds(tournament))

    if state.get("status") != "bp_in_progress":
        return state
    normalized_mode = normalize_bp_mode(state.get("bp_mode") or tournament["bp_mode"])
    if normalized_mode != BP_MODE_COMMANDER_DRAFT:
        if normalized_mode in {BP_MODE_COMMANDER_CONQUEST, BP_MODE_COMMANDER_CONQUEST_2V2}:
            if not preparation_turn_clock_view(state, tournament_bp_step_seconds(tournament))["expired"]:
                return state
            auto_complete_commander_conquest_state(state)
            finalize_commander_conquest_preparation_status(state, tournament_bp_step_seconds(tournament))
            save_match_preparation_state(connection, match_row, tournament["bp_mode"], state)
            return state
        if normalized_mode != BP_MODE_LADDER_1V1:
            return state
        if not preparation_turn_clock_view(state, tournament_bp_step_seconds(tournament))["expired"]:
            return state

        ladder_game = current_tournament_ladder_bp_game_state(state)
        if ladder_game is None or ladder_game.get("completed"):
            return state

        auto_complete_tournament_ladder_bp_game(ladder_game)
        finalize_tournament_ladder_bp_state(state, ladder_game, tournament_bp_step_seconds(tournament))
        save_match_preparation_state(connection, match_row, tournament["bp_mode"], state)
        return state
    if not preparation_turn_clock_view(state, tournament_bp_step_seconds(tournament))["expired"]:
        return state

    commander_game = current_commander_game_state(state)
    if commander_game is None or current_commander_step(commander_game) is None:
        return state

    try:
        auto_complete_commander_action(commander_game)
    except ValueError:
        return state

    update_commander_preparation_status(state, commander_game, tournament_bp_step_seconds(tournament))
    save_match_preparation_state(connection, match_row, tournament["bp_mode"], state)
    return state


def sync_tournament_preparation_states(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
) -> None:
    preparation_matches = connection.execute(
        """
        SELECT tournament_matches.*
        FROM tournament_matches
        JOIN tournament_match_preparations
          ON tournament_match_preparations.tournament_id = tournament_matches.tournament_id
         AND tournament_match_preparations.bracket_type = tournament_matches.bracket_type
         AND tournament_match_preparations.round_number = tournament_matches.round_number
         AND tournament_match_preparations.match_number = tournament_matches.match_number
        WHERE tournament_matches.tournament_id = ? AND tournament_matches.status != 'completed'
        ORDER BY tournament_matches.id ASC
        """,
        (tournament["id"],),
    ).fetchall()
    for match_row in preparation_matches:
        sync_match_preparation_state(connection, tournament, match_row)


def bracket_map_bp_snapshot(
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
    registration_map: dict[int, sqlite3.Row],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    if normalize_bp_mode(tournament["bp_mode"]) not in {BP_MODE_MAP_POOL, BP_MODE_EVO}:
        return None

    best_of = int(match_row["best_of"] or default_best_of_for_bracket(match_row["bracket_type"]))
    player1_id = match_row["player1_registration_id"]
    player2_id = match_row["player2_registration_id"]
    map_pool = parse_map_pool(tournament["map_pool"])
    try:
        map_state = state.get("map_bp") or build_map_bp_state(best_of, map_pool)
    except ValueError:
        return None

    chosen_games = {
        int(game["game_number"]): game
        for game in map_state.get("games", [])
        if game.get("game_number") is not None
    }
    actions_by_game: dict[int, list[dict[str, Any]]] = defaultdict(list)
    veto_actions: list[dict[str, Any]] = []
    for action in map_state.get("actions", []):
        actor = action.get("actor")
        actor_name = {
            "A": preparation_participant_name(registration_map, player1_id, "A"),
            "B": preparation_participant_name(registration_map, player2_id, "B"),
            "C": "系统",
        }.get(actor, actor or "系统")
        item = {
            "type": action.get("type", "pick"),
            "type_label": {
                "ban": "禁图",
                "pick": "选图",
                "decider": "决胜图",
            }.get(action.get("type", "pick"), "操作"),
            "actor": actor,
            "actor_name": actor_name,
            "map_name": action.get("map_name", "待定"),
            "game_number": action.get("game_number"),
        }
        if action.get("game_number") is None:
            veto_actions.append(item)
        else:
            actions_by_game[int(action["game_number"])] .append(item)

    display_games = []
    for game_number in range(1, best_of + 1):
        game = chosen_games.get(game_number)
        actor = game.get("actor") if game is not None else None
        actor_name = {
            "A": preparation_participant_name(registration_map, player1_id, "A"),
            "B": preparation_participant_name(registration_map, player2_id, "B"),
            "C": "系统",
        }.get(actor, "")
        display_games.append(
            {
                "game_number": game_number,
                "map_name": game["map_name"] if game is not None else "待确定",
                "status_label": "已确定" if game is not None else "待确定",
                "actor_name": actor_name,
                "actions": actions_by_game.get(game_number, []),
                "is_current": bool(
                    state.get("status") == "bp_in_progress"
                    and current_map_bp_step(map_state) is not None
                    and int(current_map_bp_step(map_state).get("game_number") or -1) == game_number
                ),
            }
        )

    return {
        "games": display_games,
        "veto_actions": veto_actions,
    }


def bracket_commander_bp_snapshot(
    match_row: sqlite3.Row,
    registration_map: dict[int, sqlite3.Row],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    best_of = int(match_row["best_of"] or default_best_of_for_bracket(match_row["bracket_type"]))
    player1_id = match_row["player1_registration_id"]
    player2_id = match_row["player2_registration_id"]
    current_game = current_commander_game_state(state)
    current_game_number = int((current_game or {}).get("game_number") or state.get("current_game_number", 1) or 1)
    commander_games = {
        int(game["game_number"]): game
        for game in state.get("commander_games") or []
        if game.get("game_number") is not None
    }

    display_games = []
    for game_number in range(1, best_of + 1):
        game = commander_games.get(game_number)
        side_assignments = (
            game["side_assignments"]
            if game is not None
            else side_assignments_for_game(game_number, player1_id, player2_id)
        )
        side_names = {
            side: preparation_participant_name(registration_map, registration_id, side)
            for side, registration_id in side_assignments.items()
        }
        display_games.append(
            {
                "game_number": game_number,
                "map_name": game.get("map_name", "待生成") if game is not None else "待生成",
                "score_limit": game.get("score_limit") if game is not None else None,
                "bans": game.get("bans", {}) if game is not None else {},
                "picks": game.get("picks", {"A": [], "B": []}) if game is not None else {"A": [], "B": []},
                "side_names": side_names,
                "is_current": state.get("status") != "complete" and game_number == current_game_number,
                "is_placeholder": game is None,
            }
        )

    return {
        "games": display_games,
    }


def bracket_commander_conquest_snapshot(
    match_row: sqlite3.Row,
    registration_map: dict[int, sqlite3.Row],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    series_state = state.get("conquest_series")
    if not isinstance(series_state, dict):
        return None

    best_of = int(match_row["best_of"] or default_best_of_for_bracket(match_row["bracket_type"]))
    current_game_number = max(1, int(state.get("current_game_number", 1) or 1))
    pool_submissions = series_state.get("pool_submissions") or {"A": [], "B": []}
    display_games = []
    for game_number in range(1, best_of + 1):
        game = next(
            (entry for entry in series_state.get("games") or [] if int(entry.get("game_number") or 0) == game_number),
            None,
        )
        if game is None:
            game = {
                "game_number": game_number,
                "map_name": "待生成",
                "pick": {"A": None, "B": None},
                "side_assignments": {"A": match_row["player1_registration_id"], "B": match_row["player2_registration_id"]},
            }
        side_names = {
            side: preparation_participant_name(registration_map, registration_id, side)
            for side, registration_id in (game.get("side_assignments") or {}).items()
        }
        result = match_game_result_by_number(state, game_number)
        display_games.append(
            {
                "game_number": game_number,
                "map_name": game.get("map_name") or "待生成",
                "pick": game.get("pick") or {"A": None, "B": None},
                "side_names": side_names,
                "winner_name": preparation_participant_name(registration_map, result.get("winner_registration_id") if result else None, ""),
                "is_current": state.get("status") != "complete" and game_number == current_game_number,
                "is_placeholder": game_number > len(series_state.get("games") or []),
            }
        )

    return {
        "first_to": int(series_state.get("first_to") or 0),
        "pool_submissions": {
            "A": normalize_commander_catalog_selection(pool_submissions.get("A") or [], int(series_state.get("first_to") or 0)),
            "B": normalize_commander_catalog_selection(pool_submissions.get("B") or [], int(series_state.get("first_to") or 0)),
        },
        "locked": {
            "A": commander_conquest_locked_commanders(state, "A"),
            "B": commander_conquest_locked_commanders(state, "B"),
        },
        "games": display_games,
    }


def build_evo_bp_view(
    match_row: sqlite3.Row,
    registration_map: dict[int, sqlite3.Row],
    state: dict[str, Any],
    viewer_registration_id: int | None,
) -> dict[str, Any]:
    best_of = int(match_row["best_of"] or default_best_of_for_bracket(match_row["bracket_type"]))
    current_game_number = max(1, int(state.get("current_game_number", 1) or 1))
    current_choice = current_evo_choice_state(match_row, state)
    map_names_by_game = {
        int(game.get("game_number") or 0): str(game.get("map_name") or "待确定")
        for game in (state.get("map_bp") or {}).get("games") or []
        if int(game.get("game_number") or 0) > 0
    }
    current_game_map_name = map_names_by_game.get(current_game_number, "待确定")

    current_choice_view = None
    if current_choice is not None:
        loser_registration_id = current_choice.get("loser_registration_id")
        winner_registration_id = current_choice.get("winner_registration_id")
        current_choice_view = {
            "game_number": int(current_choice.get("game_number") or current_game_number),
            "choice_value": str(current_choice.get("choice_value") or ""),
            "choice_label": str(current_choice.get("choice_label") or ""),
            "loser_name": preparation_participant_name(registration_map, loser_registration_id, "上局败者"),
            "winner_name": preparation_participant_name(registration_map, winner_registration_id, "上局胜者"),
        }

    action_options = [
        {
            "value": option["value"],
            "label": option["label"],
            "selected": bool(current_choice_view and current_choice_view["choice_value"] == option["value"]),
        }
        for option in evo_choice_options()
    ]

    display_games = []
    for game_number in range(1, best_of + 1):
        choice_entry = evo_choice_for_game(state, game_number)
        previous_result = match_game_result_by_number(state, game_number - 1) if game_number > 1 else None
        winner_registration_id, loser_registration_id = winner_loser_registration_ids_for_game(match_row, previous_result)
        choice_value = str((choice_entry or {}).get("choice_value") or "").strip()
        choice_label = EVO_CHOICE_LABELS.get(choice_value, "") if choice_value else ""
        if game_number == 1:
            choice_label = "无小局 BP"
            status_label = "固定"
        elif choice_label:
            status_label = "已确定"
        elif loser_registration_id is not None:
            choice_label = "待上局败者选择"
            status_label = "待选择"
        else:
            choice_label = "等待上一局结果"
            status_label = "待解锁"

        display_games.append(
            {
                "game_number": game_number,
                "map_name": map_names_by_game.get(game_number, "待确定"),
                "choice_label": choice_label,
                "status_label": status_label,
                "loser_name": preparation_participant_name(registration_map, loser_registration_id, ""),
                "winner_name": preparation_participant_name(registration_map, winner_registration_id, ""),
                "is_current": state.get("status") != "complete" and game_number == current_game_number,
            }
        )

    return {
        "current_game_number": current_game_number,
        "current_game_map_name": current_game_map_name,
        "current_choice": current_choice_view,
        "can_act": bool(
            current_choice_view is not None
            and current_choice_view["game_number"] > 1
            and current_choice_view["choice_value"] == ""
            and viewer_registration_id is not None
            and int(current_choice.get("loser_registration_id") or 0) == int(viewer_registration_id)
            and state.get("status") == "bp_in_progress"
        ),
        "action_options": action_options,
        "games": display_games,
    }


def initialize_match_preparation_state(
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
    state: dict[str, Any],
) -> dict[str, Any]:
    bp_mode = normalize_bp_mode(tournament["bp_mode"])
    bp_step_seconds = tournament_bp_step_seconds(tournament)
    state["bp_mode"] = bp_mode
    state["advance_ready_ids"] = []
    current_game_number = max(1, int(state.get("current_game_number", 1) or 1))
    state["current_game_number"] = current_game_number
    state["step_deadline_seconds"] = bp_step_seconds

    if bp_mode == BP_MODE_NONE:
        state["status"] = "ready_to_play"
        ensure_current_game_ready_at(state)
        state["map_bp"] = None
        state["commander_games"] = []
        state["conquest_series"] = None
        state["ladder_games"] = []
        state["evo_choices"] = []
        stop_preparation_turn_clock(state, bp_step_seconds)
        return state

    best_of = int(match_row["best_of"] or default_best_of_for_bracket(match_row["bracket_type"]))
    map_pool = parse_map_pool(tournament["map_pool"])
    if bp_mode in {BP_MODE_MAP_POOL, BP_MODE_EVO}:
        map_state = build_map_bp_state(best_of, map_pool)
        state["map_bp"] = finalize_map_bp_if_needed(map_state)
        state["commander_games"] = []
        state["conquest_series"] = None
        state["ladder_games"] = []
        state["evo_choices"] = [] if bp_mode == BP_MODE_EVO else state.get("evo_choices") or []
        state["status"] = "ready_to_play" if state["map_bp"]["completed"] else "bp_in_progress"
        if state["status"] == "bp_in_progress":
            clear_current_game_ready_at(state)
            start_preparation_turn_clock(state, bp_step_seconds)
        else:
            ensure_current_game_ready_at(state)
            stop_preparation_turn_clock(state, bp_step_seconds)
        return state

    if bp_mode in {BP_MODE_COMMANDER_CONQUEST, BP_MODE_COMMANDER_CONQUEST_2V2}:
        state["map_bp"] = None
        state["commander_games"] = []
        state["ladder_games"] = []
        state["evo_choices"] = []
        series_state = state.get("conquest_series")
        if isinstance(series_state, dict) and series_state.get("phase") == "pick" and len(series_state.get("games") or []) >= current_game_number:
            finalize_commander_conquest_preparation_status(state, bp_step_seconds)
            return state
        if isinstance(series_state, dict) and series_state.get("phase") == "pick":
            used_maps = [game.get("map_name") for game in series_state.get("games") or [] if game.get("map_name")]
            ppg = int(series_state.get("picks_per_game") or 1)
            next_game = build_commander_conquest_game_state(
                current_game_number,
                best_of,
                parse_map_pool(tournament["map_pool"]),
                used_maps,
                match_row["player1_registration_id"],
                match_row["player2_registration_id"],
                ppg,
            )
            series_state.setdefault("games", []).append(next_game)
            state["advance_ready_ids"] = []
            state["ready_registration_ids"] = []
            finalize_commander_conquest_preparation_status(state, bp_step_seconds)
            return state
        state["conquest_series"] = build_commander_conquest_series_state(tournament, match_row)
        finalize_commander_conquest_preparation_status(state, bp_step_seconds)
        return state

    if bp_mode == BP_MODE_LADDER_1V1:
        ladder_game = build_tournament_ladder_bp_game_state(
            current_game_number,
            best_of,
            match_row["player1_registration_id"],
            match_row["player2_registration_id"],
        )
        state["map_bp"] = None
        state["commander_games"] = []
        state["evo_choices"] = []
        preserved_games = [
            game
            for game in state.get("ladder_games") or []
            if int(game.get("game_number") or 0) < current_game_number
        ]
        state["ladder_games"] = [*preserved_games, ladder_game]
        finalize_tournament_ladder_bp_state(state, ladder_game, bp_step_seconds)
        return state

    commander_game = build_commander_game_state(
        current_game_number,
        best_of,
        map_pool,
        [game["map_name"] for game in state.get("commander_games") or [] if game.get("map_name")],
        match_row["player1_registration_id"],
        match_row["player2_registration_id"],
        pool_mode=commander_draft_settings_for_tournament(tournament)["pool_mode"],
        ban_mode=commander_draft_settings_for_tournament(tournament)["ban_mode"],
    )
    state["map_bp"] = None
    state["conquest_series"] = None
    state["ladder_games"] = []
    preserved_games = [
        game
        for game in state.get("commander_games") or []
        if int(game.get("game_number") or 0) < current_game_number
    ]
    state["commander_games"] = [*preserved_games, commander_game]
    update_commander_preparation_status(state, commander_game, bp_step_seconds)
    return state


def current_commander_game_state(state: dict[str, Any]) -> dict[str, Any] | None:
    commander_games = state.get("commander_games") or []
    if not commander_games:
        return None
    return commander_games[-1]


def advance_commander_preparation_state(
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
    state: dict[str, Any],
) -> dict[str, Any]:
    best_of = int(match_row["best_of"] or default_best_of_for_bracket(match_row["bracket_type"]))
    next_game_number = int(state.get("current_game_number", 1)) + 1
    if next_game_number > best_of:
        raise ValueError("当前 BO 已达到最大局数，不能继续下一局。")

    used_maps = [game["map_name"] for game in state.get("commander_games") or [] if game.get("map_name")]
    next_game = build_commander_game_state(
        next_game_number,
        best_of,
        parse_map_pool(tournament["map_pool"]),
        used_maps,
        match_row["player1_registration_id"],
        match_row["player2_registration_id"],
        pool_mode=commander_draft_settings_for_tournament(tournament)["pool_mode"],
        ban_mode=commander_draft_settings_for_tournament(tournament)["ban_mode"],
    )
    state.setdefault("commander_games", []).append(next_game)
    state["current_game_number"] = next_game_number
    state["advance_ready_ids"] = []
    update_commander_preparation_status(state, next_game, tournament_bp_step_seconds(tournament))
    return state


def commander_catalog_names() -> list[str]:
    return [entry["name"] for entry in commander_catalog()]


def normalize_commander_catalog_selection(values: Any, limit: int) -> list[str]:
    valid_names = set(commander_catalog_names())
    if isinstance(values, str):
        raw_values = [values]
    elif isinstance(values, (list, tuple, set)):
        raw_values = [str(item) for item in values]
    else:
        raw_values = []

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        candidate = str(item).strip()
        if candidate not in valid_names or candidate in seen:
            continue
        seen.add(candidate)
        cleaned.append(candidate)
        if len(cleaned) >= limit:
            break
    return cleaned


def commander_conquest_first_to(best_of: int, picks_per_game: int = 1) -> int:
    return best_of_required_wins(best_of) * picks_per_game


def conquest_picks_per_game(bp_mode: str) -> int:
    return 2 if normalize_bp_mode(bp_mode) == BP_MODE_COMMANDER_CONQUEST_2V2 else 1


def conquest_mode_label(bp_mode: str) -> str:
    return "指挥官2v2征服者" if normalize_bp_mode(bp_mode) == BP_MODE_COMMANDER_CONQUEST_2V2 else "指挥官1v1征服者"


def _conquest_pick_ready(pick_slot: Any, picks_per_game: int) -> bool:
    """检查某方的 pick 是否已完成（兼容 1v1 的字符串和 2v2 的列表）。"""
    if isinstance(pick_slot, list):
        return len([v for v in pick_slot if v]) >= picks_per_game
    return bool(pick_slot)


def build_commander_conquest_game_state(
    game_number: int,
    best_of: int,
    map_pool: list[str],
    used_maps: list[str],
    player1_registration_id: int | None,
    player2_registration_id: int | None,
    picks_per_game: int = 1,
) -> dict[str, Any]:
    if not map_pool:
        raise ValueError("征服者至少需要 1 张地图。")

    # 地图去重
    remaining_maps = [map_name for map_name in map_pool if map_name not in set(used_maps)] or list(map_pool)
    picker = random.Random(f"conquest:{best_of}:{game_number}:{'|'.join(map_pool)}:{player1_registration_id}:{player2_registration_id}")
    selected_map = picker.choice(remaining_maps)
    # 2v2 用列表存多个 pick，1v1 用字符串
    pick_init: dict[str, Any] = {"A": [] if picks_per_game > 1 else None, "B": [] if picks_per_game > 1 else None}
    return {
        "game_number": game_number,
        "map_name": selected_map,
        "side_assignments": {"A": player1_registration_id, "B": player2_registration_id},
        "pick": pick_init,
        "available": {"A": [], "B": []},
    }


def build_commander_conquest_series_state(
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
) -> dict[str, Any]:
    best_of = int(match_row["best_of"] or default_best_of_for_tournament_row(tournament, match_row["bracket_type"]))
    bp_mode = normalize_bp_mode(tournament["bp_mode"])
    ppg = conquest_picks_per_game(bp_mode)
    return {
        "mode": bp_mode,
        "best_of": best_of,
        "first_to": commander_conquest_first_to(best_of, ppg),
        "picks_per_game": ppg,
        "phase": "pool",
        "pool_submissions": {"A": [], "B": []},
        "games": [
            build_commander_conquest_game_state(
                1,
                best_of,
                parse_map_pool(tournament["map_pool"]),
                [],
                match_row["player1_registration_id"],
                match_row["player2_registration_id"],
                ppg,
            )
        ],
        "completed": False,
    }


def current_commander_conquest_game_state(state: dict[str, Any]) -> dict[str, Any] | None:
    series_state = state.get("conquest_series")
    if not isinstance(series_state, dict):
        return None
    games = series_state.get("games") or []
    if not games:
        return None
    return games[-1]


def commander_conquest_locked_commanders(
    state: dict[str, Any],
    side: str,
) -> list[str]:
    series_state = state.get("conquest_series")
    if not isinstance(series_state, dict):
        return []

    locked: list[str] = []
    seen: set[str] = set()
    for game in series_state.get("games") or []:
        result = match_game_result_by_number(state, int(game.get("game_number") or 0))
        if result is None:
            continue
        winner_registration_id = result.get("winner_registration_id")
        if game.get("side_assignments", {}).get(side) != winner_registration_id:
            continue
        # 支持 1v1（字符串）和 2v2（列表）
        pick_value = (game.get("pick") or {}).get(side)
        if isinstance(pick_value, list):
            for commander_name in pick_value:
                name = str(commander_name or "").strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                locked.append(name)
        else:
            commander_name = str(pick_value or "").strip()
            if not commander_name or commander_name in seen:
                continue
            seen.add(commander_name)
            locked.append(commander_name)
    return locked


def commander_conquest_available_commanders(
    state: dict[str, Any],
    side: str,
) -> list[str]:
    series_state = state.get("conquest_series")
    if not isinstance(series_state, dict):
        return []
    pool_submissions = series_state.get("pool_submissions") or {}
    selected_pool = normalize_commander_catalog_selection(
        pool_submissions.get(side) or [],
        int(series_state.get("first_to") or 0),
    )
    if not selected_pool:
        return []
    locked = set(commander_conquest_locked_commanders(state, side))
    return [commander for commander in selected_pool if commander not in locked]


def finalize_commander_conquest_preparation_status(
    state: dict[str, Any],
    duration_seconds: int = DEFAULT_TOURNAMENT_BP_STEP_SECONDS,
) -> None:
    series_state = state.get("conquest_series")
    if not isinstance(series_state, dict):
        state["status"] = "awaiting_ready"
        stop_preparation_turn_clock(state, duration_seconds)
        return

    first_to = int(series_state.get("first_to") or 0)
    pool_submissions = series_state.setdefault("pool_submissions", {"A": [], "B": []})
    phase = str(series_state.get("phase") or "pool")
    current_game = current_commander_conquest_game_state(state)

    if phase == "pool":
        pool_submissions["A"] = normalize_commander_catalog_selection(pool_submissions.get("A") or [], first_to)
        pool_submissions["B"] = normalize_commander_catalog_selection(pool_submissions.get("B") or [], first_to)
        if len(pool_submissions["A"]) == first_to and len(pool_submissions["B"]) == first_to:
            series_state["phase"] = "pick"
            phase = "pick"

    if phase == "pick" and current_game is not None:
        current_game.setdefault("available", {"A": [], "B": []})
        current_game["available"]["A"] = commander_conquest_available_commanders(state, "A")
        current_game["available"]["B"] = commander_conquest_available_commanders(state, "B")
        ppg = int(series_state.get("picks_per_game") or 1)
        pick_a_ready = _conquest_pick_ready((current_game.get("pick") or {}).get("A"), ppg)
        pick_b_ready = _conquest_pick_ready((current_game.get("pick") or {}).get("B"), ppg)
        if pick_a_ready and pick_b_ready:
            state["status"] = "ready_to_play"
            ensure_current_game_ready_at(state)
            stop_preparation_turn_clock(state, duration_seconds)
            return

    series_state["completed"] = False
    state["status"] = "bp_in_progress"
    clear_current_game_ready_at(state)
    start_preparation_turn_clock(state, duration_seconds)


def auto_complete_commander_conquest_state(state: dict[str, Any]) -> None:
    series_state = state.get("conquest_series")
    if not isinstance(series_state, dict):
        return

    first_to = int(series_state.get("first_to") or 0)
    picks_per_game = int(series_state.get("picks_per_game") or 1)
    catalog_names = commander_catalog_names()
    phase = str(series_state.get("phase") or "pool")
    if phase == "pool":
        pool_submissions = series_state.setdefault("pool_submissions", {"A": [], "B": []})
        for side in ("A", "B"):
            selected = normalize_commander_catalog_selection(pool_submissions.get(side) or [], first_to)
            if len(selected) != first_to:
                selected = catalog_names[:first_to]
            pool_submissions[side] = selected
        return

    current_game = current_commander_conquest_game_state(state)
    if current_game is None:
        return
    current_game.setdefault("available", {"A": [], "B": []})
    current_game.setdefault("pick", {"A": None, "B": None})
    for side in ("A", "B"):
        if _conquest_pick_ready(current_game["pick"].get(side), picks_per_game):
            continue
        options = list(current_game["available"].get(side) or commander_conquest_available_commanders(state, side))
        if not options:
            options = list(series_state.get("pool_submissions", {}).get(side) or catalog_names[:max(first_to, picks_per_game)])
        if picks_per_game == 1:
            current_game["pick"][side] = random.choice(options)
        else:
            # 2v2: 需要选择 picks_per_game 个不重复的指挥官
            needed = picks_per_game - len([v for v in (current_game["pick"].get(side) or []) if v])
            if needed <= 0:
                continue
            selected = [o for o in options if o not in (current_game["pick"].get(side) or [])]
            if len(selected) < needed:
                selected = options
            current_game["pick"][side] = (current_game["pick"].get(side) or []) + selected[:needed]


def advance_commander_conquest_state(
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
    state: dict[str, Any],
) -> dict[str, Any]:
    series_state = state.get("conquest_series")
    if not isinstance(series_state, dict):
        raise ValueError("当前对局还没有征服者 BP 记录。")

    best_of = int(match_row["best_of"] or default_best_of_for_tournament_row(tournament, match_row["bracket_type"]))
    next_game_number = int(state.get("current_game_number", 1) or 1) + 1
    if next_game_number > best_of:
        raise ValueError("当前 BO 已达到最大局数，不能继续下一局。")

    used_maps = [game.get("map_name") for game in series_state.get("games") or [] if game.get("map_name")]
    next_game = build_commander_conquest_game_state(
        next_game_number,
        best_of,
        parse_map_pool(tournament["map_pool"]),
        used_maps,
        match_row["player1_registration_id"],
        match_row["player2_registration_id"],
    )
    series_state.setdefault("games", []).append(next_game)
    series_state["phase"] = "pick"
    state["current_game_number"] = next_game_number
    state["advance_ready_ids"] = []
    state["ready_registration_ids"] = []
    finalize_commander_conquest_preparation_status(state, tournament_bp_step_seconds(tournament))
    return state


def build_tournament_ladder_bp_game_state(
    game_number: int,
    best_of: int,
    player1_registration_id: int | None,
    player2_registration_id: int | None,
) -> dict[str, Any]:
    return {
        "mode": BP_MODE_LADDER_1V1,
        "game_number": game_number,
        "best_of": best_of,
        "map_name": random.choice(LADDER_MAP_POOL_1V1),
        "side_assignments": {"A": player1_registration_id, "B": player2_registration_id},
        "phase": "pool",
        "commanders": {"A": [], "B": []},
        "ban": {"A": None, "B": None},
        "available": {"A": [], "B": []},
        "pick": {"A": None, "B": None},
        "completed": False,
    }


def current_tournament_ladder_bp_game_state(state: dict[str, Any]) -> dict[str, Any] | None:
    ladder_games = state.get("ladder_games") or []
    if not ladder_games:
        return None
    return ladder_games[-1]


def finalize_tournament_ladder_bp_state(
    state: dict[str, Any],
    ladder_game: dict[str, Any],
    duration_seconds: int,
) -> None:
    phase = str(ladder_game.get("phase") or "pool")
    commanders = ladder_game.setdefault("commanders", {"A": [], "B": []})
    bans = ladder_game.setdefault("ban", {"A": None, "B": None})
    available = ladder_game.setdefault("available", {"A": [], "B": []})
    picks = ladder_game.setdefault("pick", {"A": None, "B": None})

    if phase == "pool" and len(commanders.get("A") or []) == 3 and len(commanders.get("B") or []) == 3:
        ladder_game["phase"] = "ban"
        phase = "ban"
    if phase == "ban" and bans.get("A") is not None and bans.get("B") is not None:
        selected_a = list(commanders.get("A") or LADDER_COMMANDER_POOL[:3])
        selected_b = list(commanders.get("B") or LADDER_COMMANDER_POOL[:3])
        ban_a = str(bans.get("A") or "")
        ban_b = str(bans.get("B") or "")
        available["A"] = [item for item in selected_a if item != ban_b] or selected_a
        available["B"] = [item for item in selected_b if item != ban_a] or selected_b
        ladder_game["phase"] = "pick"
        phase = "pick"
    if phase == "pick" and picks.get("A") and picks.get("B"):
        ladder_game["completed"] = True
        state["status"] = "ready_to_play"
        ensure_current_game_ready_at(state)
        stop_preparation_turn_clock(state, duration_seconds)
        return

    ladder_game["completed"] = False
    state["status"] = "bp_in_progress"
    clear_current_game_ready_at(state)
    start_preparation_turn_clock(state, duration_seconds)


def auto_complete_tournament_ladder_bp_game(ladder_game: dict[str, Any]) -> None:
    phase = str(ladder_game.get("phase") or "pool")
    commanders = ladder_game.setdefault("commanders", {"A": [], "B": []})
    bans = ladder_game.setdefault("ban", {"A": None, "B": None})
    available = ladder_game.setdefault("available", {"A": [], "B": []})
    picks = ladder_game.setdefault("pick", {"A": None, "B": None})

    if phase == "pool":
        for side in ("A", "B"):
            selected = normalize_ladder_commanders(commanders.get(side) or [], 3)
            if len(selected) != 3:
                selected = LADDER_COMMANDER_POOL[:3]
            commanders[side] = selected
        return

    if phase == "ban":
        for side in ("A", "B"):
            if bans.get(side) is None:
                bans[side] = ""
        return

    if phase == "pick":
        for side in ("A", "B"):
            if picks.get(side):
                continue
            options = list(available.get(side) or commanders.get(side) or LADDER_COMMANDER_POOL[:3])
            picks[side] = random.choice(options) if options else LADDER_COMMANDER_POOL[0]


def advance_tournament_ladder_bp_state(
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
    state: dict[str, Any],
) -> dict[str, Any]:
    best_of = int(match_row["best_of"] or default_best_of_for_bracket(match_row["bracket_type"]))
    next_game_number = int(state.get("current_game_number", 1)) + 1
    if next_game_number > best_of:
        raise ValueError("当前 BO 已达到最大局数，不能继续下一局。")

    next_game = build_tournament_ladder_bp_game_state(
        next_game_number,
        best_of,
        match_row["player1_registration_id"],
        match_row["player2_registration_id"],
    )
    state.setdefault("ladder_games", []).append(next_game)
    state["current_game_number"] = next_game_number
    state["advance_ready_ids"] = []
    state["ready_registration_ids"] = []
    finalize_tournament_ladder_bp_state(state, next_game, tournament_bp_step_seconds(tournament))
    return state


def match_preparation_supported(
    match_row: sqlite3.Row,
    registration_map: dict[int, sqlite3.Row],
) -> bool:
    player_ids = match_preparation_player_ids(match_row)
    if len(player_ids) != 2:
        return False
    return not any(
        registration_map.get(registration_id) and registration_map[registration_id]["is_temporary"]
        for registration_id in player_ids
    )


def build_match_preparation_view(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
    registration: sqlite3.Row | None,
    registration_map: dict[int, sqlite3.Row],
    recent_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    bp_mode = normalize_bp_mode(tournament["bp_mode"])
    state = sync_match_preparation_state(connection, tournament, match_row) or default_match_preparation_state(tournament)
    state.setdefault("step_started_at", None)
    state["step_deadline_seconds"] = int(state.get("step_deadline_seconds") or tournament_bp_step_seconds(tournament))
    ready_ids = set(int(value) for value in state.get("ready_registration_ids") or [])
    advance_ready_ids = set(int(value) for value in state.get("advance_ready_ids") or [])
    viewer_registration_id = registration["id"] if registration is not None else None
    player1_id = match_row["player1_registration_id"]
    player2_id = match_row["player2_registration_id"]
    fixed_sides = {
        player1_id: "A",
        player2_id: "B",
    }
    current_commander_game = current_commander_game_state(state)
    current_sides = dict(fixed_sides)
    if current_commander_game is not None:
        current_sides = {
            current_commander_game["side_assignments"]["A"]: "A",
            current_commander_game["side_assignments"]["B"]: "B",
        }
    replay_enabled = replay_result_enabled_for_tournament(tournament)
    best_of = int(match_row["best_of"] or default_best_of_for_bracket(match_row["bracket_type"]))
    current_game_number = max(1, int(state.get("current_game_number", 1) or 1))
    pending_manual_judgment = pending_manual_replay_judgment(state)
    pending_admin_judgment = pending_admin_replay_judgment(state)

    players = []
    for registration_id, seat_label in ((player1_id, "上方"), (player2_id, "下方")):
        participant = registration_map.get(registration_id)
        players.append(
            {
                "registration_id": registration_id,
                "name": slot_display_name(participant, MATCH_SLOT_PLAYER),
                "seat_label": seat_label,
                "fixed_side": fixed_sides.get(registration_id, "-"),
                "current_side": current_sides.get(registration_id, fixed_sides.get(registration_id, "-")),
                "ready": registration_id in ready_ids,
                "next_game_ready": registration_id in advance_ready_ids,
            }
        )

    can_use = match_preparation_supported(match_row, registration_map)
    replay_validation_error = replay_match_validation_error(connection, match_row, registration_map) if replay_enabled else None
    series_score1, series_score2 = match_series_score(state, match_row)
    replay_game_history = []
    for entry in match_game_results(state):
        winner_registration_id = entry.get("winner_registration_id")
        uploader_registration_id = entry.get("uploader_registration_id")
        replay_game_history.append(
            {
                "game_number": int(entry.get("game_number") or 0),
                "winner_name": preparation_participant_name(registration_map, winner_registration_id, "待判定"),
                "uploader_name": preparation_participant_name(registration_map, uploader_registration_id, "系统"),
                "filename": entry.get("filename") or "第 ? 场.SC2Replay",
                "saved_path": entry.get("saved_path") or "",
                "uploaded_at": entry.get("uploaded_at") or "",
            }
        )
    preparation_view = {
        "bp_mode": bp_mode,
        "bp_mode_label": BP_MODE_LABELS.get(bp_mode, bp_mode),
        "status": state.get("status", "awaiting_ready"),
        "status_label": {
            "awaiting_ready": "等待双方准备",
            "bp_in_progress": "BP 进行中",
            "ready_to_play": "本局可开打",
            "awaiting_admin_judgment": "等待管理员裁定",
            "complete": "本场已结束",
        }.get(state.get("status", "awaiting_ready"), "等待处理"),
        "players": players,
        "recent_matches": recent_matches or [],
        "can_ready": can_use and state.get("status") == "awaiting_ready" and viewer_registration_id is not None and viewer_registration_id not in ready_ids,
        "summary": "",
        "supported": can_use,
        "map_bp": None,
        "commander_bp": None,
        "conquest_bp": None,
        "ladder_bp": None,
        "evo_bp": None,
        "bracket_map_bp": None,
        "bracket_commander_bp": None,
        "bracket_conquest_bp": None,
        "replay_result_enabled": replay_enabled,
        "replay_upload": None,
        "turn_clock": preparation_turn_clock_view(state, tournament_bp_step_seconds(tournament)),
    }

    # 无论是否启用 replay 归档，都构建投票相关数据
    participant_names = {
        player1_id: preparation_participant_name(registration_map, player1_id, "上方"),
        player2_id: preparation_participant_name(registration_map, player2_id, "下方"),
    }
    player_ids = set(match_preparation_player_ids(match_row))

    if replay_enabled:
        pending_manual_view = None
        if pending_manual_judgment is not None:
            pending_manual_view = {
                "game_number": int(pending_manual_judgment.get("game_number") or 0),
                "filename": pending_manual_judgment.get("filename") or "",
                "saved_path": pending_manual_judgment.get("saved_path") or "",
                "uploaded_at": pending_manual_judgment.get("uploaded_at") or "",
                "uploader_name": preparation_participant_name(
                    registration_map,
                    pending_manual_judgment.get("uploader_registration_id"),
                    "系统",
                ),
                "detail": pending_manual_judgment.get("detail") or "当前 replay 需要手动选择赢家。",
            }
        pending_admin_view = None
        if pending_admin_judgment is not None:
            pending_admin_view = {
                "game_number": int(pending_admin_judgment.get("game_number") or 0),
                "requested_by_name": preparation_participant_name(
                    registration_map,
                    pending_admin_judgment.get("requester_registration_id"),
                    "系统",
                ),
                "requested_at": pending_admin_judgment.get("requested_at") or "",
                "detail": pending_admin_judgment.get("detail") or "当前小局已临时跳过，等待赛事管理员仲裁。",
            }
        preparation_view["replay_upload"] = {
            "can_upload": (
                replay_validation_error is None
                and viewer_registration_id is not None
                and viewer_registration_id in player_ids
                and state.get("status") == "ready_to_play"
                and match_row["status"] == "pending"
                and pending_manual_judgment is None
                and pending_admin_judgment is None
                and state.get("status") != "awaiting_vote"
                and len(replay_game_history) < best_of
                and not match_has_recorded_or_pending_game(state, current_game_number)
            ),
            "validation_error": replay_validation_error,
            "current_game_number": current_game_number,
            "best_of": best_of,
            "player1_name": participant_names[player1_id],
            "player2_name": participant_names[player2_id],
            "player1_score": series_score1,
            "player2_score": series_score2,
            "game_results": replay_game_history,
            "manual_resolution_pending": pending_manual_judgment is not None,
            "admin_resolution_pending": pending_admin_judgment is not None,
            "can_choose_manual_winner": bool(
                pending_manual_judgment is not None
                and viewer_registration_id is not None
                and viewer_registration_id in player_ids
                and match_row["status"] == "pending"
            ),
            "can_request_admin_skip": bool(
                viewer_registration_id is not None
                and viewer_registration_id in player_ids
                and state.get("status") in {"ready_to_play", "awaiting_vote"}
                and match_row["status"] == "pending"
                and pending_manual_judgment is None
                and pending_admin_judgment is None
                and not match_has_recorded_or_pending_game(state, current_game_number)
            ),
            "can_vote": bool(
                state.get("status") in {"ready_to_play", "awaiting_vote"}
                and viewer_registration_id is not None
                and viewer_registration_id in player_ids
                and match_row["status"] == "pending"
                and not state.get("votes_resolved")
                and str(viewer_registration_id) not in (state.get("pending_votes") or {})
            ),
            "manual_choice_options": [
                {"value": "player1", "label": participant_names[player1_id]},
                {"value": "player2", "label": participant_names[player2_id]},
            ],
            "pending_manual_judgment": pending_manual_view,
            "pending_admin_judgment": pending_admin_view,
            "awaiting_vote": state.get("status") in {"ready_to_play", "awaiting_vote"},
            "current_votes": state.get("pending_votes") or {},
            "voter_names": {
                str(k): preparation_participant_name(registration_map, int(k), f"选手 {k}")
                for k in (state.get("pending_votes") or {}).keys()
            },
        }
    else:
        # 不上传 rep 归档模式：仅保留投票表决功能
        preparation_view["replay_upload"] = {
            "can_upload": False,
            "can_request_admin_skip": False,
            "can_choose_manual_winner": False,
            "manual_resolution_pending": False,
            "admin_resolution_pending": False,
            "can_vote": bool(
                state.get("status") in {"ready_to_play", "awaiting_vote"}
                and viewer_registration_id is not None
                and viewer_registration_id in player_ids
                and match_row["status"] == "pending"
                and not state.get("votes_resolved")
                and str(viewer_registration_id) not in (state.get("pending_votes") or {})
            ),
            "awaiting_vote": state.get("status") in {"ready_to_play", "awaiting_vote"},
            "current_votes": state.get("pending_votes") or {},
            "voter_names": {
                str(k): preparation_participant_name(registration_map, int(k), f"选手 {k}")
                for k in (state.get("pending_votes") or {}).keys()
            },
            "player1_name": participant_names[player1_id],
            "player2_name": participant_names[player2_id],
            "player1_score": series_score1,
            "player2_score": series_score2,
            "game_results": replay_game_history,
            "current_game_number": current_game_number,
            "best_of": best_of,
            "validation_error": None,
            "manual_choice_options": [
                {"value": "player1", "label": participant_names[player1_id]},
                {"value": "player2", "label": participant_names[player2_id]},
            ],
            "pending_manual_judgment": None,
            "pending_admin_judgment": None,
        }

    if not can_use:
        preparation_view["summary"] = "当前对局尚未确定两名正式选手，准备比赛与 BP 组件暂不可用。"
        return preparation_view

    if bp_mode in {BP_MODE_MAP_POOL, BP_MODE_EVO}:
        preparation_view["bracket_map_bp"] = bracket_map_bp_snapshot(tournament, match_row, registration_map, state)
    elif bp_mode == BP_MODE_COMMANDER_DRAFT:
        preparation_view["bracket_commander_bp"] = bracket_commander_bp_snapshot(match_row, registration_map, state)
    elif bp_mode in {BP_MODE_COMMANDER_CONQUEST, BP_MODE_COMMANDER_CONQUEST_2V2}:
        preparation_view["bracket_conquest_bp"] = bracket_commander_conquest_snapshot(match_row, registration_map, state)

    if state.get("status") == "complete" or match_row["status"] == "completed":
        preparation_view["summary"] = "当前系列赛已结束。"
        return preparation_view

    waiting_players = [player["name"] for player in players if not player["ready"]]
    if state.get("status") == "awaiting_ready":
        preparation_view["summary"] = (
            f"双方都点击准备比赛后，将进入 {preparation_view['bp_mode_label']}。"
            if not waiting_players
            else f"当前等待：{'、'.join(waiting_players)}。双方准备完成后将进入 {preparation_view['bp_mode_label']}。"
        )
        return preparation_view

    if bp_mode == BP_MODE_NONE:
        if replay_enabled:
            if state.get("status") == "awaiting_vote":
                votes = state.get("pending_votes") or {}
                voted_names = [
                    preparation_participant_name(registration_map, int(vid), f"选手 {vid}")
                    for vid in votes.keys()
                ]
                remaining = [
                    preparation_participant_name(registration_map, pid, f"选手 {pid}")
                    for pid in match_preparation_player_ids(match_row)
                    if str(pid) not in votes
                ]
                if not remaining:
                    preparation_view["summary"] = f"双方均已投票（{'、'.join(voted_names)}），等待系统判定或仲裁。"
                else:
                    preparation_view["summary"] = f"已投票：{'、'.join(voted_names) if voted_names else '暂无'}；等待 {'、'.join(remaining)} 投票。"
            elif pending_manual_judgment is not None:
                preparation_view["summary"] = f"第 {pending_manual_view['game_number']} 局 replay 已上传，当前等待玩家手动选择本局赢家。"
            elif pending_admin_judgment is not None and state.get("status") == "awaiting_admin_judgment":
                preparation_view["summary"] = f"第 {pending_admin_view['game_number']} 局已临时跳过，当前等待赛事管理员仲裁本局结果。"
            elif pending_admin_judgment is not None:
                preparation_view["summary"] = f"第 {pending_admin_view['game_number']} 局已临时跳过并提交赛事管理员仲裁。当前仍可继续后续小局。"
            else:
                preparation_view["summary"] = f"双方都已准备完成，可以直接开始第 {current_game_number} 局。打完后请上传本局 replay 归档，由双方投票判定胜负。"
        else:
            preparation_view["summary"] = "双方都已准备完成，可以直接开始本场 BO。"
        return preparation_view

    if bp_mode in {BP_MODE_MAP_POOL, BP_MODE_EVO}:
        map_state = state.get("map_bp") or {}
        current_step = current_map_bp_step(map_state)
        actor_name = "系统"
        if current_step and current_step["actor"] in {"A", "B"}:
            actor_registration_id = player1_id if current_step["actor"] == "A" else player2_id
            actor_name = preparation_participant_name(registration_map, actor_registration_id, current_step["actor"])
        viewer_side = fixed_sides.get(viewer_registration_id)
        preparation_view["map_bp"] = {
            "available_maps": map_state.get("available_maps", []),
            "actions": map_state.get("actions", []),
            "games": map_state.get("games", []),
            "current_step": {
                "type": current_step["type"],
                "actor": current_step["actor"],
                "label": current_step["label"],
                "actor_name": actor_name,
            }
            if current_step is not None
            else None,
            "can_act": bool(current_step and current_step["type"] != "decider" and current_step["actor"] == viewer_side),
        }

        if bp_mode == BP_MODE_MAP_POOL:
            if current_step is None or map_state.get("completed"):
                if replay_enabled:
                    if pending_manual_judgment is not None:
                        preparation_view["summary"] = f"第 {pending_manual_view['game_number']} 局 replay 已上传，当前等待玩家手动选择本局赢家。"
                    elif pending_admin_judgment is not None and state.get("status") == "awaiting_admin_judgment":
                        preparation_view["summary"] = f"第 {pending_admin_view['game_number']} 局已临时跳过，当前等待赛事管理员仲裁本局结果。"
                    elif pending_admin_judgment is not None:
                        preparation_view["summary"] = f"第 {pending_admin_view['game_number']} 局已临时跳过并提交赛事管理员仲裁。当前仍可继续后续小局。"
                    else:
                        preparation_view["summary"] = f"本场 BO 的地图顺序已经确定，可以开始第 {current_game_number} 局。打完后请上传本局 replay 归档，由双方投票判定胜负。"
                else:
                    preparation_view["summary"] = "本场 BO 的地图顺序已经确定，可以直接开打。"
            else:
                preparation_view["summary"] = f"当前由 {actor_name} 执行 {current_step['label']}。"
            return preparation_view

        if current_step is not None and not map_state.get("completed"):
            preparation_view["summary"] = f"当前由 {actor_name} 执行 {current_step['label']}。"
            return preparation_view

        evo_view = build_evo_bp_view(match_row, registration_map, state, viewer_registration_id)
        preparation_view["evo_bp"] = evo_view
        current_choice_view = evo_view["current_choice"]
        current_game_map_name = evo_view["current_game_map_name"]

        if replay_enabled:
            if pending_manual_judgment is not None:
                preparation_view["summary"] = f"第 {pending_manual_view['game_number']} 局 replay 已上传，当前等待玩家手动选择本局赢家。"
                return preparation_view
            if pending_admin_judgment is not None and state.get("status") == "awaiting_admin_judgment":
                preparation_view["summary"] = f"第 {pending_admin_view['game_number']} 局已临时跳过，当前等待赛事管理员仲裁本局结果。裁定完成后才能开启下一局 EVO 选择。"
                return preparation_view

        if current_game_number == 1:
            if replay_enabled:
                preparation_view["summary"] = "本场 BO 的地图顺序已经确定，第 1 局不进行小局 BP，可以直接开始第 1 局。打完后请上传本局 replay 归档，由双方投票判定胜负。"
            else:
                preparation_view["summary"] = "本场 BO 的地图顺序已经确定，第 1 局不进行小局 BP，可以直接开打。"
        elif state.get("status") == "bp_in_progress" and current_choice_view is not None and not current_choice_view["choice_value"]:
            preparation_view["summary"] = (
                f"第 {current_game_number} 局地图为 {current_game_map_name}，"
                f"当前由上局败者 {current_choice_view['loser_name']} 选择 EVO 赛制。"
            )
        elif state.get("status") == "ready_to_play" and current_choice_view is not None and current_choice_view["choice_label"]:
            if replay_enabled:
                preparation_view["summary"] = (
                    f"第 {current_game_number} 局地图为 {current_game_map_name}，"
                    f"当前赛制为 {current_choice_view['choice_label']}。本局打完后，任一方上传有效 replay 即可自动判定并进入下一局选择。"
                )
            else:
                preparation_view["summary"] = (
                    f"第 {current_game_number} 局地图为 {current_game_map_name}，"
                    f"当前赛制为 {current_choice_view['choice_label']}。"
                )
        else:
            preparation_view["summary"] = f"第 {current_game_number} 局地图为 {current_game_map_name}，当前等待根据上一局结果生成 EVO 选项。"
        return preparation_view

    if bp_mode in {BP_MODE_COMMANDER_CONQUEST, BP_MODE_COMMANDER_CONQUEST_2V2}:
        series_state = state.get("conquest_series")
        if not isinstance(series_state, dict):
            preparation_view["summary"] = f"当前尚未生成可用的{conquest_mode_label(bp_mode)} BP。"
            return preparation_view

        current_game = current_commander_conquest_game_state(state)
        if current_game is None:
            preparation_view["summary"] = "当前尚未生成可用的征服者小局。"
            return preparation_view

        side_names = {
            side: preparation_participant_name(registration_map, registration_id, side)
            for side, registration_id in current_game["side_assignments"].items()
        }
        viewer_side = fixed_sides.get(viewer_registration_id)
        opponent_side = "B" if viewer_side == "A" else "A"
        phase = str(series_state.get("phase") or "pool")
        ppg = int(series_state.get("picks_per_game") or 1)
        first_to = int(series_state.get("first_to") or commander_conquest_first_to(best_of, ppg))
        pool_submissions = series_state.get("pool_submissions") or {"A": [], "B": []}

        # ---- 双方完成前隐藏对方结果 ----
        both_pool_submitted = (
            len(normalize_commander_catalog_selection(pool_submissions.get("A") or [], first_to)) == first_to
            and len(normalize_commander_catalog_selection(pool_submissions.get("B") or [], first_to)) == first_to
        )
        both_picked_current = (
            _conquest_pick_ready((current_game.get("pick") or {}).get("A"), ppg)
            and _conquest_pick_ready((current_game.get("pick") or {}).get("B"), ppg)
        )
        # 构建对外可见的预选池：双方都提交后才展示对方选择
        view_pool_submissions: dict[str, list[str]] = {
            "A": normalize_commander_catalog_selection(pool_submissions.get("A") or [], first_to),
            "B": normalize_commander_catalog_selection(pool_submissions.get("B") or [], first_to),
        }
        if not both_pool_submitted and viewer_side in {"A", "B"}:
            view_pool_submissions[opponent_side] = []
        # -----------------------------------

        can_submit_pool = (
            state.get("status") == "bp_in_progress"
            and phase == "pool"
            and viewer_side in {"A", "B"}
            and len(normalize_commander_catalog_selection(pool_submissions.get(viewer_side) or [], first_to)) != first_to
        )
        can_submit_pick = (
            state.get("status") == "bp_in_progress"
            and phase == "pick"
            and viewer_side in {"A", "B"}
            and not _conquest_pick_ready((current_game.get("pick") or {}).get(viewer_side), ppg)
        )
        conquest_games = []
        for game in series_state.get("games") or []:
            result = match_game_result_by_number(state, int(game.get("game_number") or 0))
            is_current_game = int(game.get("game_number") or 0) == int(current_game.get("game_number") or 0)
            # 当前局双方未都完成选择时，遮蔽对方出战指挥官
            game_pick: dict[str, Any] = dict(game.get("pick") or {"A": None, "B": None})
            if is_current_game and not both_picked_current and viewer_side in {"A", "B"}:
                game_pick = {**game_pick, opponent_side: None}
            conquest_games.append(
                {
                    "game_number": int(game.get("game_number") or 0),
                    "map_name": game.get("map_name") or "待生成",
                    "pick": game_pick,
                    "winner_name": preparation_participant_name(registration_map, result.get("winner_registration_id") if result else None, ""),
                    "is_current": is_current_game,
                }
            )

        # 构建对外可见的 current_game，遮蔽对手未完成时的选择
        view_current_game = dict(current_game)
        if not both_picked_current and viewer_side in {"A", "B"}:
            view_current_game["pick"] = {**view_current_game.get("pick", {}), opponent_side: None}

        preparation_view["conquest_bp"] = {
            "first_to": first_to,
            "picks_per_game": ppg,
            "phase": phase,
            "phase_label": {
                "pool": f"双方各预选 {first_to} 位指挥官",
                "pick": f"双方各选择本局 {ppg} 位出战指挥官" if ppg > 1 else "双方各选择本局出战指挥官",
            }.get(phase, "等待处理"),
            "current_game": view_current_game,
            "side_names": side_names,
            "pool_submissions": view_pool_submissions,
            "locked": {
                "A": commander_conquest_locked_commanders(state, "A"),
                "B": commander_conquest_locked_commanders(state, "B"),
            },
            "pool_options": [
                {
                    "value": commander_name,
                    "label": commander_option_label(commander_name),
                    "selected": commander_name in normalize_commander_catalog_selection(pool_submissions.get(viewer_side) or [], first_to),
                }
                for commander_name in commander_catalog_names()
            ] if viewer_side in {"A", "B"} else [],
            "pick_options": [
                {
                    "value": commander_name,
                    "label": commander_option_label(commander_name),
                }
                for commander_name in (current_game.get("available") or {}).get(viewer_side, [])
            ] if viewer_side in {"A", "B"} else [],
            "can_submit_pool": can_submit_pool,
            "can_submit_pick": can_submit_pick,
            "games": conquest_games,
        }

        if state.get("status") == "bp_in_progress":
            if phase == "pool":
                waiting_names = [
                    side_names[side]
                    for side in ("A", "B")
                    if len(normalize_commander_catalog_selection(pool_submissions.get(side) or [], first_to)) != first_to
                ]
                preparation_view["summary"] = (
                    f"本场 BO{best_of} 为{conquest_mode_label(bp_mode)}，双方需先各自预选 {first_to} 位指挥官。"
                    f" 当前第 1 局地图为 {current_game['map_name']}。"
                )
                if waiting_names:
                    preparation_view["summary"] += f" 当前等待：{'、'.join(waiting_names)}。"
            else:
                waiting_names = [side_names[side] for side in ("A", "B") if not _conquest_pick_ready((current_game.get("pick") or {}).get(side), ppg)]
                pick_label = f"各 {ppg} 位" if ppg > 1 else ""
                preparation_view["summary"] = (
                    f"第 {current_game['game_number']} 局地图为 {current_game['map_name']}，"
                    f"双方正在从自己的征服者预选池中选择本局{pick_label}出战指挥官。"
                )
                if waiting_names:
                    preparation_view["summary"] += f" 当前等待：{'、'.join(waiting_names)}。"
        elif state.get("status") == "ready_to_play":
            preparation_view["summary"] = (
                f"第 {current_game['game_number']} 局征服者 BP 已完成，地图为 {current_game['map_name']}。"
                "本局打完后请上传有效 replay，系统会自动锁定本局获胜方使用过的指挥官并开启下一局选择。"
            )
        return preparation_view

    if bp_mode == BP_MODE_LADDER_1V1:
        ladder_game = current_tournament_ladder_bp_game_state(state)
        if ladder_game is None:
            preparation_view["summary"] = "当前尚未生成可用的天梯 1v1 BP 小局。"
            return preparation_view

        side_assignments = ladder_game["side_assignments"]
        side_names = {
            side: preparation_participant_name(registration_map, registration_id, side)
            for side, registration_id in side_assignments.items()
        }
        viewer_side = fixed_sides.get(viewer_registration_id)
        phase = str(ladder_game.get("phase") or "pool")
        can_submit_pool = state.get("status") == "bp_in_progress" and phase == "pool" and viewer_side in {"A", "B"} and len(ladder_game["commanders"].get(viewer_side) or []) != 3
        can_submit_ban = state.get("status") == "bp_in_progress" and phase == "ban" and viewer_side in {"A", "B"} and ladder_game["ban"].get(viewer_side) is None
        can_submit_pick = state.get("status") == "bp_in_progress" and phase == "pick" and viewer_side in {"A", "B"} and not ladder_game["pick"].get(viewer_side)
        opponent_side = "B" if viewer_side == "A" else "A"
        can_advance = (
            not replay_enabled
            and state.get("status") == "ready_to_play"
            and match_row["status"] == "pending"
            and int(state.get("current_game_number", 1)) < best_of
            and viewer_registration_id is not None
            and viewer_registration_id not in advance_ready_ids
        )
        waiting_next_names = [player["name"] for player in players if not player["next_game_ready"]]
        ladder_games = []
        for game in state.get("ladder_games") or []:
            game_side_names = {
                side: preparation_participant_name(registration_map, registration_id, side)
                for side, registration_id in (game.get("side_assignments") or {}).items()
            }
            ladder_games.append(
                {
                    "game_number": int(game.get("game_number") or 0),
                    "map_name": game.get("map_name") or "待生成",
                    "commanders": game.get("commanders") or {"A": [], "B": []},
                    "ban": game.get("ban") or {"A": None, "B": None},
                    "pick": game.get("pick") or {"A": None, "B": None},
                    "side_names": game_side_names,
                    "is_current": ladder_game["game_number"] == game.get("game_number"),
                }
            )
        preparation_view["ladder_bp"] = {
            "current_game": ladder_game,
            "phase": phase,
            "phase_label": {
                "pool": "双方各选 3 位指挥官",
                "ban": "双方各禁用 1 位对方指挥官",
                "pick": "双方各选择本局出战指挥官",
            }.get(phase, "等待处理"),
            "side_names": side_names,
            "pool_options": [
                {
                    "value": commander,
                    "label": commander,
                    "selected": commander in (ladder_game.get("commanders") or {}).get(viewer_side, []),
                }
                for commander in LADDER_COMMANDER_POOL
            ] if viewer_side in {"A", "B"} else [],
            "ban_options": ([{"value": "", "label": "不禁用"}] + [
                {"value": commander, "label": commander}
                for commander in (ladder_game.get("commanders") or {}).get(opponent_side, [])
            ]) if viewer_side in {"A", "B"} else [],
            "pick_options": ([
                {"value": commander, "label": commander}
                for commander in (ladder_game.get("available") or {}).get(viewer_side, [])
            ] + [{"value": "random", "label": "随机"}]) if viewer_side in {"A", "B"} else [],
            "can_submit_pool": can_submit_pool,
            "can_submit_ban": can_submit_ban,
            "can_submit_pick": can_submit_pick,
            "can_advance": can_advance,
            "waiting_next_names": waiting_next_names,
            "games": ladder_games,
        }

        if state.get("status") == "bp_in_progress":
            if phase == "pool":
                waiting_names = [side_names[side] for side in ("A", "B") if len(ladder_game["commanders"].get(side) or []) != 3]
                preparation_view["summary"] = f"第 {ladder_game['game_number']} 局地图为 {ladder_game['map_name']}，当前需要双方各自选择 3 位指挥官。"
                if waiting_names:
                    preparation_view["summary"] += f" 当前等待：{'、'.join(waiting_names)}。"
            elif phase == "ban":
                waiting_names = [side_names[side] for side in ("A", "B") if ladder_game["ban"].get(side) is None]
                preparation_view["summary"] = f"第 {ladder_game['game_number']} 局地图为 {ladder_game['map_name']}，当前进行双方 Ban。"
                if waiting_names:
                    preparation_view["summary"] += f" 当前等待：{'、'.join(waiting_names)}。"
            else:
                waiting_names = [side_names[side] for side in ("A", "B") if not ladder_game["pick"].get(side)]
                preparation_view["summary"] = f"第 {ladder_game['game_number']} 局地图为 {ladder_game['map_name']}，当前进行双方 Pick。"
                if waiting_names:
                    preparation_view["summary"] += f" 当前等待：{'、'.join(waiting_names)}。"
        elif state.get("status") == "ready_to_play":
            wait_text = ""
            if not replay_enabled and 0 < len(advance_ready_ids) < len(players) and waiting_next_names:
                wait_text = f" 当前等待：{'、'.join(waiting_next_names)} 点击下一局。"
            if replay_enabled:
                preparation_view["summary"] = (
                    f"第 {ladder_game['game_number']} 局 BP 已完成，地图为 {ladder_game['map_name']}。"
                    "本局打完后，任一方上传有效 replay 即可自动判定并进入下一局天梯 1v1 BP。"
                )
            else:
                preparation_view["summary"] = (
                    f"第 {ladder_game['game_number']} 局 BP 已完成，地图为 {ladder_game['map_name']}。"
                    "本局打完后，双方都点击下一局即可重新执行下一小局的天梯 1v1 BP。"
                    f"{wait_text}"
                )
        return preparation_view

    commander_game = current_commander_game_state(state)
    if commander_game is None:
        preparation_view["summary"] = "当前尚未生成可用的 BP 小局。"
        return preparation_view

    step = current_commander_step(commander_game)
    side_assignments = commander_game["side_assignments"]
    side_names = {
        side: preparation_participant_name(registration_map, registration_id, side)
        for side, registration_id in side_assignments.items()
    }
    viewer_side = current_sides.get(viewer_registration_id)
    current_pending_choice = pending_commander_action(commander_game)
    action_options = [
        {
            "value": option["value"],
            "label": option["label"],
            "disabled": False,
            "selected": bool(current_pending_choice and current_pending_choice.get("value") == option["value"]),
        }
        for option in commander_action_options(commander_game)
    ]

    can_advance = (
        not replay_enabled
        and
        state.get("status") == "ready_to_play"
        and match_row["status"] == "pending"
        and int(state.get("current_game_number", 1)) < best_of
        and viewer_registration_id is not None
        and viewer_registration_id not in advance_ready_ids
    )
    waiting_next_names = [player["name"] for player in players if not player["next_game_ready"]]
    commander_games = []
    for game in state.get("commander_games") or []:
        game_side_names = {
            side: preparation_participant_name(registration_map, registration_id, side)
            for side, registration_id in game["side_assignments"].items()
        }
        commander_games.append(
            {
                "game_number": game["game_number"],
                "map_name": game["map_name"],
                "score_limit": game.get("score_limit"),
                "bans": game.get("bans", {}),
                "picks": game.get("picks", {"A": [], "B": []}),
                "side_names": game_side_names,
                "is_current": commander_game["game_number"] == game["game_number"],
            }
        )
    preparation_view["commander_bp"] = {
        "current_game": commander_game,
        "current_step": {
            "type": step["type"],
            "actor": step["actor"],
            "label": {
                "score_limit": "选择分数上限",
                "ban": "禁用指挥官",
                "pick": "选择指挥官",
            }[step["type"]],
            "actor_name": side_names.get(step["actor"], step["actor"]),
        }
        if step is not None
        else None,
        "can_act": bool(step is not None and step["actor"] == viewer_side and state.get("status") == "bp_in_progress"),
        "action_options": action_options,
        "pending_choice": current_pending_choice,
        "pending_choice_label": (
            f"{current_pending_choice['value']} 分上限"
            if current_pending_choice is not None and current_pending_choice.get("type") == "score_limit"
            else commander_option_label(str(current_pending_choice["value"]))
            if current_pending_choice is not None
            else ""
        ),
        "can_confirm": bool(
            step is not None
            and step["actor"] == viewer_side
            and state.get("status") == "bp_in_progress"
            and current_pending_choice is not None
            and current_pending_choice.get("actor") == viewer_side
        ),
        "side_names": side_names,
        "pick_totals": {
            "A": commander_pick_total(commander_game, "A"),
            "B": commander_pick_total(commander_game, "B"),

        },
        "can_advance": can_advance,
        "waiting_next_names": waiting_next_names,
        "games": commander_games,
    }
    if state.get("status") == "bp_in_progress" and step is not None:
        if step["type"] == "score_limit":
            preparation_view["summary"] = (
                f"第 {commander_game['game_number']} 局地图为 {commander_game['map_name']}，"
                f"当前由 {side_names.get(step['actor'], step['actor'])} 选择分数上限。"
            )
        elif step["type"] == "ban":
            preparation_view["summary"] = (
                f"第 {commander_game['game_number']} 局地图为 {commander_game['map_name']}，"
                f"当前由 {side_names.get(step['actor'], step['actor'])} 禁用指挥官。"
            )
        else:
            preparation_view["summary"] = (
                f"第 {commander_game['game_number']} 局地图为 {commander_game['map_name']}，"
                f"当前由 {side_names.get(step['actor'], step['actor'])} 进行 ABBA 指挥官选择。"
            )
        if current_pending_choice is not None:
            preparation_view["summary"] += f" 已预选：{preparation_view['commander_bp']['pending_choice_label']}，点击确定后才会正式提交。"
    elif state.get("status") == "awaiting_admin_judgment":
        if pending_admin_judgment is not None:
            preparation_view["summary"] = f"第 {pending_admin_view['game_number']} 局已临时跳过。当前已达到 BO 局数上限，等待赛事管理员仲裁后再结算本场。"
        else:
            preparation_view["summary"] = "当前等待赛事管理员处理上一局结果。"
    elif state.get("status") == "ready_to_play":
        if replay_enabled:
            if pending_manual_judgment is not None:
                preparation_view["summary"] = (
                    f"第 {pending_manual_view['game_number']} 局 replay 已上传。"
                    "由于双方绑定昵称相同，当前等待玩家手动选择本局赢家。"
                )
            elif pending_admin_judgment is not None:
                preparation_view["summary"] = (
                    f"第 {pending_admin_view['game_number']} 局已临时跳过并提交赛事管理员仲裁。"
                    f" 当前仍可继续第 {commander_game['game_number']} 局。"
                )
            else:
                preparation_view["summary"] = (
                    f"第 {commander_game['game_number']} 局 BP 已完成，地图为 {commander_game['map_name']}。"
                    "本局打完后，任一方上传有效 replay 即可自动判定并进入下一局。"
                )
        else:
            wait_text = ""
            if 0 < len(advance_ready_ids) < len(players) and waiting_next_names:
                wait_text = f" 当前等待：{'、'.join(waiting_next_names)} 点击下一局。"
            preparation_view["summary"] = (
                f"第 {commander_game['game_number']} 局 BP 已完成，地图为 {commander_game['map_name']}。"
                "本局打完后，双方都点击下一局即可开始后续 BP。"
                f"{wait_text}"
            )
    else:
        preparation_view["summary"] = "当前系列赛已结束。"
    return preparation_view


def attach_match_preparation_view(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
    registration_map: dict[int, sqlite3.Row],
    viewer_registration: sqlite3.Row | None = None,
    recent_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not tournament_started(tournament):
        return None
    if not match_preparation_player_ids(match_row):
        return None
    return build_match_preparation_view(
        connection,
        tournament,
        match_row,
        viewer_registration,
        registration_map,
        recent_matches,
    )


def append_bp_replay_frame(
    frames: list[dict[str, Any]],
    *,
    game_number: int,
    title: str,
    detail: str,
    map_name: str = "",
    commander_name: str = "",
    side_label: str = "",
    winner_name: str = "",
) -> None:
    full_asset = commander_asset_payload(commander_name, "full") if commander_name else {"image_url": "", "has_image": False}
    frames.append(
        {
            "index": len(frames) + 1,
            "game_number": game_number,
            "title": title,
            "detail": detail,
            "map_name": map_name,
            "commander_name": commander_name,
            "side_label": side_label,
            "winner_name": winner_name,
            "image_url": str(full_asset.get("image_url") or ""),
            "has_image": bool(full_asset.get("has_image")),
        }
    )


def build_match_bp_replay_summary_lines(
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
    registration_map: dict[int, sqlite3.Row],
    state: dict[str, Any],
) -> list[str]:
    bp_mode = normalize_bp_mode(state.get("bp_mode") or tournament["bp_mode"])
    summary_lines: list[str] = []

    if bp_mode in {BP_MODE_MAP_POOL, BP_MODE_EVO}:
        map_games = list((state.get("map_bp") or {}).get("games") or [])
        for game in map_games:
            game_number = int(game.get("game_number") or 0)
            line = f"第 {game_number} 局：{game.get('map_name') or '待定'}"
            if bp_mode == BP_MODE_EVO and game_number > 1:
                choice = evo_choice_for_game(state, game_number)
                choice_label = str((choice or {}).get("choice_label") or "")
                if choice_label:
                    line += f" · EVO：{choice_label}"
            summary_lines.append(line)
    elif bp_mode == BP_MODE_COMMANDER_DRAFT:
        for game in state.get("commander_games") or []:
            side_names = {
                side: preparation_participant_name(registration_map, registration_id, side)
                for side, registration_id in (game.get("side_assignments") or {}).items()
            }
            summary_lines.append(
                (
                    f"第 {int(game.get('game_number') or 0)} 局：{game.get('map_name') or '待定'}"
                    f" · {side_names.get('A', 'A')} [{'/'.join((game.get('picks') or {}).get('A', []) or ['未选'])}]"
                    f" vs {side_names.get('B', 'B')} [{'/'.join((game.get('picks') or {}).get('B', []) or ['未选'])}]"
                )
            )
    elif bp_mode in {BP_MODE_COMMANDER_CONQUEST, BP_MODE_COMMANDER_CONQUEST_2V2}:
        for game in (state.get("conquest_series") or {}).get("games") or []:
            result = match_game_result_by_number(state, int(game.get("game_number") or 0))
            winner_name = preparation_participant_name(registration_map, result.get("winner_registration_id") if result else None, "待定")
            pick_a = (game.get("pick") or {}).get("A")
            pick_b = (game.get("pick") or {}).get("B")
            pick_a_display = " / ".join(pick_a) if isinstance(pick_a, list) else (pick_a or "未选")
            pick_b_display = " / ".join(pick_b) if isinstance(pick_b, list) else (pick_b or "未选")
            summary_lines.append(
                (
                    f"第 {int(game.get('game_number') or 0)} 局：{game.get('map_name') or '待定'}"
                    f" · A 出战 {pick_a_display}"
                    f" vs B 出战 {pick_b_display}"
                    f" · 胜者：{winner_name}"
                )
            )
    elif bp_mode == BP_MODE_LADDER_1V1:
        for game in state.get("ladder_games") or []:
            summary_lines.append(
                (
                    f"第 {int(game.get('game_number') or 0)} 局：{game.get('map_name') or '待定'}"
                    f" · A Pick {(game.get('pick') or {}).get('A') or '未选'}"
                    f" vs B Pick {(game.get('pick') or {}).get('B') or '未选'}"
                )
            )

    return summary_lines


def build_match_bp_replay_payload(
    tournament: sqlite3.Row,
    match_row: sqlite3.Row,
    registration_map: dict[int, sqlite3.Row],
    state: dict[str, Any],
    game_number: int | None = None,
) -> dict[str, Any] | None:
    """构建 BP 回放帧序列。

    game_number 为 None 时返回完整回放；指定具体局号时仅返回该小局的 BP 帧，
    且最后的总结页不包含比赛胜负信息（防止剧透）。
    """
    bp_mode = normalize_bp_mode(state.get("bp_mode") or tournament["bp_mode"])
    best_of = int(match_row["best_of"] or default_best_of_for_bracket(match_row["bracket_type"]))
    frames: list[dict[str, Any]] = []
    per_game = game_number is not None
    _filter_game_number: int | None = game_number  # 保存参数，防止被循环变量覆盖

    if bp_mode in {BP_MODE_MAP_POOL, BP_MODE_EVO}:
        map_state = state.get("map_bp") or {}
        for action in map_state.get("actions") or []:
            actor = str(action.get("actor") or "")
            actor_registration_id = match_row["player1_registration_id"] if actor == "A" else match_row["player2_registration_id"] if actor == "B" else None
            actor_name = preparation_participant_name(registration_map, actor_registration_id, "系统")
            type_label = "禁图" if action.get("type") == "ban" else "选图" if action.get("type") == "pick" else "决胜图"
            append_bp_replay_frame(
                frames,
                game_number=int(action.get("game_number") or len(frames) + 1),
                title=f"{type_label} · {actor_name}",
                detail=f"{actor_name} {type_label} {action.get('map_name') or '待定'}",
                map_name=str(action.get("map_name") or ""),
                side_label=actor_name,
            )
        if bp_mode == BP_MODE_EVO:
            for choice in state.get("evo_choices") or []:
                loser_name = preparation_participant_name(registration_map, choice.get("loser_registration_id"), "上局败者")
                append_bp_replay_frame(
                    frames,
                    game_number=int(choice.get("game_number") or 0),
                    title=f"EVO 选择 · 第 {int(choice.get('game_number') or 0)} 局",
                    detail=f"{loser_name} 选择 {choice.get('choice_label') or EVO_CHOICE_LABELS.get(choice.get('choice_value'), '')}",
                    map_name=current_match_expected_map_name(tournament, match_row, {**state, 'current_game_number': int(choice.get('game_number') or 0)}) or "",
                    side_label=loser_name,
                )
    elif bp_mode == BP_MODE_COMMANDER_DRAFT:
        for game in state.get("commander_games") or []:
            game_number = int(game.get("game_number") or 0)
            side_names = {
                side: preparation_participant_name(registration_map, registration_id, side)
                for side, registration_id in (game.get("side_assignments") or {}).items()
            }
            if game.get("score_limit"):
                append_bp_replay_frame(
                    frames,
                    game_number=game_number,
                    title=f"第 {game_number} 局分数上限",
                    detail=f"本局分数上限：{game.get('score_limit')}",
                    map_name=str(game.get("map_name") or ""),
                )
            for side in ("A", "B"):
                if (game.get("bans") or {}).get(side):
                    append_bp_replay_frame(
                        frames,
                        game_number=game_number,
                        title=f"Ban · {side_names.get(side, side)}",
                        detail=f"{side_names.get(side, side)} 禁用了 {(game.get('bans') or {}).get(side)}",
                        map_name=str(game.get("map_name") or ""),
                        commander_name=str((game.get("bans") or {}).get(side) or ""),
                        side_label=side_names.get(side, side),
                    )
            for side in ("A", "B"):
                for commander_name in (game.get("picks") or {}).get(side, []):
                    append_bp_replay_frame(
                        frames,
                        game_number=game_number,
                        title=f"Pick · {side_names.get(side, side)}",
                        detail=f"{side_names.get(side, side)} 选择 {commander_name}",
                        map_name=str(game.get("map_name") or ""),
                        commander_name=commander_name,
                        side_label=side_names.get(side, side),
                    )
    elif bp_mode in {BP_MODE_COMMANDER_CONQUEST, BP_MODE_COMMANDER_CONQUEST_2V2}:
        series_state = state.get("conquest_series") or {}
        pool_submissions = series_state.get("pool_submissions") or {"A": [], "B": []}
        side_names = {
            "A": preparation_participant_name(registration_map, match_row["player1_registration_id"], "A"),
            "B": preparation_participant_name(registration_map, match_row["player2_registration_id"], "B"),
        }
        for side in ("A", "B"):
            selected_pool = normalize_commander_catalog_selection(pool_submissions.get(side) or [], int(series_state.get("first_to") or 0))
            if selected_pool:
                append_bp_replay_frame(
                    frames,
                    game_number=1,
                    title=f"预选池 · {side_names.get(side, side)}",
                    detail=f"{side_names.get(side, side)} 预选：{' / '.join(selected_pool)}",
                    commander_name=selected_pool[0],
                    side_label=side_names.get(side, side),
                )
        for game in series_state.get("games") or []:
            game_number = int(game.get("game_number") or 0)
            side_assignments = game.get("side_assignments") or {}
            game_side_names = {
                side: preparation_participant_name(registration_map, registration_id, side)
                for side, registration_id in side_assignments.items()
            }
            for side in ("A", "B"):
                pick_value = (game.get("pick") or {}).get(side)
                if isinstance(pick_value, list):
                    for commander_name in pick_value:
                        name = str(commander_name or "").strip()
                        if not name:
                            continue
                        append_bp_replay_frame(
                            frames,
                            game_number=game_number,
                            title=f"出战选择 · {game_side_names.get(side, side)}",
                            detail=f"{game_side_names.get(side, side)} 在第 {game_number} 局出战 {name}",
                            map_name=str(game.get("map_name") or ""),
                            commander_name=name,
                            side_label=game_side_names.get(side, side),
                        )
                else:
                    commander_name = str(pick_value or "").strip()
                    if not commander_name:
                        continue
                    append_bp_replay_frame(
                        frames,
                        game_number=game_number,
                        title=f"出战选择 · {game_side_names.get(side, side)}",
                        detail=f"{game_side_names.get(side, side)} 在第 {game_number} 局出战 {commander_name}",
                        map_name=str(game.get("map_name") or ""),
                        commander_name=commander_name,
                        side_label=game_side_names.get(side, side),
                    )
            result = match_game_result_by_number(state, game_number)
            if result is not None:
                winner_name = preparation_participant_name(registration_map, result.get("winner_registration_id"), "")
                winner_side = next(
                    (side for side, registration_id in side_assignments.items() if registration_id == result.get("winner_registration_id")),
                    None,
                )
                if winner_side is not None:
                    winning_pick = (game.get("pick") or {}).get(winner_side)
                    if isinstance(winning_pick, list):
                        winning_commander = " / ".join(str(c or "").strip() for c in winning_pick)
                    else:
                        winning_commander = str(winning_pick or "").strip()
                    append_bp_replay_frame(
                        frames,
                        game_number=game_number,
                        title=f"征服者锁定 · {winner_name}",
                        detail=f"{winner_name} 使用 {winning_commander} 赢下本局，后续不可再选该指挥官。",
                        map_name=str(game.get("map_name") or ""),
                        commander_name=str(winning_pick[0] if isinstance(winning_pick, list) and winning_pick else winning_pick or ""),
                        side_label=winner_name,
                        winner_name=winner_name,
                    )
    elif bp_mode == BP_MODE_LADDER_1V1:
        for game in state.get("ladder_games") or []:
            game_number = int(game.get("game_number") or 0)
            side_names = {
                side: preparation_participant_name(registration_map, registration_id, side)
                for side, registration_id in (game.get("side_assignments") or {}).items()
            }
            for side in ("A", "B"):
                commanders = list((game.get("commanders") or {}).get(side) or [])
                if commanders:
                    append_bp_replay_frame(
                        frames,
                        game_number=game_number,
                        title=f"预选池 · {side_names.get(side, side)}",
                        detail=f"{side_names.get(side, side)} 选择：{' / '.join(commanders)}",
                        map_name=str(game.get("map_name") or ""),
                        commander_name=commanders[0],
                        side_label=side_names.get(side, side),
                    )
                if (game.get("ban") or {}).get(side) is not None:
                    banned = (game.get("ban") or {}).get(side) or "不禁用"
                    append_bp_replay_frame(
                        frames,
                        game_number=game_number,
                        title=f"Ban · {side_names.get(side, side)}",
                        detail=f"{side_names.get(side, side)} Ban：{banned}",
                        map_name=str(game.get("map_name") or ""),
                        commander_name=str((game.get("ban") or {}).get(side) or ""),
                        side_label=side_names.get(side, side),
                    )
                if (game.get("pick") or {}).get(side):
                    commander_name = str((game.get("pick") or {}).get(side) or "")
                    append_bp_replay_frame(
                        frames,
                        game_number=game_number,
                        title=f"Pick · {side_names.get(side, side)}",
                        detail=f"{side_names.get(side, side)} Pick：{commander_name}",
                        map_name=str(game.get("map_name") or ""),
                        commander_name=commander_name,
                        side_label=side_names.get(side, side),
                    )

    # ---- 逐局过滤与重组 ----
    if per_game and _filter_game_number is not None:
        target = int(_filter_game_number)
        if bp_mode in {BP_MODE_COMMANDER_CONQUEST, BP_MODE_COMMANDER_CONQUEST_2V2}:
            # 征服者逐局重组：
            #   第 1 局：预选池帧 + 本局出战选择帧
            #   第 N 局（N≥2）：第 N-1 局锁定帧（首页）+ 本局出战选择帧
            #   所有局：不含本局锁定帧；总结页不展示胜者
            rebuilt: list[dict[str, Any]] = []
            if target == 1:
                # 预选池帧
                for f in frames:
                    if "预选池" in str(f.get("title") or ""):
                        rebuilt.append(f)
            else:
                # 上一局的锁定帧作为本局首页
                for f in frames:
                    if int(f.get("game_number") or 0) == target - 1 and "征服者锁定" in str(f.get("title") or ""):
                        rebuilt.append(f)
            # 本局出战选择帧
            for f in frames:
                if int(f.get("game_number") or 0) == target and "出战选择" in str(f.get("title") or ""):
                    rebuilt.append(f)
            frames = rebuilt
        else:
            # 其他 BP 模式：简单按局号过滤
            frames = [f for f in frames if int(f.get("game_number") or 0) == target]

        # 重新编号帧索引
        for idx, f in enumerate(frames):
            f["index"] = idx + 1
    # --------------------

    if not frames:
        return None

    if per_game:
        # 逐局总结：仅展示该小局的 BP 结果，不展示比赛胜负
        single_game_summary_lines: list[str] = []
        target = int(_filter_game_number or 0)
        if bp_mode in {BP_MODE_MAP_POOL, BP_MODE_EVO}:
            map_games = list((state.get("map_bp") or {}).get("games") or [])
            for g in map_games:
                if int(g.get("game_number") or 0) == target:
                    line = f"第 {target} 局：{g.get('map_name') or '待定'}"
                    if bp_mode == BP_MODE_EVO and target > 1:
                        choice = evo_choice_for_game(state, target)
                        choice_label = str((choice or {}).get("choice_label") or "")
                        if choice_label:
                            line += f" · EVO：{choice_label}"
                    single_game_summary_lines.append(line)
        elif bp_mode == BP_MODE_COMMANDER_DRAFT:
            for g in state.get("commander_games") or []:
                if int(g.get("game_number") or 0) == target:
                    side_names_g = {
                        side: preparation_participant_name(registration_map, registration_id, side)
                        for side, registration_id in (g.get("side_assignments") or {}).items()
                    }
                    single_game_summary_lines.append(
                        f"第 {target} 局：{g.get('map_name') or '待定'}"
                        f" · {side_names_g.get('A', 'A')} [{'/'.join((g.get('picks') or {}).get('A', []) or ['未选'])}]"
                        f" vs {side_names_g.get('B', 'B')} [{'/'.join((g.get('picks') or {}).get('B', []) or ['未选'])}]"
                    )
        elif bp_mode in {BP_MODE_COMMANDER_CONQUEST, BP_MODE_COMMANDER_CONQUEST_2V2}:
            for g in (state.get("conquest_series") or {}).get("games") or []:
                if int(g.get("game_number") or 0) == target:
                    pick_a = (g.get("pick") or {}).get("A")
                    pick_b = (g.get("pick") or {}).get("B")
                    pick_a_display = " / ".join(pick_a) if isinstance(pick_a, list) else (pick_a or "未选")
                    pick_b_display = " / ".join(pick_b) if isinstance(pick_b, list) else (pick_b or "未选")
                    single_game_summary_lines.append(
                        f"第 {target} 局：{g.get('map_name') or '待定'}"
                        f" · A 出战 {pick_a_display}"
                        f" vs B 出战 {pick_b_display}"
                    )
        elif bp_mode == BP_MODE_LADDER_1V1:
            for g in state.get("ladder_games") or []:
                if int(g.get("game_number") or 0) == target:
                    single_game_summary_lines.append(
                        f"第 {target} 局：{g.get('map_name') or '待定'}"
                        f" · A Pick {(g.get('pick') or {}).get('A') or '未选'}"
                        f" vs B Pick {(g.get('pick') or {}).get('B') or '未选'}"
                    )
        summary_lines = single_game_summary_lines
        summary_detail = f"第 {target} 局 BP 播放完成，当前停留在本局结果总览页。"
    else:
        summary_lines = build_match_bp_replay_summary_lines(tournament, match_row, registration_map, state)
        summary_detail = "本轮 BP 播放完成，当前停留在结果总览页。"

    highlight_commander = next(
        (str(frame.get("commander_name") or "") for frame in reversed(frames) if str(frame.get("commander_name") or "").strip()),
        "",
    )
    highlight_asset = commander_asset_payload(highlight_commander, "full") if highlight_commander else {"image_url": "", "has_image": False}
    frames.append(
        {
            "index": len(frames) + 1,
            "game_number": (target if per_game else best_of),
            "title": "BP 结果总览",
            "detail": summary_detail,
            "map_name": "",
            "commander_name": highlight_commander,
            "side_label": "",
            "winner_name": "",
            "summary_lines": summary_lines,
            "image_url": str(highlight_asset.get("image_url") or ""),
            "has_image": bool(highlight_asset.get("has_image")),
        }
    )

    return {
        "tournament_id": int(tournament["id"]),
        "tournament_name": str(tournament["name"]),
        "match_id": int(match_row["id"]),
        "bp_mode": bp_mode,
        "bp_mode_label": BP_MODE_LABELS.get(bp_mode, bp_mode),
        "best_of": best_of,
        "series_label": f"BO{best_of}",
        "player1_name": preparation_participant_name(registration_map, match_row["player1_registration_id"], "上方"),
        "player2_name": preparation_participant_name(registration_map, match_row["player2_registration_id"], "下方"),
        "frames": frames,
    }


def list_bp_replay_match_options(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            tm.id AS match_id,
            tm.tournament_id,
            t.name AS tournament_name,
            t.bp_mode,
            tm.best_of,
            tm.bracket_type,
            tm.round_number,
            tm.match_number,
            tm.player1_registration_id,
            tm.player2_registration_id,
            tm.updated_at
        FROM tournament_match_preparations prep
        JOIN tournament_matches tm
          ON tm.tournament_id = prep.tournament_id
         AND tm.bracket_type = prep.bracket_type
         AND tm.round_number = prep.round_number
         AND tm.match_number = prep.match_number
        JOIN tournaments t ON t.id = tm.tournament_id
        ORDER BY t.created_at DESC, tm.updated_at DESC, tm.id DESC
        """
    ).fetchall()
    match_options: list[dict[str, Any]] = []
    for row in rows:
        best_of_val = int(row["best_of"] or default_best_of_for_bracket(row["bracket_type"]))
        match_options.append(
            {
                "match_id": int(row["match_id"]),
                "tournament_id": int(row["tournament_id"]),
                "tournament_name": str(row["tournament_name"]),
                "bp_mode_label": BP_MODE_LABELS.get(normalize_bp_mode(row["bp_mode"]), row["bp_mode"]),
                "series_label": f"R{int(row['round_number'])}M{int(row['match_number'])} · BO{best_of_val}",
                "best_of": best_of_val,
            }
        )
    return match_options


def build_bp_sync_view(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    active_tab: str,
    my_matches_state: dict[str, Any],
    bracket_state: dict[str, Any],
    manager_preparation_overview: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not tournament_started(tournament):
        return None

    if active_tab == "my_matches" and my_matches_state.get("current_match") is not None:
        return build_my_matches_bp_sync_view(connection, tournament, my_matches_state)

    if active_tab == "info" and manager_preparation_overview:
        return {
            "url": f"/tournaments/{tournament['id']}/bp/sync",
            "key": tournament_preparation_sync_key(connection, tournament["id"]),
            "interval_ms": BP_SYNC_POLL_MS,
            "partial_url": f"/tournaments/{tournament['id']}/info/partial",
            "partial_target": "info-partial",
        }

    if active_tab != "bracket":
        return None

    has_visible_preparation = any(
        match.get("preparation") is not None
        for group in bracket_state.get("groups", [])
        for round_view in group.get("rounds", [])
        for match in round_view.get("matches", [])
    )
    if not has_visible_preparation:
        return None
    return {
        "url": f"/tournaments/{tournament['id']}/bp/sync",
        "key": tournament_preparation_sync_key(connection, tournament["id"]),
        "interval_ms": BP_SYNC_POLL_MS,
        "partial_url": f"/tournaments/{tournament['id']}/bracket/partial",
        "partial_target": "bracket-partial",
    }


def completed_match_key(bracket_type: str, round_number: int, match_number: int) -> tuple[str, int, int]:
    return (bracket_type, round_number, match_number)


def load_completed_match_map(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> dict[tuple[str, int, int], sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT *
        FROM tournament_matches
        WHERE tournament_id = ? AND status = 'completed'
        ORDER BY bracket_type ASC,
            round_number ASC,
            match_number ASC,
            id ASC
        """,
        (tournament_id,),
    ).fetchall()
    return {
        completed_match_key(row["bracket_type"], row["round_number"], row["match_number"]): row
        for row in rows
    }


def load_match_row_map(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> dict[tuple[str, int, int], sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT *
        FROM tournament_matches
        WHERE tournament_id = ?
        ORDER BY bracket_type ASC, round_number ASC, match_number ASC, id ASC
        """,
        (tournament_id,),
    ).fetchall()
    return {
        completed_match_key(row["bracket_type"], row["round_number"], row["match_number"]): row
        for row in rows
    }


def saved_result_matches_pairing(
    saved_row: sqlite3.Row | None,
    player1_id: int | None,
    player2_id: int | None,
    player1_slot_type: str,
    player2_slot_type: str,
) -> bool:
    if saved_row is None:
        return False
    if saved_row["player1_registration_id"] != player1_id:
        return False
    if saved_row["player2_registration_id"] != player2_id:
        return False
    if saved_row["player1_slot_type"] != player1_slot_type:
        return False
    if saved_row["player2_slot_type"] != player2_slot_type:
        return False
    return saved_row["winner_registration_id"] in {player1_id, player2_id} or (
        player1_slot_type == MATCH_SLOT_BYE
        and player2_slot_type == MATCH_SLOT_BYE
        and saved_row["winner_registration_id"] is None
        and saved_row["status"] == "completed"
    ) or (
        player1_slot_type == MATCH_SLOT_PLAYER
        and player2_slot_type == MATCH_SLOT_PLAYER
        and saved_row["winner_registration_id"] is None
        and saved_row["loser_registration_id"] is None
        and saved_row["status"] == "completed"
    )


def next_power_of_two(value: int) -> int:
    size = 1
    while size < max(2, value):
        size *= 2
    return size


def create_match_round(
    connection: sqlite3.Connection,
    tournament_id: int,
    bracket_type: str,
    round_number: int,
    pairs: list[tuple[tuple[int | None, str] | int | None, tuple[int | None, str] | int | None]],
    best_of: int | None = None,
    best_of_values: list[int] | None = None,
    saved_results: dict[tuple[str, int, int], sqlite3.Row] | None = None,
    auto_advance_byes: bool = False,
    stage_index: int = 1,
    group_number: int = 1,
    existing_matches: dict[tuple[str, int, int], sqlite3.Row] | None = None,
) -> list[sqlite3.Row]:
    stored_bracket_type = bracket_storage_key(bracket_type, stage_index, group_number)
    default_best_of = int(
        best_of
        if best_of is not None
        else load_round_best_of(connection, tournament_id, bracket_type, round_number, stage_index, group_number)
    )
    validate_best_of(default_best_of, allow_even=bracket_allows_even_best_of(bracket_type))
    if best_of_values is not None and len(best_of_values) != len(pairs):
        raise ValueError("best_of_values length must match pairs length")
    created_rows: list[sqlite3.Row] = []
    for index, raw_pair in enumerate(pairs, start=1):
        match_best_of = int(best_of_values[index - 1]) if best_of_values is not None else default_best_of
        validate_best_of(match_best_of, allow_even=bracket_allows_even_best_of(bracket_type))
        player1_slot, player2_slot = (normalize_match_slot(raw_pair[0]), normalize_match_slot(raw_pair[1]))
        player1_id, player1_slot_type = player1_slot
        player2_id, player2_slot_type = player2_slot
        match_key = completed_match_key(stored_bracket_type, round_number, index)
        existing_row = None if existing_matches is None else existing_matches.get(match_key)
        inserted_at = now_text()
        if existing_row is None:
            connection.execute(
                """
                INSERT INTO tournament_matches (
                    tournament_id, bracket_type, round_number, match_number,
                    best_of,
                    player1_registration_id, player1_slot_type,
                    player2_registration_id, player2_slot_type,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tournament_id,
                    stored_bracket_type,
                    round_number,
                    index,
                    match_best_of,
                    player1_id,
                    player1_slot_type,
                    player2_id,
                    player2_slot_type,
                    inserted_at,
                    inserted_at,
                ),
            )
        else:
            connection.execute(
                """
                INSERT INTO tournament_matches (
                    id,
                    tournament_id, bracket_type, round_number, match_number,
                    best_of,
                    player1_registration_id, player1_slot_type,
                    player2_registration_id, player2_slot_type,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    existing_row["id"],
                    tournament_id,
                    stored_bracket_type,
                    round_number,
                    index,
                    match_best_of,
                    player1_id,
                    player1_slot_type,
                    player2_id,
                    player2_slot_type,
                    existing_row["created_at"],
                    inserted_at,
                ),
            )
        row = connection.execute(
            """
            SELECT *
            FROM tournament_matches
            WHERE tournament_id = ? AND bracket_type = ? AND round_number = ? AND match_number = ?
            """,
            (tournament_id, stored_bracket_type, round_number, index),
        ).fetchone()
        saved_row = None if saved_results is None else saved_results.get(match_key)
        if saved_result_matches_pairing(saved_row, player1_id, player2_id, player1_slot_type, player2_slot_type):
            complete_match_internal(
                connection,
                row,
                saved_row["winner_registration_id"],
                saved_row["score1"],
                saved_row["score2"],
                best_of=int(saved_row["best_of"] or 3),
            )
        elif auto_advance_byes and player1_slot_type == MATCH_SLOT_BYE and player2_slot_type == MATCH_SLOT_BYE:
            connection.execute(
                """
                UPDATE tournament_matches
                SET best_of = 1,
                    score1 = 0,
                    score2 = 0,
                    status = 'completed',
                    updated_at = ?
                WHERE id = ?
                """,
                (now_text(), row["id"]),
            )
        elif auto_advance_byes and player1_id is not None and player2_slot_type == MATCH_SLOT_BYE:
            complete_match_internal(connection, row, player1_id, 1, 0, best_of=1)
        elif auto_advance_byes and player2_id is not None and player1_slot_type == MATCH_SLOT_BYE:
            complete_match_internal(connection, row, player2_id, 0, 1, best_of=1)

        created_rows.append(
            connection.execute(
                "SELECT * FROM tournament_matches WHERE id = ?",
                (row["id"],),
            ).fetchone()
        )
    return created_rows


def complete_match_internal(
    connection: sqlite3.Connection,
    match_row: sqlite3.Row,
    winner_registration_id: int | None,
    score1: int,
    score2: int,
    best_of: int | None = None,
) -> None:
    player1_id = match_row["player1_registration_id"]
    player2_id = match_row["player2_registration_id"]
    actual_best_of = int(best_of if best_of is not None else (match_row["best_of"] or 3))
    validate_best_of(actual_best_of, allow_even=bracket_allows_even_best_of(match_row["bracket_type"]))
    loser_registration_id = None
    if winner_registration_id is None:
        if not bracket_allows_even_best_of(match_row["bracket_type"]) or actual_best_of % 2 != 0 or score1 != score2:
            raise HTTPException(status_code=400, detail="当前对局不能录入平局。")
    elif winner_registration_id == player1_id:
        loser_registration_id = player2_id
    elif winner_registration_id == player2_id:
        loser_registration_id = player1_id
    else:
        raise HTTPException(status_code=400, detail="获胜方不在当前对局中。")

    connection.execute(
        """
        UPDATE tournament_matches
        SET winner_registration_id = ?,
            loser_registration_id = ?,
            best_of = ?,
            score1 = ?,
            score2 = ?,
            status = 'completed',
            updated_at = ?
        WHERE id = ?
        """,
        (
            winner_registration_id,
            loser_registration_id,
            actual_best_of,
            score1,
            score2,
            now_text(),
            match_row["id"],
        ),
    )
    refresh_tournament_replay_archive_for_tournament(connection, match_row["tournament_id"])


def load_round_winners(
    connection: sqlite3.Connection,
    tournament_id: int,
    bracket_type: str,
    round_number: int,
    stage_index: int = 1,
    group_number: int = 1,
) -> list[int]:
    stored_bracket_type = bracket_storage_key(bracket_type, stage_index, group_number)
    rows = connection.execute(
        """
        SELECT * FROM tournament_matches
        WHERE tournament_id = ? AND bracket_type = ? AND round_number = ?
        ORDER BY match_number ASC
        """,
        (tournament_id, stored_bracket_type, round_number),
    ).fetchall()
    if not rows or any(row["status"] != "completed" for row in rows):
        return []
    return [row["winner_registration_id"] for row in rows if row["winner_registration_id"] is not None]


def round_robin_rounds(registration_ids: list[int], cycles: int = DEFAULT_ROUND_ROBIN_CYCLES) -> list[list[tuple[int, int]]]:
    players: list[int | None] = list(registration_ids)
    if len(players) % 2 == 1:
        players.append(None)

    total_players = len(players)
    if total_players < 2:
        return []

    single_cycle_rounds: list[list[tuple[int, int]]] = []
    order = players[:]
    for _ in range(total_players - 1):
        current_round: list[tuple[int, int]] = []
        for index in range(total_players // 2):
            left = order[index]
            right = order[total_players - 1 - index]
            if left is None or right is None:
                continue
            current_round.append((left, right))
        single_cycle_rounds.append(current_round)
        order = [order[0], order[-1], *order[1:-1]]

    rounds: list[list[tuple[int, int]]] = []
    for _ in range(max(1, cycles)):
        rounds.extend([list(current_round) for current_round in single_cycle_rounds])
    return rounds


def swiss_round_limit(participant_count: int) -> int:
    return max(1, math.ceil(math.log2(max(2, participant_count))))


def build_registration_stats(
    registrations: list[sqlite3.Row],
    match_rows: list[sqlite3.Row],
    win_points: int = DEFAULT_ROUND_ROBIN_WIN_POINTS,
    draw_points: int = DEFAULT_ROUND_ROBIN_DRAW_POINTS,
    loss_points: int = DEFAULT_ROUND_ROBIN_LOSS_POINTS,
) -> dict[int, dict[str, Any]]:
    seed_order = {row["id"]: index for index, row in enumerate(registrations)}
    stats: dict[int, dict[str, Any]] = {
        row["id"]: {
            "registration_id": row["id"],
            "nickname": (str(row["nickname"] or "").strip()
                         or str((row["account_game_nickname"] if "account_game_nickname" in row.keys() else None) or "").strip()
                         or str((row["account_name"] if "account_name" in row.keys() else None) or "").strip()
                         or "未命名"),
            "account_name": row["account_name"] if "account_name" in row.keys() else row["nickname"],
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "played": 0,
            "points": 0,
            "opponents": [],
        }
        for row in registrations
    }

    for match_row in match_rows:
        if match_row["status"] != "completed":
            continue
        player1_id = match_row["player1_registration_id"]
        player2_id = match_row["player2_registration_id"]
        winner_id = match_row["winner_registration_id"]
        loser_id = match_row["loser_registration_id"]

        if player1_id is not None and player1_id in stats:
            stats[player1_id]["played"] += 1
        if player2_id is not None and player2_id in stats:
            stats[player2_id]["played"] += 1
        if player1_id is not None and player2_id is not None:
            if player1_id in stats:
                stats[player1_id]["opponents"].append(player2_id)
            if player2_id in stats:
                stats[player2_id]["opponents"].append(player1_id)
        if winner_id is None and loser_id is None and player1_id is not None and player2_id is not None:
            stats[player1_id]["draws"] += 1
            stats[player2_id]["draws"] += 1
            stats[player1_id]["points"] += draw_points
            stats[player2_id]["points"] += draw_points
        if winner_id is not None and winner_id in stats:
            stats[winner_id]["wins"] += 1
            stats[winner_id]["points"] += win_points
        if loser_id is not None and loser_id in stats:
            stats[loser_id]["losses"] += 1
            stats[loser_id]["points"] += loss_points

    for registration_id, values in stats.items():
        values["buchholz"] = sum(
            stats[opponent_id]["wins"]
            for opponent_id in values["opponents"]
            if opponent_id in stats
        )
        values["seed"] = seed_order[registration_id]

    return stats


def calculate_standings(
    registrations: list[sqlite3.Row],
    match_rows: list[sqlite3.Row],
    points_mode: bool = False,
    win_points: int = DEFAULT_ROUND_ROBIN_WIN_POINTS,
    draw_points: int = DEFAULT_ROUND_ROBIN_DRAW_POINTS,
    loss_points: int = DEFAULT_ROUND_ROBIN_LOSS_POINTS,
) -> list[dict[str, Any]]:
    stats = build_registration_stats(
        registrations,
        match_rows,
        win_points=win_points,
        draw_points=draw_points,
        loss_points=loss_points,
    )

    if points_mode:
        return sorted(
            stats.values(),
            key=lambda row: (-row["points"], -row["wins"], -row["buchholz"], row["seed"]),
        )

    return sorted(
        stats.values(),
        key=lambda row: (-row["wins"], -row["buchholz"], row["seed"]),
    )


def build_tournament_rankings(connection, tournament):
    """赛事排行榜——统计报名选手的胜负。所有异常均安全回退。"""
    try:
        registrations = load_registrations(connection, tournament["id"])
    except Exception:
        return []
    if not registrations:
        return []
    
    regs_by_id = {r["id"]: r for r in registrations}
    finished = tournament_is_finished(connection, tournament)
    
    wins = {}
    losses = {}
    played = {}
    for r in registrations:
        wins[r["id"]] = 0
        losses[r["id"]] = 0
        played[r["id"]] = 0
    
    try:
        matches = connection.execute(
            "SELECT player1_registration_id, player2_registration_id, winner_registration_id, loser_registration_id, status FROM tournament_matches WHERE tournament_id=?",
            (tournament["id"],)
        ).fetchall()
    except Exception:
        matches = []
    
    for m in matches:
        for pid in (m["player1_registration_id"], m["player2_registration_id"]):
            if pid and pid in played:
                played[pid] += 1
        if m["status"] == "completed":
            w = m["winner_registration_id"]
            l = m["loser_registration_id"]
            if w and w in wins:
                wins[w] += 1
            if l and l in losses:
                losses[l] += 1
    
    entries = []
    for r in registrations:
        rid = r["id"]
        try:
            name = slot_display_name(r, MATCH_SLOT_PLAYER)
        except Exception:
            name = str(r.get("nickname") or "").strip() or ("选手 " + str(rid))
        entries.append({
            "rank": 0, "name": name,
            "wins": wins.get(rid, 0), "losses": losses.get(rid, 0),
            "played": played.get(rid, 0), "is_final": finished,
            "avatar_path": r["avatar_path"],
            "avatar_frame": r["avatar_frame"],
            "nickname_frame": r["nickname_frame"],
            "nickname_color": r["nickname_color"],
            "game_nickname": r["account_game_nickname"],
        })
    
    entries.sort(key=lambda e: (-e["wins"], e["losses"]))
    for i, e in enumerate(entries):
        e["rank"] = i + 1
    return entries


def swiss_pairings(
    registrations: list[sqlite3.Row],
    match_rows: list[sqlite3.Row],
) -> list[tuple[tuple[int | None, str], tuple[int | None, str]]]:
    standings = calculate_standings(registrations, match_rows)
    played_pairs = {
        tuple(sorted((row["player1_registration_id"], row["player2_registration_id"])))
        for row in match_rows
        if row["player1_registration_id"] is not None and row["player2_registration_id"] is not None
    }

    remaining = [row["registration_id"] for row in standings]
    pairs: list[tuple[tuple[int | None, str], tuple[int | None, str]]] = []
    while remaining:
        player_id = remaining.pop(0)
        if not remaining:
            pairs.append((make_match_slot(player_id, MATCH_SLOT_PLAYER), make_match_slot(None, MATCH_SLOT_BYE)))
            break

        opponent_index = 0
        for index, candidate_id in enumerate(remaining):
            if tuple(sorted((player_id, candidate_id))) not in played_pairs:
                opponent_index = index
                break
        opponent_id = remaining.pop(opponent_index)
        pairs.append((make_match_slot(player_id, MATCH_SLOT_PLAYER), make_match_slot(opponent_id, MATCH_SLOT_PLAYER)))
    return pairs


def latest_completed_match(
    connection: sqlite3.Connection,
    tournament_id: int,
    bracket_type: str,
    stage_index: int = 1,
    group_number: int = 1,
) -> sqlite3.Row | None:
    stored_bracket_type = bracket_storage_key(bracket_type, stage_index, group_number)
    return connection.execute(
        """
        SELECT *
        FROM tournament_matches
        WHERE tournament_id = ? AND bracket_type = ? AND status = 'completed'
        ORDER BY round_number DESC, match_number DESC, id DESC
        LIMIT 1
        """,
        (tournament_id, stored_bracket_type),
    ).fetchone()


def rebuild_single_elimination_schedule(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    active_rows: list[sqlite3.Row],
    saved_results: dict[tuple[str, int, int], sqlite3.Row],
    stage_index: int = 1,
    group_number: int = 1,
    target_survivors: int | None = None,
    existing_matches: dict[tuple[str, int, int], sqlite3.Row] | None = None,
) -> None:
    active_ids = [row["id"] for row in active_rows]
    if len(active_ids) < 2:
        return

    bracket_size = next_power_of_two(len(active_ids))
    current_pairs = seeded_opening_pairs(active_ids)
    total_rounds = int(math.log2(bracket_size))
    auto_advance_byes = tournament_started(tournament)

    for round_number in range(1, total_rounds + 1):
        rows = create_match_round(
            connection,
            tournament["id"],
            "winners",
            round_number,
            current_pairs,
            saved_results=saved_results,
            auto_advance_byes=auto_advance_byes,
            stage_index=stage_index,
            group_number=group_number,
            existing_matches=existing_matches,
        )
        if target_survivors is not None and len(rows) <= target_survivors:
            return
        if round_number == total_rounds:
            return

        current_pairs = pair_sequential(
            [row["winner_registration_id"] if row["status"] == "completed" else None for row in rows],
            none_slot_type=MATCH_SLOT_TBD,
        )


def rebuild_double_elimination_schedule(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    active_rows: list[sqlite3.Row],
    saved_results: dict[tuple[str, int, int], sqlite3.Row],
    stage_index: int = 1,
    group_number: int = 1,
    target_survivors: int | None = None,
    existing_matches: dict[tuple[str, int, int], sqlite3.Row] | None = None,
) -> None:
    confirmed_ids = [row["id"] for row in active_rows]
    if len(confirmed_ids) < 2:
        return

    stage_prefix = f"s{stage_index}g{group_number}:%"

    while True:
        pending = connection.execute(
            "SELECT 1 FROM tournament_matches WHERE tournament_id = ? AND bracket_type LIKE ? AND status = 'pending' LIMIT 1",
            (tournament["id"], stage_prefix),
        ).fetchone()
        if pending:
            return

        completed_rows = connection.execute(
            "SELECT * FROM tournament_matches WHERE tournament_id = ? AND bracket_type LIKE ? AND status = 'completed'",
            (tournament["id"], stage_prefix),
        ).fetchall()
        loss_count: dict[int, int] = defaultdict(int)
        for row in completed_rows:
            if row["loser_registration_id"] is not None:
                loss_count[row["loser_registration_id"]] += 1

        zero_loss = [registration_id for registration_id in confirmed_ids if loss_count[registration_id] == 0]
        one_loss = [registration_id for registration_id in confirmed_ids if loss_count[registration_id] == 1]
        surviving_ids = [registration_id for registration_id in confirmed_ids if loss_count[registration_id] < 2]
        if target_survivors is not None and len(surviving_ids) <= target_survivors:
            return
        latest_finals = latest_completed_match(connection, tournament["id"], "finals", stage_index, group_number)
        latest_reset = latest_completed_match(connection, tournament["id"], "finals_reset", stage_index, group_number)

        if (
            latest_finals is not None
            and latest_reset is None
            and latest_finals["winner_registration_id"] == latest_finals["player2_registration_id"]
            and not zero_loss
            and set(one_loss) == {
                latest_finals["player1_registration_id"],
                latest_finals["player2_registration_id"],
            }
        ):
            create_match_round(
                connection,
                tournament["id"],
                "finals_reset",
                next_round_number(connection, tournament["id"], "finals_reset", stage_index, group_number),
                [
                    (
                        latest_finals["player1_registration_id"],
                        latest_finals["player2_registration_id"],
                    )
                ],
                saved_results=saved_results,
                auto_advance_byes=tournament_started(tournament),
                stage_index=stage_index,
                group_number=group_number,
                existing_matches=existing_matches,
            )
            continue

        created = False
        if len(zero_loss) > 1:
            create_match_round(
                connection,
                tournament["id"],
                "winners",
                next_round_number(connection, tournament["id"], "winners", stage_index, group_number),
                pair_sequential(zero_loss, none_slot_type=MATCH_SLOT_BYE),
                saved_results=saved_results,
                auto_advance_byes=tournament_started(tournament),
                stage_index=stage_index,
                group_number=group_number,
                existing_matches=existing_matches,
            )
            created = True
        if len(one_loss) > 1:
            create_match_round(
                connection,
                tournament["id"],
                "losers",
                next_round_number(connection, tournament["id"], "losers", stage_index, group_number),
                pair_sequential(one_loss, none_slot_type=MATCH_SLOT_BYE),
                saved_results=saved_results,
                auto_advance_byes=tournament_started(tournament),
                stage_index=stage_index,
                group_number=group_number,
                existing_matches=existing_matches,
            )
            created = True
        if not created and len(zero_loss) == 1 and len(one_loss) == 1 and latest_finals is None:
            create_match_round(
                connection,
                tournament["id"],
                "finals",
                1,
                [(zero_loss[0], one_loss[0])],
                saved_results=saved_results,
                auto_advance_byes=tournament_started(tournament),
                stage_index=stage_index,
                group_number=group_number,
                existing_matches=existing_matches,
            )
            created = True
        if not created:
            return


def rebuild_swiss_schedule(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    active_rows: list[sqlite3.Row],
    saved_results: dict[tuple[str, int, int], sqlite3.Row],
    stage_index: int = 1,
    group_number: int = 1,
    existing_matches: dict[tuple[str, int, int], sqlite3.Row] | None = None,
) -> None:
    active_ids = [row["id"] for row in active_rows]
    if len(active_ids) < 2:
        return

    round_limit = swiss_round_limit(len(active_ids))

    round_pair_count = math.ceil(len(active_ids) / 2)
    swiss_rows: list[sqlite3.Row] = []
    auto_advance_byes = tournament_started(tournament)

    for round_number in range(1, round_limit + 1):
        progress = swiss_group_progress(active_rows, swiss_rows)
        if round_number == 1:
            pairings = pair_sequential(active_ids, none_slot_type=MATCH_SLOT_BYE)
        elif swiss_rows and all(row["status"] == "completed" for row in swiss_rows if row["bracket_type"] == "swiss" and row["round_number"] == round_number - 1):
            pairings = swiss_pairings(active_rows, swiss_rows)
        else:
            pairings = [
                (make_match_slot(None, MATCH_SLOT_TBD), make_match_slot(None, MATCH_SLOT_TBD))
                for _ in range(round_pair_count)
            ]

        round_rows = create_match_round(
            connection,
            tournament["id"],
            "swiss",
            round_number,
            pairings,
            best_of_values=swiss_match_best_of_assignments(
                tournament,
                progress["threshold"],
                progress["stats"],
                pairings,
            ),
            saved_results=saved_results,
            auto_advance_byes=auto_advance_byes,
            stage_index=stage_index,
            group_number=group_number,
            existing_matches=existing_matches,
        )
        swiss_rows.extend(round_rows)


def rebuild_round_robin_schedule(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    active_rows: list[sqlite3.Row],
    saved_results: dict[tuple[str, int, int], sqlite3.Row],
    stage_index: int = 1,
    group_number: int = 1,
    existing_matches: dict[tuple[str, int, int], sqlite3.Row] | None = None,
) -> None:
    active_ids = [row["id"] for row in active_rows]
    if len(active_ids) < 2:
        return
    for round_number, pairings in enumerate(round_robin_rounds(active_ids, round_robin_cycles_for_tournament(tournament)), start=1):
        create_match_round(
            connection,
            tournament["id"],
            "round_robin",
            round_number,
            pairings,
            best_of=round_robin_best_of_for_tournament(tournament),
            saved_results=saved_results,
            auto_advance_byes=tournament_started(tournament),
            stage_index=stage_index,
            group_number=group_number,
            existing_matches=existing_matches,
        )


def load_stage_group_matches(
    connection: sqlite3.Connection,
    tournament_id: int,
    stage_index: int,
    group_number: int,
) -> list[sqlite3.Row]:
    prefix = f"s{stage_index}g{group_number}:%"
    return connection.execute(
        """
        SELECT *
        FROM tournament_matches
        WHERE tournament_id = ? AND bracket_type LIKE ?
        ORDER BY bracket_type ASC, round_number ASC, match_number ASC, id ASC
        """,
        (tournament_id, prefix),
    ).fetchall()


def swiss_advancement_threshold(participant_count: int) -> int:
    return max(1, math.ceil(math.log2(max(2, participant_count))))


def swiss_group_progress(
    registrations: list[sqlite3.Row],
    match_rows: list[sqlite3.Row],
) -> dict[str, Any]:
    threshold = swiss_advancement_threshold(len(registrations))
    stats = build_registration_stats(registrations, match_rows)
    qualified = [row for row in registrations if stats[row["id"]]["wins"] >= threshold]
    unresolved = [
        row
        for row in registrations
        if stats[row["id"]]["wins"] < threshold and stats[row["id"]]["losses"] < threshold
    ]
    qualifier_entries = [
        {
            "row": row,
            "sort_key": (
                stats[row["id"]]["losses"],
                -stats[row["id"]]["wins"],
                -stats[row["id"]]["buchholz"],
                stats[row["id"]]["seed"],
            ),
        }
        for row in qualified
    ]
    return {
        "threshold": threshold,
        "stats": stats,
        "qualified": sorted(qualifier_entries, key=lambda item: item["sort_key"]),
        "unresolved": unresolved,
    }


def rebuild_swiss_advancement_schedule(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    active_rows: list[sqlite3.Row],
    saved_results: dict[tuple[str, int, int], sqlite3.Row],
    stage_index: int = 1,
    group_number: int = 1,
    existing_matches: dict[tuple[str, int, int], sqlite3.Row] | None = None,
) -> None:
    active_ids = [row["id"] for row in active_rows]
    if len(active_ids) < 2:
        return

    round_limit = max(1, swiss_advancement_threshold(len(active_ids)) * 2 - 1)
    swiss_rows: list[sqlite3.Row] = []
    auto_advance_byes = tournament_started(tournament)

    for round_number in range(1, round_limit + 1):
        progress = swiss_group_progress(active_rows, swiss_rows)
        if round_number == 1:
            pairings = pair_sequential(active_ids, none_slot_type=MATCH_SLOT_BYE)
        else:
            previous_round_rows = [row for row in swiss_rows if row["round_number"] == round_number - 1]
            if previous_round_rows and not all(row["status"] == "completed" for row in previous_round_rows):
                return

            if not progress["unresolved"]:
                return
            pairings = swiss_pairings(progress["unresolved"], swiss_rows)
            if not pairings:
                return

        round_rows = create_match_round(
            connection,
            tournament["id"],
            "swiss",
            round_number,
            pairings,
            best_of_values=swiss_match_best_of_assignments(
                tournament,
                progress["threshold"],
                progress["stats"],
                pairings,
            ),
            saved_results=saved_results,
            auto_advance_byes=auto_advance_byes,
            stage_index=stage_index,
            group_number=group_number,
            existing_matches=existing_matches,
        )
        swiss_rows.extend(round_rows)


def rebuild_format_schedule(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    format_type: str,
    active_rows: list[sqlite3.Row],
    saved_results: dict[tuple[str, int, int], sqlite3.Row],
    stage_index: int = 1,
    group_number: int = 1,
    advance_count: int | None = None,
    existing_matches: dict[tuple[str, int, int], sqlite3.Row] | None = None,
) -> None:
    if format_type == "single_elimination":
        target_survivors = advance_count if advance_count is not None and advance_count < len(active_rows) else None
        rebuild_single_elimination_schedule(
            connection,
            tournament,
            active_rows,
            saved_results,
            stage_index=stage_index,
            group_number=group_number,
            target_survivors=target_survivors,
            existing_matches=existing_matches,
        )
    elif format_type == "double_elimination":
        target_survivors = advance_count if advance_count is not None and advance_count < len(active_rows) else None
        rebuild_double_elimination_schedule(
            connection,
            tournament,
            active_rows,
            saved_results,
            stage_index=stage_index,
            group_number=group_number,
            target_survivors=target_survivors,
            existing_matches=existing_matches,
        )
    elif format_type == "swiss":
        if stage_index == 1 and tournament["stage_mode"] == "double_stage":
            rebuild_swiss_advancement_schedule(
                connection,
                tournament,
                active_rows,
                saved_results,
                stage_index=stage_index,
                group_number=group_number,
                existing_matches=existing_matches,
            )
        else:
            rebuild_swiss_schedule(
                connection,
                tournament,
                active_rows,
                saved_results,
                stage_index=stage_index,
                group_number=group_number,
                existing_matches=existing_matches,
            )
    else:
        rebuild_round_robin_schedule(
            connection,
            tournament,
            active_rows,
            saved_results,
            stage_index=stage_index,
            group_number=group_number,
            existing_matches=existing_matches,
        )


def stage_one_group_qualifier_entries(
    tournament: sqlite3.Row,
    group_rows: list[sqlite3.Row],
    group_matches: list[sqlite3.Row],
    advance_count: int,
) -> tuple[bool, list[dict[str, Any]]]:
    format_type = tournament["format_type"]
    normalized_advance = max(1, int(advance_count))
    if not group_rows:
        return True, []
    if len(group_rows) == 1:
        return True, [{"row": group_rows[0], "sort_key": (0, 0, 0, 0)}]

    rows_by_id = {row["id"]: row for row in group_rows}
    real_pending = any(
        row["status"] != "completed"
        and row["player1_slot_type"] == MATCH_SLOT_PLAYER
        and row["player2_slot_type"] == MATCH_SLOT_PLAYER
        for row in group_matches
    )

    if format_type == "swiss":
        swiss_matches = [row for row in group_matches if parse_bracket_key(row["bracket_type"])[2] == "swiss"]
        progress = swiss_group_progress(group_rows, swiss_matches)
        return not progress["unresolved"] and not real_pending, progress["qualified"]

    completed_matches = [row for row in group_matches if row["status"] == "completed"]
    stats = build_registration_stats(group_rows, completed_matches)
    loss_count: dict[int, int] = defaultdict(int)
    for row in completed_matches:
        if row["loser_registration_id"] is not None:
            loss_count[row["loser_registration_id"]] += 1

    if format_type == "round_robin":
        win_points, draw_points, loss_points = round_robin_points_for_tournament(tournament)
        standings = calculate_standings(
            group_rows,
            completed_matches,
            points_mode=True,
            win_points=win_points,
            draw_points=draw_points,
            loss_points=loss_points,
        )
        entries = [
            {
                "row": rows_by_id[entry["registration_id"]],
                "sort_key": (-entry["points"], -entry["wins"], -entry["buchholz"], entry["seed"]),
            }
            for entry in standings[: min(normalized_advance, len(standings))]
        ]
        return not real_pending and len(standings) >= min(normalized_advance, len(group_rows)), entries

    if format_type == "double_elimination":
        surviving = [row for row in group_rows if loss_count[row["id"]] < 2]
        entries = [
            {
                "row": row,
                "sort_key": (loss_count[row["id"]], -stats[row["id"]]["wins"], stats[row["id"]]["seed"]),
            }
            for row in surviving
        ]
        return len(surviving) <= normalized_advance and not real_pending, sorted(entries, key=lambda item: item["sort_key"])

    surviving = [row for row in group_rows if loss_count[row["id"]] == 0]
    entries = [
        {
            "row": row,
            "sort_key": (-stats[row["id"]]["wins"], stats[row["id"]]["seed"]),
        }
        for row in surviving
    ]
    return len(surviving) <= normalized_advance and not real_pending, sorted(entries, key=lambda item: item["sort_key"])


def stage_two_seeded_rows(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    active_rows: list[sqlite3.Row],
) -> tuple[bool, list[sqlite3.Row]]:
    group_count = max(1, int(tournament["stage1_group_count"] or 1))
    group_settings = {
        row["group_number"]: row
        for row in load_stage_group_settings(connection, tournament["id"], group_count)
    }
    grouped_rows = registrations_by_group(active_rows, group_count)

    all_complete = True
    qualifier_entries: list[dict[str, Any]] = []
    for group_number in range(1, group_count + 1):
        group_rows = grouped_rows.get(group_number, [])
        if not group_rows:
            continue
        group_matches = load_stage_group_matches(connection, tournament["id"], 1, group_number)
        advance_count = group_settings.get(group_number, {"advance_count": 1})["advance_count"]
        complete, entries = stage_one_group_qualifier_entries(
            tournament,
            group_rows,
            group_matches,
            advance_count,
        )
        if not complete:
            all_complete = False
        qualifier_entries.extend(entries)

    if not all_complete:
        return False, []

    return True, [entry["row"] for entry in sorted(qualifier_entries, key=lambda item: item["sort_key"])]


def build_stage_status_view(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    active_rows: list[sqlite3.Row],
) -> dict[str, Any]:
    if tournament["stage_mode"] != "double_stage":
        return {
            "enabled": False,
            "current_stage": 1,
            "current_stage_label": "单阶段",
            "summary": "当前赛事为单阶段赛。",
            "stage1_complete": False,
            "stage2_ready": False,
            "stage2_started": False,
            "qualified_count": 0,
            "total_stage2_slots": 0,
        }

    stage_two_ready, stage_two_rows = stage_two_seeded_rows(connection, tournament, active_rows)
    stage_two_matches = load_stage_group_matches(connection, tournament["id"], 2, 1)
    stage_two_started = bool(stage_two_matches)
    current_stage = 2 if stage_two_started else 1
    qualified_count = len(stage_two_rows)

    if not tournament_started(tournament):
        summary = (
            f"当前停留在第一阶段准备期。开赛后将先进行第一阶段 {FORMAT_LABELS.get(tournament['format_type'], tournament['format_type'])}，"
            f"满足出线条件后会自动切换到第二阶段 {FORMAT_LABELS.get(tournament['stage2_format_type'], tournament['stage2_format_type'])}。"
        )
    elif stage_two_started:
        summary = (
            f"第一阶段出线名单已确认，当前已切换到第二阶段 {FORMAT_LABELS.get(tournament['stage2_format_type'], tournament['stage2_format_type'])}。"
        )
    elif stage_two_ready:
        summary = (
            f"第一阶段已完成，已确定 {qualified_count} 名晋级选手；系统已准备切入第二阶段 {FORMAT_LABELS.get(tournament['stage2_format_type'], tournament['stage2_format_type'])}。"
        )
    else:
        summary = (
            f"当前仍在第一阶段 {FORMAT_LABELS.get(tournament['format_type'], tournament['format_type'])}。"
            "待各组出线名单全部确定后，系统会自动生成第二阶段对阵。"
        )

    return {
        "enabled": True,
        "current_stage": current_stage,
        "current_stage_label": f"第 {current_stage} 阶段",
        "summary": summary,
        "stage1_complete": stage_two_ready,
        "stage2_ready": stage_two_ready,
        "stage2_started": stage_two_started,
        "qualified_count": qualified_count,
        "total_stage2_slots": qualified_count,
        "stage1_label": FORMAT_LABELS.get(tournament["format_type"], tournament["format_type"]),
        "stage2_label": FORMAT_LABELS.get(tournament["stage2_format_type"], tournament["stage2_format_type"]),
    }


def build_grouped_participant_sections(
    participants: list[sqlite3.Row],
    tournament: sqlite3.Row,
    stage_group_settings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    group_count = max(1, int(tournament["stage1_group_count"] or 1))
    grouped_rows = registrations_by_group(participants, group_count)
    settings_by_group = {item["group_number"]: item for item in stage_group_settings}
    sections: list[dict[str, Any]] = []
    for group_number in range(1, group_count + 1):
        rows = grouped_rows.get(group_number, [])
        sections.append(
            {
                "group_number": group_number,
                "title": f"第 {group_number} 组",
                "advance_count": settings_by_group.get(group_number, {"advance_count": 1})["advance_count"],
                "participants": rows,
                "checked_in_count": sum(1 for row in rows if row["checked_in"]),
            }
        )
    return sections


def ensure_tournament_matches(connection: sqlite3.Connection, tournament: sqlite3.Row) -> None:
    prune_no_shows(connection, tournament["id"])
    fresh_tournament = load_tournament_row(connection, tournament["id"])
    if fresh_tournament is None:
        return
    existing_matches = load_match_row_map(connection, tournament["id"])
    saved_results = load_completed_match_map(connection, tournament["id"])
    connection.execute("DELETE FROM tournament_matches WHERE tournament_id = ?", (tournament["id"],))
    active_rows = active_registration_rows(connection, fresh_tournament)

    if fresh_tournament["stage_mode"] == "double_stage":
        group_count = max(1, int(fresh_tournament["stage1_group_count"] or 1))
        group_settings = {
            row["group_number"]: row
            for row in load_stage_group_settings(connection, fresh_tournament["id"], group_count)
        }
        grouped_rows = registrations_by_group(active_rows, group_count)
        for group_number in range(1, group_count + 1):
            group_rows = grouped_rows.get(group_number, [])
            if not group_rows:
                continue
            advance_count = group_settings.get(group_number, {"advance_count": 1})["advance_count"]
            rebuild_format_schedule(
                connection,
                fresh_tournament,
                fresh_tournament["format_type"],
                group_rows,
                saved_results,
                stage_index=1,
                group_number=group_number,
                advance_count=advance_count,
                existing_matches=existing_matches,
            )

        stage_two_ready, stage_two_rows = stage_two_seeded_rows(connection, fresh_tournament, active_rows)
        if stage_two_ready and fresh_tournament["stage2_format_type"] in FORMAT_LABELS and len(stage_two_rows) >= 2:
            rebuild_format_schedule(
                connection,
                fresh_tournament,
                fresh_tournament["stage2_format_type"],
                stage_two_rows,
                saved_results,
                stage_index=2,
                group_number=1,
                existing_matches=existing_matches,
            )
    elif fresh_tournament["format_type"] == "single_elimination":
        rebuild_single_elimination_schedule(connection, fresh_tournament, active_rows, saved_results, existing_matches=existing_matches)
    elif fresh_tournament["format_type"] == "double_elimination":
        rebuild_double_elimination_schedule(connection, fresh_tournament, active_rows, saved_results, existing_matches=existing_matches)
    elif fresh_tournament["format_type"] == "swiss":
        rebuild_swiss_schedule(connection, fresh_tournament, active_rows, saved_results, existing_matches=existing_matches)
    else:
        rebuild_round_robin_schedule(connection, fresh_tournament, active_rows, saved_results, existing_matches=existing_matches)


def build_bracket_state(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    manager: bool,
) -> dict[str, Any]:
    ensure_tournament_matches(connection, tournament)
    fresh_tournament = load_tournament_row(connection, tournament["id"])
    if fresh_tournament is None:
        return {"summary": "赛事不存在。", "groups": [], "champion": None}

    registrations = load_registrations(connection, tournament["id"])
    matches = connection.execute(
        """
        SELECT *
        FROM tournament_matches
        WHERE tournament_id = ?
        ORDER BY bracket_type ASC, round_number ASC, match_number ASC, id ASC
        """,
        (tournament["id"],),
    ).fetchall()

    registration_map = {row["id"]: row for row in registrations}
    section_matches: dict[tuple[int, int, str], dict[int, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for match_row in matches:
        stage_index, group_number, base_bracket_type = parse_bracket_key(match_row["bracket_type"])
        item = match_view(match_row, registration_map, manager, tournament_started(fresh_tournament))
        section_matches[(stage_index, group_number, base_bracket_type)][match_row["round_number"]].append(item)

    stage_formats = {1: fresh_tournament["format_type"]}
    if fresh_tournament["stage_mode"] == "double_stage" and fresh_tournament["stage2_format_type"] in FORMAT_LABELS:
        stage_formats[2] = fresh_tournament["stage2_format_type"]

    order_map = {
        "single_elimination": ["winners"],
        "double_elimination": ["winners", "losers", "finals", "finals_reset"],
        "swiss": ["swiss"],
        "round_robin": ["round_robin"],
    }

    active_rows = active_registration_rows(connection, fresh_tournament)
    grouped_stage_one_rows = registrations_by_group(active_rows, int(fresh_tournament["stage1_group_count"] or 1))
    stage_two_ready, stage_two_rows = (
        stage_two_seeded_rows(connection, fresh_tournament, active_rows)
        if fresh_tournament["stage_mode"] == "double_stage"
        else (False, [])
    )

    def stage_title(stage_index: int) -> str:
        return "第一阶段" if stage_index == 1 else "第二阶段"

    sections: list[dict[str, Any]] = []
    for stage_index, format_type in stage_formats.items():
        group_numbers = [1]
        if fresh_tournament["stage_mode"] == "double_stage" and stage_index == 1:
            group_numbers = list(range(1, max(1, int(fresh_tournament["stage1_group_count"] or 1)) + 1))

        for group_number in group_numbers:
            stage_rows = grouped_stage_one_rows.get(group_number, []) if stage_index == 1 else stage_two_rows
            group_match_rows = load_stage_group_matches(connection, tournament["id"], stage_index, group_number)
            section_standings: list[dict[str, Any]] = []
            standings_mode = "wins"
            if format_type == "swiss" and stage_rows:
                swiss_matches = [row for row in group_match_rows if parse_bracket_key(row["bracket_type"])[2] == "swiss"]
                section_standings = calculate_standings(stage_rows, swiss_matches)
            elif format_type == "round_robin" and stage_rows:
                robin_matches = [row for row in group_match_rows if parse_bracket_key(row["bracket_type"])[2] == "round_robin"]
                win_points, draw_points, loss_points = round_robin_points_for_tournament(fresh_tournament)
                section_standings = calculate_standings(
                    stage_rows,
                    robin_matches,
                    points_mode=True,
                    win_points=win_points,
                    draw_points=draw_points,
                    loss_points=loss_points,
                )
                standings_mode = "points"

            for base_bracket_type in order_map.get(format_type, []):
                rounds = section_matches.get((stage_index, group_number, base_bracket_type), {})
                if not rounds and not (base_bracket_type in {"swiss", "round_robin"} and section_standings):
                    continue

                round_views = []
                max_round_number = max(rounds.keys()) if rounds else 0
                elimination_bracket = base_bracket_type in {"winners", "losers", "finals", "finals_reset"}
                for round_number in sorted(rounds):
                    if base_bracket_type == "round_robin":
                        round_best_of = round_robin_best_of_for_tournament(fresh_tournament)
                    elif base_bracket_type == "swiss":
                        round_best_of = rounds[round_number][0]["best_of"] if rounds[round_number] else default_best_of_for_bracket(base_bracket_type)
                    else:
                        round_best_of = load_round_best_of(
                            connection,
                            tournament["id"],
                            base_bracket_type,
                            round_number,
                            stage_index,
                            group_number,
                        )
                    # 淘汰赛倒数第二轮 → 半决赛，最后一轮 → 决赛
                    if elimination_bracket and max_round_number >= 2:
                        if round_number == max_round_number:
                            round_title = "决赛"
                        elif round_number == max_round_number - 1:
                            round_title = "半决赛"
                        else:
                            round_title = match_round_title(base_bracket_type, round_number)
                    else:
                        round_title = match_round_title(base_bracket_type, round_number)
                    round_views.append(
                        {
                            "title": round_title,
                            "bracket_type": base_bracket_type,
                            "round_number": round_number,
                            "stage_index": stage_index,
                            "group_number": group_number,
                            "best_of": round_best_of,
                            "matches": rounds[round_number],
                        }
                    )

                best_of_control = None
                if base_bracket_type == "round_robin":
                    best_of_control = {
                        "mode": "round_robin",
                        "round_robin_best_of": round_robin_best_of_for_tournament(fresh_tournament),
                        "summary": f"循环赛所有轮次统一使用 BO{round_robin_best_of_for_tournament(fresh_tournament)}。",
                    }
                elif base_bracket_type == "swiss":
                    swiss_regular_best_of, swiss_decider_best_of = swiss_best_of_settings_for_tournament(fresh_tournament)
                    best_of_control = {
                        "mode": "swiss",
                        "swiss_regular_best_of": swiss_regular_best_of,
                        "swiss_decider_best_of": swiss_decider_best_of,
                        "summary": f"常规局使用 BO{swiss_regular_best_of}，晋级局/淘汰局使用 BO{swiss_decider_best_of}。",
                    }

                title_parts: list[str] = []
                if fresh_tournament["stage_mode"] == "double_stage":
                    title_parts.append(stage_title(stage_index))
                    if stage_index == 1 and int(fresh_tournament["stage1_group_count"] or 1) > 1:
                        title_parts.append(f"第 {group_number} 组")
                    if format_type in {"single_elimination", "double_elimination"}:
                        title_parts.append(BRACKET_TYPE_LABELS.get(base_bracket_type, base_bracket_type))
                else:
                    title_parts.append(BRACKET_TYPE_LABELS.get(base_bracket_type, base_bracket_type))

                sections.append(
                    {
                        "title": " · ".join(title_parts),
                        "display_mode": "tree"
                        if format_type in {"single_elimination", "double_elimination"}
                        else "list",
                        "rounds": round_views,
                        "best_of_control": best_of_control,
                        "standings": section_standings if base_bracket_type in {"swiss", "round_robin"} else [],
                        "standings_mode": standings_mode,
                    }
                )

    champion = None
    if fresh_tournament["stage_mode"] == "double_stage" and stage_two_ready and stage_two_rows:
        final_format = fresh_tournament["stage2_format_type"]
        if final_format == "single_elimination":
            last_round = connection.execute(
                "SELECT COALESCE(MAX(round_number), 0) AS value FROM tournament_matches WHERE tournament_id = ? AND bracket_type = ?",
                (tournament["id"], bracket_storage_key("winners", 2, 1)),
            ).fetchone()["value"]
            winners = load_round_winners(connection, tournament["id"], "winners", last_round, 2, 1)
            if len(winners) == 1 and winners[0] in registration_map:
                champion = slot_display_name(registration_map[winners[0]], MATCH_SLOT_PLAYER)
        elif final_format == "double_elimination":
            completed_rows = [row for row in load_stage_group_matches(connection, tournament["id"], 2, 1) if row["status"] == "completed"]
            loss_count: dict[int, int] = defaultdict(int)
            for row in completed_rows:
                if row["loser_registration_id"] is not None:
                    loss_count[row["loser_registration_id"]] += 1
            surviving_ids = [row["id"] for row in stage_two_rows if loss_count[row["id"]] < 2]
            if len(surviving_ids) == 1 and surviving_ids[0] in registration_map:
                champion = slot_display_name(registration_map[surviving_ids[0]], MATCH_SLOT_PLAYER)
        elif sections:
            final_section = next((section for section in reversed(sections) if section["standings"]), None)
            if final_section and final_section["standings"]:
                champion = final_section["standings"][0]["nickname"]
    elif fresh_tournament["format_type"] == "single_elimination":
        last_round = connection.execute(
            "SELECT COALESCE(MAX(round_number), 0) AS value FROM tournament_matches WHERE tournament_id = ? AND bracket_type = ?",
            (tournament["id"], bracket_storage_key("winners", 1, 1)),
        ).fetchone()["value"]
        winners = load_round_winners(connection, tournament["id"], "winners", last_round)
        if len(winners) == 1 and winners[0] in registration_map:
            champion = slot_display_name(registration_map[winners[0]], MATCH_SLOT_PLAYER)
    elif fresh_tournament["format_type"] == "double_elimination":
        completed_rows = [row for row in load_stage_group_matches(connection, tournament["id"], 1, 1) if row["status"] == "completed"]
        loss_count: dict[int, int] = defaultdict(int)
        for row in completed_rows:
            if row["loser_registration_id"] is not None:
                loss_count[row["loser_registration_id"]] += 1
        surviving_ids = [row["id"] for row in active_rows if loss_count[row["id"]] < 2]
        if len(surviving_ids) == 1 and surviving_ids[0] in registration_map:
            champion = slot_display_name(registration_map[surviving_ids[0]], MATCH_SLOT_PLAYER)
    elif sections:
        final_section = next((section for section in reversed(sections) if section["standings"]), None)
        if final_section and final_section["standings"]:
            champion = final_section["standings"][0]["nickname"]

    if not sections:
        summary = "当前暂无可展示的对局。"
    elif not tournament_started(fresh_tournament):
        summary = "当前为赛前预览。管理员可在参与者管理中调整种子、分组与晋级配置，正式开始后会自动刷新第一阶段与第二阶段赛程。"
    elif fresh_tournament["stage_mode"] == "double_stage":
        summary = (
            f"当前为双阶段赛：第一阶段 {FORMAT_LABELS.get(fresh_tournament['format_type'], fresh_tournament['format_type'])}，"
            f"第二阶段 {FORMAT_LABELS.get(fresh_tournament['stage2_format_type'], fresh_tournament['stage2_format_type'])}。"
        )
    else:
        summary = f"当前赛制：{FORMAT_LABELS.get(fresh_tournament['format_type'], fresh_tournament['format_type'])}。"

    return {
        "summary": summary,
        "groups": sections,
        "champion": champion,
    }


def build_my_matches_state(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    registration: sqlite3.Row | None,
) -> dict[str, Any]:
    if registration is None:
        return {
            "summary": "报名后可在这里查看你的对局。",
            "current_match": None,
            "matches": [],
        }

    ensure_tournament_matches(connection, tournament)
    registrations = load_registrations(connection, tournament["id"])
    registration_map = {row["id"]: row for row in registrations}
    my_match_rows = connection.execute(
        """
        SELECT *
        FROM tournament_matches
        WHERE tournament_id = ?
          AND (player1_registration_id = ? OR player2_registration_id = ?)
        ORDER BY
            CASE status WHEN 'pending' THEN 0 ELSE 1 END,
            bracket_type ASC,
            round_number ASC,
            match_number ASC,
            id ASC
        """,
        (tournament["id"], registration["id"], registration["id"]),
    ).fetchall()

    current_match = None
    current_match_row = None
    match_views: list[dict[str, Any]] = []
    for match_row in my_match_rows:
        item = match_view(match_row, registration_map, False, tournament_started(tournament))
        is_player1 = match_row["player1_registration_id"] == registration["id"]
        opponent_id = match_row["player2_registration_id"] if is_player1 else match_row["player1_registration_id"]
        opponent = registration_map.get(opponent_id)
        item["opponent_name"] = slot_display_name(opponent, MATCH_SLOT_PLAYER) if opponent else "待定"
        item["my_score"] = match_row["score1"] if is_player1 else match_row["score2"]
        item["opponent_score"] = match_row["score2"] if is_player1 else match_row["score1"]
        item["replay_result_enabled"] = replay_result_enabled_for_tournament(tournament)
        item["can_report"] = (
            not item["replay_result_enabled"]
            and
            tournament_started(tournament)
            and match_row["status"] == "pending"
            and opponent is not None
            and not item["has_temporary_player"]
        )
        item["result_label"] = (
            "胜利"
            if match_row["status"] == "completed" and match_row["winner_registration_id"] == registration["id"]
            else "失利"
            if match_row["status"] == "completed"
            else "待进行"
        )
        match_views.append(item)
        if current_match is None and match_row["status"] == "pending":
            current_match = item
            current_match_row = match_row

    summary = "当前还没有分配到你的对局。" if not match_views else "这里汇总你在本赛事中的所有对局。"
    recent_matches = match_views[:3]
    preparation = (
        attach_match_preparation_view(connection, tournament, current_match_row, registration_map, registration, recent_matches)
        if current_match_row is not None
        else None
    )
    return {
        "summary": summary,
        "current_match": current_match,
        "matches": match_views,
        "recent_matches": recent_matches,
        "preparation": preparation,
    }


def build_my_matches_bp_sync_view(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    my_matches_state: dict[str, Any],
) -> dict[str, Any] | None:
    current_match = my_matches_state.get("current_match")
    if current_match is None:
        return None

    match_row = connection.execute(
        "SELECT * FROM tournament_matches WHERE id = ? AND tournament_id = ?",
        (current_match["id"], tournament["id"]),
    ).fetchone()
    if match_row is None:
        return None

    return {
        "url": f"/tournaments/{tournament['id']}/bp/sync?match_id={match_row['id']}",
        "key": match_preparation_sync_key(connection, match_row),
        "interval_ms": BP_SYNC_POLL_MS,
        "partial_url": f"/tournaments/{tournament['id']}/my-matches/partial",
        "partial_target": "my-matches-panel",
    }


def build_my_matches_panel_context(
    connection: sqlite3.Connection,
    request: Request,
    tournament: sqlite3.Row,
    current_user: sqlite3.Row,
) -> dict[str, Any]:
    registration = load_registration_for_user(connection, tournament["id"], int(current_user["id"]))
    if registration is None:
        raise HTTPException(status_code=403, detail="只有已报名选手可以查看该对阵面板。")

    manager = is_tournament_manager(connection, tournament["id"], int(current_user["id"]), tournament["creator_id"])
    my_matches_state = build_my_matches_state(connection, tournament, registration)
    my_matches_state["replay_upload_error"] = request.query_params.get("replay_upload_error")
    my_matches_state["replay_upload_success"] = request.query_params.get("replay_upload_success")
    my_matches_bp_sync = build_my_matches_bp_sync_view(connection, tournament, my_matches_state)
    return {
        "current_user": current_user,
        "tournament": {"id": int(tournament["id"])},
        "registration": registration,
        "manager": manager,
        "my_matches_state": my_matches_state,
        "my_matches_bp_sync": my_matches_bp_sync,
    }


def build_manager_preparation_overview(
    connection: sqlite3.Connection,
    tournament: sqlite3.Row,
    registration_map: dict[int, sqlite3.Row],
) -> list[dict[str, Any]]:
    if not tournament_started(tournament):
        return []

    rows = connection.execute(
        """
        SELECT *
        FROM tournament_matches
        WHERE tournament_id = ? AND status != 'completed'
        ORDER BY bracket_type ASC, round_number ASC, match_number ASC, id ASC
        """,
        (tournament["id"],),
    ).fetchall()

    overview = []
    for match_row in rows:
        preparation = attach_match_preparation_view(connection, tournament, match_row, registration_map)
        if preparation is None or not preparation.get("supported"):
            continue
        if preparation["status"] == "awaiting_ready" and not any(player["ready"] for player in preparation["players"]):
            continue

        match_summary = match_view(match_row, registration_map, True, True)
        overview.append(
            {
                "match": match_summary,
                "preparation": preparation,
            }
        )

    return overview


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    current_user = get_current_user(request)
    return RedirectResponse("/hub" if current_user else "/auth", status_code=303)


@app.get("/guide", response_class=HTMLResponse)
def operation_guide_page(request: Request):
    pdf_exists = OPERATION_GUIDE_PDF_PATH.exists()
    return render(
        request,
        "guide.html",
        {
            "page_title": "操作指南",
            "guide_pdf_exists": pdf_exists,
            "guide_pdf_url": "/guide/pdf",
            "guide_pdf_relative_path": OPERATION_GUIDE_PDF_PATH.relative_to(BASE_DIR).as_posix(),
        },
    )


@app.get("/guide/pdf")
def operation_guide_pdf():
    if not OPERATION_GUIDE_PDF_PATH.exists():
        raise HTTPException(status_code=404, detail="操作指南 PDF 尚未生成。")
    return FileResponse(OPERATION_GUIDE_PDF_PATH, media_type="application/pdf", filename=OPERATION_GUIDE_PDF_PATH.name)


@app.get("/streamer/bp-replays", response_class=HTMLResponse)
def streamer_bp_replays(request: Request, match_id: int | None = None, game_number: str = ""):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not has_bp_replay_access(current_user):
        raise HTTPException(status_code=403, detail="当前账号没有查看 BP 回放的权限。")

    game_number_int: int | None = int(game_number) if str(game_number).strip() else None

    with closing(get_connection()) as connection:
        replay_match_options = list_bp_replay_match_options(connection)
        selected_match_id = match_id
        if selected_match_id is None and replay_match_options:
            selected_match_id = int(replay_match_options[0]["match_id"])

        # 获取当前选中比赛的 best_of，用于生成不剧透的局号下拉列表
        selected_best_of = 0
        if selected_match_id is not None:
            selected_match = connection.execute(
                "SELECT * FROM tournament_matches WHERE id = ?",
                (selected_match_id,),
            ).fetchone()
            if selected_match is not None:
                selected_best_of = int(selected_match["best_of"] or default_best_of_for_bracket(selected_match["bracket_type"]))

        bp_replay_payload = None
        if selected_match_id is not None and selected_match is not None:
            tournament = load_tournament_row(connection, int(selected_match["tournament_id"]))
            preparation_state = load_match_preparation_state(connection, selected_match)
            if tournament is not None and preparation_state is not None:
                registration_rows = connection.execute(
                    "SELECT * FROM registrations WHERE tournament_id = ?",
                    (selected_match["tournament_id"],),
                ).fetchall()
                registration_map = {int(row["id"]): row for row in registration_rows}
                bp_replay_payload = build_match_bp_replay_payload(
                    tournament,
                    selected_match,
                    registration_map,
                    preparation_state,
                    game_number_int,
                )

        return render(
            request,
            "streamer_bp_replays.html",
            {
                "page_title": "BP 回放控制台",
                "current_user": current_user,
                "replay_match_options": replay_match_options,
                "selected_match_id": selected_match_id,
                "selected_game_number": game_number_int,
                "selected_best_of": selected_best_of,
                "bp_replay_payload": bp_replay_payload,
                "bp_replay_payload_json": json.dumps(bp_replay_payload, ensure_ascii=False) if bp_replay_payload is not None else "",
            },
        )


@app.get("/hub", response_class=HTMLResponse)
def entry_hub(request: Request):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    return render(
        request,
        "hub.html",
        {
            "page_title": "入口选择",
            "current_user": current_user,
        },
    )


@app.get("/novel", response_class=HTMLResponse)
def novel_page(request: Request):
    """同人小说目录页——自动解析正文章节，公开访问，无需登录。"""
    novel_path = BASE_DIR / "novel" / "转生金毛娘，指引黄金舰队！.html"
    chapters: list[dict[str, Any]] = []

    if novel_path.exists():
        try:
            content = novel_path.read_text(encoding="utf-8")
            # 解析所有带 ch- 前缀锚点的 h2 章节标题
            for match in re.finditer(r'<h2\s+id="(ch-[^"]+)"\s*>(.+?)</h2>', content, re.IGNORECASE):
                anchor = match.group(1)
                title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
                if not title:
                    continue
                is_prologue = "序章" in title
                chapters.append({"anchor": anchor, "title": title, "is_prologue": is_prologue})
        except Exception:
            chapters = []

    # 按每组 10 章切分，便于目录展示
    groups: list[dict[str, Any]] = []
    current_group: list[dict[str, Any]] = []
    for index, chapter in enumerate(chapters):
        chapter["number"] = "" if chapter["is_prologue"] else str(index)
        if chapter["is_prologue"]:
            groups.append({"label": "", "chapters": [chapter]})
            continue
        current_group.append(chapter)
        if len(current_group) == 10:
            first = current_group[0]["title"].split(" ")[0]
            last = current_group[-1]["title"].split(" ")[0]
            groups.append({"label": f"{first} ~ {last}", "chapters": current_group})
            current_group = []
    if current_group:
        first = current_group[0]["title"].split(" ")[0]
        last = current_group[-1]["title"].split(" ")[0]
        groups.append({"label": f"{first} ~ {last}", "chapters": current_group})

    return render(request, "novel_index.html", {
        "request": request,
        "chapter_groups": groups,
        "chapter_count": len(chapters),
    })


@app.get("/novel/read", response_class=HTMLResponse)
def novel_read_page(request: Request):
    """同人小说正文阅读——公开访问，无需登录。"""
    novel_path = BASE_DIR / "novel" / "转生金毛娘，指引黄金舰队！.html"
    if not novel_path.exists():
        raise HTTPException(status_code=404, detail="小说页面不存在。")

    return HTMLResponse(content=novel_path.read_text(encoding="utf-8"))


@app.get("/sponsor", response_class=HTMLResponse)
def sponsor_page(request: Request):
    """赞助页面——公开访问，无需登录。"""
    return render(request, "sponsor.html", {"request": request})


@app.get("/update", response_class=HTMLResponse)
def update_page(request: Request):
    """风暴对决更新首页——补丁版本选择，公开访问，无需登录。"""
    updates_dir = BASE_DIR / "update"
    patch_entries: list[dict[str, str]] = []

    if updates_dir.exists():
        for file_path in sorted(updates_dir.glob("SC2_StormShowdown_PTR_*.html"), reverse=True):
            filename = file_path.name
            # 从文件名提取时间戳 YYYYMMDD
            timestamp_str = ""
            display_date = filename
            import re
            match = re.search(r"(\d{8})", filename)
            if match:
                ts = match.group(1)
                timestamp_str = ts
                display_date = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"

            # 尝试从 HTML 中提取标题
            patch_title = filename.replace(".html", "").replace("SC2_StormShowdown_PTR_", "补丁 ")
            try:
                content = file_path.read_text(encoding="utf-8")
                title_match = re.search(r"<title>(.+?)</title>", content)
                if title_match:
                    patch_title = title_match.group(1).strip()
            except Exception:
                pass

            patch_entries.append({
                "filename": filename,
                "display_date": display_date,
                "title": patch_title,
                "timestamp": timestamp_str,
            })

    return render(request, "update_index.html", {
        "request": request,
        "patches": patch_entries,
    })


@app.get("/update/{filename}", response_class=HTMLResponse)
def update_patch_detail(request: Request, filename: str):
    """风暴对决具体补丁页面——公开访问，无需登录。"""
    # 安全检查：只允许 .html 文件
    if not filename.endswith(".html") or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="无效的文件名。")

    update_path = BASE_DIR / "update" / filename
    if not update_path.exists() or not update_path.is_file():
        raise HTTPException(status_code=404, detail="更新页面不存在。")

    return HTMLResponse(content=update_path.read_text(encoding="utf-8"))


@app.get("/leaderboard", response_class=HTMLResponse)
def leaderboard_page(request: Request):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        users = connection.execute(
            "SELECT id, username, game_nickname, tournament_points, avatar_path, avatar_frame, nickname_frame, nickname_color, is_sponsor "
            "FROM users WHERE tournament_points > 0 ORDER BY tournament_points DESC, username COLLATE NOCASE"
        ).fetchall()

    return render(
        request,
        "leaderboard.html",
        {
            "page_title": "赛事积分排行榜",
            "current_user": current_user,
            "leaderboard": [
                {
                    "rank": idx + 1,
                    "username": row["game_nickname"] or row["username"],
                    "points": row["tournament_points"],
                    "avatar_path": row["avatar_path"],
                    "avatar_frame": row["avatar_frame"],
                    "nickname_frame": row["nickname_frame"],
                    "nickname_color": row["nickname_color"],
                    "game_nickname": row["game_nickname"],
                }
                for idx, row in enumerate(users)
            ],
        },
    )


@app.get("/auth", response_class=HTMLResponse)
def auth_page(request: Request):
    current_user = get_current_user(request)
    if current_user:
        return RedirectResponse("/hub", status_code=303)
    return render(
        request,
        "auth.html",
        {
            "page_title": "登录 / 注册",
            "auth_mode": "login",
            "form_data": {},
        },
    )


@app.post("/auth/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    remember_login: str | None = Form(None),
):
    cleaned_username = trim_username(username)
    with closing(get_connection()) as connection:
        user = connection.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (cleaned_username,),
        ).fetchone()

    if user is None or not password_matches(password, user["password_hash"]):
        return render(
            request,
            "auth.html",
            {
                "page_title": "登录 / 注册",
                "auth_mode": "login",
                "form_data": {"username": cleaned_username},
                "auth_error": "账号或密码错误。",
            },
            status_code=400,
        )

    response = RedirectResponse("/hub", status_code=303)
    set_session_cookie(response, user["id"], remember_login == "on")
    return response


@app.post("/auth/register", response_class=HTMLResponse)
def register(
    request: Request,
    username: str = Form(...),
    game_nickname: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    remember_login: str | None = Form(None),
):
    cleaned_username = trim_username(username)
    cleaned_game_nickname = normalize_game_nickname(game_nickname)
    username_error = validate_username(cleaned_username)
    game_nickname_error = validate_game_nickname(cleaned_game_nickname)
    password_error = validate_password(password)
    register_form_data = {
        "register_username": cleaned_username,
        "register_game_nickname": cleaned_game_nickname or "",
    }

    if username_error:
        return render(
            request,
            "auth.html",
            {
                "page_title": "登录 / 注册",
                "auth_mode": "register",
                "form_data": register_form_data,
                "auth_error": username_error,
            },
            status_code=400,
        )

    if game_nickname_error:
        return render(
            request,
            "auth.html",
            {
                "page_title": "登录 / 注册",
                "auth_mode": "register",
                "form_data": register_form_data,
                "auth_error": game_nickname_error,
            },
            status_code=400,
        )

    if password_error:
        return render(
            request,
            "auth.html",
            {
                "page_title": "登录 / 注册",
                "auth_mode": "register",
                "form_data": register_form_data,
                "auth_error": password_error,
            },
            status_code=400,
        )

    if password != confirm_password:
        return render(
            request,
            "auth.html",
            {
                "page_title": "登录 / 注册",
                "auth_mode": "register",
                "form_data": register_form_data,
                "auth_error": "两次输入的密码不一致。",
            },
            status_code=400,
        )

    with closing(get_connection()) as connection, connection:
        try:
            cursor = connection.execute(
                "INSERT INTO users (username, game_nickname, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (cleaned_username, cleaned_game_nickname, password_hash(password), now_text()),
            )
        except sqlite3.IntegrityError:
            return render(
                request,
                "auth.html",
                {
                    "page_title": "登录 / 注册",
                    "auth_mode": "register",
                    "form_data": register_form_data,
                    "auth_error": "该账号已存在，请更换后重试。",
                },
                status_code=400,
            )
        user_id = cursor.lastrowid

    response = RedirectResponse("/hub", status_code=303)
    set_session_cookie(response, user_id, remember_login == "on")
    return response


@app.post("/auth/logout")
def logout(request: Request):
    response = RedirectResponse("/auth", status_code=303)
    clear_session_cookie(request, response)
    return response


@app.post("/account/game-nickname")
def update_game_nickname(
    request: Request,
    game_nickname: str = Form(...),
    redirect_to: str = Form("/hub"),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    cleaned_game_nickname = normalize_game_nickname(game_nickname)
    validation_error = validate_game_nickname(cleaned_game_nickname)
    target_path = safe_redirect_path(redirect_to)
    if validation_error:
        return RedirectResponse(append_redirect_error(target_path, validation_error), status_code=303)

    with closing(get_connection()) as connection, connection:
        connection.execute(
            "UPDATE users SET game_nickname = ? WHERE id = ?",
            (cleaned_game_nickname, current_user["id"]),
        )

    return RedirectResponse(target_path, status_code=303)


def build_account_settings_context(
    current_user: sqlite3.Row,
    profile_error: str | None = None,
    profile_success: str | None = None,
    password_error: str | None = None,
    password_success: str | None = None,
    avatar_error: str | None = None,
    avatar_success: str | None = None,
    frame_error: str | None = None,
    frame_success: str | None = None,
    cosmetic_error: str | None = None,
    cosmetic_success: str | None = None,
) -> dict[str, Any]:
    context = {
        "page_title": "账号设置",
        "current_user": current_user,
        "default_avatars": ["protoss.png", "terran.png", "zerg.png"],
        "current_avatar_url": avatar_url_for_user(current_user),
        "needs_avatar_setup": not (current_user["avatar_path"] if isinstance(current_user, dict) else dict(current_user).get("avatar_path")),
        "available_frames": list_avatar_frames(),
        "current_frame": current_user["avatar_frame"] or "",
    }
    if profile_error:
        context["profile_error"] = profile_error
    if profile_success:
        context["profile_success"] = profile_success
    if password_error:
        context["password_error"] = password_error
    if password_success:
        context["password_success"] = password_success
    if avatar_error:
        context["avatar_error"] = avatar_error
    if avatar_success:
        context["avatar_success"] = avatar_success
    if frame_error:
        context["frame_error"] = frame_error
    if frame_success:
        context["frame_success"] = frame_success
    if cosmetic_error:
        context["cosmetic_error"] = cosmetic_error
    if cosmetic_success:
        context["cosmetic_success"] = cosmetic_success
    return context


@app.get("/account/settings", response_class=HTMLResponse)
def account_settings_page(request: Request):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        fresh_user = connection.execute("SELECT * FROM users WHERE id = ?", (current_user["id"],)).fetchone()

    return render(request, "account_settings.html", build_account_settings_context(
        fresh_user or current_user,
        avatar_error=request.query_params.get("avatar_error"),
        avatar_success=request.query_params.get("avatar_success"),
        frame_error=request.query_params.get("frame_error"),
        frame_success=request.query_params.get("frame_success"),
        cosmetic_error=request.query_params.get("cosmetic_error"),
        cosmetic_success=request.query_params.get("cosmetic_success"),
    ))


@app.post("/account/settings/profile", response_class=HTMLResponse)
def update_account_profile(
    request: Request,
    game_nickname: str = Form(...),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    cleaned_game_nickname = normalize_game_nickname(game_nickname)
    validation_error = validate_game_nickname(cleaned_game_nickname)
    if validation_error:
        return render(
            request,
            "account_settings.html",
            build_account_settings_context(current_user, profile_error=validation_error),
            status_code=400,
        )

    with closing(get_connection()) as connection, connection:
        connection.execute(
            "UPDATE users SET game_nickname = ? WHERE id = ?",
            (cleaned_game_nickname, current_user["id"]),
        )
        refreshed_user = connection.execute("SELECT * FROM users WHERE id = ?", (current_user["id"],)).fetchone()

    return render(
        request,
        "account_settings.html",
        build_account_settings_context(refreshed_user or current_user, profile_success="游戏内昵称已更新。"),
    )


@app.post("/account/settings/password", response_class=HTMLResponse)
def update_account_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        fresh_user = connection.execute("SELECT * FROM users WHERE id = ?", (current_user["id"],)).fetchone()
        if fresh_user is None:
            return RedirectResponse("/auth", status_code=303)
        if not password_matches(current_password, fresh_user["password_hash"]):
            return render(
                request,
                "account_settings.html",
                build_account_settings_context(fresh_user, password_error="当前密码不正确。"),
                status_code=400,
            )
        validation_error = validate_password(new_password)
        if validation_error:
            return render(
                request,
                "account_settings.html",
                build_account_settings_context(fresh_user, password_error=validation_error),
                status_code=400,
            )
        if new_password != confirm_password:
            return render(
                request,
                "account_settings.html",
                build_account_settings_context(fresh_user, password_error="两次输入的新密码不一致。"),
                status_code=400,
            )

        connection.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash(new_password), fresh_user["id"]),
        )
        updated_user = connection.execute("SELECT * FROM users WHERE id = ?", (fresh_user["id"],)).fetchone()

    return render(
        request,
        "account_settings.html",
        build_account_settings_context(updated_user or current_user, password_success="密码已更新。"),
    )


AVATAR_DIR = BASE_DIR / "avatars"
AVATAR_FRAME_DIR = AVATAR_DIR / "frame"
AVATAR_FRAME_CONFIG_PATH = AVATAR_FRAME_DIR / "frames.json"

_frame_config_cache: dict[str, Any] = {"mtime": None, "data": {}}


def list_avatar_frames() -> list[str]:
    """列出 avatars/frame 目录下的头像框文件名（SVG/PNG，不含路径）。"""
    if not AVATAR_FRAME_DIR.is_dir():
        return []
    return sorted(
        path.name
        for path in AVATAR_FRAME_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in {".svg", ".png"}
    )


def get_frame_configs() -> dict[str, dict[str, Any]]:
    """读取 avatars/frame/frames.json 配置（按文件 mtime 缓存，修改后刷新页面即生效）。"""
    global _frame_config_cache
    if not AVATAR_FRAME_CONFIG_PATH.is_file():
        return {}
    try:
        mtime = AVATAR_FRAME_CONFIG_PATH.stat().st_mtime
    except OSError:
        return {}
    if _frame_config_cache["mtime"] == mtime:
        return _frame_config_cache["data"]
    try:
        data = json.loads(AVATAR_FRAME_CONFIG_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    _frame_config_cache = {"mtime": mtime, "data": data}
    return data


def avatar_frame_scale(frame_name: str | None) -> float:
    """返回头像框的 frame_scale：头像框相对容器的大小，范围 1.0~2.0，默认 1.0（与容器同尺寸）。

    大于 1 时头像框居中放大、套在头像外面；兼容旧 avatar_scale 键（自动换算为 1/avatar_scale）。"""
    if not frame_name:
        return 1.0
    config = get_frame_configs().get(frame_name)
    if not isinstance(config, dict):
        return 1.0
    if "frame_scale" in config:
        try:
            scale = float(config["frame_scale"])
        except (TypeError, ValueError):
            scale = 1.0
    else:
        try:
            avatar_scale = float(config.get("avatar_scale", 1.0))
            scale = 1.0 / avatar_scale if avatar_scale > 0 else 1.0
        except (TypeError, ValueError):
            scale = 1.0
    return min(2.0, max(1.0, scale))


def avatar_frame_scale_for_user(user: sqlite3.Row | dict[str, Any]) -> float:
    """获取用户已装备头像框的头像占位比例。"""
    d = user if isinstance(user, dict) else dict(user)
    return avatar_frame_scale(str(d.get("avatar_frame") or "").strip())


def avatar_frame_offsets(frame_name: str | None) -> tuple[float, float]:
    """返回头像框的偏移量 (offset_x, offset_y)：相对容器宽/高的比例，范围 -0.5~0.5。

    正值向右/向下移动头像框，负值向左/向上；默认 (0, 0) 居中。"""
    if not frame_name:
        return 0.0, 0.0
    config = get_frame_configs().get(frame_name)
    if not isinstance(config, dict):
        return 0.0, 0.0
    try:
        offset_x = float(config.get("offset_x", 0.0))
    except (TypeError, ValueError):
        offset_x = 0.0
    try:
        offset_y = float(config.get("offset_y", 0.0))
    except (TypeError, ValueError):
        offset_y = 0.0
    return min(0.5, max(-0.5, offset_x)), min(0.5, max(-0.5, offset_y))


def avatar_frame_offsets_for_user(user: sqlite3.Row | dict[str, Any]) -> tuple[float, float]:
    """获取用户已装备头像框的偏移量。"""
    d = user if isinstance(user, dict) else dict(user)
    return avatar_frame_offsets(str(d.get("avatar_frame") or "").strip())


@app.post("/account/avatar/upload")
async def upload_avatar(request: Request, avatar_file: UploadFile = File(...)):
    """上传并裁剪头像（前端裁剪后上传）。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    if not avatar_file.filename:
        return RedirectResponse("/account/settings?avatar_error=请选择图片文件", status_code=303)

    content = await avatar_file.read()
    await avatar_file.close()

    if len(content) > 5 * 1024 * 1024:
        return RedirectResponse("/account/settings?avatar_error=图片大小不能超过 5MB", status_code=303)

    # 简单检查文件头
    header = content[:12]
    is_valid = (
        header[:4] == b'\x89PNG' or  # PNG
        header[:2] == b'\xff\xd8' or  # JPEG
        header[:4] == b'GIF8' or     # GIF
        header[:4] == b'RIFF'        # WEBP
    )
    if not is_valid:
        return RedirectResponse("/account/settings?avatar_error=只支持 PNG/JPEG/GIF/WebP 格式", status_code=303)

    ext_map = {b'\x89PNG': 'png', b'\xff\xd8': 'jpg', b'GIF8': 'gif', b'RIFF': 'webp'}
    ext = 'png'
    for sig, e in ext_map.items():
        if header[:len(sig)] == sig:
            ext = e
            break
    save_name = f"user_{current_user['id']}_{int(datetime.now().timestamp())}.{ext}"
    save_path = AVATAR_DIR / save_name
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    save_path.write_bytes(content)

    avatar_url = f"/avatars/{save_name}"
    with closing(get_connection()) as connection, connection:
        connection.execute("UPDATE users SET avatar_path = ? WHERE id = ?", (avatar_url, current_user["id"]))

    return RedirectResponse(f"/account/settings?avatar_success=头像已更新", status_code=303)


@app.post("/account/avatar/select")
async def select_default_avatar(request: Request):
    """从默认头像中选择。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    form = await request.form()
    avatar_name = str(form.get("avatar_name") or "").strip()
    allowed = {"protoss.png", "terran.png", "zerg.png"}
    if avatar_name not in allowed:
        return RedirectResponse("/account/settings?avatar_error=无效的默认头像", status_code=303)

    avatar_url = f"/background/normal/{avatar_name}"
    with closing(get_connection()) as connection, connection:
        connection.execute("UPDATE users SET avatar_path = ? WHERE id = ?", (avatar_url, current_user["id"]))

    return RedirectResponse(f"/account/settings?avatar_success=头像已更新", status_code=303)


@app.post("/account/avatar/frame")
async def equip_avatar_frame(request: Request):
    """赞助用户装备/卸下 avatars/frame 目录下的 SVG 头像框。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    form = await request.form()
    frame_name = str(form.get("frame") or "").strip()

    if frame_name:
        if not bool(current_user["is_sponsor"]):
            return RedirectResponse("/account/settings?frame_error=只有赞助用户可以装备头像框", status_code=303)
        if frame_name not in list_avatar_frames():
            return RedirectResponse("/account/settings?frame_error=无效的头像框", status_code=303)
    else:
        frame_name = None

    with closing(get_connection()) as connection, connection:
        connection.execute(
            "UPDATE users SET avatar_frame = ? WHERE id = ?",
            (frame_name, current_user["id"]),
        )

    return RedirectResponse("/account/settings?frame_success=头像框已更新", status_code=303)


@app.post("/account/cosmetics")
async def update_cosmetics(request: Request):
    """更新昵称颜色。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    form = await request.form()
    nickname_color = str(form.get("nickname_color") or "").strip()

    if nickname_color and not nickname_color.startswith("#"):
        nickname_color = ""

    with closing(get_connection()) as connection, connection:
        connection.execute(
            "UPDATE users SET nickname_color = ? WHERE id = ?",
            (nickname_color or None, current_user["id"]),
        )

    return RedirectResponse("/account/settings?cosmetic_success=昵称颜色已更新", status_code=303)


@app.get("/sponsor-thanks", response_class=HTMLResponse)
def sponsor_thanks_page(request: Request):
    """赞助感谢页面——公开访问。"""
    with closing(get_connection()) as connection, connection:
        sponsors = connection.execute(
            "SELECT username, game_nickname, avatar_path, avatar_frame FROM users WHERE is_sponsor = 1 ORDER BY id"
        ).fetchall()

    return render(request, "sponsor_thanks.html", {
        "request": request,
        "sponsors": [dict(s) for s in sponsors],
    })


@app.post("/admin/users/bulk-sponsor")
async def bulk_set_sponsor(request: Request):
    """超管批量设置赞助用户。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_superadmin_user(current_user):
        raise HTTPException(status_code=403, detail="无权限。")

    form = await request.form()
    sponsor_value = int(form.get("sponsor_value", "1"))
    user_ids = []
    for key in form.keys():
        if key.startswith("sponsor_user_"):
            try:
                user_ids.append(int(key.split("_")[-1]))
            except ValueError:
                pass

    if user_ids:
        with closing(get_connection()) as connection, connection:
            placeholders = ",".join("?" for _ in user_ids)
            connection.execute(
                f"UPDATE users SET is_sponsor = ? WHERE id IN ({placeholders})",
                [sponsor_value] + user_ids,
            )

    return RedirectResponse("/admin?sponsor_updated=1", status_code=303)


def avatar_url_for_user(user: sqlite3.Row | dict[str, Any]) -> str:
    """获取用户头像 URL，未设置则返回默认。"""
    d = user if isinstance(user, dict) else dict(user)
    path = d.get("avatar_path")
    if path and str(path).strip():
        return str(path).strip()
    return "/background/normal/terran.png"  # 默认人族头像


def avatar_frame_url_for_user(user: sqlite3.Row | dict[str, Any]) -> str:
    """获取用户已装备的头像框 URL；未装备或值非法则返回空字符串。"""
    d = user if isinstance(user, dict) else dict(user)
    frame = str(d.get("avatar_frame") or "").strip()
    if not frame:
        return ""
    if not frame.lower().endswith((".svg", ".png")) or "/" in frame or "\\" in frame or ".." in frame:
        return ""
    return f"/avatars/frame/{frame}"


def render_user_avatar_img(user: sqlite3.Row | dict[str, Any], size: int = 32) -> str:
    """渲染用户头像；已装备头像框时返回「头像 + SVG/PNG 头像框」叠加结构。"""
    avatar_url = avatar_url_for_user(user)
    frame_url = avatar_frame_url_for_user(user)
    if not frame_url:
        return (
            f'<img class="user-avatar-img" src="{avatar_url}" alt="" '
            f'style="width:{size}px;height:{size}px;border-radius:6px;object-fit:cover;flex-shrink:0;vertical-align:middle;" '
            f'onerror="this.src=\'/background/normal/terran.png\'">'
        )
    scale = avatar_frame_scale_for_user(user)
    offset_x, offset_y = avatar_frame_offsets_for_user(user)
    overhang_pct = (scale - 1.0) / 2.0 * 100
    box_pct = scale * 100
    left_pct = -overhang_pct + offset_x * 100
    top_pct = -overhang_pct + offset_y * 100
    container_style = (
        f"position:relative;display:inline-block;width:{size}px;height:{size}px;"
        f"flex-shrink:0;vertical-align:middle;"
    )
    image_html = (
        f'<img class="user-avatar-img" src="{avatar_url}" alt="" '
        f'style="position:absolute;inset:0;width:100%;height:100%;border-radius:6px;object-fit:cover;" '
        f'onerror="this.src=\'/background/normal/terran.png\'">'
    )
    frame_html = (
        f'<img class="user-avatar-frame" src="{frame_url}" alt="" '
        f'style="position:absolute;left:{left_pct:.4g}%;top:{top_pct:.4g}%;'
        f'width:{box_pct:.4g}%;height:{box_pct:.4g}%;pointer-events:none;" '
        f'onerror="this.style.display=\'none\'">'
    )
    return f'<span class="user-avatar-composed" style="{container_style}">{image_html}{frame_html}</span>'


def render_avatar_frame_preview(user: sqlite3.Row | dict[str, Any], frame_name: str, size: int = 64) -> str:
    """渲染单个头像框的预览：头像铺满，头像框按该框 frame_scale 居中放大套在外面。"""
    avatar_url = avatar_url_for_user(user)
    frame_url = f"/avatars/frame/{frame_name}"
    scale = avatar_frame_scale(frame_name)
    offset_x, offset_y = avatar_frame_offsets(frame_name)
    overhang_pct = (scale - 1.0) / 2.0 * 100
    box_pct = scale * 100
    left_pct = -overhang_pct + offset_x * 100
    top_pct = -overhang_pct + offset_y * 100
    image_html = (
        f'<img src="{avatar_url}" alt="" '
        f'style="position:absolute;inset:0;width:100%;height:100%;border-radius:6px;object-fit:cover;" '
        f'onerror="this.src=\'/background/normal/terran.png\'">'
    )
    frame_html = (
        f'<img src="{frame_url}" alt="" '
        f'style="position:absolute;left:{left_pct:.4g}%;top:{top_pct:.4g}%;'
        f'width:{box_pct:.4g}%;height:{box_pct:.4g}%;pointer-events:none;" '
        f'onerror="this.style.display=\'none\'">'
    )
    return (
        f'<span style="position:relative;display:inline-block;width:{size}px;height:{size}px;">'
        f'{image_html}{frame_html}</span>'
    )


def render_user_nickname_img(user: sqlite3.Row | dict[str, Any], max_height: int = 32) -> str:
    """渲染用户昵称（带颜色），使用固定字号保持一致尺寸。"""
    u = user if isinstance(user, dict) else dict(user)
    name = u.get("game_nickname") or u.get("username") or ""
    color = u.get("nickname_color") or ""
    style = f"font-size:{max_height * 0.58}px;font-weight:700;white-space:nowrap;"
    if color:
        style += f"color:{color};"
    return f'<span style="{style}">{name}</span>'


templates.env.globals["avatar_url_for_user"] = avatar_url_for_user
templates.env.globals["avatar_frame_url_for_user"] = avatar_frame_url_for_user
templates.env.globals["render_user_avatar_html"] = render_user_avatar_img
templates.env.globals["render_user_nickname_html"] = render_user_nickname_img
templates.env.globals["render_avatar_frame_preview"] = render_avatar_frame_preview
templates.env.globals["avatar_frame_scale"] = avatar_frame_scale


def tournament_form_context(
    connection: sqlite3.Connection,
    current_user: sqlite3.Row,
    form_values: dict[str, Any] | None = None,
    form_error: str | None = None,
) -> dict[str, Any]:
    users = connection.execute(
        "SELECT username FROM users WHERE id != ? ORDER BY username COLLATE NOCASE",
        (current_user["id"],),
    ).fetchall()
    merged_form_values = {
        "format_type": "single_elimination",
        "stage_mode": "single_stage",
        "stage2_format_type": "",
        "bp_step_seconds": DEFAULT_TOURNAMENT_BP_STEP_SECONDS,
        "bp_mode": BP_MODE_NONE,
        "replay_result_enabled": False,
        "round_robin_cycles": DEFAULT_ROUND_ROBIN_CYCLES,
        "round_robin_win_points": DEFAULT_ROUND_ROBIN_WIN_POINTS,
        "round_robin_draw_points": DEFAULT_ROUND_ROBIN_DRAW_POINTS,
        "round_robin_loss_points": DEFAULT_ROUND_ROBIN_LOSS_POINTS,
        "round_robin_best_of": DEFAULT_ROUND_ROBIN_BEST_OF,
        "swiss_regular_best_of": DEFAULT_SWISS_REGULAR_BEST_OF,
        "swiss_decider_best_of": DEFAULT_SWISS_DECIDER_BEST_OF,
        "commander_shared_pool_mode": COMMANDER_POOL_SHARED,
        "commander_ban_mode": COMMANDER_BAN_GLOBAL,
    }
    if form_values:
        merged_form_values.update(form_values)

    active_formats = {merged_form_values["format_type"]}
    if merged_form_values["stage_mode"] == "double_stage" and merged_form_values["stage2_format_type"] in FORMAT_LABELS:
        active_formats.add(merged_form_values["stage2_format_type"])

    context = {
        "page_title": "创建赛事",
        "current_user": current_user,
        "user_suggestions": [row["username"] for row in users],
        "form_values": merged_form_values,
        "show_round_robin_settings": "round_robin" in active_formats,
        "show_swiss_settings": "swiss" in active_formats,
        "is_superadmin": is_superadmin_user(current_user),
    }
    if form_error:
        context["form_error"] = form_error
    return context


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, view: str = "all"):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    current_view = view if view in {"all", "mine", "managed", "history"} else "all"

    with closing(get_connection()) as connection, connection:
        cleanup_all_tournaments(connection)
        query = """
            SELECT tournaments.*, COALESCE(users.game_nickname, users.username) AS creator_name,
                   EXISTS(
                       SELECT 1
                       FROM registrations
                       WHERE registrations.tournament_id = tournaments.id
                         AND registrations.user_id = ?
                   ) AS joined,
                   EXISTS(
                       SELECT 1
                       FROM tournament_admins
                       WHERE tournament_admins.tournament_id = tournaments.id
                         AND tournament_admins.user_id = ?
                   ) AS shared_admin,
                   (
                       SELECT COUNT(*)
                       FROM registrations
                       WHERE registrations.tournament_id = tournaments.id
                   ) AS participant_count,
                   (
                       SELECT COUNT(*)
                       FROM registrations
                       WHERE registrations.tournament_id = tournaments.id
                         AND registrations.checked_in = 1
                   ) AS checked_in_count
            FROM tournaments
            JOIN users ON users.id = tournaments.creator_id
        """
        params: list[Any] = [current_user["id"], current_user["id"]]
        if current_view == "all":
            # 全部比赛：排除私密赛事（除非已加入或管理）
            query += """
                WHERE (tournaments.is_private = 0
                   OR EXISTS(
                       SELECT 1
                       FROM registrations
                       WHERE registrations.tournament_id = tournaments.id
                         AND registrations.user_id = ?
                   )
                   OR tournaments.creator_id = ?
                   OR EXISTS(
                       SELECT 1
                       FROM tournament_admins
                       WHERE tournament_admins.tournament_id = tournaments.id
                         AND tournament_admins.user_id = ?
                   ))
            """
            params.extend([current_user["id"], current_user["id"], current_user["id"]])
        elif current_view == "mine":
            # 我参与的赛事：已注册但非创建者/管理员的赛事
            query += """
                WHERE EXISTS(
                       SELECT 1
                       FROM registrations
                       WHERE registrations.tournament_id = tournaments.id
                         AND registrations.user_id = ?
                   )
            """
            params.append(current_user["id"])
        if current_view == "managed":
            # 我管理的赛事：创建者或共享管理员
            query += """
                WHERE tournaments.creator_id = ?
                   OR EXISTS(
                       SELECT 1
                       FROM tournament_admins
                       WHERE tournament_admins.tournament_id = tournaments.id
                         AND tournament_admins.user_id = ?
                   )
            """
            params.extend([current_user["id"], current_user["id"]])
        if current_view == "history":
            query += " ORDER BY tournaments.start_time DESC, tournaments.created_at DESC"
        else:
            query += " ORDER BY tournaments.start_time ASC, tournaments.created_at DESC"
        tournaments = connection.execute(query, params).fetchall()

        cards = []
        for row in tournaments:
            finished = tournament_is_finished(connection, row)
            if current_view == "all" and finished:
                continue
            if current_view == "history" and not finished:
                continue

            managed = row["creator_id"] == current_user["id"] or bool(row["shared_admin"])
            cards.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "creator_name": row["creator_name"],
                    "start_time": row["start_time"],
                    "checkin_time": row["checkin_time"],
                    "format_label": FORMAT_LABELS.get(row["format_type"], row["format_type"]),
                    "stage_label": STAGE_LABELS.get(row["stage_mode"], row["stage_mode"]),
                    "participant_count": row["participant_count"],
                    "checked_in_count": row["checked_in_count"],
                    "joined": bool(row["joined"]),
                    "managed": managed,
                    "description_excerpt": excerpt(row["description"]),
                    "hide_bracket_until_publish": bool(row["hide_bracket_until_publish"]),
                    "bracket_published": bool(row["bracket_published"]),
                    "phase_label": "比赛已结束" if finished else tournament_phase_label(tournament_phase(row)),
                    "creator_can_delete": row["creator_id"] == current_user["id"],
                    "is_points_event": bool(row["is_points_event"]),
                    "points_for_champion": int(row["points_for_champion"] or 0),
                    "is_private": bool(row["is_private"]),
                }
            )

    empty_title = "还没有赛事"
    empty_message = "创建后会在大厅中展示。"
    if current_view == "mine":
        empty_title = "还没有参与的赛事"
        empty_message = "你报名的赛事会显示在这里。"
    elif current_view == "managed":
        empty_title = "还没有管理的赛事"
        empty_message = "你创建或担任共享管理员的赛事会显示在这里。"
    elif current_view == "history":
        empty_title = "还没有历史比赛"
        empty_message = "已经结束的赛事会显示在这里。"

    return render(
        request,
        "dashboard.html",
        {
            "page_title": "赛事大厅",
            "current_user": current_user,
            "tournaments": cards,
            "current_view": current_view,
            "empty_title": empty_title,
            "empty_message": empty_message,
            "dashboard_error": request.query_params.get("error"),
            "dashboard_success": request.query_params.get("success"),
        },
    )


@app.post("/dashboard/join-by-invite")
def join_tournament_by_invite(
    request: Request,
    invite_code: str = Form(""),
):
    """通过邀请码加入私密赛事。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    redirect_path = "/dashboard"

    if not invite_code or not invite_code.strip():
        return RedirectResponse(
            append_redirect_query_value(redirect_path, "error", "邀请码不能为空。"),
            status_code=303,
        )

    invite_code = invite_code.strip().upper()
    if len(invite_code) != 6:
        return RedirectResponse(
            append_redirect_query_value(redirect_path, "error", "邀请码应为 6 位字符。"),
            status_code=303,
        )

    with closing(get_connection()) as connection, connection:
        tournament = connection.execute(
            "SELECT * FROM tournaments WHERE invite_code = ? AND is_private = 1",
            (invite_code,),
        ).fetchone()
        if tournament is None:
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "error", "无效的邀请码。"),
                status_code=303,
            )

        existing = connection.execute(
            "SELECT id FROM registrations WHERE tournament_id = ? AND user_id = ?",
            (tournament["id"], current_user["id"]),
        ).fetchone()
        if existing is not None:
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "error", "你已经报名了该赛事。"),
                status_code=303,
            )

        display_name = current_user_game_nickname(current_user) or current_user["username"]
        connection.execute(
            "INSERT INTO registrations (tournament_id, user_id, nickname, checked_in, seed_order, group_number, joined_at) VALUES (?, ?, ?, 0, 0, 1, ?)",
            (tournament["id"], current_user["id"], display_name, now_text()),
        )

    return RedirectResponse(
        append_redirect_query_value(redirect_path, "success", f"已通过邀请码加入「{tournament['name']}」！"),
        status_code=303,
    )


@app.get("/ladder", response_class=HTMLResponse)
def ladder_page(request: Request, mode: str = LADDER_MODE_1V1):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    active_mode = normalize_ladder_mode(mode)
    with closing(get_connection()) as connection, connection:
        context = build_ladder_page_context(connection, current_user, active_mode)

    context["ladder_error"] = request.query_params.get("ladder_error")
    context["ladder_success"] = request.query_params.get("ladder_success")
    context["ladder_replay_error"] = request.query_params.get("ladder_replay_error")
    context["ladder_replay_success"] = request.query_params.get("ladder_replay_success")
    return render(request, "ladder.html", context)


@app.get("/ladder/bounties", response_class=HTMLResponse)
def ladder_bounties_page(request: Request):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        ladder_touch_presence(connection, int(current_user["id"]))
        sync_ladder_state(connection)
        context = build_ladder_bounties_context(connection, current_user)

    context["bounty_error"] = request.query_params.get("bounty_error") or ""
    context["bounty_success"] = request.query_params.get("bounty_success") or ""
    return render(request, "ladder_bounties.html", context)


@app.post("/ladder/settings/bp-seconds")
def update_ladder_bp_seconds(request: Request, mode: str = Form(LADDER_MODE_1V1), bp_seconds: int = Form(...)):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_superadmin_user(current_user):
        raise HTTPException(status_code=403, detail="只有 sudo 可以调整网页天梯的 BP 时长。")

    active_mode = normalize_ladder_mode(mode)
    redirect_path = f"/ladder?mode={quote(active_mode)}"
    if bp_seconds < LADDER_BP_SECONDS_MIN or bp_seconds > LADDER_BP_SECONDS_MAX:
        return RedirectResponse(
            append_redirect_query_value(
                redirect_path,
                "ladder_error",
                f"天梯 BP 时长必须在 {LADDER_BP_SECONDS_MIN} 到 {LADDER_BP_SECONDS_MAX} 秒之间。",
            ),
            status_code=303,
        )

    with closing(get_connection()) as connection, connection:
        set_system_setting(connection, SYSTEM_SETTING_LADDER_BP_SECONDS, str(bp_seconds))
        updated_at = now_text()
        bp_deadline = timestamp_text_after(bp_seconds)
        bp_states = (
            LADDER_MATCH_STATE_BP_BAN,
            LADDER_MATCH_STATE_TEAM_BAN,
            LADDER_MATCH_STATE_BP_PICK,
            LADDER_MATCH_STATE_TEAM_PICK,
        )
        placeholders = ", ".join("?" for _ in bp_states)
        connection.execute(
            f"UPDATE ladder_matches SET deadline_at = ?, updated_at = ? WHERE state IN ({placeholders})",
            (bp_deadline, updated_at, *bp_states),
        )

    return RedirectResponse(
        append_redirect_query_value(redirect_path, "ladder_success", f"已将网页天梯 BP 时长调整为 {bp_seconds} 秒。"),
        status_code=303,
    )


@app.post("/ladder/bounties/publish")
def publish_ladder_bounty(
    request: Request,
    name: str = Form(""),
    slot_count: int = Form(1),
    amount: str = Form(""),
    description: str = Form(""),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not has_ladder_admin_access(current_user):
        raise HTTPException(status_code=403, detail="只有天梯管理员或超级管理员可以发布悬赏。")

    redirect_path = "/ladder/bounties"
    bounty_name = str(name or "").strip()
    bounty_amount = str(amount or "").strip()
    bounty_description = str(description or "").strip()
    if not bounty_name:
        return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_error", "请输入悬赏名。"), status_code=303)
    if slot_count < 1:
        return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_error", "悬赏名额至少为 1。"), status_code=303)
    if not bounty_amount:
        return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_error", "请输入悬赏金额。"), status_code=303)
    if not bounty_description:
        return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_error", "请输入悬赏内容。"), status_code=303)

    with closing(get_connection()) as connection, connection:
        existing = connection.execute(
            "SELECT id FROM ladder_bounties WHERE name = ? COLLATE NOCASE",
            (bounty_name,),
        ).fetchone()
        if existing is not None:
            return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_error", "该悬赏名已存在。"), status_code=303)

        current_time = now_text()
        connection.execute(
            """
            INSERT INTO ladder_bounties (name, slot_count, amount, description, status, created_by_user_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bounty_name,
                slot_count,
                bounty_amount,
                bounty_description,
                LADDER_BOUNTY_STATUS_ACTIVE,
                int(current_user["id"]),
                current_time,
                current_time,
            ),
        )

    return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_success", "已发布新的天梯悬赏。"), status_code=303)


@app.post("/ladder/bounties/submit")
def submit_ladder_bounty(
    request: Request,
    bounty_id: int = Form(...),
    replay_files: list[str] = Form([]),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    redirect_path = "/ladder/bounties"
    with closing(get_connection()) as connection, connection:
        bounty = load_ladder_bounty_row(connection, bounty_id)
        if bounty is None:
            raise HTTPException(status_code=404, detail="悬赏不存在。")
        if bounty["status"] != LADDER_BOUNTY_STATUS_ACTIVE:
            return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_error", "该悬赏已撤回，不能再提交。"), status_code=303)
        if ladder_bounty_valid_submission_count(connection, bounty_id) >= int(bounty["slot_count"] or 0):
            return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_error", "该悬赏已经满额，不能再提交。"), status_code=303)

        fresh_user = load_user_row(connection, int(current_user["id"])) or current_user
        replay_map = ladder_user_replay_file_map(fresh_user)
        selected_files = [name for name in dict.fromkeys(str(item or "").strip() for item in replay_files) if name]
        if not selected_files:
            return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_error", "请至少选择一个你自己的 replay 文件。"), status_code=303)
        if any(name not in replay_map for name in selected_files):
            return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_error", "所选 replay 中包含无效文件。"), status_code=303)

        existing_submission = connection.execute(
            "SELECT * FROM ladder_bounty_submissions WHERE bounty_id = ? AND user_id = ?",
            (bounty_id, int(fresh_user["id"])),
        ).fetchone()
        if existing_submission is not None and existing_submission["status"] in {
            LADDER_BOUNTY_SUBMISSION_PENDING,
            LADDER_BOUNTY_SUBMISSION_VALID,
        }:
            return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_error", "你已经提交过该悬赏，等待审核或已通过，无需重复提交。"), status_code=303)

        submission_dir = ladder_bounty_submission_path(str(bounty["name"]), str(fresh_user["username"]))
        archive_path = ladder_bounty_submission_archive_path(str(bounty["name"]), str(fresh_user["username"]))
        if submission_dir.exists():
            shutil.rmtree(submission_dir, ignore_errors=True)
        if archive_path.exists():
            archive_path.unlink()
        submission_dir.mkdir(parents=True, exist_ok=True)
        for file_name in selected_files:
            shutil.copy2(replay_map[file_name], submission_dir / file_name)

        current_time = now_text()
        payload = json.dumps(selected_files, ensure_ascii=False)
        saved_dir = replay_saved_path_text(submission_dir)
        if existing_submission is None:
            connection.execute(
                """
                INSERT INTO ladder_bounty_submissions (
                    bounty_id, user_id, status, invalid_reason, selected_files_json, copied_dir,
                    reviewed_by_user_id, reviewed_at, created_at, updated_at
                ) VALUES (?, ?, ?, NULL, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    bounty_id,
                    int(fresh_user["id"]),
                    LADDER_BOUNTY_SUBMISSION_PENDING,
                    payload,
                    saved_dir,
                    current_time,
                    current_time,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE ladder_bounty_submissions
                SET status = ?, invalid_reason = NULL, selected_files_json = ?, copied_dir = ?,
                    reviewed_by_user_id = NULL, reviewed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    LADDER_BOUNTY_SUBMISSION_PENDING,
                    payload,
                    saved_dir,
                    current_time,
                    int(existing_submission["id"]),
                ),
            )

    return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_success", "已提交悬赏，等待天梯管理员审核。"), status_code=303)


@app.post("/ladder/bounties/{bounty_id}/revoke")
def revoke_ladder_bounty(request: Request, bounty_id: int, revoke_reason: str = Form("")):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not has_ladder_admin_access(current_user):
        raise HTTPException(status_code=403, detail="只有天梯管理员或超级管理员可以撤回悬赏。")

    redirect_path = "/ladder/bounties"
    with closing(get_connection()) as connection, connection:
        bounty = load_ladder_bounty_row(connection, bounty_id)
        if bounty is None:
            raise HTTPException(status_code=404, detail="悬赏不存在。")
        if bounty["status"] == LADDER_BOUNTY_STATUS_REVOKED:
            return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_error", "该悬赏已经撤回。"), status_code=303)

        current_time = now_text()
        cleaned_reason = str(revoke_reason or "").strip() or "管理员已撤回该悬赏。"
        connection.execute(
            """
            UPDATE ladder_bounties
            SET status = ?, revoked_by_user_id = ?, revoked_reason = ?, revoked_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                LADDER_BOUNTY_STATUS_REVOKED,
                int(current_user["id"]),
                cleaned_reason,
                current_time,
                current_time,
                bounty_id,
            ),
        )

    return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_success", "已撤回该天梯悬赏。"), status_code=303)


@app.post("/ladder/bounties/submissions/{submission_id}/review")
def review_ladder_bounty_submission(
    request: Request,
    submission_id: int,
    decision: str = Form(""),
    invalid_reason: str = Form(""),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not has_ladder_admin_access(current_user):
        raise HTTPException(status_code=403, detail="只有天梯管理员或超级管理员可以审核悬赏提交。")

    redirect_path = "/ladder/bounties"
    action = str(decision or "").strip().lower()
    with closing(get_connection()) as connection, connection:
        submission = load_ladder_bounty_submission_row(connection, submission_id)
        if submission is None:
            raise HTTPException(status_code=404, detail="悬赏提交不存在。")
        bounty = load_ladder_bounty_row(connection, int(submission["bounty_id"]))
        if bounty is None:
            raise HTTPException(status_code=404, detail="悬赏不存在。")
        if submission["status"] != LADDER_BOUNTY_SUBMISSION_PENDING:
            return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_error", "当前提交不在待审核状态。"), status_code=303)

        current_time = now_text()
        if action == LADDER_BOUNTY_SUBMISSION_VALID:
            if ladder_bounty_valid_submission_count(connection, int(bounty["id"])) >= int(bounty["slot_count"] or 0):
                return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_error", "该悬赏已经满额，不能再标记新的有效提交。"), status_code=303)
            connection.execute(
                """
                UPDATE ladder_bounty_submissions
                SET status = ?, invalid_reason = NULL, reviewed_by_user_id = ?, reviewed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    LADDER_BOUNTY_SUBMISSION_VALID,
                    int(current_user["id"]),
                    current_time,
                    current_time,
                    submission_id,
                ),
            )
            success_message = "已将该悬赏提交判定为有效。"
        elif action == LADDER_BOUNTY_SUBMISSION_INVALID:
            cleaned_reason = str(invalid_reason or "").strip()
            if not cleaned_reason:
                return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_error", "判定无效时必须填写无效原因。"), status_code=303)
            connection.execute(
                """
                UPDATE ladder_bounty_submissions
                SET status = ?, invalid_reason = ?, reviewed_by_user_id = ?, reviewed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    LADDER_BOUNTY_SUBMISSION_INVALID,
                    cleaned_reason,
                    int(current_user["id"]),
                    current_time,
                    current_time,
                    submission_id,
                ),
            )
            success_message = "已将该悬赏提交判定为无效。"
        else:
            return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_error", "审核动作无效。"), status_code=303)

    return RedirectResponse(append_redirect_query_value(redirect_path, "bounty_success", success_message), status_code=303)


@app.get("/ladder/bounties/submissions/{submission_id}/archive")
def download_ladder_bounty_submission_archive(request: Request, submission_id: int):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not has_ladder_admin_access(current_user):
        raise HTTPException(status_code=403, detail="只有天梯管理员或超级管理员可以下载悬赏提交归档。")

    with closing(get_connection()) as connection, connection:
        submission = load_ladder_bounty_submission_row(connection, submission_id)
        if submission is None:
            raise HTTPException(status_code=404, detail="悬赏提交不存在。")
        bounty = load_ladder_bounty_row(connection, int(submission["bounty_id"]))
        if bounty is None:
            raise HTTPException(status_code=404, detail="悬赏不存在。")
        submitter = load_user_row(connection, int(submission["user_id"]))
        if submitter is None:
            raise HTTPException(status_code=404, detail="提交用户不存在。")

        archive_path = refresh_ladder_bounty_submission_archive(str(bounty["name"]), str(submitter["username"]))
        if archive_path is None or not archive_path.exists():
            raise HTTPException(status_code=404, detail="当前提交还没有可下载的 replay 文件。")

    return FileResponse(
        path=str(archive_path),
        filename=archive_path.name,
        media_type="application/gzip",
    )


@app.get("/ladder/partial", response_class=HTMLResponse)
def ladder_partial(request: Request, mode: str = LADDER_MODE_1V1):
    current_user = require_user(request)
    if current_user is None:
        raise HTTPException(status_code=401, detail="请先登录。")

    active_mode = normalize_ladder_mode(mode)
    with closing(get_connection()) as connection, connection:
        context = build_ladder_page_context(connection, current_user, active_mode)

    return render(request, "_ladder_match_panel.html", context)


@app.get("/ladder/sync")
def ladder_sync_status(request: Request):
    current_user = require_user(request)
    if current_user is None:
        raise HTTPException(status_code=401, detail="请先登录。")

    with closing(get_connection()) as connection, connection:
        ladder_touch_presence(connection, int(current_user["id"]))
        sync_ladder_state(connection)
        live_state = build_ladder_live_state(connection, int(current_user["id"]))
        return {
            "sync_key": ladder_match_sync_key(connection, int(current_user["id"])),
            "interval_ms": LADDER_SYNC_POLL_MS,
            "lobby": live_state["lobby"],
            "lobby_chat_messages": live_state["lobby_chat_messages"],
            "current_match_id": live_state["current_match_id"],
            "match_chat_messages": live_state["match_chat_messages"],
            "chat_messages": live_state["chat_messages"],
        }


@app.post("/ladder/queue/start")
def ladder_start_queue(
    request: Request,
    mode: str = Form(LADDER_MODE_1V1),
    commanders: list[str] = Form([]),
    teammate: str = Form(""),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    active_mode = normalize_ladder_mode(mode)
    redirect_path = f"/ladder?mode={quote(active_mode)}"

    with closing(get_connection()) as connection, connection:
        sync_ladder_state(connection)
        fresh_user = load_user_row(connection, int(current_user["id"])) or current_user
        if ladder_user_is_busy(connection, int(fresh_user["id"])):
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "ladder_error", "你当前已在匹配或比赛中。"),
                status_code=303,
            )

        current_time = now_text()
        if active_mode == LADDER_MODE_1V1:
            selected_commanders = normalize_ladder_commanders(commanders, 3)
            if len(selected_commanders) != 3:
                return RedirectResponse(
                    append_redirect_query_value(redirect_path, "ladder_error", "1v1 匹配必须选择 3 位有效指挥官。"),
                    status_code=303,
                )
            connection.execute(
                """
                INSERT INTO ladder_queue_entries (mode, captain_user_id, teammate_user_id, commanders_json, rating, joined_at, created_at, updated_at)
                VALUES (?, ?, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    LADDER_MODE_1V1,
                    fresh_user["id"],
                    json.dumps(selected_commanders, ensure_ascii=False),
                    float(fresh_user["ladder_rating_1v1"] or 1500.0),
                    current_time,
                    current_time,
                    current_time,
                ),
            )
        else:
            teammate_name = trim_username(teammate)
            if not teammate_name:
                return RedirectResponse(
                    append_redirect_query_value(redirect_path, "ladder_error", "请输入 2v2 队友账号。"),
                    status_code=303,
                )
            teammate_row = connection.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (teammate_name,),
            ).fetchone()
            if teammate_row is None:
                return RedirectResponse(
                    append_redirect_query_value(redirect_path, "ladder_error", "该队友账号不存在。"),
                    status_code=303,
                )
            if int(teammate_row["id"]) == int(fresh_user["id"]):
                return RedirectResponse(
                    append_redirect_query_value(redirect_path, "ladder_error", "2v2 队友不能是自己。"),
                    status_code=303,
                )
            if ladder_user_is_busy(connection, int(teammate_row["id"])):
                return RedirectResponse(
                    append_redirect_query_value(redirect_path, "ladder_error", "该队友当前已在匹配或比赛中。"),
                    status_code=303,
                )
            pair_rating = ladder_get_pair_rating(connection, int(fresh_user["id"]), int(teammate_row["id"]))
            connection.execute(
                """
                INSERT INTO ladder_queue_entries (mode, captain_user_id, teammate_user_id, commanders_json, rating, joined_at, created_at, updated_at)
                VALUES (?, ?, ?, '[]', ?, ?, ?, ?)
                """,
                (
                    LADDER_MODE_2V2,
                    fresh_user["id"],
                    teammate_row["id"],
                    round(float(pair_rating.rating), 2),
                    current_time,
                    current_time,
                    current_time,
                ),
            )

        sync_ladder_state(connection)

    return RedirectResponse(
        append_redirect_query_value(redirect_path, "ladder_success", "已进入天梯匹配队列。"),
        status_code=303,
    )


@app.post("/ladder/queue/cancel")
def ladder_cancel_queue(request: Request, mode: str = Form(LADDER_MODE_1V1)):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    active_mode = normalize_ladder_mode(mode)
    redirect_path = f"/ladder?mode={quote(active_mode)}"
    with closing(get_connection()) as connection, connection:
        queue_row = load_ladder_queue_entry_for_user(connection, int(current_user["id"]))
        if queue_row is None:
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "ladder_error", "当前没有可取消的匹配队列。"),
                status_code=303,
            )
        connection.execute("DELETE FROM ladder_queue_entries WHERE id = ?", (queue_row["id"],))

    return RedirectResponse(
        append_redirect_query_value(redirect_path, "ladder_success", "已取消当前匹配。"),
        status_code=303,
    )


@app.post("/ladder/matches/{match_id}/accept")
def ladder_accept_match(request: Request, match_id: int, mode: str = Form(LADDER_MODE_1V1)):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    active_mode = normalize_ladder_mode(mode)
    redirect_path = f"/ladder?mode={quote(active_mode)}"
    with closing(get_connection()) as connection, connection:
        sync_ladder_state(connection)
        match_row = connection.execute("SELECT * FROM ladder_matches WHERE id = ?", (match_id,)).fetchone()
        if match_row is None:
            raise HTTPException(status_code=404, detail="天梯比赛不存在。")
        if ladder_user_side(match_row, int(current_user["id"])) == "":
            raise HTTPException(status_code=403, detail="只有当前比赛选手可以确认比赛。")
        if match_row["state"] != LADDER_MATCH_STATE_PENDING_ACCEPT:
            return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_error", "当前比赛已经不在等待确认阶段。"), status_code=303)

        state = ladder_match_state(match_row)
        acceptance = state.setdefault("acceptance", {})
        confirmed_user_ids = [int(value) for value in acceptance.get("confirmed_user_ids") or [] if str(value).isdigit()]
        current_user_id = int(current_user["id"])
        if current_user_id not in confirmed_user_ids:
            confirmed_user_ids.append(current_user_id)
        acceptance["confirmed_user_ids"] = sorted(set(confirmed_user_ids))

        participant_ids = set(ladder_match_user_ids(match_row))
        if participant_ids.issubset(set(acceptance["confirmed_user_ids"])):
            row_state, deadline_at, state = ladder_transition_to_bp_start(match_row, state, ladder_bp_seconds_setting(connection))
            save_ladder_match_state(connection, match_row, state, row_state, deadline_at)
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "ladder_success", "所有选手已确认比赛，已进入 BP 阶段。"),
                status_code=303,
            )

        save_ladder_match_state(connection, match_row, state, LADDER_MATCH_STATE_PENDING_ACCEPT, match_row["deadline_at"])

    return RedirectResponse(
        append_redirect_query_value(redirect_path, "ladder_success", "已确认比赛，等待其他选手确认。"),
        status_code=303,
    )


@app.post("/ladder/matches/{match_id}/reject")
def ladder_reject_match(request: Request, match_id: int, mode: str = Form(LADDER_MODE_1V1)):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    active_mode = normalize_ladder_mode(mode)
    redirect_path = f"/ladder?mode={quote(active_mode)}"
    with closing(get_connection()) as connection, connection:
        sync_ladder_state(connection)
        match_row = connection.execute("SELECT * FROM ladder_matches WHERE id = ?", (match_id,)).fetchone()
        if match_row is None:
            raise HTTPException(status_code=404, detail="天梯比赛不存在。")
        if ladder_user_side(match_row, int(current_user["id"])) == "":
            raise HTTPException(status_code=403, detail="只有当前比赛选手可以拒绝比赛。")
        if match_row["state"] != LADDER_MATCH_STATE_PENDING_ACCEPT:
            return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_error", "当前比赛已经不在等待确认阶段。"), status_code=303)

        ladder_cancel_proposed_match(connection, match_row, int(current_user["id"]))

    return RedirectResponse(
        append_redirect_query_value(redirect_path, "ladder_success", "已拒绝本次匹配，本局不会建立 BP，可重新开始下一次匹配。"),
        status_code=303,
    )


@app.post("/ladder/matches/{match_id}/bp")
def ladder_submit_bp(
    request: Request,
    match_id: int,
    mode: str = Form(LADDER_MODE_1V1),
    choice: str = Form(""),
    choices: list[str] = Form([]),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    active_mode = normalize_ladder_mode(mode)
    redirect_path = f"/ladder?mode={quote(active_mode)}"
    with closing(get_connection()) as connection, connection:
        sync_ladder_state(connection)
        match_row = connection.execute("SELECT * FROM ladder_matches WHERE id = ?", (match_id,)).fetchone()
        if match_row is None:
            raise HTTPException(status_code=404, detail="天梯比赛不存在。")
        if ladder_user_side(match_row, int(current_user["id"])) == "":
            raise HTTPException(status_code=403, detail="只有当前比赛选手可以操作 BP。")

        state = ladder_match_state(match_row)
        side = ladder_user_side(match_row, int(current_user["id"]))
        row_state = str(match_row["state"])
        deadline_at = match_row["deadline_at"]

        if match_row["mode"] == LADDER_MODE_1V1:
            if row_state == LADDER_MATCH_STATE_BP_BAN:
                if state["bp"]["ban"].get(side) is not None:
                    return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_error", "你已提交过 Ban。"), status_code=303)
                cleaned_choice = str(choice or "").strip()
                opponent_side = "B" if side == "A" else "A"
                opponent_pool = state["teams"][opponent_side].get("commanders") or []
                if cleaned_choice and cleaned_choice not in opponent_pool:
                    return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_error", "Ban 目标无效。"), status_code=303)
                state["bp"]["ban"][side] = cleaned_choice
                if state["bp"]["ban"].get("A") is not None and state["bp"]["ban"].get("B") is not None:
                    row_state, deadline_at, state = ladder_transition_to_pick(match_row, state, ladder_bp_seconds_setting(connection))
            elif row_state == LADDER_MATCH_STATE_BP_PICK:
                if state["bp"]["pick"].get(side) is not None:
                    return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_error", "你已提交过 Pick。"), status_code=303)
                cleaned_choice = str(choice or "").strip()
                available = list(state["bp"]["available"].get(side) or [])
                if cleaned_choice.lower() == "random":
                    cleaned_choice = random.choice(available) if available else ""
                if cleaned_choice not in available:
                    return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_error", "Pick 目标无效。"), status_code=303)
                state["bp"]["pick"][side] = cleaned_choice
                if state["bp"]["pick"].get("A") is not None and state["bp"]["pick"].get("B") is not None:
                    row_state, deadline_at, state = ladder_transition_to_ready(match_row, state, timeout=False)
            else:
                return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_error", "当前阶段不接受 BP 操作。"), status_code=303)
        else:
            captain_user_id = int(state["teams"][side].get("captain_user_id") or 0)
            if captain_user_id != int(current_user["id"]):
                return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_error", "2v2 BP 仅允许队长提交。"), status_code=303)
            if row_state == LADDER_MATCH_STATE_TEAM_BAN:
                if state["bp"]["ban"].get(side) is not None:
                    return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_error", "你方已提交过 Ban。"), status_code=303)
                selected = normalize_ladder_commanders(choices, 3)
                opponent_side = "B" if side == "A" else "A"
                opponent_pool = state["teams"][opponent_side].get("commanders") or []
                if any(item not in opponent_pool for item in selected):
                    return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_error", "Ban 目标无效。"), status_code=303)
                state["bp"]["ban"][side] = selected
                if state["bp"]["ban"].get("A") is not None and state["bp"]["ban"].get("B") is not None:
                    row_state, deadline_at, state = ladder_transition_to_pick(match_row, state, ladder_bp_seconds_setting(connection))
            elif row_state == LADDER_MATCH_STATE_TEAM_PICK:
                if state["bp"]["pick"].get(side) is not None:
                    return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_error", "你方已提交过 Pick。"), status_code=303)
                selected = normalize_ladder_commanders(choices, 2)
                available = list(state["bp"]["available"].get(side) or [])
                if len(selected) != 2 or any(item not in available for item in selected):
                    return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_error", "请选择 2 位有效指挥官。"), status_code=303)
                state["bp"]["pick"][side] = selected
                if state["bp"]["pick"].get("A") is not None and state["bp"]["pick"].get("B") is not None:
                    row_state, deadline_at, state = ladder_transition_to_ready(match_row, state, timeout=False)
            else:
                return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_error", "当前阶段不接受 BP 操作。"), status_code=303)

        save_ladder_match_state(connection, match_row, state, row_state, deadline_at)

    return RedirectResponse(
        append_redirect_query_value(redirect_path, "ladder_success", "BP 提交成功。"),
        status_code=303,
    )


@app.post("/ladder/matches/{match_id}/admin-review")
def ladder_request_admin_review(request: Request, match_id: int, mode: str = Form(LADDER_MODE_1V1)):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    active_mode = normalize_ladder_mode(mode)
    redirect_path = f"/ladder?mode={quote(active_mode)}"
    with closing(get_connection()) as connection, connection:
        sync_ladder_state(connection)
        match_row = connection.execute("SELECT * FROM ladder_matches WHERE id = ?", (match_id,)).fetchone()
        if match_row is None:
            raise HTTPException(status_code=404, detail="天梯比赛不存在。")
        if ladder_user_side(match_row, int(current_user["id"])) == "":
            raise HTTPException(status_code=403, detail="只有当前比赛选手可以申请管理员介入。")
        if match_row["state"] != LADDER_MATCH_STATE_READY:
            return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_error", "当前比赛尚未进入结果汇报阶段。"), status_code=303)

        state = ladder_match_state(match_row)
        state["admin_review"] = {
            "requested_by_user_id": int(current_user["id"]),
            "requested_by_username": str(current_user["username"]),
            "requested_at": now_text(),
            "reason": "玩家申请管理员介入，等待 sudo 裁定获胜方。",
            "resolved_by_admin": False,
            "resolved_at": None,
        }
        save_ladder_match_state(connection, match_row, state, LADDER_MATCH_STATE_ADMIN_REVIEW, None)

    return RedirectResponse(
        append_redirect_query_value(redirect_path, "ladder_success", "已提交到 sudo 后台裁定，本局不再阻塞你开始下一次匹配。"),
        status_code=303,
    )


@app.post("/ladder/matches/{match_id}/replay")
async def ladder_upload_replay(
    request: Request,
    match_id: int,
    mode: str = Form(LADDER_MODE_1V1),
    replay_file: UploadFile = File(...),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    active_mode = normalize_ladder_mode(mode)
    redirect_path = f"/ladder?mode={quote(active_mode)}"
    replay_bytes = b""
    try:
        replay_bytes = await replay_file.read()
        with closing(get_connection()) as connection, connection:
            sync_ladder_state(connection)
            match_row = connection.execute("SELECT * FROM ladder_matches WHERE id = ?", (match_id,)).fetchone()
            if match_row is None:
                raise HTTPException(status_code=404, detail="天梯比赛不存在。")
            if ladder_user_side(match_row, int(current_user["id"])) == "":
                raise HTTPException(status_code=403, detail="只有当前比赛选手可以上传 replay。")
            if match_row["state"] != LADDER_MATCH_STATE_READY:
                return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_replay_error", "当前比赛尚未完成 BP，暂时不能上传 replay。"), status_code=303)
            if match_row["winner_side"]:
                return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_replay_error", "当前比赛结果已经记录，无需重复上传。"), status_code=303)

            state = ladder_match_state(match_row)
            replay_result = inspect_replay(BytesIO(replay_bytes), replay_file.filename or "", bound_game_nickname=current_user_game_nickname(current_user))
            team_a_rows = [load_user_row(connection, int(match_row["team_a_user1_id"])), load_user_row(connection, int(match_row["team_a_user2_id"])) if match_row["team_a_user2_id"] is not None else None]
            team_b_rows = [load_user_row(connection, int(match_row["team_b_user1_id"])), load_user_row(connection, int(match_row["team_b_user2_id"])) if match_row["team_b_user2_id"] is not None else None]
            team_a_nicknames = [current_user_game_nickname(row) for row in team_a_rows if row is not None]
            team_b_nicknames = [current_user_game_nickname(row) for row in team_b_rows if row is not None]
            verdict = build_strict_team_match_verdict(
                replay_result["teams"],
                team_a_nicknames,
                team_b_nicknames,
                team_a_label=ladder_team_display_name(team_a_rows),
                team_b_label=ladder_team_display_name(team_b_rows),
            )
            if verdict["status"] != "valid":
                return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_replay_error", verdict["detail"]), status_code=303)

            expected_map = replay_map_match_key(match_row["map_name"])
            actual_map = replay_map_match_key(replay_result["map_name"])
            if expected_map and actual_map and expected_map != actual_map:
                return RedirectResponse(
                    append_redirect_query_value(redirect_path, "ladder_replay_error", f"当前比赛地图应为 {match_row['map_name']}，与上传 replay 不一致。"),
                    status_code=303,
                )

            ready_at = replay_datetime_value(state.get("ready_at"))
            replay_start = replay_datetime_value(replay_result.get("start_time_dt") or replay_result.get("start_time"))
            if ready_at is not None and replay_start is not None and replay_start < ready_at:
                return RedirectResponse(
                    append_redirect_query_value(redirect_path, "ladder_replay_error", "当前 replay 开始时间早于本局 BP 完成时间，不能用于结算。"),
                    status_code=303,
                )

            cleanup_expired_ladder_replays()
            winner_side = str(verdict["winner_side"])
            replay_paths = ladder_rank_replay_paths(
                connection,
                match_row,
                state,
                replay_result,
                winner_side,
                team_a_rows,
                team_b_rows,
                replay_result["filename"],
            )
            for replay_path in replay_paths:
                replay_path.parent.mkdir(parents=True, exist_ok=True)
                replay_path.write_bytes(replay_bytes)
            uploaded_at = now_text()
            saved_path_text = replay_saved_paths_text(replay_paths)
            ladder_resolve_match(connection, match_row, winner_side, replay_result["filename"], saved_path_text, uploaded_at)
    except (RuntimeError, ValueError) as exc:
        return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_replay_error", str(exc)), status_code=303)
    except HTTPException:
        raise
    except Exception as exc:
        return RedirectResponse(append_redirect_query_value(redirect_path, "ladder_replay_error", f"解析 replay 失败：{exc}"), status_code=303)
    finally:
        await replay_file.close()

    return RedirectResponse(
        append_redirect_query_value(redirect_path, "ladder_replay_success", "已读取 replay 并完成天梯结算。"),
        status_code=303,
    )


@app.post("/ladder/matches/{match_id}/chat")
def ladder_send_chat(
    request: Request,
    match_id: int,
    mode: str = Form(LADDER_MODE_1V1),
    text: str = Form(""),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    active_mode = normalize_ladder_mode(mode)
    redirect_path = f"/ladder?mode={quote(active_mode)}"
    with closing(get_connection()) as connection, connection:
        ladder_touch_presence(connection, int(current_user["id"]))
        match_row = connection.execute("SELECT * FROM ladder_matches WHERE id = ?", (match_id,)).fetchone()
        if match_row is None:
            raise HTTPException(status_code=404, detail="天梯比赛不存在。")
        if ladder_user_side(match_row, int(current_user["id"])) == "":
            raise HTTPException(status_code=403, detail="只有当前比赛选手可以发送聊天消息。")
        if match_row["state"] == LADDER_MATCH_STATE_RESOLVED:
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "ladder_error", "当前比赛已结算，临时聊天室已关闭。"),
                status_code=303,
            )

        message_text = normalize_ladder_chat_text(text)
        if not message_text:
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "ladder_error", "聊天消息不能为空。"),
                status_code=303,
            )

        ladder_append_chat_message(connection, match_id, int(current_user["id"]), message_text)

    return RedirectResponse(f"/ladder?mode={quote(active_mode)}", status_code=303)


@app.post("/ladder/chat/lobby")
def ladder_send_lobby_chat(
    request: Request,
    mode: str = Form(LADDER_MODE_1V1),
    text: str = Form(""),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    active_mode = normalize_ladder_mode(mode)
    redirect_path = f"/ladder?mode={quote(active_mode)}"
    with closing(get_connection()) as connection, connection:
        ladder_touch_presence(connection, int(current_user["id"]))
        message_text = normalize_ladder_chat_text(text)
        if not message_text:
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "ladder_error", "大厅聊天消息不能为空。"),
                status_code=303,
            )
        ladder_append_lobby_chat_message(connection, int(current_user["id"]), message_text)

    return RedirectResponse(redirect_path, status_code=303)


@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request, panel: str = "replay"):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_superadmin_user(current_user):
        raise HTTPException(status_code=403, detail="只有超级管理员可以访问后台。")

    with closing(get_connection()) as connection, connection:
        context = build_admin_panel_context(connection, current_user, active_admin_panel=normalize_admin_panel(panel))

    return render(request, "admin.html", context)


def build_admin_panel_context(
    connection: sqlite3.Connection,
    current_user: sqlite3.Row,
    replay_result: dict[str, Any] | None = None,
    replay_error: str | None = None,
    active_admin_panel: str = "replay",
) -> dict[str, Any]:
    cleanup_all_tournaments(connection)
    users = connection.execute(
        """
        SELECT users.*,
               (
                   SELECT COUNT(*)
                   FROM tournaments
                   WHERE tournaments.creator_id = users.id
               ) AS created_tournaments,
               (
                   SELECT COUNT(*)
                   FROM registrations
                   WHERE registrations.user_id = users.id
               ) AS joined_tournaments
        FROM users
         ORDER BY users.is_superadmin DESC, users.is_tournament_admin DESC, users.is_ladder_admin DESC, users.username COLLATE NOCASE
        """
    ).fetchall()
    tournaments = connection.execute(
        """
        SELECT tournaments.*, COALESCE(users.game_nickname, users.username) AS creator_name,
               (
                   SELECT COUNT(*)
                   FROM registrations
                   WHERE registrations.tournament_id = tournaments.id
               ) AS participant_count
        FROM tournaments
        JOIN users ON users.id = tournaments.creator_id
        ORDER BY tournaments.created_at DESC
        """
    ).fetchall()

    context = {
        "page_title": "超级管理员后台",
        "current_user": current_user,
        "active_admin_panel": normalize_admin_panel(active_admin_panel),
        "users": users,
        "tournaments": tournaments,
        "ladder_admin_reviews": load_ladder_admin_review_matches(connection),
    }
    if replay_result is not None:
        context["replay_result"] = replay_result
    if replay_error:
        context["replay_error"] = replay_error
    return context


def build_replay_tool_context(
    current_user: sqlite3.Row,
    replay_result: dict[str, Any] | None = None,
    replay_error: str | None = None,
) -> dict[str, Any]:
    context = {
        "page_title": "Replay 测试",
        "current_user": current_user,
    }
    if replay_result is not None:
        context["replay_result"] = replay_result
    if replay_error:
        context["replay_error"] = replay_error
    return context


@app.get("/tools/replay", response_class=HTMLResponse)
def replay_tool_page(request: Request):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    return render(request, "replay_test.html", build_replay_tool_context(current_user))


@app.post("/tools/replay/inspect", response_class=HTMLResponse)
async def inspect_replay_for_current_user(request: Request, replay_file: UploadFile = File(...)):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    replay_result: dict[str, Any] | None = None
    replay_error: str | None = None
    try:
        replay_result = inspect_replay(
            replay_file.file,
            replay_file.filename or "",
            bound_game_nickname=current_user_game_nickname(current_user),
        )
    except (RuntimeError, ValueError) as exc:
        replay_error = str(exc)
    except Exception as exc:
        replay_error = f"解析 replay 失败：{exc}"
    finally:
        await replay_file.close()

    return render(
        request,
        "replay_test.html",
        build_replay_tool_context(current_user, replay_result=replay_result, replay_error=replay_error),
        status_code=200 if replay_result is not None else 400,
    )


@app.post("/admin/replay/inspect", response_class=HTMLResponse)
async def inspect_replay_as_admin(request: Request, replay_file: UploadFile = File(...)):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_superadmin_user(current_user):
        raise HTTPException(status_code=403, detail="只有超级管理员可以测试 replay 解析。")

    replay_result: dict[str, Any] | None = None
    replay_error: str | None = None
    try:
        replay_result = inspect_replay(
            replay_file.file,
            replay_file.filename or "",
            bound_game_nickname=current_user_game_nickname(current_user),
        )
    except (RuntimeError, ValueError) as exc:
        replay_error = str(exc)
    except Exception as exc:
        replay_error = f"解析 replay 失败：{exc}"
    finally:
        await replay_file.close()

    with closing(get_connection()) as connection, connection:
        context = build_admin_panel_context(
            connection,
            current_user,
            replay_result=replay_result,
            replay_error=replay_error,
            active_admin_panel="replay",
        )

    return render(request, "admin.html", context, status_code=200 if replay_result is not None else 400)


@app.post("/admin/ladder/matches/{match_id}/resolve")
def resolve_ladder_match_as_admin(request: Request, match_id: int, winner_side: str = Form(""), panel: str = Form("ladder_reviews")):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_superadmin_user(current_user):
        raise HTTPException(status_code=403, detail="只有超级管理员可以裁定天梯比赛。")

    chosen_side = str(winner_side or "").strip().upper()
    if chosen_side not in {"A", "B"}:
        raise HTTPException(status_code=400, detail="裁定的胜方必须为 A 或 B。")

    with closing(get_connection()) as connection, connection:
        match_row = connection.execute("SELECT * FROM ladder_matches WHERE id = ?", (match_id,)).fetchone()
        if match_row is None:
            raise HTTPException(status_code=404, detail="天梯比赛不存在。")
        if match_row["state"] != LADDER_MATCH_STATE_ADMIN_REVIEW:
            raise HTTPException(status_code=400, detail="当前比赛不在等待 sudo 裁定状态。")
        ladder_resolve_match(
            connection,
            match_row,
            chosen_side,
            str(match_row["replay_filename"] or ""),
            str(match_row["replay_saved_path"] or ""),
            now_text(),
        )

    return RedirectResponse(admin_panel_redirect_path(panel), status_code=303)


@app.post("/admin/users/{user_id}/delete")
def delete_user(request: Request, user_id: int, panel: str = Form("users")):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_superadmin_user(current_user):
        raise HTTPException(status_code=403, detail="只有超级管理员可以删除用户。")

    with closing(get_connection()) as connection, connection:
        user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在。")
        if user["id"] == current_user["id"] or user["is_superadmin"]:
            raise HTTPException(status_code=400, detail="不能删除当前超级管理员账号。")
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))

    return RedirectResponse(admin_panel_redirect_path(panel), status_code=303)


@app.post("/admin/users/{user_id}/roles")
def update_user_roles(
    request: Request,
    user_id: int,
    ladder_admin: str | None = Form(None),
    tournament_admin: str | None = Form(None),
    shop_admin: str | None = Form(None),
    panel: str = Form("users"),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_superadmin_user(current_user):
        raise HTTPException(status_code=403, detail="只有超级管理员可以调整用户角色。")

    with closing(get_connection()) as connection, connection:
        user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在。")
        if int(user["id"]) == int(current_user["id"]) or bool(user["is_superadmin"]):
            raise HTTPException(status_code=400, detail="不能修改当前超级管理员账号的角色。")
        connection.execute(
            "UPDATE users SET is_ladder_admin = ?, is_tournament_admin = ?, is_shop_admin = ? WHERE id = ?",
            (1 if ladder_admin == "on" else 0, 1 if tournament_admin == "on" else 0, 1 if shop_admin == "on" else 0, user_id),
        )

    return RedirectResponse(admin_panel_redirect_path(panel), status_code=303)


@app.post("/admin/users/{user_id}/points")
def update_user_points(
    request: Request,
    user_id: int,
    points: int = Form(0),
    panel: str = Form("users"),
):
    """超级管理员手动调整用户赛事积分。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_superadmin_user(current_user):
        raise HTTPException(status_code=403, detail="只有超级管理员可以调整积分。")

    with closing(get_connection()) as connection, connection:
        user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在。")
        new_points = max(0, points)
        connection.execute(
            "UPDATE users SET tournament_points = ? WHERE id = ?",
            (new_points, user_id),
        )

    return RedirectResponse(admin_panel_redirect_path(panel), status_code=303)


@app.post("/admin/tournaments/{tournament_id}/delete")
def delete_tournament_as_admin(request: Request, tournament_id: int, panel: str = Form("tournaments")):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_superadmin_user(current_user):
        raise HTTPException(status_code=403, detail="只有超级管理员可以删除赛事。")

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        delete_tournament_record(connection, tournament)

    return RedirectResponse(admin_panel_redirect_path(panel), status_code=303)


@app.post("/admin/users/bulk-points")
async def bulk_update_user_points(
    request: Request,
    panel: str = Form("users"),
):
    """超级管理员批量修改用户赛事积分、兑换积分和赞助状态。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_superadmin_user(current_user):
        raise HTTPException(status_code=403, detail="只有超级管理员可以调整。")

    form_data = await request.form()
    with closing(get_connection()) as connection, connection:
        sponsor_set: set[int] = set()
        for key in form_data.keys():
            if key.startswith("sponsor_user_"):
                try:
                    sponsor_set.add(int(key.split("_")[-1]))
                except ValueError:
                    pass

        for key, value in form_data.items():
            if key.startswith("tournament_points_"):
                user_id = int(key.split("_")[-1])
                connection.execute(
                    "UPDATE users SET tournament_points = ? WHERE id = ?",
                    (max(0, int(value or 0)), user_id),
                )
            elif key.startswith("exchange_points_"):
                user_id = int(key.split("_")[-1])
                connection.execute(
                    "UPDATE users SET exchange_points = ? WHERE id = ?",
                    (max(0, int(value or 0)), user_id),
                )

        # 处理赞助状态：遍历所有 sponsor_user_ keys，设为 1；不在集合中的设为 0
        all_user_ids = connection.execute("SELECT id FROM users").fetchall()
        for row in all_user_ids:
            uid = row["id"]
            is_sponsor = 1 if uid in sponsor_set else 0
            connection.execute("UPDATE users SET is_sponsor = ? WHERE id = ?", (is_sponsor, uid))

    return RedirectResponse(admin_panel_redirect_path(panel), status_code=303)


# ── 积分商城 ──

SHOP_ITEM_IMAGE_DIR = BASE_DIR / "static" / "shop_images"


@app.get("/shop", response_class=HTMLResponse)
def shop_page(request: Request):
    """积分商城页面。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        items = connection.execute(
            "SELECT * FROM shop_items ORDER BY id DESC"
        ).fetchall()
        orders = connection.execute(
            """SELECT shop_orders.*, shop_items.name AS item_name, shop_items.points AS item_points,
                      users.username, users.game_nickname
               FROM shop_orders
               JOIN shop_items ON shop_items.id = shop_orders.item_id
               JOIN users ON users.id = shop_orders.user_id
               ORDER BY shop_orders.created_at DESC"""
        ).fetchall() if is_superadmin_user(current_user) or is_shop_admin_user(current_user) else []

    can_manage = is_superadmin_user(current_user) or is_shop_admin_user(current_user)

    return render(request, "shop.html", {
        "page_title": "积分商城",
        "current_user": current_user,
        "shop_items": items,
        "shop_orders": orders,
        "can_manage": can_manage,
        "is_superadmin": is_superadmin_user(current_user),
    })


def is_shop_admin_user(user: sqlite3.Row | None) -> bool:
    return bool(user and (user["is_shop_admin"] or user["is_superadmin"]))


@app.get("/shop/items/new", response_class=HTMLResponse)
def add_shop_item_page(request: Request):
    """添加积分商城物品页面。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_shop_admin_user(current_user):
        raise HTTPException(status_code=403, detail="无权限。")
    return render(request, "shop_add_item.html", {
        "page_title": "添加物品",
        "current_user": current_user,
    })


@app.get("/shop/orders/manage", response_class=HTMLResponse)
def shop_order_manage_page(request: Request):
    """订单管理页面。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_shop_admin_user(current_user):
        raise HTTPException(status_code=403, detail="无权限。")

    with closing(get_connection()) as connection, connection:
        orders = connection.execute(
            """SELECT shop_orders.*, shop_items.name AS item_name, shop_items.points AS item_points,
                      users.username, users.game_nickname
               FROM shop_orders
               JOIN shop_items ON shop_items.id = shop_orders.item_id
               JOIN users ON users.id = shop_orders.user_id
               ORDER BY shop_orders.created_at DESC"""
        ).fetchall()

    return render(request, "shop_orders.html", {
        "page_title": "订单管理",
        "current_user": current_user,
        "shop_orders": orders,
    })


@app.post("/shop/items")
async def add_shop_item(
    request: Request,
    name: str = Form(...),
    points: int = Form(...),
):
    """添加积分商城物品（管理员/超管）。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_shop_admin_user(current_user):
        raise HTTPException(status_code=403, detail="无权限。")

    SHOP_ITEM_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    image_path = ""
    try:
        form = await request.form()
        image_file = form.get("image")
        if image_file is not None and hasattr(image_file, "filename") and image_file.filename:
            ext = Path(image_file.filename).suffix or ".png"
            save_name = f"shop_{int(datetime.utcnow().timestamp())}_{secrets.token_hex(4)}{ext}"
            save_path = SHOP_ITEM_IMAGE_DIR / save_name
            save_path.write_bytes(await image_file.read())
            image_path = f"/static/shop_images/{save_name}"
    except Exception:
        pass

    with closing(get_connection()) as connection, connection:
        connection.execute(
            "INSERT INTO shop_items (name, points, image_path, created_at) VALUES (?, ?, ?, ?)",
            (name.strip(), max(1, points), image_path, now_text()),
        )

    return RedirectResponse("/shop", status_code=303)


@app.post("/shop/items/{item_id}/delete")
def delete_shop_item(request: Request, item_id: int):
    """删除积分商城物品。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_shop_admin_user(current_user):
        raise HTTPException(status_code=403, detail="无权限。")

    with closing(get_connection()) as connection, connection:
        connection.execute("DELETE FROM shop_items WHERE id = ?", (item_id,))
    return RedirectResponse("/shop", status_code=303)


@app.post("/shop/orders")
def place_shop_order(
    request: Request,
    item_id: int = Form(...),
    recipient_name: str = Form(""),
    phone: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
):
    """用户兑换积分商城物品。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        item = connection.execute("SELECT * FROM shop_items WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            return RedirectResponse("/shop?error=物品不存在", status_code=303)

        user = connection.execute("SELECT * FROM users WHERE id = ?", (current_user["id"],)).fetchone()
        exchange_pts = int(user["exchange_points"] or 0)
        if exchange_pts < int(item["points"]):
            return RedirectResponse("/shop?error=兑换积分不足", status_code=303)

        connection.execute(
            "UPDATE users SET exchange_points = exchange_points - ? WHERE id = ?",
            (int(item["points"]), current_user["id"]),
        )
        connection.execute(
            """INSERT INTO shop_orders (user_id, item_id, status, recipient_name, phone, address, notes, created_at)
               VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)""",
            (current_user["id"], item_id, recipient_name.strip(), phone.strip(), address.strip(), notes.strip(), now_text()),
        )

    return RedirectResponse("/shop?success=兑换成功", status_code=303)


@app.post("/shop/orders/{order_id}/accept")
def accept_shop_order(request: Request, order_id: int):
    """接受订单，加入下载队列。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_shop_admin_user(current_user):
        raise HTTPException(status_code=403, detail="无权限。")

    with closing(get_connection()) as connection, connection:
        connection.execute(
            "UPDATE shop_orders SET status = 'accepted' WHERE id = ? AND status = 'pending'",
            (order_id,),
        )
    return RedirectResponse("/shop", status_code=303)


@app.post("/shop/orders/{order_id}/revoke")
def revoke_shop_order(request: Request, order_id: int):
    """撤销订单，返还积分。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_shop_admin_user(current_user):
        raise HTTPException(status_code=403, detail="无权限。")

    with closing(get_connection()) as connection, connection:
        order = connection.execute(
            "SELECT shop_orders.*, shop_items.points FROM shop_orders JOIN shop_items ON shop_items.id = shop_orders.item_id WHERE shop_orders.id = ?",
            (order_id,),
        ).fetchone()
        if order is None:
            return RedirectResponse("/shop?error=订单不存在", status_code=303)
        if order["status"] == "revoked":
            return RedirectResponse("/shop?error=订单已被撤销", status_code=303)

        # 返还积分
        connection.execute(
            "UPDATE users SET exchange_points = exchange_points + ? WHERE id = ?",
            (int(order["points"]), int(order["user_id"])),
        )
        connection.execute(
            "UPDATE shop_orders SET status = 'revoked' WHERE id = ?",
            (order_id,),
        )
    return RedirectResponse("/shop", status_code=303)


@app.post("/shop/orders/download")
async def download_shop_orders(request: Request):
    """下载选中的已接受订单为 xlsx。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_shop_admin_user(current_user):
        raise HTTPException(status_code=403, detail="无权限。")

    form_data = await request.form()
    order_ids = [int(k.split("_")[-1]) for k in form_data.keys() if k.startswith("order_")]

    if not order_ids:
        return RedirectResponse("/shop?error=未选中任何订单", status_code=303)

    with closing(get_connection()) as connection, connection:
        placeholders = ",".join("?" for _ in order_ids)
        rows = connection.execute(
            f"""SELECT shop_orders.*, shop_items.name AS item_name, shop_items.points AS item_points,
                       users.username, users.game_nickname
                FROM shop_orders
                JOIN shop_items ON shop_items.id = shop_orders.item_id
                JOIN users ON users.id = shop_orders.user_id
                WHERE shop_orders.id IN ({placeholders}) AND shop_orders.status = 'accepted'
                ORDER BY shop_orders.id""",
            order_ids,
        ).fetchall()

        if not rows:
            return RedirectResponse("/shop?error=没有可下载的已接受订单", status_code=303)

        # 标记为已下载
        connection.execute(
            f"UPDATE shop_orders SET downloaded = 1 WHERE id IN ({placeholders})",
            order_ids,
        )

    # 生成 xlsx
    import io
    import xlsxwriter
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output)
    worksheet = workbook.add_worksheet("订单")
    headers = ["订单ID", "物品", "所需积分", "用户", "收货人", "手机号", "地址", "备注", "时间"]
    for col, h in enumerate(headers):
        worksheet.write(0, col, h)
    for row_idx, r in enumerate(rows, 1):
        worksheet.write(row_idx, 0, r["id"])
        worksheet.write(row_idx, 1, r["item_name"])
        worksheet.write(row_idx, 2, r["item_points"])
        worksheet.write(row_idx, 3, r["game_nickname"] or r["username"])
        worksheet.write(row_idx, 4, r["recipient_name"] or "")
        worksheet.write(row_idx, 5, r["phone"] or "")
        worksheet.write(row_idx, 6, r["address"] or "")
        worksheet.write(row_idx, 7, r["notes"] or "")
        worksheet.write(row_idx, 8, r["created_at"] or "")
    workbook.close()
    output.seek(0)

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=shop_orders.xlsx"},
    )


@app.post("/shop/orders/uncomplete")
async def uncomplete_shop_orders(request: Request):
    """将已下载订单恢复为未下载状态。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)
    if not is_shop_admin_user(current_user):
        raise HTTPException(status_code=403, detail="无权限。")

    form_data = await request.form()
    order_ids = [int(k.split("_")[-1]) for k in form_data.keys() if k.startswith("order_")]
    if order_ids:
        with closing(get_connection()) as connection, connection:
            placeholders = ",".join("?" for _ in order_ids)
            connection.execute(
                f"UPDATE shop_orders SET downloaded = 0 WHERE id IN ({placeholders})",
                order_ids,
            )
    return RedirectResponse("/shop", status_code=303)


@app.post("/tournaments/{tournament_id}/delete")
def delete_tournament_as_creator(
    request: Request,
    tournament_id: int,
    source_view: str = Form("all"),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    redirect_view = source_view if source_view in {"all", "mine", "history"} else "all"
    redirect_target = "/dashboard" if redirect_view == "all" else f"/dashboard?view={quote(redirect_view)}"

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        if tournament["creator_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="只有赛事创建者可以删除自己创建的赛事。")
        delete_tournament_record(connection, tournament)

    return RedirectResponse(redirect_target, status_code=303)


@app.get("/tournaments/new", response_class=HTMLResponse)
def tournament_create_page(request: Request):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection:
        context = tournament_form_context(connection, current_user)
    return render(request, "tournament_form.html", context)


@app.post("/tournaments", response_class=HTMLResponse)
def create_tournament(
    request: Request,
    name: str = Form(...),
    start_time: str = Form(...),
    checkin_time: str = Form(...),
    format_type: str = Form(...),
    stage_mode: str = Form(...),
    stage2_format_type: str = Form(""),
    round_robin_cycles: int = Form(DEFAULT_ROUND_ROBIN_CYCLES),
    round_robin_win_points: int = Form(DEFAULT_ROUND_ROBIN_WIN_POINTS),
    round_robin_draw_points: int = Form(DEFAULT_ROUND_ROBIN_DRAW_POINTS),
    round_robin_loss_points: int = Form(DEFAULT_ROUND_ROBIN_LOSS_POINTS),
    round_robin_best_of: int = Form(DEFAULT_ROUND_ROBIN_BEST_OF),
    swiss_regular_best_of: int = Form(DEFAULT_SWISS_REGULAR_BEST_OF),
    swiss_decider_best_of: int = Form(DEFAULT_SWISS_DECIDER_BEST_OF),
    bp_step_seconds: int = Form(DEFAULT_TOURNAMENT_BP_STEP_SECONDS),
    bp_mode: str = Form(BP_MODE_NONE),
    replay_result_enabled: str | None = Form(None),
    commander_shared_pool_mode: str = Form(COMMANDER_POOL_SHARED),
    commander_ban_mode: str = Form(COMMANDER_BAN_GLOBAL),
    description: str = Form(...),
    prize_info: str = Form(""),
    map_pool: str = Form(""),
    shared_admins: str = Form(""),
    hide_bracket_until_publish: str | None = Form(None),
    is_points_event: str | None = Form(None),
    points_for_champion: int = Form(0),
    is_private: str | None = Form(None),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    cleaned_name = name.strip()
    cleaned_description = description.strip()
    cleaned_prize = prize_info.strip() or "待公布"
    cleaned_map_pool = normalize_map_pool(map_pool)
    normalized_commander_pool_mode = normalize_commander_pool_mode(commander_shared_pool_mode)
    normalized_commander_ban_mode = normalize_commander_ban_mode(commander_ban_mode)
    form_values = {
        "name": cleaned_name,
        "start_time": start_time,
        "checkin_time": checkin_time,
        "format_type": format_type,
        "stage_mode": stage_mode,
        "stage2_format_type": stage2_format_type,
        "round_robin_cycles": round_robin_cycles,
        "round_robin_win_points": round_robin_win_points,
        "round_robin_draw_points": round_robin_draw_points,
        "round_robin_loss_points": round_robin_loss_points,
        "round_robin_best_of": round_robin_best_of,
        "swiss_regular_best_of": swiss_regular_best_of,
        "swiss_decider_best_of": swiss_decider_best_of,
        "bp_step_seconds": bp_step_seconds,
        "bp_mode": bp_mode,
        "replay_result_enabled": replay_result_enabled == "on",
        "commander_shared_pool_mode": normalized_commander_pool_mode,
        "commander_ban_mode": normalized_commander_ban_mode,
        "description": cleaned_description,
        "prize_info": cleaned_prize,
        "map_pool": cleaned_map_pool,
        "shared_admins": shared_admins,
        "hide_bracket_until_publish": hide_bracket_until_publish == "on",
        "is_points_event": is_points_event == "on",
        "points_for_champion": points_for_champion,
        "is_private": is_private == "on",
    }

    if len(cleaned_name) < 3:
        with closing(get_connection()) as connection:
            context = tournament_form_context(connection, current_user, form_values, "赛事名称至少需要 3 个字符。")
        return render(request, "tournament_form.html", context, status_code=400)
    if not cleaned_description:
        with closing(get_connection()) as connection:
            context = tournament_form_context(connection, current_user, form_values, "请填写赛事描述信息。")
        return render(request, "tournament_form.html", context, status_code=400)
    normalized_bp_mode = normalize_bp_mode(bp_mode)
    if format_type not in FORMAT_LABELS or stage_mode not in STAGE_LABELS or normalized_bp_mode not in BP_MODE_LABELS:
        raise HTTPException(status_code=400, detail="赛事配置无效。")
    if round_robin_cycles < 1 or round_robin_cycles > 10:
        with closing(get_connection()) as connection:
            context = tournament_form_context(connection, current_user, form_values, "循环轮数必须在 1 到 10 之间。")
        return render(request, "tournament_form.html", context, status_code=400)
    if any(value < -100 or value > 100 for value in (round_robin_win_points, round_robin_draw_points, round_robin_loss_points)):
        with closing(get_connection()) as connection:
            context = tournament_form_context(connection, current_user, form_values, "循环赛积分设置必须在 -100 到 100 之间。")
        return render(request, "tournament_form.html", context, status_code=400)
    try:
        validate_best_of(round_robin_best_of, allow_even=True)
        validate_best_of(swiss_regular_best_of)
        validate_best_of(swiss_decider_best_of)
    except HTTPException as exc:
        with closing(get_connection()) as connection:
            context = tournament_form_context(connection, current_user, form_values, str(exc.detail))
        return render(request, "tournament_form.html", context, status_code=400)
    if bp_step_seconds < TOURNAMENT_BP_STEP_SECONDS_MIN or bp_step_seconds > TOURNAMENT_BP_STEP_SECONDS_MAX:
        with closing(get_connection()) as connection:
            context = tournament_form_context(
                connection,
                current_user,
                form_values,
                f"每轮 BP 时间必须在 {TOURNAMENT_BP_STEP_SECONDS_MIN} 到 {TOURNAMENT_BP_STEP_SECONDS_MAX} 秒之间。",
            )
        return render(request, "tournament_form.html", context, status_code=400)
    if stage_mode == "double_stage" and stage2_format_type not in FORMAT_LABELS:
        with closing(get_connection()) as connection:
            context = tournament_form_context(connection, current_user, form_values, "双阶段赛必须选择第二阶段赛制。")
        return render(request, "tournament_form.html", context, status_code=400)
    if normalized_bp_mode != BP_MODE_NONE and not parse_map_pool(cleaned_map_pool):
        with closing(get_connection()) as connection:
            context = tournament_form_context(connection, current_user, form_values, "启用 BP 模式时必须填写地图池。")
        return render(request, "tournament_form.html", context, status_code=400)
    if normalized_bp_mode in {BP_MODE_MAP_POOL, BP_MODE_EVO} and len(parse_map_pool(cleaned_map_pool)) < map_bp_required_map_count(3):
        with closing(get_connection()) as connection:
            context = tournament_form_context(connection, current_user, form_values, "地图池 BP 至少需要 9 张地图。")
        return render(request, "tournament_form.html", context, status_code=400)

    if is_points_event == "on" and not is_superadmin_user(current_user):
        with closing(get_connection()) as connection:
            context = tournament_form_context(connection, current_user, form_values, "只有超级管理员可以创建积分赛事。")
        return render(request, "tournament_form.html", context, status_code=400)

    if is_points_event == "on" and points_for_champion < 1:
        with closing(get_connection()) as connection:
            context = tournament_form_context(connection, current_user, form_values, "积分赛事必须填写冠军积分（至少为 1）。")
        return render(request, "tournament_form.html", context, status_code=400)

    normalized_stage2_format = stage2_format_type if stage_mode == "double_stage" else None

    try:
        start_time_text = normalize_datetime_input(start_time)
        checkin_time_text = normalize_datetime_input(checkin_time)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="赛事时间格式不正确。") from exc

    if parse_tournament_datetime(checkin_time_text) > parse_tournament_datetime(start_time_text):
        with closing(get_connection()) as connection:
            context = tournament_form_context(connection, current_user, form_values, "签到时间不能晚于比赛开始时间。")
        return render(request, "tournament_form.html", context, status_code=400)

    with closing(get_connection()) as connection, connection:
        resolved_admin_ids, missing_admins = resolve_shared_admin_user_ids(connection, int(current_user["id"]), shared_admins)

        if missing_admins:
            context = tournament_form_context(
                connection,
                current_user,
                form_values,
                f"以下共享管理员账号不存在：{', '.join(missing_admins)}",
            )
            return render(request, "tournament_form.html", context, status_code=400)

        tournament_slug = next_available_slug(connection, cleaned_name)
        invite_code = generate_invite_code() if is_private == "on" else None
        cursor = connection.execute(
            """
            INSERT INTO tournaments (
                slug, name, creator_id, start_time, checkin_time, format_type,
                stage2_format_type, stage_mode, stage1_group_count,
                round_robin_cycles, round_robin_win_points, round_robin_draw_points, round_robin_loss_points,
                round_robin_best_of, swiss_regular_best_of, swiss_decider_best_of,
                bp_step_seconds, bp_mode, replay_result_enabled, commander_shared_pool, commander_ban_mode,
                description, prize_info, map_pool,
                hide_bracket_until_publish, started_at, created_at,
                is_points_event, points_for_champion, is_private, invite_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                tournament_slug,
                cleaned_name,
                current_user["id"],
                start_time_text,
                checkin_time_text,
                format_type,
                normalized_stage2_format,
                stage_mode,
                round_robin_cycles,
                round_robin_win_points,
                round_robin_draw_points,
                round_robin_loss_points,
                round_robin_best_of,
                swiss_regular_best_of,
                swiss_decider_best_of,
                bp_step_seconds,
                normalized_bp_mode,
                1 if replay_result_enabled == "on" else 0,
                1 if normalized_commander_pool_mode == COMMANDER_POOL_SHARED else 0,
                normalized_commander_ban_mode,
                cleaned_description,
                cleaned_prize,
                cleaned_map_pool,
                1 if hide_bracket_until_publish == "on" else 0,
                now_text(),
                1 if is_points_event == "on" else 0,
                points_for_champion if is_points_event == "on" else 0,
                1 if is_private == "on" else 0,
                invite_code,
            ),
        )
        tournament_id = cursor.lastrowid
        for admin_id in resolved_admin_ids:
            connection.execute(
                "INSERT OR IGNORE INTO tournament_admins (tournament_id, user_id) VALUES (?, ?)",
                (tournament_id, admin_id),
            )

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=info", status_code=303)


@app.post("/tournaments/{tournament_id}/settings")
def update_tournament_settings(
    request: Request,
    tournament_id: int,
    name: str = Form(...),
    start_time: str = Form(...),
    checkin_time: str = Form(...),
    bp_mode: str = Form(BP_MODE_NONE),
    bp_step_seconds: int = Form(DEFAULT_TOURNAMENT_BP_STEP_SECONDS),
    replay_result_enabled: str | None = Form(None),
    commander_shared_pool_mode: str = Form(COMMANDER_POOL_SHARED),
    commander_ban_mode: str = Form(COMMANDER_BAN_GLOBAL),
    description: str = Form(...),
    prize_info: str = Form(""),
    map_pool: str = Form(""),
    hide_bracket_until_publish: str | None = Form(None),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    redirect_path = f"/tournaments/{tournament_id}?tab=info"
    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        if int(tournament["creator_id"]) != int(current_user["id"]):
            raise HTTPException(status_code=403, detail="只有赛事创建者可以修改赛事信息。")
        if tournament_started(tournament):
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "tournament_settings_error", "比赛开始后不再允许修改该赛事信息组件。"),
                status_code=303,
            )

        cleaned_name = name.strip()
        cleaned_description = description.strip()
        cleaned_prize = prize_info.strip() or "待公布"
        cleaned_map_pool = normalize_map_pool(map_pool)
        normalized_bp_mode = normalize_bp_mode(bp_mode)
        normalized_commander_pool_mode = normalize_commander_pool_mode(commander_shared_pool_mode)
        normalized_commander_ban_mode = normalize_commander_ban_mode(commander_ban_mode)

        if len(cleaned_name) < 3:
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "tournament_settings_error", "赛事名称至少需要 3 个字符。"),
                status_code=303,
            )
        if not cleaned_description:
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "tournament_settings_error", "请填写赛事描述信息。"),
                status_code=303,
            )
        if normalized_bp_mode not in BP_MODE_LABELS:
            raise HTTPException(status_code=400, detail="赛事配置无效。")
        if bp_step_seconds < TOURNAMENT_BP_STEP_SECONDS_MIN or bp_step_seconds > TOURNAMENT_BP_STEP_SECONDS_MAX:
            return RedirectResponse(
                append_redirect_query_value(
                    redirect_path,
                    "tournament_settings_error",
                    f"每轮 BP 时间必须在 {TOURNAMENT_BP_STEP_SECONDS_MIN} 到 {TOURNAMENT_BP_STEP_SECONDS_MAX} 秒之间。",
                ),
                status_code=303,
            )
        if normalized_bp_mode != BP_MODE_NONE and not parse_map_pool(cleaned_map_pool):
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "tournament_settings_error", "启用 BP 模式时必须填写地图池。"),
                status_code=303,
            )
        if normalized_bp_mode in {BP_MODE_MAP_POOL, BP_MODE_EVO} and len(parse_map_pool(cleaned_map_pool)) < map_bp_required_map_count(3):
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "tournament_settings_error", "地图池 BP 至少需要 9 张地图。"),
                status_code=303,
            )

        try:
            start_time_text = normalize_datetime_input(start_time)
            checkin_time_text = normalize_datetime_input(checkin_time)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="赛事时间格式不正确。") from exc

        if parse_tournament_datetime(checkin_time_text) > parse_tournament_datetime(start_time_text):
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "tournament_settings_error", "签到时间不能晚于比赛开始时间。"),
                status_code=303,
            )

        connection.execute(
            """
            UPDATE tournaments
            SET name = ?,
                start_time = ?,
                checkin_time = ?,
                bp_mode = ?,
                bp_step_seconds = ?,
                replay_result_enabled = ?,
                commander_shared_pool = ?,
                commander_ban_mode = ?,
                description = ?,
                prize_info = ?,
                map_pool = ?,
                hide_bracket_until_publish = ?
            WHERE id = ?
            """,
            (
                cleaned_name,
                start_time_text,
                checkin_time_text,
                normalized_bp_mode,
                bp_step_seconds,
                1 if replay_result_enabled == "on" else 0,
                1 if normalized_commander_pool_mode == COMMANDER_POOL_SHARED else 0,
                normalized_commander_ban_mode,
                cleaned_description,
                cleaned_prize,
                cleaned_map_pool,
                1 if hide_bracket_until_publish == "on" else 0,
                tournament_id,
            ),
        )
        refreshed_tournament = load_tournament_row(connection, tournament_id) or tournament
        reset_unfinished_bracket(connection, tournament_id)
        ensure_tournament_matches(connection, refreshed_tournament)

    return RedirectResponse(
        append_redirect_query_value(redirect_path, "tournament_settings_success", "已更新赛事信息。"),
        status_code=303,
    )


@app.get("/tournaments/{tournament_id}", response_class=HTMLResponse)
def tournament_detail(request: Request, tournament_id: int, tab: str = "info"):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        prune_no_shows(connection, tournament_id)
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        try:
            sync_tournament_preparation_states(connection, tournament)
        except Exception:
            pass

        registration = load_registration_for_user(connection, tournament_id, current_user["id"])
        participants = load_registrations(connection, tournament_id)
        admins = connection.execute(
            """
            SELECT users.username
            FROM tournament_admins
            JOIN users ON users.id = tournament_admins.user_id
            WHERE tournament_admins.tournament_id = ?
            ORDER BY users.username COLLATE NOCASE
            """,
            (tournament_id,),
        ).fetchall()
        user_suggestions = connection.execute(
            "SELECT username FROM users WHERE id != ? ORDER BY username COLLATE NOCASE",
            (current_user["id"],),
        ).fetchall()

        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        stage_group_settings = load_stage_group_settings(connection, tournament_id, int(tournament["stage1_group_count"] or 1))
        grouped_participant_sections = build_grouped_participant_sections(participants, tournament, stage_group_settings)
        registration_map = {row["id"]: row for row in participants}
        registered = registration is not None
        bracket_visible = can_view_bracket(tournament, registered, manager)
        show_bracket_nav = manager or bracket_visible
        show_participants_nav = manager
        show_my_matches_nav = registered
        active_tab = tab if tab in {"info", "signup", "participants", "my_matches", "bracket", "rankings"} else "info"
        if active_tab == "bracket" and not show_bracket_nav:
            active_tab = "info"
        if active_tab == "participants" and not show_participants_nav:
            active_tab = "info"
        if active_tab == "my_matches" and not show_my_matches_nav:
            active_tab = "info"

        phase = tournament_phase(tournament)
        participant_actions_locked = tournament_started(tournament)
        try:
            active_rows = active_registration_rows(connection, tournament)
        except Exception:
            active_rows = []
        stage_status = build_stage_status_view(connection, tournament, active_rows)
        bracket_state = build_bracket_state(connection, tournament, manager) if active_tab == "bracket" else {
            "summary": "",
            "groups": [],
            "champion": None,
        }
        try:
            tournament_rankings = build_tournament_rankings(connection, tournament) if active_tab == "rankings" else []
        except Exception:
            tournament_rankings = []
        my_matches_state = build_my_matches_state(connection, tournament, registration) if active_tab == "my_matches" else {
            "summary": "",
            "current_match": None,
            "matches": [],
            "recent_matches": [],
            "preparation": None,
        }
        if active_tab == "my_matches":
            my_matches_state["replay_upload_error"] = request.query_params.get("replay_upload_error")
            my_matches_state["replay_upload_success"] = request.query_params.get("replay_upload_success")
        try:
            manager_preparation_overview = (
                build_manager_preparation_overview(connection, tournament, registration_map)
                if manager
                else []
            )
        except Exception:
            manager_preparation_overview = []
        tournament_replay_archive = tournament_replay_archive_download_view(connection, tournament, manager)
        try:
            bp_sync = build_bp_sync_view(
                connection,
                tournament,
                active_tab,
                my_matches_state,
                bracket_state,
                manager_preparation_overview,
        )
        except Exception:
            bp_sync = {"sync_url": "", "sync_key": "", "poll_ms": BP_SYNC_POLL_MS}

    tournament_view = {
        "id": tournament["id"],
        "name": tournament["name"],
        "creator_name": tournament["creator_name"],
        "start_time": tournament["start_time"],
        "checkin_time": tournament["checkin_time"],
        "format_type": tournament["format_type"],
        "format_label": FORMAT_LABELS.get(tournament["format_type"], tournament["format_type"]),
        "stage2_format_type": tournament["stage2_format_type"],
        "stage2_format_label": FORMAT_LABELS.get(tournament["stage2_format_type"], tournament["stage2_format_type"])
        if tournament["stage2_format_type"]
        else "未设置",
        "stage_label": STAGE_LABELS.get(tournament["stage_mode"], tournament["stage_mode"]),
        "stage_mode": tournament["stage_mode"],
        "stage1_group_count": int(tournament["stage1_group_count"] or 1),
        "round_robin_cycles": round_robin_cycles_for_tournament(tournament),
        "round_robin_points": round_robin_points_for_tournament(tournament),
        "round_robin_best_of": round_robin_best_of_for_tournament(tournament),
        "swiss_best_of": swiss_best_of_settings_for_tournament(tournament),
        "bp_step_seconds": tournament_bp_step_seconds(tournament),
        "replay_result_enabled": replay_result_enabled_for_tournament(tournament),
        "commander_shared_pool_mode": commander_draft_settings_for_tournament(tournament)["pool_mode"],
        "commander_ban_mode": commander_draft_settings_for_tournament(tournament)["ban_mode"],
        "commander_pool_mode_label": COMMANDER_POOL_MODE_LABELS.get(
            commander_draft_settings_for_tournament(tournament)["pool_mode"],
            commander_draft_settings_for_tournament(tournament)["pool_mode"],
        ),
        "commander_ban_mode_label": COMMANDER_BAN_MODE_LABELS.get(
            commander_draft_settings_for_tournament(tournament)["ban_mode"],
            commander_draft_settings_for_tournament(tournament)["ban_mode"],
        ),
        "bp_mode": normalize_bp_mode(tournament["bp_mode"]),
        "bp_mode_label": BP_MODE_LABELS.get(normalize_bp_mode(tournament["bp_mode"]), normalize_bp_mode(tournament["bp_mode"])),
        "current_stage_label": stage_status["current_stage_label"],
        "description": tournament["description"],
        "prize_info": tournament["prize_info"],
        "map_pool": tournament["map_pool"],
        "map_pool_tags": parse_map_pool(tournament["map_pool"]),
        "hide_bracket_until_publish": bool(tournament["hide_bracket_until_publish"]),
        "bracket_published": bool(tournament["bracket_published"]),
        "creator_id": tournament["creator_id"],
        "phase_label": tournament_phase_label(phase),
        "checkin_open": checkin_is_open(tournament),
        "registration_open": can_register_for_tournament(tournament),
        "started": tournament_started(tournament),
        "awaiting_start": phase == "awaiting_start",
        "started_at": tournament["started_at"],
        "is_private": bool(tournament["is_private"]),
        "invite_code": tournament["invite_code"] or "",
        "is_points_event": bool(tournament["is_points_event"]),
        "points_for_champion": int(tournament["points_for_champion"] or 0),
    }

    return render(
        request,
        "tournament_detail.html",
        {
            "page_title": tournament["name"],
            "current_user": current_user,
            "tournament": tournament_view,
            "admins": [row["username"] for row in admins],
            "user_suggestions": [row["username"] for row in user_suggestions],
            "participants": participants,
            "grouped_participant_sections": grouped_participant_sections,
            "stage_group_settings": stage_group_settings,
            "participant_count": len(participants),
            "registration": registration,
            "manager": manager,
            "creator_can_manage_admins": int(tournament["creator_id"]) == int(current_user["id"]),
            "shared_admin_error": request.query_params.get("shared_admin_error"),
            "shared_admin_success": request.query_params.get("shared_admin_success"),
            "tournament_settings_error": request.query_params.get("tournament_settings_error"),
            "tournament_settings_success": request.query_params.get("tournament_settings_success"),
            "registered": registered,
            "show_bracket_nav": show_bracket_nav,
            "show_participants_nav": show_participants_nav,
            "show_my_matches_nav": show_my_matches_nav,
            "bracket_visible": bracket_visible,
            "active_tab": active_tab,
            "bracket_state": bracket_state,
            "tournament_rankings": tournament_rankings,
            "my_matches_state": my_matches_state,
            "my_matches_bp_sync": bp_sync if active_tab == "my_matches" else None,
            "manager_preparation_overview": manager_preparation_overview,
            "tournament_replay_archive": tournament_replay_archive,
            "bp_sync": bp_sync,
            "can_self_checkin": bool(registration and not registration["checked_in"] and checkin_is_open(tournament)),
            "can_start_tournament": manager and not tournament_started(tournament),
            "can_revert_tournament": manager and tournament_started(tournament),
            "can_withdraw": can_register_for_tournament(tournament),
            "participant_actions_locked": participant_actions_locked,
            "manager_can_checkin": manager and checkin_is_open(tournament),
            "stage_status": stage_status,
        },
    )


@app.get("/tournaments/{tournament_id}/bp/sync")
def bp_sync_status(
    request: Request,
    tournament_id: int,
    match_id: int | None = None,
):
    current_user = require_user(request)
    if current_user is None:
        raise HTTPException(status_code=401, detail="请先登录。")

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        sync_tournament_preparation_states(connection, tournament)

        registration = load_registration_for_user(connection, tournament_id, current_user["id"])
        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        registered = registration is not None

        if match_id is not None:
            match_row = connection.execute(
                "SELECT * FROM tournament_matches WHERE id = ? AND tournament_id = ?",
                (match_id, tournament_id),
            ).fetchone()
            if match_row is None:
                raise HTTPException(status_code=404, detail="对局不存在。")

            player_ids = set(match_preparation_player_ids(match_row))
            if not manager and (registration is None or registration["id"] not in player_ids):
                raise HTTPException(status_code=403, detail="只有当前对局选手或管理员可以同步该 BP 页面。")

            return {
                "sync_key": match_preparation_sync_key(connection, match_row),
                "interval_ms": BP_SYNC_POLL_MS,
            }

        if not (manager or can_view_bracket(tournament, registered, manager)):
            raise HTTPException(status_code=403, detail="当前无权查看该赛事的 BP 同步状态。")

        return {
            "sync_key": tournament_preparation_sync_key(connection, tournament_id),
            "interval_ms": BP_SYNC_POLL_MS,
        }


@app.get("/tournaments/{tournament_id}/my-matches/partial", response_class=HTMLResponse)
def tournament_my_matches_partial(request: Request, tournament_id: int):
    current_user = require_user(request)
    if current_user is None:
        raise HTTPException(status_code=401, detail="请先登录。")

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        sync_tournament_preparation_states(connection, tournament)
        context = build_my_matches_panel_context(connection, request, tournament, current_user)

    return render(request, "_tournament_my_matches_panel.html", context)


@app.get("/tournaments/{tournament_id}/info/partial", response_class=HTMLResponse)
def tournament_info_partial(request: Request, tournament_id: int):
    current_user = require_user(request)
    if current_user is None:
        raise HTTPException(status_code=401, detail="请先登录。")

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        sync_tournament_preparation_states(connection, tournament)
        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        registrations = load_registrations(connection, tournament_id)
        registration_map = {row["id"]: row for row in registrations}
        registration = load_registration_for_user(connection, tournament_id, current_user["id"])
        active_rows = active_registration_rows(connection, tournament)
        stage_status = build_stage_status_view(connection, tournament, active_rows)
        manager_preparation_overview = (
            build_manager_preparation_overview(connection, tournament, registration_map)
            if manager
            else []
        )

    return render(
        request,
        "_info_partial.html",
        {
            "tournament": {
                "id": tournament["id"],
                "bp_mode_label": BP_MODE_LABELS.get(normalize_bp_mode(tournament["bp_mode"]), tournament["bp_mode"]),
                "started": tournament_started(tournament),
            },
            "stage_status": stage_status,
            "show_my_matches_nav": registration is not None,
            "manager_preparation_overview": manager_preparation_overview,
            "manager": manager,
        },
    )


@app.get("/tournaments/{tournament_id}/bracket/partial", response_class=HTMLResponse)
def tournament_bracket_partial(request: Request, tournament_id: int):
    current_user = require_user(request)
    if current_user is None:
        raise HTTPException(status_code=401, detail="请先登录。")

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        sync_tournament_preparation_states(connection, tournament)
        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        bracket_state = build_bracket_state(connection, tournament, manager)

    return render(
        request,
        "_bracket_partial.html",
        {
            "tournament": {"id": tournament["id"]},
            "bracket_state": bracket_state,
            "manager": manager,
        },
    )


@app.post("/tournaments/{tournament_id}/admins")
def add_tournament_shared_admins(
    request: Request,
    tournament_id: int,
    shared_admins: str = Form(""),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    redirect_path = f"/tournaments/{tournament_id}?tab=info"
    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        if int(tournament["creator_id"]) != int(current_user["id"]):
            raise HTTPException(status_code=403, detail="只有赛事创建者可以添加共享管理员。")

        resolved_admin_ids, missing_admins = resolve_shared_admin_user_ids(connection, int(current_user["id"]), shared_admins)
        if missing_admins:
            return RedirectResponse(
                append_redirect_query_value(
                    redirect_path,
                    "shared_admin_error",
                    f"以下共享管理员账号不存在：{', '.join(missing_admins)}",
                ),
                status_code=303,
            )
        if not resolved_admin_ids:
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "shared_admin_error", "请输入至少一个有效共享管理员账号。"),
                status_code=303,
            )

        existing_admin_ids = {
            int(row["user_id"])
            for row in connection.execute(
                "SELECT user_id FROM tournament_admins WHERE tournament_id = ?",
                (tournament_id,),
            ).fetchall()
        }
        new_admin_ids = [user_id for user_id in resolved_admin_ids if user_id not in existing_admin_ids]
        if not new_admin_ids:
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "shared_admin_error", "所填账号都已经是共享管理员。"),
                status_code=303,
            )

        for admin_id in new_admin_ids:
            connection.execute(
                "INSERT OR IGNORE INTO tournament_admins (tournament_id, user_id) VALUES (?, ?)",
                (tournament_id, admin_id),
            )

    return RedirectResponse(
        append_redirect_query_value(
            redirect_path,
            "shared_admin_success",
            f"已新增 {len(new_admin_ids)} 名共享管理员。",
        ),
        status_code=303,
    )


@app.post("/tournaments/{tournament_id}/register")
def join_tournament(
    request: Request,
    tournament_id: int,
    nickname: str = Form(...),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    cleaned_nickname = nickname.strip()
    if len(cleaned_nickname) < 2 or len(cleaned_nickname) > 24:
        return RedirectResponse(f"/tournaments/{tournament_id}?tab=signup", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        if not can_register_for_tournament(tournament):
            return RedirectResponse(f"/tournaments/{tournament_id}?tab=signup", status_code=303)

        existing = load_registration_for_user(connection, tournament_id, current_user["id"])
        if existing:
            connection.execute(
                "UPDATE registrations SET nickname = ? WHERE id = ?",
                (cleaned_nickname, existing["id"]),
            )
        else:
            connection.execute(
                """
                INSERT INTO registrations (
                    tournament_id, user_id, nickname, is_temporary,
                    checked_in, checked_in_at, seed_order, joined_at
                )
                VALUES (?, ?, ?, 0, 0, NULL, ?, ?)
                """,
                (
                    tournament_id,
                    current_user["id"],
                    cleaned_nickname,
                    next_registration_seed_order(connection, tournament_id),
                    now_text(),
                ),
            )
        reset_unfinished_bracket(connection, tournament_id)
        ensure_tournament_matches(connection, tournament)

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=signup", status_code=303)


@app.post("/tournaments/{tournament_id}/withdraw")
def withdraw_tournament(request: Request, tournament_id: int):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        if not can_register_for_tournament(tournament):
            return RedirectResponse(f"/tournaments/{tournament_id}?tab=signup", status_code=303)

        connection.execute(
            "DELETE FROM registrations WHERE tournament_id = ? AND user_id = ?",
            (tournament_id, current_user["id"]),
        )
        reset_unfinished_bracket(connection, tournament_id)
        ensure_tournament_matches(connection, tournament)

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=signup", status_code=303)


@app.post("/tournaments/{tournament_id}/checkin")
def self_checkin(request: Request, tournament_id: int):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        if not checkin_is_open(tournament):
            return RedirectResponse(f"/tournaments/{tournament_id}?tab=signup", status_code=303)

        registration = load_registration_for_user(connection, tournament_id, current_user["id"])
        if registration is not None:
            connection.execute(
                "UPDATE registrations SET checked_in = 1, checked_in_at = ? WHERE id = ?",
                (current_utc8_text(), registration["id"]),
            )

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=signup", status_code=303)


@app.post("/tournaments/{tournament_id}/participants/{registration_id}/nickname")
def manager_update_participant_nickname(
    request: Request,
    tournament_id: int,
    registration_id: int,
    nickname: str = Form(...),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    cleaned_nickname = nickname.strip()
    if len(cleaned_nickname) < 2 or len(cleaned_nickname) > 24:
        return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        if not manager:
            raise HTTPException(status_code=403, detail="只有赛事管理员可以修改选手昵称。")
        if tournament_started(tournament):
            return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)

        connection.execute(
            "UPDATE registrations SET nickname = ? WHERE id = ? AND tournament_id = ?",
            (cleaned_nickname, registration_id, tournament_id),
        )

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)


@app.post("/tournaments/{tournament_id}/participants/{registration_id}/checkin")
def manager_checkin_participant(request: Request, tournament_id: int, registration_id: int):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        if not manager:
            raise HTTPException(status_code=403, detail="只有赛事管理员可以帮助签到。")
        if not checkin_is_open(tournament):
            return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)

        connection.execute(
            "UPDATE registrations SET checked_in = 1, checked_in_at = ? WHERE id = ? AND tournament_id = ?",
            (current_utc8_text(), registration_id, tournament_id),
        )

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)


@app.post("/tournaments/{tournament_id}/participants/{registration_id}/remove")
def manager_remove_participant(request: Request, tournament_id: int, registration_id: int):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        if not manager:
            raise HTTPException(status_code=403, detail="只有赛事管理员可以移除选手。")
        if tournament_started(tournament):
            return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)

        connection.execute(
            "DELETE FROM registrations WHERE id = ? AND tournament_id = ?",
            (registration_id, tournament_id),
        )
        reset_unfinished_bracket(connection, tournament_id)
        ensure_tournament_matches(connection, tournament)

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)


@app.post("/tournaments/{tournament_id}/participants/temp")
def manager_add_temporary_participant(
    request: Request,
    tournament_id: int,
    nickname: str = Form(...),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    cleaned_nickname = nickname.strip()
    if len(cleaned_nickname) < 2 or len(cleaned_nickname) > 24:
        return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        if not manager:
            raise HTTPException(status_code=403, detail="只有赛事管理员可以添加临时选手。")
        if tournament_started(tournament):
            return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)

        connection.execute(
            """
            INSERT INTO registrations (
                tournament_id, user_id, nickname, is_temporary,
                checked_in, checked_in_at, seed_order, joined_at
            ) VALUES (?, NULL, ?, 1, 0, NULL, ?, ?)
            """,
            (
                tournament_id,
                cleaned_nickname,
                next_registration_seed_order(connection, tournament_id),
                now_text(),
            ),
        )
        ensure_tournament_matches(connection, tournament)

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)


@app.post("/tournaments/{tournament_id}/groups")
def manager_update_group_count(
    request: Request,
    tournament_id: int,
    group_count: int = Form(...),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    normalized_group_count = max(1, min(16, int(group_count)))
    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        if not manager:
            raise HTTPException(status_code=403, detail="只有赛事管理员可以调整小组数量。")
        if tournament_started(tournament):
            return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)

        connection.execute(
            "UPDATE tournaments SET stage1_group_count = ? WHERE id = ?",
            (normalized_group_count, tournament_id),
        )
        ensure_stage_group_settings(connection, tournament_id, normalized_group_count)
        connection.execute(
            "UPDATE registrations SET group_number = ? WHERE tournament_id = ? AND group_number > ?",
            (normalized_group_count, tournament_id, normalized_group_count),
        )
        ensure_tournament_matches(connection, load_tournament_row(connection, tournament_id) or tournament)

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)


@app.post("/tournaments/{tournament_id}/groups/{group_number}/advance")
def manager_update_group_advance(
    request: Request,
    tournament_id: int,
    group_number: int,
    advance_count: int = Form(...),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    normalized_advance = max(1, int(advance_count))
    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        if not manager:
            raise HTTPException(status_code=403, detail="只有赛事管理员可以调整小组晋级人数。")
        if tournament_started(tournament):
            return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)

        group_count = int(tournament["stage1_group_count"] or 1)
        normalized_group_number = normalize_group_number(group_number, group_count)
        ensure_stage_group_settings(connection, tournament_id, group_count)
        connection.execute(
            """
            UPDATE tournament_stage_groups
            SET advance_count = ?
            WHERE tournament_id = ? AND stage_index = 1 AND group_number = ?
            """,
            (normalized_advance, tournament_id, normalized_group_number),
        )

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)


@app.post("/tournaments/{tournament_id}/participants/{registration_id}/group")
def manager_update_participant_group(
    request: Request,
    tournament_id: int,
    registration_id: int,
    group_number: int = Form(...),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        if not manager:
            raise HTTPException(status_code=403, detail="只有赛事管理员可以调整选手分组。")
        if tournament_started(tournament):
            return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)

        normalized_group = normalize_group_number(group_number, int(tournament["stage1_group_count"] or 1))
        connection.execute(
            "UPDATE registrations SET group_number = ? WHERE id = ? AND tournament_id = ?",
            (normalized_group, registration_id, tournament_id),
        )
        ensure_tournament_matches(connection, tournament)

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)


@app.post("/tournaments/{tournament_id}/participants/{registration_id}/seed")
def manager_move_participant_seed(
    request: Request,
    tournament_id: int,
    registration_id: int,
    direction: str = Form(...),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        if not manager:
            raise HTTPException(status_code=403, detail="只有赛事管理员可以调整种子顺序。")
        if tournament_started(tournament):
            return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)

        swap_registration_seed_order(connection, tournament_id, registration_id, direction)
        ensure_tournament_matches(connection, tournament)

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=participants", status_code=303)


@app.post("/tournaments/{tournament_id}/start")
def start_tournament(request: Request, tournament_id: int):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        if not manager:
            raise HTTPException(status_code=403, detail="只有赛事管理员可以开始比赛。")
        if tournament_started(tournament):
            return RedirectResponse(f"/tournaments/{tournament_id}?tab=bracket", status_code=303)

        connection.execute(
            "UPDATE tournaments SET started_at = ? WHERE id = ?",
            (current_utc8_text(), tournament_id),
        )
        started_tournament = load_tournament_row(connection, tournament_id)
        if started_tournament is not None:
            ensure_tournament_matches(connection, started_tournament)

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=bracket", status_code=303)


@app.post("/tournaments/{tournament_id}/revert-start")
def revert_tournament_start(request: Request, tournament_id: int):
    """回退已开始的赛事到未开始状态，清除所有对局记录并允许重新生成对阵。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        if not manager:
            raise HTTPException(status_code=403, detail="只有赛事管理员可以回退比赛。")
        if not tournament_started(tournament):
            return RedirectResponse(f"/tournaments/{tournament_id}?tab=info", status_code=303)

        # 清除所有对局准备状态
        connection.execute(
            "DELETE FROM tournament_match_preparations WHERE tournament_id = ?",
            (tournament_id,),
        )
        # 清除所有对局记录
        connection.execute("DELETE FROM tournament_matches WHERE tournament_id = ?", (tournament_id,))
        # 回退 started_at
        connection.execute(
            "UPDATE tournaments SET started_at = NULL WHERE id = ?",
            (tournament_id,),
        )

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=info", status_code=303)


@app.post("/tournaments/{tournament_id}/publish-bracket")
def publish_bracket(request: Request, tournament_id: int):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        if not manager:
            raise HTTPException(status_code=403, detail="只有赛事管理员可以公布对阵表。")

        ensure_tournament_matches(connection, tournament)
        connection.execute(
            "UPDATE tournaments SET bracket_published = 1 WHERE id = ?",
            (tournament_id,),
        )

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=bracket", status_code=303)


@app.post("/tournaments/{tournament_id}/round-settings")
def update_round_settings(
    request: Request,
    tournament_id: int,
    bracket_type: str = Form(...),
    round_number: int = Form(...),
    best_of: int = Form(...),
    stage_index: int = Form(1),
    group_number: int = Form(1),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        if not manager:
            raise HTTPException(status_code=403, detail="只有赛事管理员可以调整每轮 BO。")
        if bracket_type in {"swiss", "round_robin"}:
            raise HTTPException(status_code=400, detail="瑞士轮和循环赛请使用对应的全局 BO 设置入口。")
        if bracket_type not in BRACKET_TYPE_LABELS:
            raise HTTPException(status_code=400, detail="不支持的对阵分组。")
        if round_number < 1:
            raise HTTPException(status_code=400, detail="轮次必须从 1 开始。")

        stored_bracket_type = bracket_storage_key(bracket_type, stage_index, group_number)

        match_rows = connection.execute(
            """
            SELECT *
            FROM tournament_matches
            WHERE tournament_id = ? AND bracket_type = ? AND round_number = ?
            """,
            (tournament_id, stored_bracket_type, round_number),
        ).fetchall()
        real_matches = [
            row
            for row in match_rows
            if row["player1_slot_type"] == MATCH_SLOT_PLAYER
            and row["player2_slot_type"] == MATCH_SLOT_PLAYER
            and row["player1_registration_id"] is not None
            and row["player2_registration_id"] is not None
        ]
        invalid_completed = [row for row in real_matches if not completed_match_score_fits_best_of(row, best_of)]
        if invalid_completed:
            raise HTTPException(status_code=400, detail="该轮已有已完成对局与新 BO 不兼容，请先调整比分或选择其他 BO。")

        upsert_round_best_of(connection, tournament_id, bracket_type, round_number, best_of, stage_index, group_number)
        for match_row in real_matches:
            connection.execute(
                "UPDATE tournament_matches SET best_of = ?, updated_at = ? WHERE id = ?",
                (best_of, now_text(), match_row["id"]),
            )

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=bracket", status_code=303)


@app.post("/tournaments/{tournament_id}/format-best-of-settings")
def update_format_best_of_settings(
    request: Request,
    tournament_id: int,
    setting_mode: str = Form(...),
    round_robin_best_of: int = Form(DEFAULT_ROUND_ROBIN_BEST_OF),
    swiss_regular_best_of: int = Form(DEFAULT_SWISS_REGULAR_BEST_OF),
    swiss_decider_best_of: int = Form(DEFAULT_SWISS_DECIDER_BEST_OF),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        if not manager:
            raise HTTPException(status_code=403, detail="只有赛事管理员可以调整全局 BO。")

        if setting_mode == "round_robin":
            validate_best_of(round_robin_best_of, allow_even=True)
            match_rows = connection.execute(
                "SELECT * FROM tournament_matches WHERE tournament_id = ? AND bracket_type LIKE ? ORDER BY id ASC",
                (tournament_id, "%:round_robin"),
            ).fetchall()
            real_matches = [
                row
                for row in match_rows
                if row["player1_slot_type"] == MATCH_SLOT_PLAYER
                and row["player2_slot_type"] == MATCH_SLOT_PLAYER
                and row["player1_registration_id"] is not None
                and row["player2_registration_id"] is not None
            ]
            invalid_completed = [row for row in real_matches if not completed_match_score_fits_best_of(row, round_robin_best_of)]
            if invalid_completed:
                raise HTTPException(status_code=400, detail="已有已完成循环赛对局与新的全局 BO 不兼容。")

            connection.execute(
                "UPDATE tournaments SET round_robin_best_of = ? WHERE id = ?",
                (round_robin_best_of, tournament_id),
            )
            connection.execute(
                "DELETE FROM tournament_round_settings WHERE tournament_id = ? AND bracket_type = 'round_robin'",
                (tournament_id,),
            )
            for match_row in real_matches:
                connection.execute(
                    "UPDATE tournament_matches SET best_of = ?, updated_at = ? WHERE id = ?",
                    (round_robin_best_of, now_text(), match_row["id"]),
                )
        elif setting_mode == "swiss":
            validate_best_of(swiss_regular_best_of)
            validate_best_of(swiss_decider_best_of)

            swiss_rows = connection.execute(
                "SELECT * FROM tournament_matches WHERE tournament_id = ? AND bracket_type LIKE ? ORDER BY bracket_type ASC, round_number ASC, match_number ASC, id ASC",
                (tournament_id, "%:swiss"),
            ).fetchall()
            active_rows = active_registration_rows(connection, tournament)
            grouped_stage_one_rows = registrations_by_group(active_rows, int(tournament["stage1_group_count"] or 1))
            stage_two_ready, stage_two_rows = (
                stage_two_seeded_rows(connection, tournament, active_rows)
                if tournament["stage_mode"] == "double_stage"
                else (False, [])
            )
            swiss_mode_by_id: dict[int, str] = {}
            swiss_rows_by_stage: dict[tuple[int, int], list[sqlite3.Row]] = defaultdict(list)
            for row in swiss_rows:
                stage_index, group_number, _ = parse_bracket_key(row["bracket_type"])
                swiss_rows_by_stage[(stage_index, group_number)].append(row)

            for (stage_index, group_number), stage_rows in swiss_rows_by_stage.items():
                registrations = grouped_stage_one_rows.get(group_number, []) if stage_index == 1 else stage_two_rows
                swiss_mode_by_id.update(swiss_match_modes_by_id(registrations, stage_rows))

            real_matches = [
                row
                for row in swiss_rows
                if row["player1_slot_type"] == MATCH_SLOT_PLAYER
                and row["player2_slot_type"] == MATCH_SLOT_PLAYER
                and row["player1_registration_id"] is not None
                and row["player2_registration_id"] is not None
            ]
            invalid_completed = []
            for row in real_matches:
                mode = swiss_mode_by_id.get(int(row["id"]), "swiss_regular")
                target_best_of = swiss_decider_best_of if mode == "swiss_decider" else swiss_regular_best_of
                if not completed_match_score_fits_best_of(row, target_best_of):
                    invalid_completed.append(row)
            if invalid_completed:
                raise HTTPException(status_code=400, detail="已有已完成瑞士轮对局与新的常规局/生死局 BO 不兼容。")

            connection.execute(
                "UPDATE tournaments SET swiss_regular_best_of = ?, swiss_decider_best_of = ? WHERE id = ?",
                (swiss_regular_best_of, swiss_decider_best_of, tournament_id),
            )
            connection.execute(
                "DELETE FROM tournament_round_settings WHERE tournament_id = ? AND bracket_type = 'swiss'",
                (tournament_id,),
            )
            for match_row in real_matches:
                mode = swiss_mode_by_id.get(int(match_row["id"]), "swiss_regular")
                target_best_of = swiss_decider_best_of if mode == "swiss_decider" else swiss_regular_best_of
                connection.execute(
                    "UPDATE tournament_matches SET best_of = ?, updated_at = ? WHERE id = ?",
                    (target_best_of, now_text(), match_row["id"]),
                )
        else:
            raise HTTPException(status_code=400, detail="不支持的全局 BO 设置类型。")

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=bracket", status_code=303)


@app.post("/tournaments/{tournament_id}/matches/{match_id}/report")
def report_match_result(
    request: Request,
    tournament_id: int,
    match_id: int,
    score1: int = Form(...),
    score2: int = Form(...),
    source_tab: str = Form("bracket"),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    redirect_tab = source_tab if source_tab in {"bracket", "my_matches"} else "bracket"

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        if not tournament_started(tournament):
            return RedirectResponse(f"/tournaments/{tournament_id}?tab={redirect_tab}", status_code=303)

        manager = is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"])
        registration = load_registration_for_user(connection, tournament_id, current_user["id"])

        match_row = connection.execute(
            "SELECT * FROM tournament_matches WHERE id = ? AND tournament_id = ?",
            (match_id, tournament_id),
        ).fetchone()
        if match_row is None:
            raise HTTPException(status_code=404, detail="对局不存在。")

        participants_by_id = {row["id"]: row for row in load_registrations(connection, tournament_id)}
        player_ids = {match_row["player1_registration_id"], match_row["player2_registration_id"]}
        has_temporary_player = any(
            participants_by_id[registration_id]["is_temporary"]
            for registration_id in player_ids
            if registration_id is not None and registration_id in participants_by_id
        )
        player_can_report = (
            registration is not None
            and registration["id"] in player_ids
            and not has_temporary_player
        )

        if not manager and not player_can_report:
            raise HTTPException(status_code=403, detail="只有赛事管理员或当前对局选手可以录入赛果。")
        if not manager and replay_result_enabled_for_tournament(tournament):
            raise HTTPException(status_code=403, detail="当前赛事已启用 replay 自动判定，请上传本局 replay。")
        if not manager and match_row["status"] == "completed":
            return RedirectResponse(f"/tournaments/{tournament_id}?tab=my_matches", status_code=303)

        actual_best_of = int(match_row["best_of"] or default_best_of_for_bracket(match_row["bracket_type"]))
        winner_registration_id, _ = resolve_match_result_from_scores(match_row, actual_best_of, score1, score2)
        complete_match_internal(connection, match_row, winner_registration_id, score1, score2, best_of=actual_best_of)
        updated_match_row = connection.execute(
            "SELECT * FROM tournament_matches WHERE id = ? AND tournament_id = ?",
            (match_id, tournament_id),
        ).fetchone()
        if updated_match_row is not None:
            preparation_state = load_match_preparation_state(connection, updated_match_row)
            if preparation_state is not None:
                preparation_state["status"] = "complete"
                preparation_state["advance_ready_ids"] = []
                stop_preparation_turn_clock(preparation_state, tournament_bp_step_seconds(tournament))
                save_match_preparation_state(connection, updated_match_row, tournament["bp_mode"], preparation_state)
        ensure_tournament_matches(connection, tournament)

    destination_tab = redirect_tab if manager else "my_matches"
    return RedirectResponse(f"/tournaments/{tournament_id}?tab={destination_tab}", status_code=303)


@app.post("/tournaments/{tournament_id}/matches/{match_id}/replay")
async def upload_match_replay(
    request: Request,
    tournament_id: int,
    match_id: int,
    replay_file: UploadFile = File(...),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    redirect_path = f"/tournaments/{tournament_id}?tab=my_matches"
    replay_bytes = b""
    try:
        replay_bytes = await replay_file.read()
        with closing(get_connection()) as connection, connection:
            tournament = load_tournament_row(connection, tournament_id)
            if tournament is None:
                raise HTTPException(status_code=404, detail="赛事不存在。")
            if not replay_result_enabled_for_tournament(tournament):
                return RedirectResponse(
                    append_redirect_query_value(redirect_path, "replay_upload_error", "当前赛事未启用 replay 自动判定。"),
                    status_code=303,
                )
            if not tournament_started(tournament):
                return RedirectResponse(redirect_path, status_code=303)

            registration = load_registration_for_user(connection, tournament_id, current_user["id"])
            if registration is None:
                raise HTTPException(status_code=403, detail="只有当前对局选手可以上传 replay。")

            match_row = connection.execute(
                "SELECT * FROM tournament_matches WHERE id = ? AND tournament_id = ?",
                (match_id, tournament_id),
            ).fetchone()
            if match_row is None:
                raise HTTPException(status_code=404, detail="对局不存在。")
            if match_row["status"] == "completed":
                return RedirectResponse(redirect_path, status_code=303)

            registrations = load_registrations(connection, tournament_id)
            registration_map = {row["id"]: row for row in registrations}
            player_ids = set(match_preparation_player_ids(match_row))
            if registration["id"] not in player_ids:
                raise HTTPException(status_code=403, detail="只有当前对局选手可以上传 replay。")

            validation_error = replay_match_validation_error(connection, match_row, registration_map)
            if validation_error:
                return RedirectResponse(
                    append_redirect_query_value(redirect_path, "replay_upload_error", validation_error),
                    status_code=303,
                )

            state = sync_match_preparation_state(connection, tournament, match_row) or default_match_preparation_state(tournament)
            if state.get("status") not in {"ready_to_play", "awaiting_vote"}:
                return RedirectResponse(
                    append_redirect_query_value(redirect_path, "replay_upload_error", "当前小局尚未准备完成，暂时不能上传 replay。"),
                    status_code=303,
                )

            current_game_number = max(1, int(state.get("current_game_number", 1) or 1))
            if match_has_recorded_or_pending_game(state, current_game_number):
                pending_entry = pending_manual_replay_judgment(state)
                duplicate_message = "当前小局的 replay 已经记录，无需重复上传。"
                if pending_entry is not None and int(pending_entry.get("game_number") or 0) == current_game_number:
                    duplicate_message = "当前小局的 replay 已上传，正在等待手动选择赢家。"
                return RedirectResponse(
                    append_redirect_query_value(redirect_path, "replay_upload_error", duplicate_message),
                    status_code=303,
                )

            replay_path = replay_storage_path(tournament, match_row, registration_map, current_game_number)
            replay_path.parent.mkdir(parents=True, exist_ok=True)
            replay_path.write_bytes(replay_bytes)

            saved_path_text = replay_saved_path_text(replay_path)
            uploaded_at = now_text()

            # 仅归档保存 replay，不做任何检定
            state.setdefault("game_replays", []).append(
                {
                    "game_number": current_game_number,
                    "uploader_registration_id": registration["id"],
                    "filename": replay_file.filename or f"game_{current_game_number}.SC2Replay",
                    "saved_path": saved_path_text,
                    "uploaded_at": uploaded_at,
                }
            )
            state["status"] = "awaiting_vote"
            state["pending_votes"] = state.get("pending_votes") or {}
            stop_preparation_turn_clock(state, tournament_bp_step_seconds(tournament))

            # 如果投票已一致，replay 上传后自动推进
            if state.get("votes_resolved"):
                winning_slot = state.pop("resolved_winner_slot")
                state.pop("votes_resolved", None)
                winner_registration_id = (
                    match_row["player1_registration_id"] if winning_slot == "player1"
                    else match_row["player2_registration_id"]
                )
                replays = state.get("game_replays") or []
                replay_info = next((r for r in replays if int(r.get("game_number") or 0) == current_game_number), {})
                success_message = finalize_match_replay_game(
                    connection, tournament, match_row, state,
                    int(winner_registration_id), int(registration["id"]),
                    replay_info.get("filename", "投票裁定+归档"),
                    replay_info.get("saved_path", ""),
                    replay_info.get("uploaded_at", now_text()),
                    current_game_number,
                )
            else:
                success_message = f"已上传第 {current_game_number} 局 replay。请双方选择本局胜者。"

            save_match_preparation_state(connection, match_row, tournament["bp_mode"], state)
    except (RuntimeError, ValueError) as exc:
        return RedirectResponse(
            append_redirect_query_value(redirect_path, "replay_upload_error", str(exc)),
            status_code=303,
        )
    except HTTPException:
        raise
    except Exception as exc:
        return RedirectResponse(
            append_redirect_query_value(redirect_path, "replay_upload_error", f"保存 replay 失败：{exc}"),
            status_code=303,
        )
    finally:
        await replay_file.close()

    return RedirectResponse(
        append_redirect_query_value(redirect_path, "replay_upload_success", success_message),
        status_code=303,
    )


@app.post("/tournaments/{tournament_id}/matches/{match_id}/replay/vote")
def vote_match_replay_winner(
    request: Request,
    tournament_id: int,
    match_id: int,
    winner_slot: str = Form("player1"),
):
    """双方投票选择本局胜者。票数一致则判定通过，否则触发管理员仲裁。"""
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    redirect_path = f"/tournaments/{tournament_id}?tab=my_matches"

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        if not tournament_started(tournament):
            return RedirectResponse(redirect_path, status_code=303)

        registration = load_registration_for_user(connection, tournament_id, current_user["id"])
        if registration is None:
            raise HTTPException(status_code=403, detail="只有对局选手可以投票。")

        match_row = connection.execute(
            "SELECT * FROM tournament_matches WHERE id = ? AND tournament_id = ?",
            (match_id, tournament_id),
        ).fetchone()
        if match_row is None:
            raise HTTPException(status_code=404, detail="对局不存在。")
        if registration["id"] not in set(match_preparation_player_ids(match_row)):
            raise HTTPException(status_code=403, detail="只有当前对局选手可以投票。")

        state = sync_match_preparation_state(connection, tournament, match_row) or default_match_preparation_state(tournament)
        # 投票可在 ready_to_play（投票前置）或 awaiting_vote（上传后投票）状态下进行
        if state.get("status") not in {"ready_to_play", "awaiting_vote"}:
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "replay_upload_error", "当前不在可投票状态。"),
                status_code=303,
            )

        # 如果本轮投票已达成一致，不允许重复投票
        if state.get("votes_resolved"):
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "replay_upload_success", "本轮投票已达成一致，请上传 replay 或跳过上传以完成本局。"),
                status_code=303,
            )

        current_game_number = max(1, int(state.get("current_game_number", 1) or 1))
        winner_slot = winner_slot.strip()
        if winner_slot not in {"player1", "player2"}:
            raise HTTPException(status_code=400, detail="无效的投票选项。")

        # 记录投票
        votes = state.setdefault("pending_votes", {})
        voter_key = str(registration["id"])
        votes[voter_key] = winner_slot

        # 检查是否双方都已投票
        player_ids = match_preparation_player_ids(match_row)
        all_voted = all(str(pid) in votes for pid in player_ids)

        if not all_voted:
            save_match_preparation_state(connection, match_row, tournament["bp_mode"], state)
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "replay_upload_success", f"已记录你的投票，等待对方投票。"),
                status_code=303,
            )

        # 双方都已投票——检查是否一致
        vote_values = set(votes.values())
        if len(vote_values) == 1:
            # 一致——记录投票结果，等待 replay 上传/跳过后再推进
            state["votes_resolved"] = True
            state["resolved_winner_slot"] = list(vote_values)[0]
            state["pending_votes"] = {}
            # 检查 replay 是否已完成（已上传或已跳过）
            replay_done = state.get("replay_skipped") or any(
                int(r.get("game_number") or 0) == current_game_number
                for r in (state.get("game_replays") or [])
            )
            if replay_done:
                # 投票和 replay 都完成 → 推进
                winning_slot = state.pop("resolved_winner_slot")
                state.pop("votes_resolved", None)
                winner_registration_id = (
                    match_row["player1_registration_id"] if winning_slot == "player1"
                    else match_row["player2_registration_id"]
                )
                replays = state.get("game_replays") or []
                replay_info = next((r for r in replays if int(r.get("game_number") or 0) == current_game_number), {})
                success_message = finalize_match_replay_game(
                    connection, tournament, match_row, state,
                    int(winner_registration_id), int(registration["id"]),
                    replay_info.get("filename", "投票裁定"),
                    replay_info.get("saved_path", ""),
                    replay_info.get("uploaded_at", now_text()),
                    current_game_number,
                )
            else:
                # 投票一致但 replay 未上传——不清空 pending_votes，保持状态以便上传/skip
                success_message = "投票一致！请上传本局 replay 或点击跳过上传以完成本局。"
        else:
            # 不一致——触发管理员仲裁
            state["pending_admin_replay_judgment"] = {
                "game_number": current_game_number,
                "requester_registration_id": registration["id"],
                "requested_at": now_text(),
                "detail": f"双方投票不一致（player1 投 {votes.get(str(match_row['player1_registration_id']), '未投')}，player2 投 {votes.get(str(match_row['player2_registration_id']), '未投')}），请管理员裁定。",
            }
            state = wait_for_admin_replay_judgment_state(state)
            success_message = "双方投票不一致，已触发管理员仲裁。"

        save_match_preparation_state(connection, match_row, tournament["bp_mode"], state)

    return RedirectResponse(
        append_redirect_query_value(redirect_path, "replay_upload_success", success_message),
        status_code=303,
    )


@app.post("/tournaments/{tournament_id}/matches/{match_id}/replay/skip")
def skip_match_replay_for_admin_review(
    request: Request,
    tournament_id: int,
    match_id: int,
    source_tab: str = Form("my_matches"),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    redirect_tab = source_tab if source_tab in {"bracket", "my_matches"} else "my_matches"
    redirect_path = f"/tournaments/{tournament_id}?tab={redirect_tab}"

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        if not replay_result_enabled_for_tournament(tournament):
            return RedirectResponse(redirect_path, status_code=303)
        if not tournament_started(tournament):
            return RedirectResponse(redirect_path, status_code=303)

        registration = load_registration_for_user(connection, tournament_id, current_user["id"])
        if registration is None:
            raise HTTPException(status_code=403, detail="只有当前对局选手可以临时跳过本局 replay。")

        match_row = connection.execute(
            "SELECT * FROM tournament_matches WHERE id = ? AND tournament_id = ?",
            (match_id, tournament_id),
        ).fetchone()
        if match_row is None:
            raise HTTPException(status_code=404, detail="对局不存在。")
        if match_row["status"] == "completed":
            return RedirectResponse(redirect_path, status_code=303)
        if registration["id"] not in set(match_preparation_player_ids(match_row)):
            raise HTTPException(status_code=403, detail="只有当前对局选手可以临时跳过本局 replay。")

        state = sync_match_preparation_state(connection, tournament, match_row) or default_match_preparation_state(tournament)
        if state.get("status") not in {"ready_to_play", "awaiting_vote"}:
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "replay_upload_error", "当前小局尚未准备完成，暂时不能跳过 replay。"),
                status_code=303,
            )

        current_game_number = max(1, int(state.get("current_game_number", 1) or 1))
        if pending_admin_replay_judgment(state) is not None:
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "replay_upload_error", "当前已有等待赛事管理员仲裁的小局，请先完成该局裁定。"),
                status_code=303,
            )
        if match_has_recorded_or_pending_game(state, current_game_number):
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "replay_upload_error", "当前小局结果已记录或正在等待处理，无需再次跳过。"),
                status_code=303,
            )

        state["replay_skipped"] = True
        # 如果投票已一致，跳过 replay 后自动推进
        if state.get("votes_resolved"):
            winning_slot = state.pop("resolved_winner_slot")
            state.pop("votes_resolved", None)
            winner_registration_id = (
                match_row["player1_registration_id"] if winning_slot == "player1"
                else match_row["player2_registration_id"]
            )
            replays = state.get("game_replays") or []
            replay_info = next((r for r in replays if int(r.get("game_number") or 0) == current_game_number), {})
            success_message = finalize_match_replay_game(
                connection, tournament, match_row, state,
                int(winner_registration_id), int(registration["id"]),
                replay_info.get("filename", "投票裁定（无 replay）"),
                replay_info.get("saved_path", ""),
                replay_info.get("uploaded_at", now_text()),
                current_game_number,
            )
            save_match_preparation_state(connection, match_row, tournament["bp_mode"], state)
        else:
            state["status"] = "awaiting_vote"
            save_match_preparation_state(connection, match_row, tournament["bp_mode"], state)
            success_message = f"已跳过第 {current_game_number} 局 replay 上传，等待双方投票。"

    return RedirectResponse(
        append_redirect_query_value(redirect_path, "replay_upload_success", success_message),
        status_code=303,
    )


@app.post("/tournaments/{tournament_id}/matches/{match_id}/replay/manual-result")
def choose_manual_replay_winner(
    request: Request,
    tournament_id: int,
    match_id: int,
    winner_slot: str = Form(...),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    redirect_path = f"/tournaments/{tournament_id}?tab=my_matches"
    if winner_slot not in {"player1", "player2"}:
        return RedirectResponse(
            append_redirect_query_value(redirect_path, "replay_upload_error", "手动选择的赢家无效。"),
            status_code=303,
        )

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        if not replay_result_enabled_for_tournament(tournament):
            return RedirectResponse(redirect_path, status_code=303)

        registration = load_registration_for_user(connection, tournament_id, current_user["id"])
        if registration is None:
            raise HTTPException(status_code=403, detail="只有当前对局选手可以手动选择赢家。")

        match_row = connection.execute(
            "SELECT * FROM tournament_matches WHERE id = ? AND tournament_id = ?",
            (match_id, tournament_id),
        ).fetchone()
        if match_row is None:
            raise HTTPException(status_code=404, detail="对局不存在。")
        if registration["id"] not in set(match_preparation_player_ids(match_row)):
            raise HTTPException(status_code=403, detail="只有当前对局选手可以手动选择赢家。")

        state = sync_match_preparation_state(connection, tournament, match_row) or default_match_preparation_state(tournament)
        pending_entry = pending_manual_replay_judgment(state)
        if pending_entry is None:
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "replay_upload_error", "当前没有等待手动裁定的 replay。"),
                status_code=303,
            )

        winner_registration_id = (
            int(match_row["player1_registration_id"])
            if winner_slot == "player1"
            else int(match_row["player2_registration_id"])
        )
        success_message = finalize_match_replay_game(
            connection,
            tournament,
            match_row,
            state,
            winner_registration_id,
            int(pending_entry.get("uploader_registration_id") or registration["id"]),
            str(pending_entry.get("filename") or f"第{pending_entry.get('game_number') or state.get('current_game_number', 1)}局 replay"),
            str(pending_entry.get("saved_path") or ""),
            str(pending_entry.get("uploaded_at") or now_text()),
            int(pending_entry.get("game_number") or state.get("current_game_number", 1)),
        )

    return RedirectResponse(
        append_redirect_query_value(redirect_path, "replay_upload_success", success_message),
        status_code=303,
    )


@app.post("/tournaments/{tournament_id}/matches/{match_id}/replay/admin-result")
def choose_admin_replay_winner(
    request: Request,
    tournament_id: int,
    match_id: int,
    winner_slot: str = Form(...),
    source_tab: str = Form("bracket"),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    redirect_tab = source_tab if source_tab in {"bracket", "my_matches"} else "bracket"
    redirect_path = f"/tournaments/{tournament_id}?tab={redirect_tab}"
    if winner_slot not in {"player1", "player2"}:
        return RedirectResponse(
            append_redirect_query_value(redirect_path, "replay_upload_error", "管理员选择的赢家无效。"),
            status_code=303,
        )

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        if not is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"]):
            raise HTTPException(status_code=403, detail="只有赛事管理员可以仲裁跳过的小局结果。")

        match_row = connection.execute(
            "SELECT * FROM tournament_matches WHERE id = ? AND tournament_id = ?",
            (match_id, tournament_id),
        ).fetchone()
        if match_row is None:
            raise HTTPException(status_code=404, detail="对局不存在。")

        state = sync_match_preparation_state(connection, tournament, match_row) or default_match_preparation_state(tournament)
        pending_entry = pending_admin_replay_judgment(state)
        if pending_entry is None:
            return RedirectResponse(
                append_redirect_query_value(redirect_path, "replay_upload_error", "当前没有等待赛事管理员仲裁的小局。"),
                status_code=303,
            )

        winner_registration_id = (
            int(match_row["player1_registration_id"])
            if winner_slot == "player1"
            else int(match_row["player2_registration_id"])
        )
        state.setdefault("game_results", []).append(
            {
                "game_number": int(pending_entry.get("game_number") or state.get("current_game_number", 1)),
                "winner_registration_id": winner_registration_id,
                "uploader_registration_id": int(pending_entry.get("requester_registration_id") or 0) or None,
                "filename": str(pending_entry.get("filename") or f"第{pending_entry.get('game_number') or state.get('current_game_number', 1)}局 管理员仲裁"),
                "saved_path": str(pending_entry.get("saved_path") or ""),
                "uploaded_at": str(pending_entry.get("requested_at") or now_text()),
            }
        )
        state["pending_admin_replay_judgment"] = None

        series_completed, series_winner_id, score1, score2 = match_series_completion(state, match_row)
        actual_best_of = int(match_row["best_of"] or default_best_of_for_bracket(match_row["bracket_type"]))
        if series_completed:
            complete_match_internal(connection, match_row, series_winner_id, score1, score2, best_of=actual_best_of)
            refreshed_match = connection.execute(
                "SELECT * FROM tournament_matches WHERE id = ? AND tournament_id = ?",
                (match_row["id"], match_row["tournament_id"]),
            ).fetchone()
            state["status"] = "complete"
            state["advance_ready_ids"] = []
            stop_preparation_turn_clock(state, tournament_bp_step_seconds(tournament))
            save_match_preparation_state(connection, refreshed_match or match_row, tournament["bp_mode"], state)
            ensure_tournament_matches(connection, tournament)
            success_message = f"已仲裁第 {int(pending_entry.get('game_number') or 0)} 局，并完成整场比分 {score1}:{score2}。"
        else:
            if state.get("status") == "awaiting_admin_judgment":
                if consumed_replay_game_count(state) >= actual_best_of:
                    state = wait_for_admin_replay_judgment_state(state)
                elif normalize_bp_mode(tournament["bp_mode"]) == BP_MODE_EVO:
                    state = advance_replay_series_state(tournament, match_row, state)
                else:
                    state["status"] = "ready_to_play" if normalize_bp_mode(tournament["bp_mode"]) != BP_MODE_COMMANDER_DRAFT else "awaiting_ready"
            save_match_preparation_state(connection, match_row, tournament["bp_mode"], state)
            success_message = f"已仲裁第 {int(pending_entry.get('game_number') or 0)} 局结果。"

    return RedirectResponse(
        append_redirect_query_value(redirect_path, "replay_upload_success", success_message),
        status_code=303,
    )


@app.get("/tournaments/{tournament_id}/replay-archive")
def download_tournament_replay_archive(
    request: Request,
    tournament_id: int,
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        if not is_tournament_manager(connection, tournament_id, current_user["id"], tournament["creator_id"]):
            raise HTTPException(status_code=403, detail="只有赛事管理员可以下载赛事 replay 总压缩包。")

        ensure_tournament_matches(connection, tournament)
        if not tournament_replays_ready_for_download(connection, tournament_id):
            raise HTTPException(status_code=400, detail="当前赛事尚未全部结束，暂时不能下载 replay 总压缩包。")

        archive_path = refresh_tournament_replay_archive_for_tournament(connection, tournament_id)
        if archive_path is None or not archive_path.exists():
            raise HTTPException(status_code=404, detail="当前赛事还没有可下载的 replay 文件。")

    return FileResponse(
        path=str(archive_path),
        filename=archive_path.name,
        media_type="application/gzip",
    )


@app.post("/tournaments/{tournament_id}/matches/{match_id}/prepare")
def prepare_match(
    request: Request,
    tournament_id: int,
    match_id: int,
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        if not tournament_started(tournament):
            return RedirectResponse(f"/tournaments/{tournament_id}?tab=my_matches", status_code=303)

        registration = load_registration_for_user(connection, tournament_id, current_user["id"])
        if registration is None:
            raise HTTPException(status_code=403, detail="只有当前对局选手可以准备比赛。")

        match_row = connection.execute(
            "SELECT * FROM tournament_matches WHERE id = ? AND tournament_id = ?",
            (match_id, tournament_id),
        ).fetchone()
        if match_row is None:
            raise HTTPException(status_code=404, detail="对局不存在。")
        if match_row["status"] == "completed":
            return RedirectResponse(f"/tournaments/{tournament_id}?tab=my_matches", status_code=303)

        registrations = load_registrations(connection, tournament_id)
        registration_map = {row["id"]: row for row in registrations}
        if not match_preparation_supported(match_row, registration_map):
            raise HTTPException(status_code=400, detail="当前对局暂不支持准备比赛与 BP。")
        if registration["id"] not in set(match_preparation_player_ids(match_row)):
            raise HTTPException(status_code=403, detail="只有当前对局选手可以准备比赛。")

        state = load_match_preparation_state(connection, match_row) or default_match_preparation_state(tournament)
        ready_ids = set(int(value) for value in state.get("ready_registration_ids") or [])
        ready_ids.add(int(registration["id"]))
        state["ready_registration_ids"] = sorted(ready_ids)
        if ready_ids == set(match_preparation_player_ids(match_row)) and state.get("status") == "awaiting_ready":
            try:
                state = initialize_match_preparation_state(tournament, match_row, state)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        save_match_preparation_state(connection, match_row, tournament["bp_mode"], state)

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=my_matches", status_code=303)


@app.post("/tournaments/{tournament_id}/matches/{match_id}/bp/action")
def apply_match_bp_action(
    request: Request,
    tournament_id: int,
    match_id: int,
    action_value: str = Form(""),
    action_values: list[str] = Form([]),
    action_mode: str = Form("select"),
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")

        registration = load_registration_for_user(connection, tournament_id, current_user["id"])
        if registration is None:
            raise HTTPException(status_code=403, detail="只有当前对局选手可以操作 BP。")

        match_row = connection.execute(
            "SELECT * FROM tournament_matches WHERE id = ? AND tournament_id = ?",
            (match_id, tournament_id),
        ).fetchone()
        if match_row is None:
            raise HTTPException(status_code=404, detail="对局不存在。")

        state = sync_match_preparation_state(connection, tournament, match_row) or default_match_preparation_state(tournament)
        if state.get("status") != "bp_in_progress":
            raise HTTPException(status_code=400, detail="当前对局还没有进入可操作的 BP 阶段。")

        bp_mode = normalize_bp_mode(state.get("bp_mode") or tournament["bp_mode"])
        if bp_mode in {BP_MODE_MAP_POOL, BP_MODE_EVO}:
            viewer_side = "A" if registration["id"] == match_row["player1_registration_id"] else "B"
            map_state = state.get("map_bp") or {}
            current_step = current_map_bp_step(map_state)
            if current_step is not None and not map_state.get("completed"):
                try:
                    apply_map_bp_action(map_state, viewer_side, action_value)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                state["map_bp"] = map_state
                state["status"] = "ready_to_play" if map_state.get("completed") else "bp_in_progress"
                if state["status"] == "bp_in_progress":
                    clear_current_game_ready_at(state)
                    start_preparation_turn_clock(state, tournament_bp_step_seconds(tournament))
                else:
                    ensure_current_game_ready_at(state)
                    stop_preparation_turn_clock(state, tournament_bp_step_seconds(tournament))
            elif bp_mode == BP_MODE_EVO:
                current_choice = current_evo_choice_state(match_row, state)
                if current_choice is None or int(current_choice.get("game_number") or 0) <= 1:
                    raise HTTPException(status_code=400, detail="当前小局不需要执行 EVO 选择。")
                if int(current_choice.get("loser_registration_id") or 0) != int(registration["id"]):
                    raise HTTPException(status_code=403, detail="只有上一局败者可以选择本局 EVO 赛制。")
                cleaned_choice = str(action_value or "").strip()
                if cleaned_choice not in EVO_CHOICE_LABELS:
                    raise HTTPException(status_code=400, detail="请选择一个有效的 EVO 赛制选项。")
                current_choice["choice_value"] = cleaned_choice
                upsert_evo_choice(state, current_choice)
                state["status"] = "ready_to_play"
                ensure_current_game_ready_at(state)
                stop_preparation_turn_clock(state, tournament_bp_step_seconds(tournament))
            else:
                raise HTTPException(status_code=400, detail="当前地图池 BP 已完成，无需继续操作。")
        elif bp_mode == BP_MODE_COMMANDER_DRAFT:
            commander_game = current_commander_game_state(state)
            if commander_game is None:
                raise HTTPException(status_code=400, detail="当前没有可操作的指挥官 BP 小局。")

            side_assignments = commander_game["side_assignments"]
            viewer_side = None
            for side, registration_id in side_assignments.items():
                if registration_id == registration["id"]:
                    viewer_side = side
                    break
            if viewer_side is None:
                raise HTTPException(status_code=403, detail="只有当前对局选手可以操作 BP。")

            try:
                if action_mode == "select":
                    if not action_value:
                        raise HTTPException(status_code=400, detail="请选择一个预选项。")
                    set_pending_commander_action(commander_game, viewer_side, action_value)
                elif action_mode == "confirm":
                    confirm_commander_action(commander_game, viewer_side)
                    update_commander_preparation_status(state, commander_game, tournament_bp_step_seconds(tournament))
                else:
                    raise HTTPException(status_code=400, detail="不支持的指挥官 BP 操作。")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        elif bp_mode in {BP_MODE_COMMANDER_CONQUEST, BP_MODE_COMMANDER_CONQUEST_2V2}:
            series_state = state.get("conquest_series")
            current_game = current_commander_conquest_game_state(state)
            if not isinstance(series_state, dict) or current_game is None:
                raise HTTPException(status_code=400, detail="当前没有可操作的征服者 BP 小局。")

            viewer_side = "A" if registration["id"] == match_row["player1_registration_id"] else "B"
            phase = str(series_state.get("phase") or "pool")
            ppg = int(series_state.get("picks_per_game") or 1)
            first_to = int(series_state.get("first_to") or commander_conquest_first_to(int(match_row["best_of"] or 3), ppg))
            try:
                if phase == "pool":
                    existing_selection = normalize_commander_catalog_selection(
                        (series_state.get("pool_submissions") or {}).get(viewer_side) or [],
                        first_to,
                    )
                    if len(existing_selection) == first_to:
                        raise HTTPException(status_code=400, detail="你已提交过本场的征服者预选池。")
                    selected = normalize_commander_catalog_selection(action_values, first_to)
                    if len(selected) != first_to:
                        raise HTTPException(status_code=400, detail=f"请选择 {first_to} 位有效指挥官。")
                    series_state.setdefault("pool_submissions", {"A": [], "B": []})[viewer_side] = selected
                elif phase == "pick":
                    pick_slot = current_game.get("pick", {}).get(viewer_side)
                    if _conquest_pick_ready(pick_slot, ppg):
                        raise HTTPException(status_code=400, detail="你已提交过本局出战指挥官。")
                    available = list(current_game.get("available", {}).get(viewer_side) or commander_conquest_available_commanders(state, viewer_side))
                    # 支持单值 action_value 和多值 action_values（勾选+确认模式）
                    if action_value and not action_values:
                        action_values = [action_value]
                    cleaned_choices = [str(v or "").strip() for v in (action_values or [])]
                    cleaned_choices = [c for c in cleaned_choices if c]
                    # random 占位符替换
                    cleaned_choices = [random.choice(available) if c.lower() == "random" and available else c for c in cleaned_choices]
                    if not cleaned_choices:
                        raise HTTPException(status_code=400, detail="请选择出战指挥官。")
                    for c in cleaned_choices:
                        if c not in available:
                            raise HTTPException(status_code=400, detail=f"指挥官 '{c}' 不在你的可用征服者池中。")
                    pick_entry = current_game.setdefault("pick", {"A": None, "B": None})
                    if ppg > 1:
                        pick_list: list[str] = list(pick_entry.get(viewer_side) or [])
                        pick_list.extend(cleaned_choices)
                        pick_entry[viewer_side] = pick_list
                    else:
                        pick_entry[viewer_side] = cleaned_choices[0]
                else:
                    raise HTTPException(status_code=400, detail="当前征服者 BP 阶段无效。")
                finalize_commander_conquest_preparation_status(state, tournament_bp_step_seconds(tournament))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        elif bp_mode == BP_MODE_LADDER_1V1:
            ladder_game = current_tournament_ladder_bp_game_state(state)
            if ladder_game is None:
                raise HTTPException(status_code=400, detail="当前没有可操作的天梯 1v1 BP 小局。")

            viewer_side = "A" if registration["id"] == match_row["player1_registration_id"] else "B"
            phase = str(ladder_game.get("phase") or "pool")
            try:
                if phase == "pool":
                    if len(ladder_game["commanders"].get(viewer_side) or []) == 3:
                        raise HTTPException(status_code=400, detail="你已提交本局的 3 位指挥官。")
                    selected = normalize_ladder_commanders(action_values, 3)
                    if len(selected) != 3:
                        raise HTTPException(status_code=400, detail="请选择 3 位有效指挥官。")
                    ladder_game["commanders"][viewer_side] = selected
                elif phase == "ban":
                    if ladder_game["ban"].get(viewer_side) is not None:
                        raise HTTPException(status_code=400, detail="你已提交过 Ban。")
                    opponent_side = "B" if viewer_side == "A" else "A"
                    cleaned_choice = str(action_value or "").strip()
                    if cleaned_choice and cleaned_choice not in (ladder_game["commanders"].get(opponent_side) or []):
                        raise HTTPException(status_code=400, detail="Ban 目标无效。")
                    ladder_game["ban"][viewer_side] = cleaned_choice
                elif phase == "pick":
                    if ladder_game["pick"].get(viewer_side):
                        raise HTTPException(status_code=400, detail="你已提交过 Pick。")
                    cleaned_choice = str(action_value or "").strip()
                    available = list(ladder_game["available"].get(viewer_side) or [])
                    if cleaned_choice.lower() == "random":
                        cleaned_choice = random.choice(available) if available else ""
                    if cleaned_choice not in available:
                        raise HTTPException(status_code=400, detail="Pick 目标无效。")
                    ladder_game["pick"][viewer_side] = cleaned_choice
                else:
                    raise HTTPException(status_code=400, detail="当前天梯 1v1 BP 阶段无效。")
                finalize_tournament_ladder_bp_state(state, ladder_game, tournament_bp_step_seconds(tournament))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            raise HTTPException(status_code=400, detail="当前赛事未启用可操作的 BP 模式。")

        save_match_preparation_state(connection, match_row, tournament["bp_mode"], state)

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=my_matches", status_code=303)


@app.post("/tournaments/{tournament_id}/matches/{match_id}/bp/advance")
def advance_match_bp_game(
    request: Request,
    tournament_id: int,
    match_id: int,
):
    current_user = require_user(request)
    if current_user is None:
        return RedirectResponse("/auth", status_code=303)

    with closing(get_connection()) as connection, connection:
        tournament = load_tournament_row(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="赛事不存在。")
        if replay_result_enabled_for_tournament(tournament):
            raise HTTPException(status_code=400, detail="当前赛事已启用 replay 自动判定，不再使用“下一局”按钮。")

        registration = load_registration_for_user(connection, tournament_id, current_user["id"])
        if registration is None:
            raise HTTPException(status_code=403, detail="只有当前对局选手可以进入下一局。")

        match_row = connection.execute(
            "SELECT * FROM tournament_matches WHERE id = ? AND tournament_id = ?",
            (match_id, tournament_id),
        ).fetchone()
        if match_row is None:
            raise HTTPException(status_code=404, detail="对局不存在。")

        state = sync_match_preparation_state(connection, tournament, match_row)
        bp_mode = normalize_bp_mode((state or {}).get("bp_mode"))
        if state is None or bp_mode not in {BP_MODE_COMMANDER_DRAFT, BP_MODE_LADDER_1V1}:
            raise HTTPException(status_code=400, detail="当前对局没有启用逐局执行的小局 BP。")
        if state.get("status") != "ready_to_play":
            raise HTTPException(status_code=400, detail="当前小局 BP 尚未完成，暂时不能进入下一局。")

        advance_ready_ids = set(int(value) for value in state.get("advance_ready_ids") or [])
        advance_ready_ids.add(int(registration["id"]))
        state["advance_ready_ids"] = sorted(advance_ready_ids)
        if advance_ready_ids == set(match_preparation_player_ids(match_row)):
            try:
                state = advance_commander_preparation_state(tournament, match_row, state) if bp_mode == BP_MODE_COMMANDER_DRAFT else advance_tournament_ladder_bp_state(tournament, match_row, state)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        save_match_preparation_state(connection, match_row, tournament["bp_mode"], state)

    return RedirectResponse(f"/tournaments/{tournament_id}?tab=my_matches", status_code=303)