from __future__ import annotations

import json
import queue
import random
import socket
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

from shared.constants import BP_SECONDS
from shared.constants import COMMANDER_POOL
from shared.constants import COMMANDER_SET
from shared.constants import DEFAULT_HOST
from shared.constants import DEFAULT_PORT
from shared.constants import INITIAL_RATING
from shared.constants import MAP_POOL_1V1
from shared.constants import MAP_POOL_2V2
from shared.constants import MATCH_BASE_WINDOW
from shared.constants import MATCH_EXPAND_SECONDS
from shared.constants import MATCH_EXPAND_STEP
from shared.constants import MATCH_MAX_WINDOW
from shared.net import iter_json_messages
from shared.net import open_server_socket
from shared.net import sanitize_text
from shared.net import send_json
from shared.rating import RatingValue
from shared.rating import update_elo
from shared.security import hash_password
from shared.security import verify_password


def resolve_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "server_data"
    return Path(__file__).resolve().parent / "server_data"


DATA_DIR = resolve_data_dir()
USERS_FILE = DATA_DIR / "users.json"
PAIR_RATINGS_FILE = DATA_DIR / "pair_ratings.json"
PENDING_FILE = DATA_DIR / "pending_reviews.json"
MATCH_LOG_FILE = DATA_DIR / "matches.jsonl"


def now_ts() -> float:
    return time.time()


def ts_text(ts_value: float | None = None) -> str:
    if ts_value is None:
        ts_value = now_ts()
    return datetime.fromtimestamp(ts_value).strftime("%Y-%m-%d %H:%M:%S")


def pair_key(name_a: str, name_b: str) -> str:
    a, b = sorted([name_a, name_b])
    return f"{a}|{b}"


def parse_port(text: str) -> int:
    port = int(text)
    if port < 1 or port > 65535:
        raise ValueError("port out of range")
    return port


def parse_datetime_input(text: str) -> float | None:
    clean = text.strip()
    if not clean:
        return None

    formats = [
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(clean, fmt).timestamp()
        except ValueError:
            continue
    return None


def normalize_commander_list(raw: Any, limit: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = sanitize_text(item, 20)
        if not name or name not in COMMANDER_SET or name in seen:
            continue
        seen.add(name)
        result.append(name)
        if len(result) >= limit:
            break
    return result


@dataclass
class ClientSession:
    conn: socket.socket
    addr: tuple[Any, ...]
    nickname: str = ""
    authenticated: bool = False
    stop_event: threading.Event = field(default_factory=threading.Event)
    latency_ms: int = 50


@dataclass
class QueueEntry:
    queue_id: str
    mode: str
    captain: str
    players: list[str]
    rating: float
    join_time: float
    latency_ms: int
    commanders_by_player: dict[str, list[str]] = field(default_factory=dict)


class LadderStore:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.users: dict[str, dict[str, Any]] = {}
        self.pair_ratings: dict[str, dict[str, Any]] = {}
        self.pending_reviews: list[dict[str, Any]] = []

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._load_all()

    def _load_json(self, path: Path, fallback: Any) -> Any:
        if not path.exists():
            return fallback
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return fallback

    def _save_json(self, path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_all(self) -> None:
        users_raw = self._load_json(USERS_FILE, {})
        pair_raw = self._load_json(PAIR_RATINGS_FILE, {})
        pending_raw = self._load_json(PENDING_FILE, [])

        if isinstance(users_raw, dict):
            self.users = users_raw
        if isinstance(pair_raw, dict):
            self.pair_ratings = pair_raw
        if isinstance(pending_raw, list):
            cleaned: list[dict[str, Any]] = []
            for item in pending_raw:
                if isinstance(item, dict) and "match_id" in item:
                    cleaned.append(item)
            self.pending_reviews = cleaned

    def _save_users(self) -> None:
        self._save_json(USERS_FILE, self.users)

    def _save_pairs(self) -> None:
        self._save_json(PAIR_RATINGS_FILE, self.pair_ratings)

    def _save_pending(self) -> None:
        self._save_json(PENDING_FILE, self.pending_reviews)

    def append_match_log(self, record: dict[str, Any]) -> None:
        try:
            with MATCH_LOG_FILE.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            return

    def user_exists(self, nickname: str) -> bool:
        with self.lock:
            return nickname in self.users

    def register_user(self, nickname: str, password: str) -> tuple[bool, str]:
        with self.lock:
            if nickname in self.users:
                return False, "昵称已存在。"
            salt_hex, digest_hex = hash_password(password)
            self.users[nickname] = {
                "salt": salt_hex,
                "password_hash": digest_hex,
                "created_at": ts_text(),
                "rating_1v1": {
                    "rating": INITIAL_RATING,
                    "games": 0,
                },
            }
            self._save_users()
        return True, "注册成功，请登录。"

    def verify_login(self, nickname: str, password: str) -> tuple[bool, str]:
        with self.lock:
            user = self.users.get(nickname)
            if not isinstance(user, dict):
                return False, "用户不存在。"
            salt_hex = str(user.get("salt", ""))
            digest_hex = str(user.get("password_hash", ""))
            if not verify_password(password, salt_hex, digest_hex):
                return False, "密码错误。"
        return True, "登录成功。"

    def get_1v1_rating(self, nickname: str) -> RatingValue:
        with self.lock:
            user = self.users.get(nickname)
            if not isinstance(user, dict):
                return RatingValue()
            raw = user.get("rating_1v1", {})
            if not isinstance(raw, dict):
                raw = {}
            rating = float(raw.get("rating", INITIAL_RATING))
            games = int(raw.get("games", 0))
            return RatingValue(rating=rating, games=games)

    def set_1v1_rating(self, nickname: str, value: RatingValue) -> None:
        with self.lock:
            user = self.users.get(nickname)
            if not isinstance(user, dict):
                return
            user["rating_1v1"] = {
                "rating": round(value.rating, 2),
                "games": int(value.games),
            }
            self._save_users()

    def get_pair_rating(self, name_a: str, name_b: str) -> RatingValue:
        key = pair_key(name_a, name_b)
        with self.lock:
            raw = self.pair_ratings.get(key)
            if not isinstance(raw, dict):
                self.pair_ratings[key] = {
                    "rating": INITIAL_RATING,
                    "games": 0,
                }
                self._save_pairs()
                return RatingValue()
            return RatingValue(
                rating=float(raw.get("rating", INITIAL_RATING)),
                games=int(raw.get("games", 0)),
            )

    def set_pair_rating(self, name_a: str, name_b: str, value: RatingValue) -> None:
        key = pair_key(name_a, name_b)
        with self.lock:
            self.pair_ratings[key] = {
                "rating": round(value.rating, 2),
                "games": int(value.games),
            }
            self._save_pairs()

    def list_1v1_leaderboard(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with self.lock:
            for nickname, user in self.users.items():
                if not isinstance(user, dict):
                    continue
                raw = user.get("rating_1v1", {})
                if not isinstance(raw, dict):
                    raw = {}
                try:
                    rating = float(raw.get("rating", INITIAL_RATING))
                except (TypeError, ValueError):
                    rating = INITIAL_RATING
                try:
                    games = int(raw.get("games", 0))
                except (TypeError, ValueError):
                    games = 0

                rows.append({
                    "nickname": nickname,
                    "rating": round(rating, 2),
                    "games": games,
                })

        rows.sort(key=lambda x: (-x["rating"], -x["games"], x["nickname"]))
        return rows

    def list_2v2_leaderboard(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with self.lock:
            for key, raw in self.pair_ratings.items():
                if not isinstance(raw, dict):
                    continue
                left, sep, right = key.partition("|")
                if not sep or not left or not right:
                    continue
                try:
                    rating = float(raw.get("rating", INITIAL_RATING))
                except (TypeError, ValueError):
                    rating = INITIAL_RATING
                try:
                    games = int(raw.get("games", 0))
                except (TypeError, ValueError):
                    games = 0

                rows.append({
                    "pair_key": key,
                    "players": [left, right],
                    "rating": round(rating, 2),
                    "games": games,
                })

        rows.sort(key=lambda x: (-x["rating"], -x["games"], x["pair_key"]))
        return rows

    def list_pair_ratings_for(self, nickname: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with self.lock:
            for key, raw in self.pair_ratings.items():
                if not isinstance(raw, dict):
                    continue
                parts = key.split("|", 1)
                if len(parts) != 2:
                    continue
                left, right = parts
                if nickname not in (left, right):
                    continue
                mate = right if left == nickname else left
                rows.append({
                    "teammate": mate,
                    "rating": round(float(raw.get("rating", INITIAL_RATING)), 2),
                    "games": int(raw.get("games", 0)),
                })
        rows.sort(key=lambda x: (-x["rating"], x["teammate"]))
        return rows

    def add_pending_review(self, review: dict[str, Any]) -> None:
        with self.lock:
            self.pending_reviews.append(review)
            self._save_pending()

    def remove_pending_review(self, match_id: str) -> dict[str, Any] | None:
        with self.lock:
            idx = -1
            for i, item in enumerate(self.pending_reviews):
                if str(item.get("match_id", "")) == match_id:
                    idx = i
                    break
            if idx < 0:
                return None
            removed = self.pending_reviews.pop(idx)
            self._save_pending()
            return removed

    def get_pending_reviews(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(x) for x in self.pending_reviews]

    def list_match_history(self, limit: int = 500) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        if not MATCH_LOG_FILE.exists():
            return []

        rows: list[dict[str, Any]] = []
        try:
            with MATCH_LOG_FILE.open("r", encoding="utf-8") as fp:
                for line in fp:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        item = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        rows.append(item)
        except OSError:
            return []

        if len(rows) > limit:
            rows = rows[-limit:]
        rows.reverse()
        return rows


class LadderServer:
    def __init__(self, event_queue: queue.Queue[tuple[str, Any]]) -> None:
        self.event_queue = event_queue
        self.lock = threading.RLock()
        self.store = LadderStore()

        self.running = False
        self.server_socket: socket.socket | None = None
        self.stop_event = threading.Event()

        self.clients: dict[socket.socket, ClientSession] = {}
        self.user_sessions: dict[str, ClientSession] = {}

        self.queue_1v1: list[QueueEntry] = []
        self.queue_2v2: list[QueueEntry] = []
        self.queue_index: dict[str, QueueEntry] = {}
        self.user_queue_map: dict[str, str] = {}
        self.locked_teammate: dict[str, str] = {}

        self.active_matches: dict[str, dict[str, Any]] = {}
        self.user_active_match: dict[str, str] = {}
        self.recent_opponents: dict[str, list[str]] = {}

    def start(self, host: str, port: int) -> tuple[bool, str]:
        if self.running:
            return False, "服务器已在运行。"

        self.stop_event = threading.Event()

        try:
            sock = open_server_socket(host, port)
        except OSError as exc:
            return False, f"启动失败: {exc}"

        self.server_socket = sock
        self.running = True

        threading.Thread(target=self.accept_loop, daemon=True).start()
        threading.Thread(target=self.matchmaker_loop, daemon=True).start()
        threading.Thread(target=self.phase_monitor_loop, daemon=True).start()

        self.log(f"服务器已启动 [{host}]:{port}")
        self.publish_admin_snapshot()
        return True, "启动成功。"

    def stop(self) -> None:
        if not self.running:
            return

        self.running = False
        self.stop_event.set()

        if self.server_socket is not None:
            try:
                self.server_socket.close()
            except OSError:
                pass
        self.server_socket = None

        with self.lock:
            sessions = list(self.clients.values())
            self.clients.clear()
            self.user_sessions.clear()
            self.queue_1v1.clear()
            self.queue_2v2.clear()
            self.queue_index.clear()
            self.user_queue_map.clear()
            self.locked_teammate.clear()
            self.active_matches.clear()
            self.user_active_match.clear()

        for session in sessions:
            try:
                session.conn.close()
            except OSError:
                pass

        self.log("服务器已停止。")
        self.publish_admin_snapshot()

    def accept_loop(self) -> None:
        while self.running and not self.stop_event.is_set():
            try:
                assert self.server_socket is not None
                conn, addr = self.server_socket.accept()
            except OSError:
                break

            session = ClientSession(conn=conn, addr=addr)
            with self.lock:
                self.clients[conn] = session

            self.log(f"客户端接入: {addr[0]}")
            threading.Thread(target=self.client_loop, args=(session,), daemon=True).start()
            self.publish_admin_snapshot()

    def client_loop(self, session: ClientSession) -> None:
        try:
            for message in iter_json_messages(session.conn, self.stop_event):
                try:
                    self.handle_message(session, message)
                except Exception as exc:
                    who = session.nickname if session.nickname else str(session.addr[0])
                    self.log(f"处理客户端消息失败: {who} | {exc}")
                    self.send(session, {
                        "type": "error",
                        "message": "服务器处理请求时出现异常，请重试。",
                    })
        finally:
            self.remove_client(session)

    def send(self, session: ClientSession, payload: dict[str, Any]) -> None:
        try:
            send_json(session.conn, payload)
        except OSError:
            self.remove_client(session)

    def send_to_user(self, nickname: str, payload: dict[str, Any]) -> None:
        target: ClientSession | None = None
        with self.lock:
            target = self.user_sessions.get(nickname)
        if target is not None:
            self.send(target, payload)

    def broadcast_to_users(self, nicknames: list[str], payload: dict[str, Any]) -> None:
        for nickname in nicknames:
            self.send_to_user(nickname, payload)

    def log(self, text: str) -> None:
        self.event_queue.put(("log", text))

    def queue_player_counts_locked(self) -> tuple[int, int]:
        queue_1v1_players = sum(len(entry.players) for entry in self.queue_1v1)
        queue_2v2_players = sum(len(entry.players) for entry in self.queue_2v2)
        return queue_1v1_players, queue_2v2_players

    def publish_admin_snapshot(self) -> None:
        sessions: list[ClientSession] = []
        lobby_payload: dict[str, Any] = {}
        with self.lock:
            queue_1v1_players, queue_2v2_players = self.queue_player_counts_locked()
            pending_reviews = self.store.get_pending_reviews()
            snapshot = {
                "online": len(self.user_sessions),
                "queue_1v1": len(self.queue_1v1),
                "queue_2v2": len(self.queue_2v2),
                "queue_1v1_players": queue_1v1_players,
                "queue_2v2_players": queue_2v2_players,
                "active_matches": len(self.active_matches),
                "pending_reviews": pending_reviews,
            }

            sessions = list(self.user_sessions.values())
            lobby_payload = {
                "type": "lobby_stats",
                "online_count": len(self.user_sessions),
                "queue_1v1_players": queue_1v1_players,
                "queue_2v2_players": queue_2v2_players,
            }

        self.event_queue.put(("snapshot", snapshot))

        for session in sessions:
            self.send(session, lobby_payload)

    def handle_message(self, session: ClientSession, message: dict[str, Any]) -> None:
        msg_type = sanitize_text(message.get("type"), 32)
        if not msg_type:
            return

        if msg_type == "register":
            self.handle_register(session, message)
            return
        if msg_type == "login":
            self.handle_login(session, message)
            return

        if not session.authenticated:
            self.send(session, {"type": "error", "message": "请先登录。"})
            return

        if msg_type == "request_state":
            self.send_state_snapshot(session.nickname)
        elif msg_type == "request_leaderboard":
            self.handle_request_leaderboard(session)
        elif msg_type == "queue_start":
            self.handle_queue_start(session, message)
        elif msg_type == "queue_cancel":
            self.handle_queue_cancel(session.nickname)
        elif msg_type == "bp_submit":
            self.handle_bp_submit(session, message)
        elif msg_type == "chat_send":
            self.handle_chat_send(session, message)
        elif msg_type == "report_result":
            self.handle_report_result(session, message)
        elif msg_type == "latency":
            latency = int(message.get("latency_ms", 50))
            latency = max(0, min(1200, latency))
            session.latency_ms = latency
        else:
            self.send(session, {"type": "error", "message": "未知指令。"})

    def handle_register(self, session: ClientSession, message: dict[str, Any]) -> None:
        nickname = sanitize_text(message.get("nickname"), 20)
        password = str(message.get("password", ""))
        if not nickname:
            self.send(session, {"type": "auth_result", "ok": False, "message": "昵称不能为空。"})
            return
        if len(password) < 6:
            self.send(session, {"type": "auth_result", "ok": False, "message": "密码至少 6 位。"})
            return

        try:
            ok, result_text = self.store.register_user(nickname, password)
        except Exception as exc:
            self.log(f"注册处理异常: {nickname} | {exc}")
            self.send(session, {
                "type": "auth_result",
                "ok": False,
                "message": "注册失败，服务器保存数据异常。",
                "action": "register",
            })
            return

        self.send(session, {"type": "auth_result", "ok": ok, "message": result_text, "action": "register"})
        if ok:
            self.log(f"新用户注册: {nickname}")

    def handle_login(self, session: ClientSession, message: dict[str, Any]) -> None:
        nickname = sanitize_text(message.get("nickname"), 20)
        password = str(message.get("password", ""))

        if not nickname or not password:
            self.send(session, {"type": "auth_result", "ok": False, "message": "昵称和密码不能为空。", "action": "login"})
            return

        with self.lock:
            if nickname in self.user_sessions:
                self.send(session, {"type": "auth_result", "ok": False, "message": "该账号已在其他客户端登录。", "action": "login"})
                return

        ok, result_text = self.store.verify_login(nickname, password)
        if not ok:
            self.send(session, {"type": "auth_result", "ok": False, "message": result_text, "action": "login"})
            return

        with self.lock:
            session.nickname = nickname
            session.authenticated = True
            self.user_sessions[nickname] = session

        self.send(session, {
            "type": "auth_result",
            "ok": True,
            "message": result_text,
            "action": "login",
            "nickname": nickname,
        })
        self.send_state_snapshot(nickname)
        self.log(f"用户登录: {nickname}")
        self.publish_admin_snapshot()

    def send_state_snapshot(self, nickname: str) -> None:
        rating_1v1 = self.store.get_1v1_rating(nickname)
        pair_ratings = self.store.list_pair_ratings_for(nickname)

        with self.lock:
            queue_1v1_players, queue_2v2_players = self.queue_player_counts_locked()
            queue_id = self.user_queue_map.get(nickname)
            queue_mode = ""
            if queue_id:
                entry = self.queue_index.get(queue_id)
                if entry is not None:
                    queue_mode = entry.mode
            in_match = nickname in self.user_active_match
            current_match_payload: dict[str, Any] | None = None
            if in_match:
                match_id = self.user_active_match.get(nickname)
                if match_id:
                    match = self.active_matches.get(match_id)
                    if isinstance(match, dict):
                        current_match_payload = self.match_public_payload(match, nickname)

            payload = {
                "type": "state_snapshot",
                "rating_1v1": {
                    "rating": round(rating_1v1.rating, 2),
                    "games": rating_1v1.games,
                },
                "pair_ratings": pair_ratings,
                "queued_mode": queue_mode,
                "in_match": in_match,
                "online_count": len(self.user_sessions),
                "queue_1v1_count": queue_1v1_players,
                "queue_2v2_count": queue_2v2_players,
                "queue_1v1_players": queue_1v1_players,
                "queue_2v2_players": queue_2v2_players,
                "pending_review_count": len(self.store.get_pending_reviews()),
                "current_match": current_match_payload,
            }

        self.send_to_user(nickname, payload)

    def handle_request_leaderboard(self, session: ClientSession) -> None:
        rows_1v1 = self.store.list_1v1_leaderboard()
        rows_2v2 = self.store.list_2v2_leaderboard()
        self.send(session, {
            "type": "leaderboard_snapshot",
            "updated_at": ts_text(),
            "one_v_one": rows_1v1,
            "two_v_two": rows_2v2,
        })

    def is_user_busy_locked(self, nickname: str) -> bool:
        return nickname in self.user_queue_map or nickname in self.user_active_match

    def add_queue_locked(self, entry: QueueEntry) -> None:
        if entry.mode == "1v1":
            self.queue_1v1.append(entry)
        else:
            self.queue_2v2.append(entry)

        self.queue_index[entry.queue_id] = entry
        for name in entry.players:
            self.user_queue_map[name] = entry.queue_id

        if entry.mode == "2v2" and len(entry.players) == 2:
            captain = entry.captain
            mate = entry.players[1] if entry.players[0] == captain else entry.players[0]
            self.locked_teammate[mate] = captain

    def remove_queue_locked(self, queue_id: str) -> QueueEntry | None:
        entry = self.queue_index.pop(queue_id, None)
        if entry is None:
            return None

        target_list = self.queue_1v1 if entry.mode == "1v1" else self.queue_2v2
        target_list[:] = [item for item in target_list if item.queue_id != queue_id]

        for name in entry.players:
            mapped = self.user_queue_map.get(name)
            if mapped == queue_id:
                self.user_queue_map.pop(name, None)

        if entry.mode == "2v2" and len(entry.players) == 2:
            captain = entry.captain
            mate = entry.players[1] if entry.players[0] == captain else entry.players[0]
            if self.locked_teammate.get(mate) == captain:
                self.locked_teammate.pop(mate, None)

        return entry

    def handle_queue_start(self, session: ClientSession, message: dict[str, Any]) -> None:
        nickname = session.nickname
        mode = sanitize_text(message.get("mode"), 8)

        with self.lock:
            if self.is_user_busy_locked(nickname):
                self.send(session, {"type": "queue_status", "ok": False, "message": "你当前已在匹配或比赛中。"})
                return

            if mode == "1v1":
                commanders = normalize_commander_list(message.get("commanders"), 3)
                if len(commanders) != 3:
                    self.send(session, {"type": "queue_status", "ok": False, "message": "1v1 必须选择 3 位有效指挥官。"})
                    return

                rating = self.store.get_1v1_rating(nickname).rating
                entry = QueueEntry(
                    queue_id=uuid.uuid4().hex,
                    mode="1v1",
                    captain=nickname,
                    players=[nickname],
                    rating=rating,
                    join_time=now_ts(),
                    latency_ms=session.latency_ms,
                    commanders_by_player={nickname: commanders},
                )
                self.add_queue_locked(entry)
                self.send(session, {
                    "type": "queue_status",
                    "ok": True,
                    "queued": True,
                    "mode": "1v1",
                    "message": "已进入 1v1 匹配队列。",
                })

            elif mode == "2v2":
                teammate = sanitize_text(message.get("teammate"), 20)
                if not teammate:
                    self.send(session, {"type": "queue_status", "ok": False, "message": "请输入 2v2 队友昵称。"})
                    return
                if teammate == nickname:
                    self.send(session, {"type": "queue_status", "ok": False, "message": "2v2 队友不能是自己。"})
                    return
                if not self.store.user_exists(teammate):
                    self.send(session, {"type": "queue_status", "ok": False, "message": "该队友昵称不存在。"})
                    return

                mate_session = self.user_sessions.get(teammate)
                if mate_session is None:
                    self.send(session, {"type": "queue_status", "ok": False, "message": "该队友当前未在线，无法发起 2v2 匹配。"})
                    return
                if self.is_user_busy_locked(teammate):
                    self.send(session, {"type": "queue_status", "ok": False, "message": "该队友当前在匹配或比赛中。"})
                    return
                if teammate in self.locked_teammate:
                    self.send(session, {"type": "queue_status", "ok": False, "message": "该队友已被其他人临时绑定 2v2 匹配。"})
                    return

                rating = self.store.get_pair_rating(nickname, teammate).rating
                avg_latency = int((session.latency_ms + mate_session.latency_ms) / 2)

                entry = QueueEntry(
                    queue_id=uuid.uuid4().hex,
                    mode="2v2",
                    captain=nickname,
                    players=[nickname, teammate],
                    rating=rating,
                    join_time=now_ts(),
                    latency_ms=avg_latency,
                )
                self.add_queue_locked(entry)

                self.send(session, {
                    "type": "queue_status",
                    "ok": True,
                    "queued": True,
                    "mode": "2v2",
                    "message": f"已与 {teammate} 绑定，进入 2v2 匹配队列。",
                })
                self.send_to_user(teammate, {
                    "type": "info",
                    "message": f"你已被 {nickname} 绑定到 2v2 匹配中，当前无法单独匹配。",
                })

            else:
                self.send(session, {"type": "queue_status", "ok": False, "message": "未知匹配模式。"})
                return

        self.log(f"开始匹配: {nickname} | 模式 {mode}")
        self.send_state_snapshot(nickname)
        if mode == "2v2":
            mate = sanitize_text(message.get("teammate"), 20)
            if mate:
                self.send_state_snapshot(mate)
        self.publish_admin_snapshot()

    def handle_queue_cancel(self, nickname: str) -> None:
        affected: list[str] = []
        with self.lock:
            queue_id = self.user_queue_map.get(nickname)
            if not queue_id:
                self.send_to_user(nickname, {"type": "queue_status", "ok": False, "message": "当前不在匹配队列中。"})
                return

            entry = self.remove_queue_locked(queue_id)
            if entry is None:
                self.send_to_user(nickname, {"type": "queue_status", "ok": False, "message": "取消匹配失败。"})
                return
            affected = entry.players[:]

        for name in affected:
            self.send_to_user(name, {
                "type": "queue_status",
                "ok": True,
                "queued": False,
                "message": "已取消匹配。",
            })
            self.send_state_snapshot(name)

        self.log(f"取消匹配: {nickname}")
        self.publish_admin_snapshot()

    def current_window(self, waited_seconds: float) -> int:
        steps = int(waited_seconds // MATCH_EXPAND_SECONDS)
        return min(MATCH_BASE_WINDOW + steps * MATCH_EXPAND_STEP, MATCH_MAX_WINDOW)

    def has_recent_meet_locked(self, team_a: list[str], team_b: list[str]) -> bool:
        for name in team_a:
            old = self.recent_opponents.get(name, [])
            for opp in team_b:
                if opp in old:
                    return True
        return False

    def queue_pair_cost(self, a: QueueEntry, b: QueueEntry, now_value: float) -> float:
        diff = abs(a.rating - b.rating)
        wait_a = now_value - a.join_time
        wait_b = now_value - b.join_time

        wait_penalty = abs(wait_a - wait_b) * 0.2
        latency_penalty = ((a.latency_ms + b.latency_ms) / 2.0) * 0.05
        repeat_penalty = 80.0 if self.has_recent_meet_locked(a.players, b.players) else 0.0
        long_wait_bonus = min(wait_a + wait_b, 120.0) * 0.1

        return diff + wait_penalty + latency_penalty + repeat_penalty - long_wait_bonus

    def matchmaker_loop(self) -> None:
        while self.running and not self.stop_event.is_set():
            created_matches: list[dict[str, Any]] = []

            with self.lock:
                while True:
                    pair = self.pick_best_pair_locked(self.queue_1v1)
                    if pair is None:
                        break
                    left, right = pair
                    self.remove_queue_locked(left.queue_id)
                    self.remove_queue_locked(right.queue_id)
                    created_matches.append(self.create_match_locked(left, right))

                while True:
                    pair = self.pick_best_pair_locked(self.queue_2v2)
                    if pair is None:
                        break
                    left, right = pair
                    self.remove_queue_locked(left.queue_id)
                    self.remove_queue_locked(right.queue_id)
                    created_matches.append(self.create_match_locked(left, right))

            for match in created_matches:
                self.announce_match(match)

            if created_matches:
                self.publish_admin_snapshot()
            time.sleep(1.0)

    def pick_best_pair_locked(self, entries: list[QueueEntry]) -> tuple[QueueEntry, QueueEntry] | None:
        if len(entries) < 2:
            return None

        now_value = now_ts()
        best_pair: tuple[QueueEntry, QueueEntry] | None = None
        best_cost = 1e18

        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                left = entries[i]
                right = entries[j]

                wait_left = now_value - left.join_time
                wait_right = now_value - right.join_time
                window_left = self.current_window(wait_left)
                window_right = self.current_window(wait_right)
                diff = abs(left.rating - right.rating)

                if diff > max(window_left, window_right):
                    continue

                cost = self.queue_pair_cost(left, right, now_value)
                if cost < best_cost:
                    best_cost = cost
                    best_pair = (left, right)

        return best_pair

    def create_match_locked(self, team_a: QueueEntry, team_b: QueueEntry) -> dict[str, Any]:
        mode = team_a.mode
        match_id = uuid.uuid4().hex
        created = now_ts()

        if mode == "1v1":
            map_name = random.choice(MAP_POOL_1V1)
            nick_a = team_a.players[0]
            nick_b = team_b.players[0]
            commanders_a = team_a.commanders_by_player.get(nick_a, COMMANDER_POOL[:3])
            commanders_b = team_b.commanders_by_player.get(nick_b, COMMANDER_POOL[:3])

            match = {
                "match_id": match_id,
                "mode": "1v1",
                "created_at": created,
                "map_name": map_name,
                "state": "bp_ban",
                "deadline": created + BP_SECONDS,
                "teams": {
                    "A": {
                        "players": [nick_a],
                        "captain": nick_a,
                        "commanders": commanders_a,
                    },
                    "B": {
                        "players": [nick_b],
                        "captain": nick_b,
                        "commanders": commanders_b,
                    },
                },
                "bp": {
                    "ban": {"A": None, "B": None},
                    "pick": {"A": None, "B": None},
                    "available": {"A": [], "B": []},
                },
                "chat": [],
                "reports": {
                    "by_user": {},
                    "by_side": {"A": None, "B": None},
                    "side_conflict": False,
                },
            }
        else:
            map_name = random.choice(MAP_POOL_2V2)
            team_a_players = team_a.players[:]
            team_b_players = team_b.players[:]

            match = {
                "match_id": match_id,
                "mode": "2v2",
                "created_at": created,
                "map_name": map_name,
                "state": "team_ban",
                "deadline": created + BP_SECONDS,
                "teams": {
                    "A": {
                        "players": team_a_players,
                        "captain": team_a.captain,
                        "commanders": COMMANDER_POOL[:],
                    },
                    "B": {
                        "players": team_b_players,
                        "captain": team_b.captain,
                        "commanders": COMMANDER_POOL[:],
                    },
                },
                "bp": {
                    "ban": {"A": None, "B": None},
                    "pick": {"A": None, "B": None},
                    "available": {"A": [], "B": []},
                },
                "chat": [],
                "reports": {
                    "by_user": {},
                    "by_side": {"A": None, "B": None},
                    "side_conflict": False,
                },
            }

        self.active_matches[match_id] = match
        for player in match["teams"]["A"]["players"] + match["teams"]["B"]["players"]:
            self.user_active_match[player] = match_id

        return match

    def match_players(self, match: dict[str, Any]) -> list[str]:
        return match["teams"]["A"]["players"] + match["teams"]["B"]["players"]

    def attached_match_players_locked(self, match: dict[str, Any]) -> list[str]:
        match_id = str(match.get("match_id", ""))
        return [
            nickname
            for nickname in self.match_players(match)
            if self.user_active_match.get(nickname) == match_id
        ]

    def release_match_side_locked(self, match: dict[str, Any], side: str) -> list[str]:
        match_id = str(match.get("match_id", ""))
        released: list[str] = []
        players = match.get("teams", {}).get(side, {}).get("players", [])
        for player in players:
            nickname = str(player)
            if self.user_active_match.get(nickname) == match_id:
                self.user_active_match.pop(nickname, None)
                released.append(nickname)
        return released

    def player_side(self, match: dict[str, Any], nickname: str) -> str:
        if nickname in match["teams"]["A"]["players"]:
            return "A"
        if nickname in match["teams"]["B"]["players"]:
            return "B"
        return ""

    def announce_match(self, match: dict[str, Any]) -> None:
        players = self.match_players(match)
        for nickname in players:
            side = self.player_side(match, nickname)
            opp_side = "B" if side == "A" else "A"
            self.send_to_user(nickname, {
                "type": "match_found",
                "match_id": match["match_id"],
                "mode": match["mode"],
                "map_name": match["map_name"],
                "my_side": side,
                "my_team": match["teams"][side]["players"],
                "opponent_team": match["teams"][opp_side]["players"],
                "message": "匹配成功，已创建临时聊天室。",
            })
            self.send_state_snapshot(nickname)

        self.broadcast_match_state(match)
        mode_text = "1v1" if match["mode"] == "1v1" else "2v2"
        self.log(f"匹配成功: {mode_text} | {match['teams']['A']['players']} VS {match['teams']['B']['players']}")

    def match_public_payload(self, match: dict[str, Any], nickname: str) -> dict[str, Any]:
        side = self.player_side(match, nickname)
        return {
            "type": "match_state",
            "match_id": match["match_id"],
            "mode": match["mode"],
            "state": match["state"],
            "deadline": match["deadline"],
            "map_name": match["map_name"],
            "my_side": side,
            "teams": match["teams"],
            "bp": match["bp"],
            "reports": {
                "my_report": match["reports"]["by_user"].get(nickname),
                "side_a_reported": match["reports"]["by_side"]["A"],
                "side_b_reported": match["reports"]["by_side"]["B"],
            },
        }

    def broadcast_match_state(self, match: dict[str, Any]) -> None:
        with self.lock:
            targets = self.attached_match_players_locked(match)
        for nickname in targets:
            self.send_to_user(nickname, self.match_public_payload(match, nickname))

    def phase_monitor_loop(self) -> None:
        while self.running and not self.stop_event.is_set():
            changed_matches: list[dict[str, Any]] = []

            with self.lock:
                now_value = now_ts()
                for match in list(self.active_matches.values()):
                    deadline = match.get("deadline")
                    if not isinstance(deadline, (int, float)):
                        continue
                    if now_value < float(deadline):
                        continue

                    mode = match["mode"]
                    state = match["state"]

                    if mode == "1v1":
                        if state == "bp_ban":
                            if match["bp"]["ban"]["A"] is None:
                                match["bp"]["ban"]["A"] = ""
                            if match["bp"]["ban"]["B"] is None:
                                match["bp"]["ban"]["B"] = ""
                            self.transition_1v1_to_pick_locked(match)
                            changed_matches.append(match)
                        elif state == "bp_pick":
                            self.transition_1v1_to_ready_locked(match, timeout=True)
                            changed_matches.append(match)
                    else:
                        if state == "team_ban":
                            if match["bp"]["ban"]["A"] is None:
                                match["bp"]["ban"]["A"] = []
                            if match["bp"]["ban"]["B"] is None:
                                match["bp"]["ban"]["B"] = []
                            self.transition_2v2_to_pick_locked(match)
                            changed_matches.append(match)
                        elif state == "team_pick":
                            self.transition_2v2_to_ready_locked(match, timeout=True)
                            changed_matches.append(match)

            for match in changed_matches:
                self.broadcast_match_state(match)

            time.sleep(0.25)

    def transition_1v1_to_pick_locked(self, match: dict[str, Any]) -> None:
        ban_a = str(match["bp"]["ban"]["A"] or "")
        ban_b = str(match["bp"]["ban"]["B"] or "")

        pool_a = match["teams"]["A"]["commanders"]
        pool_b = match["teams"]["B"]["commanders"]

        available_a = [x for x in pool_a if x != ban_b]
        available_b = [x for x in pool_b if x != ban_a]

        if not available_a:
            available_a = pool_a[:]
        if not available_b:
            available_b = pool_b[:]

        match["bp"]["available"]["A"] = available_a
        match["bp"]["available"]["B"] = available_b
        match["state"] = "bp_pick"
        match["deadline"] = now_ts() + BP_SECONDS

    def transition_1v1_to_ready_locked(self, match: dict[str, Any], timeout: bool) -> None:
        available_a = match["bp"]["available"]["A"] or match["teams"]["A"]["commanders"]
        available_b = match["bp"]["available"]["B"] or match["teams"]["B"]["commanders"]

        pick_a = match["bp"]["pick"]["A"]
        pick_b = match["bp"]["pick"]["B"]

        if not pick_a:
            match["bp"]["pick"]["A"] = random.choice(available_a)
        if not pick_b:
            match["bp"]["pick"]["B"] = random.choice(available_b)

        match["state"] = "ready"
        match["deadline"] = None
        if timeout:
            self.log(f"1v1 BP 超时自动完成: {match['match_id']}")

    def transition_2v2_to_pick_locked(self, match: dict[str, Any]) -> None:
        ban_a = match["bp"]["ban"]["A"] or []
        ban_b = match["bp"]["ban"]["B"] or []

        pool_a = match["teams"]["A"]["commanders"]
        pool_b = match["teams"]["B"]["commanders"]

        available_a = [x for x in pool_a if x not in ban_b]
        available_b = [x for x in pool_b if x not in ban_a]

        if len(available_a) < 2:
            available_a = pool_a[:]
        if len(available_b) < 2:
            available_b = pool_b[:]

        match["bp"]["available"]["A"] = available_a
        match["bp"]["available"]["B"] = available_b
        match["state"] = "team_pick"
        match["deadline"] = now_ts() + BP_SECONDS

    def _default_two(self, pool: list[str]) -> list[str]:
        if len(pool) >= 2:
            return [pool[0], pool[1]]
        if len(pool) == 1:
            return [pool[0], pool[0]]
        return COMMANDER_POOL[:2]

    def transition_2v2_to_ready_locked(self, match: dict[str, Any], timeout: bool) -> None:
        available_a = match["bp"]["available"]["A"] or match["teams"]["A"]["commanders"]
        available_b = match["bp"]["available"]["B"] or match["teams"]["B"]["commanders"]

        pick_a = match["bp"]["pick"]["A"]
        pick_b = match["bp"]["pick"]["B"]

        if not isinstance(pick_a, list) or len(pick_a) != 2:
            match["bp"]["pick"]["A"] = self._default_two(available_a)
        if not isinstance(pick_b, list) or len(pick_b) != 2:
            match["bp"]["pick"]["B"] = self._default_two(available_b)

        match["state"] = "ready"
        match["deadline"] = None
        if timeout:
            self.log(f"2v2 BP 超时自动完成: {match['match_id']}")

    def handle_bp_submit(self, session: ClientSession, message: dict[str, Any]) -> None:
        nickname = session.nickname

        with self.lock:
            match_id = self.user_active_match.get(nickname)
            if not match_id:
                self.send_to_user(nickname, {"type": "bp_result", "ok": False, "message": "你当前不在比赛中。"})
                return
            match = self.active_matches.get(match_id)
            if match is None:
                self.send_to_user(nickname, {"type": "bp_result", "ok": False, "message": "比赛状态异常，请稍后重试。"})
                return

            side = self.player_side(match, nickname)
            if not side:
                self.send_to_user(nickname, {"type": "bp_result", "ok": False, "message": "你不在当前比赛中。"})
                return

            mode = match["mode"]
            state = match["state"]

            if mode == "1v1":
                if state == "bp_ban":
                    if match["bp"]["ban"][side] is not None:
                        self.send_to_user(nickname, {"type": "bp_result", "ok": False, "message": "你已提交过 Ban。"})
                        return
                    choice = sanitize_text(message.get("choice"), 20)
                    if choice and choice not in match["teams"]["B" if side == "A" else "A"]["commanders"]:
                        self.send_to_user(nickname, {"type": "bp_result", "ok": False, "message": "Ban 目标无效。"})
                        return
                    match["bp"]["ban"][side] = choice

                    if match["bp"]["ban"]["A"] is not None and match["bp"]["ban"]["B"] is not None:
                        self.transition_1v1_to_pick_locked(match)

                elif state == "bp_pick":
                    if match["bp"]["pick"][side] is not None:
                        self.send_to_user(nickname, {"type": "bp_result", "ok": False, "message": "你已提交过 Pick。"})
                        return
                    choice = sanitize_text(message.get("choice"), 20)
                    available = match["bp"]["available"][side]
                    if choice.lower() == "random":
                        choice = random.choice(available)
                    if choice not in available:
                        self.send_to_user(nickname, {"type": "bp_result", "ok": False, "message": "Pick 目标无效。"})
                        return

                    match["bp"]["pick"][side] = choice
                    if match["bp"]["pick"]["A"] is not None and match["bp"]["pick"]["B"] is not None:
                        self.transition_1v1_to_ready_locked(match, timeout=False)
                else:
                    self.send_to_user(nickname, {"type": "bp_result", "ok": False, "message": "当前阶段不接受 BP 操作。"})
                    return

            else:
                captain = sanitize_text(match["teams"].get(side, {}).get("captain"), 20)
                if captain and nickname != captain:
                    self.send_to_user(
                        nickname,
                        {"type": "bp_result", "ok": False, "message": f"2v2 BP 仅允许发起人 {captain} 提交。"},
                    )
                    return

                if state == "team_ban":
                    if match["bp"]["ban"][side] is not None:
                        self.send_to_user(nickname, {"type": "bp_result", "ok": False, "message": "你方已提交过 Ban。"})
                        return
                    bans = normalize_commander_list(message.get("choices"), 3)
                    opp_pool = match["teams"]["B" if side == "A" else "A"]["commanders"]
                    for item in bans:
                        if item not in opp_pool:
                            self.send_to_user(nickname, {"type": "bp_result", "ok": False, "message": "Ban 目标无效。"})
                            return
                    match["bp"]["ban"][side] = bans
                    if match["bp"]["ban"]["A"] is not None and match["bp"]["ban"]["B"] is not None:
                        self.transition_2v2_to_pick_locked(match)

                elif state == "team_pick":
                    if match["bp"]["pick"][side] is not None:
                        self.send_to_user(nickname, {"type": "bp_result", "ok": False, "message": "你方已提交过 Pick。"})
                        return
                    picks = normalize_commander_list(message.get("choices"), 2)
                    available = match["bp"]["available"][side]
                    if len(picks) != 2:
                        self.send_to_user(nickname, {"type": "bp_result", "ok": False, "message": "请选择 2 个指挥官。"})
                        return
                    for item in picks:
                        if item not in available:
                            self.send_to_user(nickname, {"type": "bp_result", "ok": False, "message": "Pick 目标无效。"})
                            return
                    match["bp"]["pick"][side] = picks
                    if match["bp"]["pick"]["A"] is not None and match["bp"]["pick"]["B"] is not None:
                        self.transition_2v2_to_ready_locked(match, timeout=False)
                else:
                    self.send_to_user(nickname, {"type": "bp_result", "ok": False, "message": "当前阶段不接受 BP 操作。"})
                    return

        self.send_to_user(nickname, {"type": "bp_result", "ok": True, "message": "提交成功。"})
        self.broadcast_match_state(match)

    def handle_chat_send(self, session: ClientSession, message: dict[str, Any]) -> None:
        nickname = session.nickname
        text = sanitize_text(message.get("text"), 200)
        if not text:
            return

        with self.lock:
            match_id = self.user_active_match.get(nickname)
            if not match_id:
                self.send_to_user(nickname, {"type": "chat_result", "ok": False, "message": "当前无可用聊天室。"})
                return
            match = self.active_matches.get(match_id)
            if match is None:
                self.send_to_user(nickname, {"type": "chat_result", "ok": False, "message": "当前无可用聊天室。"})
                return
            line = {
                "time": ts_text(),
                "sender": nickname,
                "text": text,
            }
            match["chat"].append(line)
            if len(match["chat"]) > 200:
                match["chat"] = match["chat"][-200:]
            players = self.match_players(match)

        for player in players:
            self.send_to_user(player, {
                "type": "chat_message",
                "match_id": match_id,
                "time": line["time"],
                "sender": nickname,
                "text": text,
            })

    def handle_report_result(self, session: ClientSession, message: dict[str, Any]) -> None:
        nickname = session.nickname
        winner_pick = sanitize_text(message.get("winner"), 10).lower()
        if winner_pick not in ("self", "opponent"):
            self.send_to_user(nickname, {"type": "report_result", "ok": False, "message": "无效结果选项。"})
            return

        released_players: list[str] = []
        teammate_released = False

        with self.lock:
            match_id = self.user_active_match.get(nickname)
            if not match_id:
                self.send_to_user(nickname, {"type": "report_result", "ok": False, "message": "当前无可汇报比赛。"})
                return

            match = self.active_matches.get(match_id)
            if match is None:
                self.send_to_user(nickname, {"type": "report_result", "ok": False, "message": "比赛状态异常。"})
                return

            if match["state"] != "ready":
                self.send_to_user(nickname, {"type": "report_result", "ok": False, "message": "当前比赛尚未完成 BP，不能汇报。"})
                return

            side = self.player_side(match, nickname)
            if not side:
                self.send_to_user(nickname, {"type": "report_result", "ok": False, "message": "你不在当前比赛中。"})
                return

            winner_side = side if winner_pick == "self" else ("B" if side == "A" else "A")
            match["reports"]["by_user"][nickname] = winner_side

            old_side_report = match["reports"]["by_side"][side]
            if old_side_report is None:
                match["reports"]["by_side"][side] = winner_side
                released_players = self.release_match_side_locked(match, side)
                teammate_released = len(released_players) > 1
            elif old_side_report != winner_side:
                match["reports"]["side_conflict"] = True

            side_a = match["reports"]["by_side"]["A"]
            side_b = match["reports"]["by_side"]["B"]

            players = self.match_players(match)

            # 两边都汇报后进行自动判定
            if side_a is not None and side_b is not None:
                self.active_matches.pop(match_id, None)
                for player in players:
                    if self.user_active_match.get(player) == match_id:
                        self.user_active_match.pop(player, None)

                if match["reports"]["side_conflict"] or side_a != side_b:
                    pending = {
                        "match_id": match_id,
                        "mode": match["mode"],
                        "map_name": match["map_name"],
                        "teams": match["teams"],
                        "bp": match["bp"],
                        "reports": match["reports"],
                        "created_at": match["created_at"],
                        "closed_at": now_ts(),
                        "status": "pending_review",
                    }
                    self.store.add_pending_review(pending)
                    self.store.append_match_log(pending)

                    for player in players:
                        self.send_to_user(player, {
                            "type": "pending_review",
                            "match_id": match_id,
                            "message": "双方上报结果不一致，已提交服务器管理员判定。",
                        })
                        self.send_state_snapshot(player)

                    self.log(f"比赛待审: {match_id}")
                    self.publish_admin_snapshot()
                    return

                # 自动一致，直接结算
                winner_side_final = side_a

        # 在锁外结算，减少阻塞
        if "winner_side_final" in locals():
            self.resolve_match(match, winner_side_final, admin=False)
            return

        if released_players:
            for player in released_players:
                if player == nickname:
                    self.send_to_user(player, {
                        "type": "report_result",
                        "ok": True,
                        "message": "结果已提交，你方可开始下一把匹配，等待对手上报核实。",
                    })
                else:
                    self.send_to_user(player, {
                        "type": "info",
                        "message": "你方结果已上报，可开始下一把匹配，最终结果仍以双方核实为准。",
                    })
                self.send_state_snapshot(player)

            if teammate_released:
                self.log(f"比赛已释放一方继续匹配: {match['match_id']} | 队伍 {side}")
            else:
                self.log(f"比赛已释放汇报方继续匹配: {match['match_id']} | 玩家 {nickname}")
            self.publish_admin_snapshot()
        else:
            self.send_to_user(nickname, {
                "type": "report_result",
                "ok": True,
                "message": "结果已提交，等待对手上报。",
            })

        self.broadcast_match_state(match)

    def update_recent_opponents(self, team_a: list[str], team_b: list[str]) -> None:
        with self.lock:
            for left in team_a:
                bag = self.recent_opponents.setdefault(left, [])
                for right in team_b:
                    bag.append(right)
                if len(bag) > 12:
                    self.recent_opponents[left] = bag[-12:]

            for left in team_b:
                bag = self.recent_opponents.setdefault(left, [])
                for right in team_a:
                    bag.append(right)
                if len(bag) > 12:
                    self.recent_opponents[left] = bag[-12:]

    def apply_rating_for_match(self, match_record: dict[str, Any], winner_side: str) -> dict[str, Any]:
        mode = str(match_record.get("mode", ""))
        teams = match_record.get("teams", {})
        team_a_players = list(teams.get("A", {}).get("players", []))
        team_b_players = list(teams.get("B", {}).get("players", []))

        if mode == "1v1":
            if len(team_a_players) != 1 or len(team_b_players) != 1:
                return {}

            a_name = team_a_players[0]
            b_name = team_b_players[0]
            rating_a = self.store.get_1v1_rating(a_name)
            rating_b = self.store.get_1v1_rating(b_name)
            score_a = 1.0 if winner_side == "A" else 0.0

            delta_a, delta_b = update_elo(rating_a, rating_b, score_a)
            self.store.set_1v1_rating(a_name, rating_a)
            self.store.set_1v1_rating(b_name, rating_b)

            return {
                "mode": "1v1",
                "A": {
                    "players": [a_name],
                    "delta": round(delta_a, 2),
                    "new_rating": round(rating_a.rating, 2),
                },
                "B": {
                    "players": [b_name],
                    "delta": round(delta_b, 2),
                    "new_rating": round(rating_b.rating, 2),
                },
            }

        if mode == "2v2":
            if len(team_a_players) != 2 or len(team_b_players) != 2:
                return {}

            a_key = pair_key(team_a_players[0], team_a_players[1])
            b_key = pair_key(team_b_players[0], team_b_players[1])

            a_rating = self.store.get_pair_rating(team_a_players[0], team_a_players[1])
            b_rating = self.store.get_pair_rating(team_b_players[0], team_b_players[1])
            score_a = 1.0 if winner_side == "A" else 0.0

            delta_a, delta_b = update_elo(a_rating, b_rating, score_a)
            self.store.set_pair_rating(team_a_players[0], team_a_players[1], a_rating)
            self.store.set_pair_rating(team_b_players[0], team_b_players[1], b_rating)

            return {
                "mode": "2v2",
                "A": {
                    "players": team_a_players,
                    "pair_key": a_key,
                    "delta": round(delta_a, 2),
                    "new_rating": round(a_rating.rating, 2),
                },
                "B": {
                    "players": team_b_players,
                    "pair_key": b_key,
                    "delta": round(delta_b, 2),
                    "new_rating": round(b_rating.rating, 2),
                },
            }

        return {}

    def resolve_match(self, match: dict[str, Any], winner_side: str, admin: bool) -> None:
        players = self.match_players(match)
        team_a = match["teams"]["A"]["players"]
        team_b = match["teams"]["B"]["players"]

        rating_info = self.apply_rating_for_match(match, winner_side)

        result_record = {
            "match_id": match["match_id"],
            "mode": match["mode"],
            "map_name": match["map_name"],
            "teams": match["teams"],
            "bp": match["bp"],
            "reports": match["reports"],
            "winner_side": winner_side,
            "resolved_at": now_ts(),
            "resolved_by": "admin" if admin else "auto",
            "rating": rating_info,
            "status": "resolved",
        }
        self.store.append_match_log(result_record)

        self.update_recent_opponents(team_a, team_b)

        for nickname in players:
            side = self.player_side(match, nickname)
            my_outcome = "win" if side == winner_side else "lose"
            side_key = side
            delta = 0.0
            new_rating = 0.0
            if isinstance(rating_info, dict):
                row = rating_info.get(side_key)
                if isinstance(row, dict):
                    delta = float(row.get("delta", 0.0))
                    new_rating = float(row.get("new_rating", 0.0))

            self.send_to_user(nickname, {
                "type": "match_resolved",
                "match_id": match["match_id"],
                "mode": match["mode"],
                "winner_side": winner_side,
                "my_outcome": my_outcome,
                "delta": delta,
                "new_rating": new_rating,
                "message": "比赛结果已确认，天梯分已更新。",
            })
            self.send_state_snapshot(nickname)

        self.log(f"比赛结算: {match['match_id']} | 胜方 {winner_side}")
        self.publish_admin_snapshot()

    def resolve_pending_match(self, match_id: str, winner_side: str) -> tuple[bool, str]:
        winner_side = winner_side.upper().strip()
        if winner_side not in ("A", "B"):
            return False, "胜方参数无效。"

        pending = self.store.remove_pending_review(match_id)
        if pending is None:
            return False, "未找到该待审比赛。"

        rating_info = self.apply_rating_for_match(pending, winner_side)
        pending["winner_side"] = winner_side
        pending["resolved_at"] = now_ts()
        pending["resolved_by"] = "admin"
        pending["rating"] = rating_info
        pending["status"] = "resolved_by_admin"
        self.store.append_match_log(pending)

        players = list(pending.get("teams", {}).get("A", {}).get("players", [])) + list(
            pending.get("teams", {}).get("B", {}).get("players", [])
        )
        for nickname in players:
            side = "A" if nickname in pending.get("teams", {}).get("A", {}).get("players", []) else "B"
            my_outcome = "win" if side == winner_side else "lose"
            side_info = rating_info.get(side, {}) if isinstance(rating_info, dict) else {}
            self.send_to_user(nickname, {
                "type": "match_resolved",
                "match_id": match_id,
                "mode": pending.get("mode"),
                "winner_side": winner_side,
                "my_outcome": my_outcome,
                "delta": float(side_info.get("delta", 0.0)),
                "new_rating": float(side_info.get("new_rating", 0.0)),
                "message": "管理员已判定该场比赛结果。",
            })
            self.send_state_snapshot(nickname)

        self.log(f"管理员判定比赛: {match_id} -> {winner_side}")
        self.publish_admin_snapshot()
        return True, "判定成功。"

    def remove_client(self, session: ClientSession) -> None:
        nickname = ""
        with self.lock:
            if session.conn not in self.clients:
                return

            self.clients.pop(session.conn, None)
            if session.authenticated and session.nickname:
                nickname = session.nickname
                self.user_sessions.pop(session.nickname, None)

        if nickname:
            self.handle_queue_cancel(nickname)
            self.log(f"客户端离线: {nickname}")
            self.publish_admin_snapshot()
        else:
            self.log(f"未登录客户端离线: {session.addr[0]}")

        try:
            session.conn.close()
        except OSError:
            pass


class ServerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Icelin 天梯 - 服务器端")
        self.root.geometry("1220x840")
        self.root.option_add("*Font", ("Microsoft YaHei UI", 13))

        self.host_var = tk.StringVar(value=DEFAULT_HOST)
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self.info_var = tk.StringVar(value="在线玩家:0 | 1v1匹配玩家:0 | 2v2匹配玩家:0 | 活动比赛:0")

        self.filter_start_var = tk.StringVar(value="")
        self.filter_end_var = tk.StringVar(value="")
        self.filter_mode_var = tk.StringVar(value="全部")
        self.filter_player_var = tk.StringVar(value="")

        self.event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.server = LadderServer(self.event_queue)

        self.pending_id_by_index: dict[int, str] = {}
        self.pending_cache: list[dict[str, Any]] = []

        self.leaderboard_window: tk.Toplevel | None = None
        self.server_lb_1v1_tree: ttk.Treeview | None = None
        self.server_lb_2v2_tree: ttk.Treeview | None = None
        self.server_lb_updated_var = tk.StringVar(value="更新时间: -")

        self.history_window: tk.Toplevel | None = None
        self.history_tree: ttk.Treeview | None = None
        self.history_updated_var = tk.StringVar(value="最近记录: -")

        self.build_ui()
        self.update_buttons()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.poll_events)

    def build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        top = ttk.LabelFrame(main, text="服务配置")
        top.pack(fill=tk.X, padx=2, pady=4)

        ttk.Label(top, text="监听地址:").grid(row=0, column=0, padx=6, pady=8, sticky=tk.W)
        self.host_entry = ttk.Entry(top, textvariable=self.host_var, width=34)
        self.host_entry.grid(row=0, column=1, padx=6, pady=8, sticky=tk.W)

        ttk.Label(top, text="端口:").grid(row=0, column=2, padx=6, pady=8, sticky=tk.W)
        self.port_entry = ttk.Entry(top, textvariable=self.port_var, width=10)
        self.port_entry.grid(row=0, column=3, padx=6, pady=8, sticky=tk.W)

        self.start_button = ttk.Button(top, text="启动服务器", command=self.start_server)
        self.start_button.grid(row=0, column=4, padx=6, pady=8)

        self.stop_button = ttk.Button(top, text="停止服务器", command=self.stop_server)
        self.stop_button.grid(row=0, column=5, padx=6, pady=8)

        self.show_leaderboard_button = ttk.Button(top, text="排行榜", command=self.show_leaderboard_window)
        self.show_leaderboard_button.grid(row=0, column=6, padx=6, pady=8)

        self.show_history_button = ttk.Button(top, text="比赛记录", command=self.show_history_window)
        self.show_history_button.grid(row=0, column=7, padx=6, pady=8)

        ttk.Label(top, textvariable=self.info_var, foreground="#1a4f9e").grid(
            row=1,
            column=0,
            columnspan=8,
            padx=6,
            pady=(0, 8),
            sticky=tk.W,
        )

        center = ttk.Frame(main)
        center.pack(fill=tk.BOTH, expand=True)

        pending_frame = ttk.LabelFrame(center, text="待管理员判定的比赛")
        pending_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4), pady=4)

        filter_frame = ttk.LabelFrame(pending_frame, text="筛选")
        filter_frame.pack(fill=tk.X, padx=8, pady=(8, 4))

        ttk.Label(filter_frame, text="开始时间:").grid(row=0, column=0, padx=4, pady=4, sticky=tk.W)
        self.filter_start_entry = ttk.Entry(filter_frame, textvariable=self.filter_start_var, width=20)
        self.filter_start_entry.grid(row=0, column=1, padx=4, pady=4, sticky=tk.W)

        ttk.Label(filter_frame, text="结束时间:").grid(row=0, column=2, padx=4, pady=4, sticky=tk.W)
        self.filter_end_entry = ttk.Entry(filter_frame, textvariable=self.filter_end_var, width=20)
        self.filter_end_entry.grid(row=0, column=3, padx=4, pady=4, sticky=tk.W)

        ttk.Label(filter_frame, text="模式:").grid(row=1, column=0, padx=4, pady=4, sticky=tk.W)
        self.filter_mode_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.filter_mode_var,
            values=["全部", "1v1", "2v2"],
            width=10,
            state="readonly",
        )
        self.filter_mode_combo.grid(row=1, column=1, padx=4, pady=4, sticky=tk.W)

        ttk.Label(filter_frame, text="玩家昵称:").grid(row=1, column=2, padx=4, pady=4, sticky=tk.W)
        self.filter_player_entry = ttk.Entry(filter_frame, textvariable=self.filter_player_var, width=20)
        self.filter_player_entry.grid(row=1, column=3, padx=4, pady=4, sticky=tk.W)

        self.apply_filter_button = ttk.Button(filter_frame, text="应用筛选", command=self.apply_filters)
        self.apply_filter_button.grid(row=0, column=4, rowspan=2, padx=6, pady=4)

        self.clear_filter_button = ttk.Button(filter_frame, text="清空筛选", command=self.clear_filters)
        self.clear_filter_button.grid(row=0, column=5, rowspan=2, padx=6, pady=4)

        ttk.Label(
            filter_frame,
            text="时间格式: YYYY-MM-DD 或 YYYY-MM-DD HH:MM[:SS]",
            foreground="#666666",
        ).grid(row=2, column=0, columnspan=6, padx=4, pady=(0, 4), sticky=tk.W)

        self.pending_listbox = tk.Listbox(pending_frame)
        self.pending_listbox.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 8))

        pending_actions = ttk.Frame(pending_frame)
        pending_actions.pack(fill=tk.X, padx=8, pady=(0, 8))

        self.win_a_button = ttk.Button(pending_actions, text="判定 A 队获胜", command=self.resolve_a)
        self.win_a_button.pack(side=tk.LEFT, padx=(0, 8))

        self.win_b_button = ttk.Button(pending_actions, text="判定 B 队获胜", command=self.resolve_b)
        self.win_b_button.pack(side=tk.LEFT, padx=(0, 8))

        self.refresh_button = ttk.Button(pending_actions, text="刷新", command=self.refresh_pending_from_store)
        self.refresh_button.pack(side=tk.LEFT)

        log_frame = ttk.LabelFrame(center, text="服务器日志")
        log_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0), pady=4)

        self.log_text = tk.Text(log_frame, state=tk.DISABLED, font=("Consolas", 12))
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    def append_log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{stamp}] {text}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def start_server(self) -> None:
        host = self.host_var.get().strip()
        if not host:
            messagebox.showwarning("提示", "监听地址不能为空。")
            return

        try:
            port = parse_port(self.port_var.get().strip())
        except Exception:
            messagebox.showwarning("提示", "端口不合法，请输入 1-65535。")
            return

        ok, text = self.server.start(host, port)
        self.append_log(text)
        self.update_buttons()

    def stop_server(self) -> None:
        self.server.stop()
        self.update_buttons()

    def update_buttons(self) -> None:
        running = self.server.running
        self.start_button.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_button.configure(state=tk.NORMAL if running else tk.DISABLED)

        self.host_entry.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.port_entry.configure(state=tk.DISABLED if running else tk.NORMAL)

    def _clear_tree(self, tree: ttk.Treeview) -> None:
        for item in tree.get_children():
            tree.delete(item)

    def show_leaderboard_window(self) -> None:
        if self.leaderboard_window is not None and self.leaderboard_window.winfo_exists():
            self.refresh_server_leaderboard()
            self.leaderboard_window.lift()
            return

        win = tk.Toplevel(self.root)
        self.leaderboard_window = win
        win.title("全服排行榜")
        win.geometry("840x560")

        top_row = ttk.Frame(win, padding=10)
        top_row.pack(fill=tk.X)
        ttk.Label(top_row, textvariable=self.server_lb_updated_var, foreground="#1a4f9e").pack(side=tk.LEFT)
        ttk.Button(top_row, text="刷新", command=self.refresh_server_leaderboard).pack(side=tk.RIGHT)

        notebook = ttk.Notebook(win)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        tab_1v1 = ttk.Frame(notebook)
        tab_2v2 = ttk.Frame(notebook)
        notebook.add(tab_1v1, text="1v1")
        notebook.add(tab_2v2, text="2v2")

        tree_1v1 = ttk.Treeview(tab_1v1, columns=("rank", "nickname", "rating", "games"), show="headings")
        tree_1v1.heading("rank", text="#")
        tree_1v1.heading("nickname", text="玩家")
        tree_1v1.heading("rating", text="分数")
        tree_1v1.heading("games", text="局数")
        tree_1v1.column("rank", width=70, anchor=tk.CENTER)
        tree_1v1.column("nickname", width=260, anchor=tk.W)
        tree_1v1.column("rating", width=140, anchor=tk.E)
        tree_1v1.column("games", width=140, anchor=tk.E)

        scroll_1v1 = ttk.Scrollbar(tab_1v1, orient=tk.VERTICAL, command=tree_1v1.yview)
        tree_1v1.configure(yscrollcommand=scroll_1v1.set)
        tree_1v1.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_1v1.pack(side=tk.RIGHT, fill=tk.Y)

        tree_2v2 = ttk.Treeview(tab_2v2, columns=("rank", "pair", "rating", "games"), show="headings")
        tree_2v2.heading("rank", text="#")
        tree_2v2.heading("pair", text="组合")
        tree_2v2.heading("rating", text="组合分")
        tree_2v2.heading("games", text="局数")
        tree_2v2.column("rank", width=70, anchor=tk.CENTER)
        tree_2v2.column("pair", width=360, anchor=tk.W)
        tree_2v2.column("rating", width=140, anchor=tk.E)
        tree_2v2.column("games", width=140, anchor=tk.E)

        scroll_2v2 = ttk.Scrollbar(tab_2v2, orient=tk.VERTICAL, command=tree_2v2.yview)
        tree_2v2.configure(yscrollcommand=scroll_2v2.set)
        tree_2v2.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_2v2.pack(side=tk.RIGHT, fill=tk.Y)

        self.server_lb_1v1_tree = tree_1v1
        self.server_lb_2v2_tree = tree_2v2

        def close_window() -> None:
            self.leaderboard_window = None
            self.server_lb_1v1_tree = None
            self.server_lb_2v2_tree = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close_window)
        self.refresh_server_leaderboard()

    def refresh_server_leaderboard(self) -> None:
        if self.server_lb_1v1_tree is None or self.server_lb_2v2_tree is None:
            return

        rows_1v1 = self.server.store.list_1v1_leaderboard()
        rows_2v2 = self.server.store.list_2v2_leaderboard()

        self._clear_tree(self.server_lb_1v1_tree)
        self._clear_tree(self.server_lb_2v2_tree)

        for i, row in enumerate(rows_1v1, start=1):
            nickname = str(row.get("nickname", ""))
            try:
                rating = float(row.get("rating", 0.0))
            except (TypeError, ValueError):
                rating = 0.0
            try:
                games = int(row.get("games", 0))
            except (TypeError, ValueError):
                games = 0
            self.server_lb_1v1_tree.insert("", tk.END, values=(i, nickname, f"{rating:.2f}", games))

        for i, row in enumerate(rows_2v2, start=1):
            players = row.get("players", [])
            pair_name = str(row.get("pair_key", ""))
            if isinstance(players, list) and len(players) == 2:
                pair_name = f"{players[0]}/{players[1]}"
            try:
                rating = float(row.get("rating", 0.0))
            except (TypeError, ValueError):
                rating = 0.0
            try:
                games = int(row.get("games", 0))
            except (TypeError, ValueError):
                games = 0
            self.server_lb_2v2_tree.insert("", tk.END, values=(i, pair_name, f"{rating:.2f}", games))

        self.server_lb_updated_var.set(
            f"更新时间: {ts_text()} | 1v1 {len(rows_1v1)} 条 | 2v2 {len(rows_2v2)} 条"
        )

    def show_history_window(self) -> None:
        if self.history_window is not None and self.history_window.winfo_exists():
            self.refresh_history_window()
            self.history_window.lift()
            return

        win = tk.Toplevel(self.root)
        self.history_window = win
        win.title("比赛记录历史")
        win.geometry("1280x620")

        top_row = ttk.Frame(win, padding=10)
        top_row.pack(fill=tk.X)
        ttk.Label(top_row, textvariable=self.history_updated_var, foreground="#1a4f9e").pack(side=tk.LEFT)
        ttk.Button(top_row, text="刷新", command=self.refresh_history_window).pack(side=tk.RIGHT)

        table_wrap = ttk.Frame(win)
        table_wrap.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        columns = ("time", "match_id", "mode", "teams", "winner", "resolved_by", "status", "map_name")
        tree = ttk.Treeview(table_wrap, columns=columns, show="headings")
        tree.heading("time", text="时间")
        tree.heading("match_id", text="比赛ID")
        tree.heading("mode", text="模式")
        tree.heading("teams", text="对阵")
        tree.heading("winner", text="胜方")
        tree.heading("resolved_by", text="结算方式")
        tree.heading("status", text="状态")
        tree.heading("map_name", text="地图")

        tree.column("time", width=180, anchor=tk.W)
        tree.column("match_id", width=120, anchor=tk.W)
        tree.column("mode", width=80, anchor=tk.CENTER)
        tree.column("teams", width=420, anchor=tk.W)
        tree.column("winner", width=70, anchor=tk.CENTER)
        tree.column("resolved_by", width=120, anchor=tk.CENTER)
        tree.column("status", width=140, anchor=tk.CENTER)
        tree.column("map_name", width=120, anchor=tk.W)

        scroll_y = ttk.Scrollbar(table_wrap, orient=tk.VERTICAL, command=tree.yview)
        scroll_x = ttk.Scrollbar(table_wrap, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)

        self.history_tree = tree

        def close_window() -> None:
            self.history_window = None
            self.history_tree = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close_window)
        self.refresh_history_window()

    def refresh_history_window(self) -> None:
        if self.history_tree is None:
            return

        rows = self.server.store.list_match_history(limit=3000)
        self._clear_tree(self.history_tree)

        for row in rows:
            if not isinstance(row, dict):
                continue

            teams = row.get("teams", {})
            team_a = "/".join(str(x) for x in teams.get("A", {}).get("players", []))
            team_b = "/".join(str(x) for x in teams.get("B", {}).get("players", []))

            mode = str(row.get("mode", ""))
            winner = str(row.get("winner_side", "-") or "-")
            resolved_by = str(row.get("resolved_by", "-") or "-")
            status = str(row.get("status", "-") or "-")
            map_name = str(row.get("map_name", ""))
            match_id = str(row.get("match_id", ""))[:12]

            time_raw: Any = row.get("resolved_at")
            if not isinstance(time_raw, (int, float)):
                time_raw = row.get("closed_at")
            if not isinstance(time_raw, (int, float)):
                time_raw = row.get("created_at")

            if isinstance(time_raw, (int, float)):
                time_text = ts_text(float(time_raw))
            else:
                time_text = str(time_raw or "-")

            self.history_tree.insert(
                "",
                tk.END,
                values=(
                    time_text,
                    match_id,
                    mode,
                    f"A:{team_a} vs B:{team_b}",
                    winner,
                    resolved_by,
                    status,
                    map_name,
                ),
            )

        self.history_updated_var.set(f"最近记录: {len(rows)} 条 | 更新时间: {ts_text()}")

    def refresh_pending_from_store(self) -> None:
        self.pending_cache = self.server.store.get_pending_reviews()
        self.render_pending(self.pending_cache)

    def apply_filters(self) -> None:
        self.render_pending(self.pending_cache, show_error=True)

    def clear_filters(self) -> None:
        self.filter_start_var.set("")
        self.filter_end_var.set("")
        self.filter_mode_var.set("全部")
        self.filter_player_var.set("")
        self.render_pending(self.pending_cache)

    def filter_pending_items(self, pending_list: list[dict[str, Any]], show_error: bool) -> list[dict[str, Any]]:
        mode_filter = self.filter_mode_var.get().strip()
        player_filter = self.filter_player_var.get().strip().lower()

        start_text = self.filter_start_var.get().strip()
        end_text = self.filter_end_var.get().strip()

        start_ts = parse_datetime_input(start_text) if start_text else None
        end_ts = parse_datetime_input(end_text) if end_text else None

        if show_error:
            if start_text and start_ts is None:
                messagebox.showwarning("提示", "开始时间格式错误，请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM[:SS]。")
                return []
            if end_text and end_ts is None:
                messagebox.showwarning("提示", "结束时间格式错误，请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM[:SS]。")
                return []

        filtered: list[dict[str, Any]] = []
        for item in pending_list:
            if not isinstance(item, dict):
                continue

            mode = str(item.get("mode", ""))
            if mode_filter in ("1v1", "2v2") and mode != mode_filter:
                continue

            teams = item.get("teams", {})
            team_a = [str(x) for x in teams.get("A", {}).get("players", [])]
            team_b = [str(x) for x in teams.get("B", {}).get("players", [])]
            players = team_a + team_b
            if player_filter:
                name_text = "|".join(players).lower()
                if player_filter not in name_text:
                    continue

            ref_ts_raw = item.get("closed_at")
            if not isinstance(ref_ts_raw, (int, float)):
                ref_ts_raw = item.get("created_at")
            ref_ts = float(ref_ts_raw) if isinstance(ref_ts_raw, (int, float)) else None

            if start_ts is not None and ref_ts is not None and ref_ts < start_ts:
                continue
            if end_ts is not None and ref_ts is not None and ref_ts > end_ts:
                continue
            if (start_ts is not None or end_ts is not None) and ref_ts is None:
                continue

            filtered.append(item)

        filtered.sort(
            key=lambda row: float(row.get("closed_at") or row.get("created_at") or 0.0),
            reverse=True,
        )
        return filtered

    def render_pending(self, pending_list: list[dict[str, Any]], show_error: bool = False) -> None:
        self.pending_listbox.delete(0, tk.END)
        self.pending_id_by_index.clear()

        filtered = self.filter_pending_items(pending_list, show_error=show_error)

        if not pending_list:
            self.pending_listbox.insert(tk.END, "暂无待判定比赛。")
            return

        if not filtered:
            self.pending_listbox.insert(tk.END, "没有符合筛选条件的待判定比赛。")
            return

        for idx, item in enumerate(filtered):
            match_id = str(item.get("match_id", ""))
            mode = str(item.get("mode", ""))
            teams = item.get("teams", {})
            team_a = "/".join(teams.get("A", {}).get("players", []))
            team_b = "/".join(teams.get("B", {}).get("players", []))
            ts_value = item.get("closed_at")
            if not isinstance(ts_value, (int, float)):
                ts_value = item.get("created_at")
            time_text = ts_text(float(ts_value)) if isinstance(ts_value, (int, float)) else "-"
            row = f"{match_id[:8]} | {mode} | {time_text} | A:{team_a} vs B:{team_b}"
            self.pending_listbox.insert(tk.END, row)
            self.pending_id_by_index[idx] = match_id

    def selected_pending_id(self) -> str:
        selection = self.pending_listbox.curselection()
        if not selection:
            return ""
        return self.pending_id_by_index.get(selection[0], "")

    def resolve_a(self) -> None:
        match_id = self.selected_pending_id()
        if not match_id:
            messagebox.showinfo("提示", "请先在左侧选择一场待判定比赛。")
            return
        ok, text = self.server.resolve_pending_match(match_id, "A")
        if not ok:
            messagebox.showerror("失败", text)
        self.append_log(text)
        self.refresh_pending_from_store()

    def resolve_b(self) -> None:
        match_id = self.selected_pending_id()
        if not match_id:
            messagebox.showinfo("提示", "请先在左侧选择一场待判定比赛。")
            return
        ok, text = self.server.resolve_pending_match(match_id, "B")
        if not ok:
            messagebox.showerror("失败", text)
        self.append_log(text)
        self.refresh_pending_from_store()

    def poll_events(self) -> None:
        try:
            while True:
                event_type, payload = self.event_queue.get_nowait()
                if event_type == "log":
                    self.append_log(str(payload))
                elif event_type == "snapshot" and isinstance(payload, dict):
                    online = int(payload.get("online", 0))
                    q1 = int(payload.get("queue_1v1_players", payload.get("queue_1v1", 0)))
                    q2 = int(payload.get("queue_2v2_players", payload.get("queue_2v2", 0)))
                    active = int(payload.get("active_matches", 0))
                    self.info_var.set(f"在线玩家:{online} | 1v1匹配玩家:{q1} | 2v2匹配玩家:{q2} | 活动比赛:{active}")

                    pending = payload.get("pending_reviews", [])
                    if isinstance(pending, list):
                        self.pending_cache = pending
                        self.render_pending(self.pending_cache)
        except queue.Empty:
            pass

        self.root.after(100, self.poll_events)

    def on_close(self) -> None:
        self.server.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    ServerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
