# database.py — 数据库连接、Schema DDL、列迁移
# 从 app.py 拆分，供所有模块导入

import sqlite3
from contextlib import closing
from datetime import datetime as _datetime

from config import (
    DB_PATH, DEFAULT_TOURNAMENT_BP_STEP_SECONDS, TOURNAMENT_BP_STEP_SECONDS_MIN,
    TOURNAMENT_BP_STEP_SECONDS_MAX, LADDER_BP_SECONDS, SYSTEM_SETTING_LADDER_BP_SECONDS,
    COMMANDER_ASSET_THUMB_DIR, COMMANDER_ASSET_FULL_DIR, GUIDE_ASSETS_DIR,
)


def now_text() -> str:
    return _datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def table_info(connection: sqlite3.Connection, table_name: str) -> "list[sqlite3.Row]":
    return connection.execute(f"PRAGMA table_info({table_name})").fetchall()


def table_columns(connection: sqlite3.Connection, table_name: str) -> "set[str]":
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


def init_db(ensure_superadmin_fn=None, ensure_streamer_fn=None) -> None:
    """初始化数据库 schema 并执行所有列迁移。
    
    ensure_superadmin_fn 和 ensure_streamer_fn 是可选的种子账号创建回调，
    由 app.py 传入以避免循环导入。
    """
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
            connection.execute("UPDATE tournaments SET is_points_event = 0 WHERE is_points_event IS NULL")
            connection.execute("UPDATE tournaments SET points_for_champion = 0 WHERE points_for_champion IS NULL")
            connection.execute("UPDATE tournaments SET is_private = 0 WHERE is_private IS NULL")
            connection.execute("UPDATE tournaments SET stage1_group_count = 1 WHERE stage1_group_count IS NULL OR stage1_group_count < 1")
            connection.execute("UPDATE tournaments SET round_robin_cycles = 1 WHERE round_robin_cycles IS NULL OR round_robin_cycles < 1")
            connection.execute("UPDATE tournaments SET round_robin_win_points = 3 WHERE round_robin_win_points IS NULL")
            connection.execute("UPDATE tournaments SET round_robin_draw_points = 1 WHERE round_robin_draw_points IS NULL")
            connection.execute("UPDATE tournaments SET round_robin_loss_points = 0 WHERE round_robin_loss_points IS NULL")
            connection.execute("UPDATE tournaments SET round_robin_best_of = 3 WHERE round_robin_best_of IS NULL")
            connection.execute("UPDATE tournaments SET swiss_regular_best_of = 3 WHERE swiss_regular_best_of IS NULL")
            connection.execute("UPDATE tournaments SET swiss_decider_best_of = 3 WHERE swiss_decider_best_of IS NULL")
            connection.execute(
                "UPDATE tournaments SET bp_step_seconds = ? WHERE bp_step_seconds IS NULL OR bp_step_seconds < ? OR bp_step_seconds > ?",
                (DEFAULT_TOURNAMENT_BP_STEP_SECONDS, TOURNAMENT_BP_STEP_SECONDS_MIN, TOURNAMENT_BP_STEP_SECONDS_MAX),
            )
            connection.execute("UPDATE tournaments SET bp_mode = 'none' WHERE bp_mode IS NULL OR bp_mode = ''")
            connection.execute("UPDATE tournaments SET replay_result_enabled = 0 WHERE replay_result_enabled IS NULL")
            connection.execute("UPDATE tournaments SET commander_shared_pool = 1 WHERE commander_shared_pool IS NULL")
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
            if ensure_superadmin_fn is not None:
                ensure_superadmin_fn(connection)
            if ensure_streamer_fn is not None:
                ensure_streamer_fn(connection)
            connection.execute(
                "INSERT OR IGNORE INTO system_settings (key, value, updated_at) VALUES (?, ?, ?)",
                (SYSTEM_SETTING_LADDER_BP_SECONDS, str(LADDER_BP_SECONDS), now_text()),
            )
    COMMANDER_ASSET_THUMB_DIR.mkdir(parents=True, exist_ok=True)
    COMMANDER_ASSET_FULL_DIR.mkdir(parents=True, exist_ok=True)
    GUIDE_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
