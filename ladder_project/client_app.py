from __future__ import annotations

import json
import queue
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

from shared.constants import COMMANDER_POOL
from shared.constants import DEFAULT_HOST
from shared.constants import DEFAULT_PORT
from shared.net import iter_json_messages
from shared.net import open_client_socket
from shared.net import sanitize_text
from shared.net import send_json
from shared.security import decrypt_blob
from shared.security import encrypt_blob


def resolve_client_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.home() / ".icelin_ladder"


APP_DIR = resolve_client_data_dir()
SETTINGS_FILE = APP_DIR / "client_settings.json"
CREDENTIAL_FILE = APP_DIR / "credentials.bin"
AUTO_CONNECT_INTERVAL_MS = 3000


class LadderClientApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Icelin 天梯 - 客户端")
        self.root.geometry("1220x920")
        self.root.option_add("*Font", ("Microsoft YaHei UI", 13))

        self.settings = self.load_settings()

        self.host_var = tk.StringVar(value=str(self.settings.get("host", DEFAULT_HOST)))
        self.port_var = tk.StringVar(value=str(self.settings.get("port", DEFAULT_PORT)))

        self.nickname_var = tk.StringVar(value="")
        self.password_var = tk.StringVar(value="")
        self.remember_var = tk.BooleanVar(value=True)

        self.status_var = tk.StringVar(value="未连接服务器。")
        self.user_var = tk.StringVar(value="未登录")
        self.rating_var = tk.StringVar(value="1v1 分数: -- (局数 --)")
        self.pair_rating_var = tk.StringVar(value="2v2 分数: -- (局数 --)")
        self.lobby_var = tk.StringVar(value="在线玩家:0 | 1v1匹配玩家:0 | 2v2匹配玩家:0")
        self.queue_var = tk.StringVar(value="当前未在匹配")
        self.countdown_var = tk.StringVar(value="倒计时: --")
        self.match_var = tk.StringVar(value="当前无比赛")
        self.match_team_var = tk.StringVar(value="对阵信息: -")
        self.match_commander_var = tk.StringVar(value="指挥官信息: -")
        self.match_bp_var = tk.StringVar(value="BP信息: -")

        self.mode_var = tk.StringVar(value="1v1")
        self.teammate_var = tk.StringVar(value="")

        self.connected = False
        self.authenticated = False
        self.auth_pending = False
        self.auth_pending_action = ""
        self.nickname = ""
        self.queued_mode = ""

        self.sock: socket.socket | None = None
        self.stop_event = threading.Event()
        self.send_lock = threading.Lock()
        self.message_queue: queue.Queue[dict[str, Any]] = queue.Queue()

        self.auto_login_tried = False
        self.saved_credentials = self.load_saved_credentials()

        self.current_match: dict[str, Any] | None = None
        self.current_match_id = ""
        self.my_side = ""
        self.match_deadline: float | None = None
        self.pair_rating_rows: list[dict[str, Any]] = []

        self.leaderboard_window: tk.Toplevel | None = None
        self.leaderboard_1v1_tree: ttk.Treeview | None = None
        self.leaderboard_2v2_tree: ttk.Treeview | None = None
        self.leaderboard_time_var = tk.StringVar(value="排行榜更新时间: -")

        self.commander_vars: dict[str, tk.BooleanVar] = {
            name: tk.BooleanVar(value=False)
            for name in COMMANDER_POOL
        }
        self.commander_checks: list[ttk.Checkbutton] = []

        self.bp_radio_var = tk.StringVar(value="")
        self.bp_multi_vars: dict[str, tk.BooleanVar] = {}
        self.bp_multi_limit = 0

        self.build_ui()
        self.restore_default_commanders()
        self.update_buttons()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.poll_messages)
        self.root.after(250, self.update_countdown)
        self.root.after(200, self.auto_connect_tick)

        self.append_log("客户端已启动，正在自动连接服务器。")

    def load_settings(self) -> dict[str, Any]:
        defaults = {
            "host": DEFAULT_HOST,
            "port": DEFAULT_PORT,
            "commanders": COMMANDER_POOL[:3],
            "remember": True,
        }
        if not SETTINGS_FILE.exists():
            return defaults
        try:
            loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return defaults

        if isinstance(loaded, dict):
            for key in defaults:
                if key in loaded:
                    defaults[key] = loaded[key]
        return defaults

    def save_settings(self) -> None:
        port_raw = self.port_var.get().strip()
        try:
            port_value = int(port_raw or DEFAULT_PORT)
        except ValueError:
            port_value = DEFAULT_PORT

        values = {
            "host": self.host_var.get().strip() or DEFAULT_HOST,
            "port": port_value,
            "commanders": self.selected_commanders(),
            "remember": bool(self.remember_var.get()),
        }
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            return

    def load_saved_credentials(self) -> tuple[str, str] | None:
        if not CREDENTIAL_FILE.exists():
            return None
        try:
            blob = CREDENTIAL_FILE.read_bytes()
            payload = decrypt_blob(blob)
            data = json.loads(payload.decode("utf-8"))
            nickname = sanitize_text(data.get("nickname"), 20)
            password = str(data.get("password", ""))
            if nickname and password:
                self.remember_var.set(True)
                return nickname, password
        except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        return None

    def save_credentials(self, nickname: str, password: str) -> None:
        if not self.remember_var.get():
            self.clear_saved_credentials()
            return

        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({
                "nickname": nickname,
                "password": password,
            }, ensure_ascii=False).encode("utf-8")
            blob = encrypt_blob(payload)
            CREDENTIAL_FILE.write_bytes(blob)
        except OSError:
            return

    def clear_saved_credentials(self) -> None:
        try:
            if CREDENTIAL_FILE.exists():
                CREDENTIAL_FILE.unlink()
        except OSError:
            return

    def build_ui(self) -> None:
        container = ttk.Frame(self.root)
        container.pack(fill=tk.BOTH, expand=True)

        self.main_canvas = tk.Canvas(container, highlightthickness=0)
        self.main_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.main_scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self.main_canvas.yview)
        self.main_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.main_canvas.configure(yscrollcommand=self.main_scrollbar.set)

        main = ttk.Frame(self.main_canvas, padding=12)
        self.main_canvas_window = self.main_canvas.create_window((0, 0), window=main, anchor=tk.NW)
        main.bind("<Configure>", self.on_main_frame_configure)
        self.main_canvas.bind("<Configure>", self.on_main_canvas_configure)
        self.root.bind_all("<MouseWheel>", self.on_main_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self.on_main_mousewheel_linux, add="+")
        self.root.bind_all("<Button-5>", self.on_main_mousewheel_linux, add="+")

        top = ttk.LabelFrame(main, text="连接")
        top.pack(fill=tk.X, padx=2, pady=4)

        ttk.Label(top, text="服务器:").grid(row=0, column=0, padx=6, pady=8, sticky=tk.W)
        self.host_entry = ttk.Entry(top, textvariable=self.host_var, width=28)
        self.host_entry.grid(row=0, column=1, padx=6, pady=8, sticky=tk.W)

        ttk.Label(top, text="端口:").grid(row=0, column=2, padx=6, pady=8, sticky=tk.W)
        self.port_entry = ttk.Entry(top, textvariable=self.port_var, width=8)
        self.port_entry.grid(row=0, column=3, padx=6, pady=8, sticky=tk.W)

        self.status_label = ttk.Label(top, textvariable=self.status_var, foreground="#1a4f9e")
        self.status_label.grid(row=0, column=4, padx=6, pady=8, sticky=tk.W)

        pages = ttk.Frame(main)
        pages.pack(fill=tk.BOTH, expand=True, padx=2, pady=4)

        self.login_frame = ttk.LabelFrame(pages, text="登录 / 注册")
        self.login_frame.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(self.login_frame, text="昵称:").grid(row=0, column=0, padx=8, pady=8, sticky=tk.W)
        self.nickname_entry = ttk.Entry(self.login_frame, textvariable=self.nickname_var, width=20)
        self.nickname_entry.grid(row=0, column=1, padx=8, pady=8, sticky=tk.W)

        ttk.Label(self.login_frame, text="密码:").grid(row=0, column=2, padx=8, pady=8, sticky=tk.W)
        self.password_entry = ttk.Entry(self.login_frame, textvariable=self.password_var, show="*", width=20)
        self.password_entry.grid(row=0, column=3, padx=8, pady=8, sticky=tk.W)

        self.remember_check = ttk.Checkbutton(self.login_frame, text="记住账号并自动登录", variable=self.remember_var)
        self.remember_check.grid(row=0, column=4, padx=8, pady=8, sticky=tk.W)

        self.register_button = ttk.Button(self.login_frame, text="注册", command=self.register)
        self.register_button.grid(row=0, column=5, padx=8, pady=8)

        self.login_button = ttk.Button(self.login_frame, text="登录", command=self.login)
        self.login_button.grid(row=0, column=6, padx=8, pady=8)

        profile = ttk.LabelFrame(pages, text="账号信息")
        profile.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(profile, textvariable=self.user_var, font=("Microsoft YaHei UI", 14, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 6))
        ttk.Label(profile, textvariable=self.rating_var, foreground="#1a4f9e").pack(anchor=tk.W, padx=10, pady=(0, 10))
        ttk.Label(profile, textvariable=self.pair_rating_var, foreground="#6b3a0f").pack(anchor=tk.W, padx=10, pady=(0, 10))
        ttk.Label(profile, textvariable=self.lobby_var, foreground="#0f6a38").pack(anchor=tk.W, padx=10, pady=(0, 10))

        profile_actions = ttk.Frame(profile)
        profile_actions.pack(fill=tk.X, padx=10, pady=(0, 10))
        self.leaderboard_button = ttk.Button(profile_actions, text="排行榜", command=self.request_leaderboard)
        self.leaderboard_button.pack(side=tk.LEFT)

        queue_frame = ttk.LabelFrame(pages, text="天梯匹配")
        queue_frame.pack(fill=tk.X, pady=(0, 6))

        mode_row = ttk.Frame(queue_frame)
        mode_row.pack(fill=tk.X, padx=10, pady=(10, 6))

        self.mode_1v1 = ttk.Radiobutton(mode_row, text="1v1", value="1v1", variable=self.mode_var, command=self.on_mode_changed)
        self.mode_1v1.pack(side=tk.LEFT, padx=(0, 12))
        self.mode_2v2 = ttk.Radiobutton(mode_row, text="2v2", value="2v2", variable=self.mode_var, command=self.on_mode_changed)
        self.mode_2v2.pack(side=tk.LEFT)

        self.queue_state_label = ttk.Label(mode_row, textvariable=self.queue_var, foreground="#1a4f9e")
        self.queue_state_label.pack(side=tk.LEFT, padx=16)

        self.mode_stack = ttk.Frame(queue_frame)
        self.mode_stack.pack(fill=tk.X, padx=10, pady=6)

        self.mode_1v1_panel = ttk.Frame(self.mode_stack)
        ttk.Label(self.mode_1v1_panel, text="1v1 报名请选择 3 位指挥官:").pack(anchor=tk.W)

        commander_grid = ttk.Frame(self.mode_1v1_panel)
        commander_grid.pack(fill=tk.X, pady=(4, 0))

        for i, commander in enumerate(COMMANDER_POOL):
            check = ttk.Checkbutton(
                commander_grid,
                text=commander,
                variable=self.commander_vars[commander],
                command=lambda name=commander: self.on_commander_toggle(name),
            )
            check.grid(row=i // 6, column=i % 6, padx=6, pady=4, sticky=tk.W)
            self.commander_checks.append(check)

        self.mode_2v2_panel = ttk.Frame(self.mode_stack)
        teammate_row = ttk.Frame(self.mode_2v2_panel)
        teammate_row.pack(fill=tk.X)
        ttk.Label(teammate_row, text="2v2 队友昵称:").pack(side=tk.LEFT)
        self.teammate_entry = ttk.Entry(teammate_row, textvariable=self.teammate_var, width=18)
        self.teammate_entry.pack(side=tk.LEFT, padx=(8, 0))
        self.teammate_entry.bind("<KeyRelease>", self.on_teammate_changed)
        ttk.Label(self.mode_2v2_panel, text="发起后会自动绑定该队友并临时禁用其匹配。", foreground="#666666").pack(anchor=tk.W, pady=(6, 0))

        action_row = ttk.Frame(queue_frame)
        action_row.pack(fill=tk.X, padx=10, pady=(4, 10))

        self.start_queue_button = ttk.Button(action_row, text="开始匹配", command=self.start_queue)
        self.start_queue_button.pack(side=tk.LEFT, padx=(0, 8))

        self.cancel_queue_button = ttk.Button(action_row, text="取消匹配", command=self.cancel_queue)
        self.cancel_queue_button.pack(side=tk.LEFT)

        match_frame = ttk.LabelFrame(pages, text="当前比赛")
        match_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        head = ttk.Frame(match_frame)
        head.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(head, textvariable=self.match_var, font=("Microsoft YaHei UI", 14, "bold")).pack(anchor=tk.W)
        ttk.Label(head, textvariable=self.countdown_var, foreground="#b02222").pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(head, textvariable=self.match_team_var).pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(head, textvariable=self.match_commander_var, foreground="#1a4f9e").pack(anchor=tk.W, pady=(2, 0))
        ttk.Label(head, textvariable=self.match_bp_var).pack(anchor=tk.W, pady=(2, 0))

        result_frame = ttk.LabelFrame(match_frame, text="比赛结果上报")
        result_frame.pack(fill=tk.X, padx=10, pady=(0, 8))

        self.report_self_button = ttk.Button(result_frame, text="我方获胜", command=lambda: self.report_result("self"))
        self.report_self_button.pack(side=tk.LEFT, padx=8, pady=8)

        self.report_opp_button = ttk.Button(result_frame, text="对方获胜", command=lambda: self.report_result("opponent"))
        self.report_opp_button.pack(side=tk.LEFT, padx=8, pady=8)

        self.bp_frame = ttk.LabelFrame(match_frame, text="BP 操作")
        self.bp_frame.pack(fill=tk.X, padx=10, pady=(0, 8))

        self.bp_hint_var = tk.StringVar(value="当前无可操作项。")
        ttk.Label(self.bp_frame, textvariable=self.bp_hint_var).pack(anchor=tk.W, padx=8, pady=(8, 4))

        self.bp_options_frame = ttk.Frame(self.bp_frame)
        self.bp_options_frame.pack(fill=tk.X, padx=8, pady=4)

        self.bp_submit_button = ttk.Button(self.bp_frame, text="提交 BP", command=self.submit_bp)
        self.bp_submit_button.pack(anchor=tk.W, padx=8, pady=(0, 8))

        chat_frame = ttk.LabelFrame(match_frame, text="临时聊天室")
        chat_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))

        self.chat_text = tk.Text(chat_frame, state=tk.DISABLED, height=10, font=("Consolas", 12))
        self.chat_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 6))

        chat_send_row = ttk.Frame(chat_frame)
        chat_send_row.pack(fill=tk.X, padx=8, pady=(0, 8))

        self.chat_entry_var = tk.StringVar(value="")
        self.chat_entry = ttk.Entry(chat_send_row, textvariable=self.chat_entry_var)
        self.chat_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.chat_entry.bind("<Return>", self.on_chat_enter)
        self.chat_entry.bind("<KP_Enter>", self.on_chat_enter)

        self.chat_send_button = ttk.Button(chat_send_row, text="发送", command=self.send_chat)
        self.chat_send_button.pack(side=tk.LEFT, padx=(8, 0))

        log_frame = ttk.LabelFrame(pages, text="日志")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_frame, state=tk.DISABLED, height=8, font=("Consolas", 12))
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.show_mode_panel("1v1")

    def on_main_frame_configure(self, _event: Any) -> None:
        region = self.main_canvas.bbox("all")
        if region is not None:
            self.main_canvas.configure(scrollregion=region)

    def on_main_canvas_configure(self, event: Any) -> None:
        self.main_canvas.itemconfigure(self.main_canvas_window, width=event.width)

    def on_main_mousewheel(self, event: Any) -> str | None:
        widget = event.widget
        if isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview)):
            return None

        delta = int(getattr(event, "delta", 0))
        if delta == 0:
            return None

        steps = int(-delta / 120)
        if steps == 0:
            steps = -1 if delta > 0 else 1
        self.main_canvas.yview_scroll(steps, "units")
        return "break"

    def on_main_mousewheel_linux(self, event: Any) -> str | None:
        widget = event.widget
        if isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview)):
            return None

        num = int(getattr(event, "num", 0))
        if num == 4:
            self.main_canvas.yview_scroll(-1, "units")
            return "break"
        if num == 5:
            self.main_canvas.yview_scroll(1, "units")
            return "break"
        return None

    def _clear_tree(self, tree: ttk.Treeview) -> None:
        for item in tree.get_children():
            tree.delete(item)

    def request_leaderboard(self) -> None:
        if not self.connected:
            messagebox.showwarning("提示", "尚未连接服务器。")
            return
        if not self.authenticated:
            messagebox.showwarning("提示", "请先登录后再查看排行榜。")
            return
        if self.send({"type": "request_leaderboard"}):
            self.append_log("已请求排行榜数据。")

    def show_leaderboard(self, rows_1v1: list[dict[str, Any]], rows_2v2: list[dict[str, Any]], updated_at: str) -> None:
        if self.leaderboard_window is None or not self.leaderboard_window.winfo_exists():
            win = tk.Toplevel(self.root)
            self.leaderboard_window = win
            win.title("全服排行榜")
            win.geometry("860x580")

            top_row = ttk.Frame(win, padding=10)
            top_row.pack(fill=tk.X)
            ttk.Label(top_row, textvariable=self.leaderboard_time_var, foreground="#1a4f9e").pack(side=tk.LEFT)
            ttk.Button(top_row, text="刷新", command=self.request_leaderboard).pack(side=tk.RIGHT)

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

            self.leaderboard_1v1_tree = tree_1v1
            self.leaderboard_2v2_tree = tree_2v2

            def close_window() -> None:
                self.leaderboard_window = None
                self.leaderboard_1v1_tree = None
                self.leaderboard_2v2_tree = None
                win.destroy()

            win.protocol("WM_DELETE_WINDOW", close_window)

        if self.leaderboard_1v1_tree is None or self.leaderboard_2v2_tree is None:
            return

        self._clear_tree(self.leaderboard_1v1_tree)
        self._clear_tree(self.leaderboard_2v2_tree)

        for i, row in enumerate(rows_1v1, start=1):
            nickname = sanitize_text(row.get("nickname"), 20)
            try:
                rating = float(row.get("rating", 0.0))
            except (TypeError, ValueError):
                rating = 0.0
            try:
                games = int(row.get("games", 0))
            except (TypeError, ValueError):
                games = 0
            self.leaderboard_1v1_tree.insert("", tk.END, values=(i, nickname, f"{rating:.2f}", games))

        for i, row in enumerate(rows_2v2, start=1):
            pair_name = ""
            players = row.get("players", [])
            if isinstance(players, list) and len(players) == 2:
                pair_name = f"{players[0]}/{players[1]}"
            if not pair_name:
                pair_name = str(row.get("pair_key", ""))
            try:
                rating = float(row.get("rating", 0.0))
            except (TypeError, ValueError):
                rating = 0.0
            try:
                games = int(row.get("games", 0))
            except (TypeError, ValueError):
                games = 0
            self.leaderboard_2v2_tree.insert("", tk.END, values=(i, pair_name, f"{rating:.2f}", games))

        updated_text = sanitize_text(updated_at, 32) or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.leaderboard_time_var.set(
            f"排行榜更新时间: {updated_text} | 1v1 {len(rows_1v1)} 条 | 2v2 {len(rows_2v2)} 条"
        )

    def append_log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{stamp}] {text}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def append_chat(self, sender: str, text: str, ts: str | None = None) -> None:
        stamp = ts or datetime.now().strftime("%H:%M:%S")
        self.chat_text.configure(state=tk.NORMAL)
        self.chat_text.insert(tk.END, f"[{stamp}] {sender}: {text}\n")
        self.chat_text.see(tk.END)
        self.chat_text.configure(state=tk.DISABLED)

    def on_chat_enter(self, _event: Any) -> str:
        self.send_chat()
        return "break"

    def on_teammate_changed(self, _event: Any) -> None:
        self.update_pair_rating_text(self.pair_rating_rows)

    def set_auth_pending(self, pending: bool, action: str = "") -> None:
        self.auth_pending = pending
        self.auth_pending_action = action if pending else ""
        self.update_buttons()

    def update_pair_rating_text(self, pair_rows: list[dict[str, Any]]) -> None:
        if not pair_rows:
            self.pair_rating_var.set("2v2 分数: -- (局数 --)")
            return

        teammate_filter = sanitize_text(self.teammate_var.get(), 20)
        chosen: dict[str, Any] | None = None

        if teammate_filter:
            for row in pair_rows:
                if sanitize_text(row.get("teammate"), 20) == teammate_filter:
                    chosen = row
                    break

        if chosen is None:
            chosen = pair_rows[0]

        teammate = sanitize_text(chosen.get("teammate"), 20) or "未知队友"
        try:
            rating = float(chosen.get("rating", 0.0))
        except (TypeError, ValueError):
            rating = 0.0
        try:
            games = int(chosen.get("games", 0))
        except (TypeError, ValueError):
            games = 0

        self.pair_rating_var.set(f"2v2 分数({teammate}): {rating:.2f} (局数 {games})")

    def _format_choice(self, value: Any, default_wait: str = "待定") -> str:
        if value is None:
            return default_wait
        if isinstance(value, list):
            return "/".join(str(x) for x in value) if value else "无"
        text = str(value).strip()
        if not text:
            return "不禁用"
        return text

    def update_match_overview(self) -> None:
        match = self.current_match
        if not isinstance(match, dict):
            self.match_team_var.set("对阵信息: -")
            self.match_commander_var.set("指挥官信息: -")
            self.match_bp_var.set("BP信息: -")
            return

        teams = match.get("teams", {})
        bp = match.get("bp", {})
        side = self.my_side if self.my_side in ("A", "B") else ""
        opp_side = "B" if side == "A" else "A"

        team_a = [str(x) for x in teams.get("A", {}).get("players", [])]
        team_b = [str(x) for x in teams.get("B", {}).get("players", [])]
        self.match_team_var.set(f"对阵信息: A {'/'.join(team_a) if team_a else '-'} VS B {'/'.join(team_b) if team_b else '-'}")

        if side:
            my_pool = [str(x) for x in teams.get(side, {}).get("commanders", [])]
            opp_pool = [str(x) for x in teams.get(opp_side, {}).get("commanders", [])]
            self.match_commander_var.set(
                f"指挥官信息: 我方 {'/'.join(my_pool) if my_pool else '-'} | 对方 {'/'.join(opp_pool) if opp_pool else '-'}"
            )
        else:
            self.match_commander_var.set("指挥官信息: -")

        ban_a = self._format_choice(bp.get("ban", {}).get("A"))
        ban_b = self._format_choice(bp.get("ban", {}).get("B"))
        pick_a = self._format_choice(bp.get("pick", {}).get("A"))
        pick_b = self._format_choice(bp.get("pick", {}).get("B"))
        self.match_bp_var.set(f"BP信息: Ban A={ban_a} B={ban_b} | Pick A={pick_a} B={pick_b}")

    def clear_match_context(self) -> None:
        self.current_match = None
        self.current_match_id = ""
        self.my_side = ""
        self.match_deadline = None
        self.match_var.set("当前无比赛")
        self.bp_hint_var.set("当前无可操作项。")
        self.render_bp_options([], mode="single")
        self.update_match_overview()

    def apply_match_state_payload(self, payload: dict[str, Any]) -> None:
        old_match_id = self.current_match_id
        old_state = ""
        if isinstance(self.current_match, dict):
            old_state = sanitize_text(self.current_match.get("state"), 32)

        self.current_match = payload
        self.current_match_id = sanitize_text(payload.get("match_id"), 64)
        self.my_side = sanitize_text(payload.get("my_side"), 2)
        self.match_deadline = float(payload.get("deadline")) if isinstance(payload.get("deadline"), (int, float)) else None

        mode = sanitize_text(payload.get("mode"), 8)
        state = sanitize_text(payload.get("state"), 32)
        map_name = sanitize_text(payload.get("map_name"), 64)
        self.match_var.set(f"比赛 {self.current_match_id[:8]} | {mode} | {state} | 地图: {map_name}")

        teams = payload.get("teams", {})
        team_a = teams.get("A", {}).get("players", [])
        team_b = teams.get("B", {}).get("players", [])
        if self.current_match_id != old_match_id or state != old_state:
            self.append_log(f"当前对阵: A {team_a} VS B {team_b}")

        self.update_match_overview()
        self.render_bp_panel()

    def show_mode_panel(self, mode: str) -> None:
        if self.mode_1v1_panel.winfo_ismapped():
            self.mode_1v1_panel.pack_forget()
        if self.mode_2v2_panel.winfo_ismapped():
            self.mode_2v2_panel.pack_forget()

        if mode == "2v2":
            self.mode_2v2_panel.pack(fill=tk.X)
        else:
            self.mode_1v1_panel.pack(fill=tk.X)

    def on_mode_changed(self) -> None:
        self.show_mode_panel(self.mode_var.get())
        self.update_pair_rating_text(self.pair_rating_rows)
        self.update_buttons()

    def restore_default_commanders(self) -> None:
        raw = self.settings.get("commanders", COMMANDER_POOL[:3])
        if not isinstance(raw, list):
            raw = COMMANDER_POOL[:3]

        selected = [x for x in raw if isinstance(x, str) and x in self.commander_vars]
        if len(selected) != 3:
            selected = COMMANDER_POOL[:3]

        selected_set = set(selected)
        for name, var in self.commander_vars.items():
            var.set(name in selected_set)

    def selected_commanders(self) -> list[str]:
        return [name for name in COMMANDER_POOL if self.commander_vars[name].get()]

    def on_commander_toggle(self, changed_name: str) -> None:
        selected = self.selected_commanders()
        if len(selected) > 3:
            self.commander_vars[changed_name].set(False)
            self.append_log("最多只能选择 3 位指挥官。")
        self.update_buttons()

    def auto_connect_tick(self) -> None:
        if not self.connected:
            self.connect_server(silent=True)
        self.root.after(AUTO_CONNECT_INTERVAL_MS, self.auto_connect_tick)

    def connect_server(self, silent: bool = False) -> bool:
        if self.connected:
            return True

        host = self.host_var.get().strip() or DEFAULT_HOST
        port_text = self.port_var.get().strip()
        try:
            port = int(port_text)
        except ValueError:
            self.status_var.set("端口格式错误。")
            return False

        try:
            sock = open_client_socket(host, port)
        except OSError as exc:
            self.status_var.set("服务器连接失败，自动重连中...")
            if not silent:
                messagebox.showerror("连接失败", str(exc))
            return False

        self.sock = sock
        self.connected = True
        self.stop_event = threading.Event()

        threading.Thread(target=self.recv_loop, daemon=True).start()

        self.status_var.set("已连接服务器。")
        self.append_log("已连接服务器。")

        self.save_settings()
        self.try_auto_login()
        self.update_buttons()
        return True

    def disconnect_server(self, reason: str = "连接断开。") -> None:
        if not self.connected:
            return

        self.connected = False
        self.stop_event.set()

        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

        self.authenticated = False
        self.auth_pending = False
        self.auth_pending_action = ""
        self.nickname = ""
        self.queued_mode = ""

        self.user_var.set("未登录")
        self.rating_var.set("1v1 分数: -- (局数 --)")
        self.pair_rating_rows = []
        self.pair_rating_var.set("2v2 分数: -- (局数 --)")
        self.lobby_var.set("在线玩家:0 | 1v1匹配玩家:0 | 2v2匹配玩家:0")
        self.queue_var.set("当前未在匹配")
        self.countdown_var.set("倒计时: --")
        self.clear_match_context()

        self.status_var.set(reason)
        self.append_log(reason)
        self.update_buttons()

    def try_auto_login(self) -> None:
        if self.auto_login_tried:
            return
        if self.authenticated:
            return
        if self.auth_pending:
            return
        if self.saved_credentials is None:
            return

        nickname, password = self.saved_credentials
        self.auto_login_tried = True
        self.nickname_var.set(nickname)
        self.password_var.set(password)

        if self.send({"type": "login", "nickname": nickname, "password": password}):
            self.set_auth_pending(True, "login")
            self.append_log(f"已尝试自动登录账号: {nickname}")

    def send(self, payload: dict[str, Any]) -> bool:
        if not self.connected or self.sock is None:
            return False

        try:
            with self.send_lock:
                if self.sock is None:
                    return False
                send_json(self.sock, payload)
            return True
        except OSError:
            self.disconnect_server("网络异常，自动重连中...")
            return False

    def recv_loop(self) -> None:
        if self.sock is None:
            return

        for message in iter_json_messages(self.sock, self.stop_event):
            self.message_queue.put(message)

        self.message_queue.put({"type": "_disconnect"})

    def poll_messages(self) -> None:
        try:
            while True:
                message = self.message_queue.get_nowait()
                self.handle_message(message)
        except queue.Empty:
            pass

        self.root.after(100, self.poll_messages)

    def handle_message(self, message: dict[str, Any]) -> None:
        msg_type = sanitize_text(message.get("type"), 32)

        if msg_type == "_disconnect":
            self.disconnect_server("连接断开，自动重连中...")
            return

        if msg_type == "auth_result":
            self.set_auth_pending(False)
            ok = bool(message.get("ok"))
            text = sanitize_text(message.get("message"), 160)
            action = sanitize_text(message.get("action"), 16)

            if ok and action == "login":
                self.authenticated = True
                self.nickname = sanitize_text(message.get("nickname"), 20)
                self.user_var.set(f"当前账号: {self.nickname}")
                self.status_var.set("登录成功。")
                self.append_log(text or "登录成功。")
                self.save_credentials(self.nickname_var.get().strip(), self.password_var.get())
                self.send({"type": "request_state"})
            elif ok and action == "register":
                self.append_log(text or "注册成功。")
                nickname = sanitize_text(self.nickname_var.get(), 20)
                password = self.password_var.get()
                if nickname and password:
                    self.append_log("正在自动登录刚注册的账号...")
                    sent = self.send({
                        "type": "login",
                        "nickname": nickname,
                        "password": password,
                    })
                    if sent:
                        self.set_auth_pending(True, "login")
            else:
                if action == "login" and self.auto_login_tried:
                    self.append_log(f"自动登录失败: {text or '未知错误'}")
                else:
                    self.append_log(text or "认证失败。")
                if not ok and action == "login":
                    self.authenticated = False
                    self.nickname = ""

            self.update_buttons()
            return

        if msg_type == "leaderboard_snapshot":
            rows_1v1_raw = message.get("one_v_one", [])
            rows_2v2_raw = message.get("two_v_two", [])
            rows_1v1 = [x for x in rows_1v1_raw if isinstance(x, dict)] if isinstance(rows_1v1_raw, list) else []
            rows_2v2 = [x for x in rows_2v2_raw if isinstance(x, dict)] if isinstance(rows_2v2_raw, list) else []
            updated_at = sanitize_text(message.get("updated_at"), 32)
            self.show_leaderboard(rows_1v1, rows_2v2, updated_at)
            return

        if msg_type == "state_snapshot":
            rating = message.get("rating_1v1", {})
            pair_rows = message.get("pair_ratings", [])

            score = float(rating.get("rating", 0.0))
            games = int(rating.get("games", 0))
            self.rating_var.set(f"1v1 分数: {score:.2f} (局数 {games})")

            queued_mode = sanitize_text(message.get("queued_mode"), 8)
            self.queued_mode = queued_mode
            if queued_mode:
                self.queue_var.set(f"当前匹配中: {queued_mode}")
            else:
                self.queue_var.set("当前未在匹配")

            online_count = int(message.get("online_count", 0))
            queue_1v1_players = int(message.get("queue_1v1_players", message.get("queue_1v1_count", 0)))
            queue_2v2_players = int(message.get("queue_2v2_players", message.get("queue_2v2_count", 0)))
            self.lobby_var.set(
                f"在线玩家:{online_count} | 1v1匹配玩家:{queue_1v1_players} | 2v2匹配玩家:{queue_2v2_players}"
            )

            if isinstance(pair_rows, list):
                self.pair_rating_rows = [x for x in pair_rows if isinstance(x, dict)]
            else:
                self.pair_rating_rows = []
            self.update_pair_rating_text(self.pair_rating_rows)

            current_match_payload = message.get("current_match")
            if isinstance(current_match_payload, dict):
                self.apply_match_state_payload(current_match_payload)
            else:
                in_match = bool(message.get("in_match", False))
                if not in_match and self.current_match is not None:
                    self.clear_match_context()

            if self.pair_rating_rows:
                top = self.pair_rating_rows[:3]
                if top:
                    fragments = [f"{x.get('teammate', '?')}:{float(x.get('rating', 0.0)):.1f}" for x in top if isinstance(x, dict)]
                    if fragments:
                        self.append_log("2v2 组合分: " + " | ".join(fragments))

            self.update_buttons()
            return

        if msg_type == "lobby_stats":
            online_count = int(message.get("online_count", 0))
            queue_1v1_players = int(message.get("queue_1v1_players", 0))
            queue_2v2_players = int(message.get("queue_2v2_players", 0))
            self.lobby_var.set(
                f"在线玩家:{online_count} | 1v1匹配玩家:{queue_1v1_players} | 2v2匹配玩家:{queue_2v2_players}"
            )
            return

        if msg_type == "queue_status":
            ok = bool(message.get("ok", True))
            text = sanitize_text(message.get("message"), 160)
            queued = bool(message.get("queued", False))
            mode = sanitize_text(message.get("mode"), 8)

            if ok:
                self.queued_mode = mode if queued else ""
                self.queue_var.set(f"当前匹配中: {mode}" if queued and mode else "当前未在匹配")
            if text:
                self.append_log(text)

            self.update_buttons()
            return

        if msg_type == "match_found":
            self.current_match_id = sanitize_text(message.get("match_id"), 64)
            self.my_side = sanitize_text(message.get("my_side"), 2)
            mode = sanitize_text(message.get("mode"), 8)
            map_name = sanitize_text(message.get("map_name"), 64)
            my_team = message.get("my_team", [])
            opp_team = message.get("opponent_team", [])

            self.match_var.set(f"比赛 {self.current_match_id[:8]} | {mode} | 地图: {map_name}")
            self.append_log(f"匹配成功: {my_team} VS {opp_team}")
            self.queue_var.set("当前未在匹配")
            self.queued_mode = ""
            self.update_buttons()
            return

        if msg_type == "match_state":
            self.apply_match_state_payload(message)
            self.update_buttons()
            return

        if msg_type == "bp_result":
            text = sanitize_text(message.get("message"), 160)
            if text:
                self.append_log(text)
            return

        if msg_type == "chat_message":
            sender = sanitize_text(message.get("sender"), 20)
            text = sanitize_text(message.get("text"), 200)
            ts = sanitize_text(message.get("time"), 24)
            self.append_chat(sender, text, ts)
            return

        if msg_type == "report_result":
            text = sanitize_text(message.get("message"), 160)
            if text:
                self.append_log(text)
            return

        if msg_type == "pending_review":
            text = sanitize_text(message.get("message"), 160)
            self.append_log(text or "该局进入管理员判定流程。")
            self.clear_match_context()
            self.update_buttons()
            return

        if msg_type == "match_resolved":
            mode = sanitize_text(message.get("mode"), 8)
            outcome = sanitize_text(message.get("my_outcome"), 8)
            delta = float(message.get("delta", 0.0))
            new_rating = float(message.get("new_rating", 0.0))
            text = sanitize_text(message.get("message"), 160)

            self.append_log(text or "比赛已结算。")
            if mode == "2v2":
                teammate = ""
                if isinstance(self.current_match, dict):
                    side = self.my_side if self.my_side in ("A", "B") else ""
                    if side:
                        my_players = [str(x) for x in self.current_match.get("teams", {}).get(side, {}).get("players", [])]
                        teammate = next((x for x in my_players if x != self.nickname), "")
                if teammate:
                    self.pair_rating_var.set(f"2v2 分数({teammate}): {new_rating:.2f} (刚更新)")
                else:
                    self.pair_rating_var.set(f"2v2 分数: {new_rating:.2f} (刚更新)")
                self.append_log(f"本局结果(2v2): {outcome} | 组合分变化 {delta:+.2f} | 新组合分 {new_rating:.2f}")
            else:
                self.append_log(f"本局结果: {outcome} | 分数变化 {delta:+.2f} | 新分 {new_rating:.2f}")

            self.clear_match_context()

            self.send({"type": "request_state"})
            self.update_buttons()
            return

        if msg_type == "info":
            text = sanitize_text(message.get("message"), 200)
            if text:
                self.append_log(text)
            return

        if msg_type == "error":
            text = sanitize_text(message.get("message"), 200)
            if text:
                self.append_log(f"服务器错误: {text}")
            return

    def register(self) -> None:
        if not self.connected:
            messagebox.showwarning("提示", "尚未连接服务器。")
            return
        if self.auth_pending:
            messagebox.showinfo("提示", "认证请求处理中，请等待服务器响应。")
            return
        if self.authenticated:
            messagebox.showinfo("提示", "你已登录，无需重复注册或登录。")
            return

        nickname = sanitize_text(self.nickname_var.get(), 20)
        password = self.password_var.get()
        if not nickname or not password:
            messagebox.showwarning("提示", "请输入昵称和密码。")
            return

        if not self.send({"type": "register", "nickname": nickname, "password": password}):
            self.append_log("发送注册请求失败。")
            return
        self.set_auth_pending(True, "register")
        self.append_log("已发送注册请求，等待服务器响应。")

    def login(self) -> None:
        if not self.connected:
            messagebox.showwarning("提示", "尚未连接服务器。")
            return
        if self.auth_pending:
            messagebox.showinfo("提示", "认证请求处理中，请等待服务器响应。")
            return
        if self.authenticated:
            messagebox.showinfo("提示", "你已登录，无需重复登录。")
            return

        nickname = sanitize_text(self.nickname_var.get(), 20)
        password = self.password_var.get()
        if not nickname or not password:
            messagebox.showwarning("提示", "请输入昵称和密码。")
            return

        if not self.send({"type": "login", "nickname": nickname, "password": password}):
            self.append_log("发送登录请求失败。")
            return
        self.set_auth_pending(True, "login")
        self.append_log("已发送登录请求，等待服务器响应。")

    def start_queue(self) -> None:
        if not self.authenticated:
            messagebox.showwarning("提示", "请先登录。")
            return
        if not self.connected:
            messagebox.showwarning("提示", "尚未连接服务器。")
            return
        if self.queued_mode:
            messagebox.showinfo("提示", "你已经在匹配中。")
            return
        if self.current_match is not None:
            messagebox.showinfo("提示", "你当前在比赛中。")
            return

        mode = self.mode_var.get()
        if mode == "1v1":
            selected = self.selected_commanders()
            if len(selected) != 3:
                messagebox.showwarning("提示", "1v1 需要选择 3 位指挥官。")
                return
            payload = {
                "type": "queue_start",
                "mode": "1v1",
                "commanders": selected,
            }
        else:
            teammate = sanitize_text(self.teammate_var.get(), 20)
            if not teammate:
                messagebox.showwarning("提示", "请输入 2v2 队友昵称。")
                return
            payload = {
                "type": "queue_start",
                "mode": "2v2",
                "teammate": teammate,
            }

        if self.send(payload):
            self.append_log("已发送开始匹配请求。")
            self.save_settings()

    def cancel_queue(self) -> None:
        if not self.authenticated:
            return
        if not self.queued_mode:
            messagebox.showinfo("提示", "当前不在匹配队列中。")
            return
        if self.send({"type": "queue_cancel"}):
            self.append_log("已发送取消匹配请求。")

    def render_bp_options(self, options: list[tuple[str, str]], mode: str, limit: int = 0) -> None:
        for widget in self.bp_options_frame.winfo_children():
            widget.destroy()

        self.bp_radio_var.set("")
        self.bp_multi_vars.clear()
        self.bp_multi_limit = limit

        if not options:
            ttk.Label(self.bp_options_frame, text="当前没有可操作项。", foreground="#666666").pack(anchor=tk.W)
            return

        if mode == "single":
            for value, text in options:
                rb = ttk.Radiobutton(
                    self.bp_options_frame,
                    text=text,
                    value=value,
                    variable=self.bp_radio_var,
                )
                rb.pack(anchor=tk.W, pady=2)
            self.bp_radio_var.set(options[0][0])
        else:
            for value, text in options:
                var = tk.BooleanVar(value=False)
                self.bp_multi_vars[value] = var
                cb = ttk.Checkbutton(
                    self.bp_options_frame,
                    text=text,
                    variable=var,
                    command=lambda key=value: self.on_bp_multi_toggle(key),
                )
                cb.pack(anchor=tk.W, pady=2)

    def on_bp_multi_toggle(self, changed_key: str) -> None:
        selected = [k for k, v in self.bp_multi_vars.items() if v.get()]
        if self.bp_multi_limit > 0 and len(selected) > self.bp_multi_limit:
            self.bp_multi_vars[changed_key].set(False)
            self.append_log(f"最多可选 {self.bp_multi_limit} 项。")

    def render_bp_panel(self) -> None:
        match = self.current_match
        if not isinstance(match, dict):
            self.bp_hint_var.set("当前无可操作项。")
            self.render_bp_options([], mode="single")
            return

        mode = sanitize_text(match.get("mode"), 8)
        state = sanitize_text(match.get("state"), 32)
        teams = match.get("teams", {})
        bp = match.get("bp", {})

        side = self.my_side
        if side not in ("A", "B"):
            self.bp_hint_var.set("你不在当前对局中。")
            self.render_bp_options([], mode="single")
            return

        opp_side = "B" if side == "A" else "A"

        if mode == "1v1":
            if state == "bp_ban":
                submitted = bp.get("ban", {}).get(side) is not None
                if submitted:
                    self.bp_hint_var.set("你已提交 Ban，等待对手。")
                    self.render_bp_options([], mode="single")
                    return
                opp_pool = teams.get(opp_side, {}).get("commanders", [])
                options = [(name, name) for name in opp_pool]
                options.append(("", "不禁用"))
                self.bp_hint_var.set("Ban 阶段：请选择禁用目标或不禁用。")
                self.render_bp_options(options, mode="single")
                return

            if state == "bp_pick":
                submitted = bp.get("pick", {}).get(side) is not None
                if submitted:
                    self.bp_hint_var.set("你已提交 Pick，等待对手。")
                    self.render_bp_options([], mode="single")
                    return
                available = bp.get("available", {}).get(side, [])
                options = [(name, name) for name in available]
                options.append(("random", "随机"))
                self.bp_hint_var.set("Pick 阶段：选择出战指挥官或随机。")
                self.render_bp_options(options, mode="single")
                return

            if state == "ready":
                self.bp_hint_var.set("BP 已完成，可在下方上报比赛结果。")
                self.render_bp_options([], mode="single")
                return

            self.bp_hint_var.set("当前阶段不可操作。")
            self.render_bp_options([], mode="single")
            return

        if mode == "2v2":
            captain = sanitize_text(teams.get(side, {}).get("captain"), 20)
            if state in ("team_ban", "team_pick") and captain and self.nickname != captain:
                self.bp_hint_var.set(f"2v2 BP 仅允许发起人 {captain} 操作，等待其提交。")
                self.render_bp_options([], mode="single")
                return

            if state == "team_ban":
                submitted = bp.get("ban", {}).get(side) is not None
                if submitted:
                    self.bp_hint_var.set("你方已提交 Ban，等待对方。")
                    self.render_bp_options([], mode="single")
                    return
                opp_pool = teams.get(opp_side, {}).get("commanders", [])
                options = [(name, name) for name in opp_pool]
                self.bp_hint_var.set("2v2 Ban 阶段：可最多禁用对方 3 个指挥官（可不禁）。")
                self.render_bp_options(options, mode="multi", limit=3)
                return

            if state == "team_pick":
                submitted = bp.get("pick", {}).get(side) is not None
                if submitted:
                    self.bp_hint_var.set("你方已提交 Pick，等待对方。")
                    self.render_bp_options([], mode="single")
                    return
                available = bp.get("available", {}).get(side, [])
                options = [(name, name) for name in available]
                self.bp_hint_var.set("2v2 Pick 阶段：请选择我方 2 个出战指挥官。")
                self.render_bp_options(options, mode="multi", limit=2)
                return

            if state == "ready":
                self.bp_hint_var.set("BP 已完成，可在下方上报比赛结果。")
                self.render_bp_options([], mode="single")
                return

            self.bp_hint_var.set("当前阶段不可操作。")
            self.render_bp_options([], mode="single")
            return

        self.bp_hint_var.set("未知比赛模式。")
        self.render_bp_options([], mode="single")

    def submit_bp(self) -> None:
        match = self.current_match
        if not isinstance(match, dict):
            messagebox.showinfo("提示", "当前没有可操作比赛。")
            return

        mode = sanitize_text(match.get("mode"), 8)
        state = sanitize_text(match.get("state"), 32)

        if mode == "1v1":
            if state in ("bp_ban", "bp_pick"):
                choice = self.bp_radio_var.get()
                if not choice and state == "bp_pick":
                    messagebox.showwarning("提示", "请选择一个 Pick 选项。")
                    return
                payload = {
                    "type": "bp_submit",
                    "choice": choice,
                }
                self.send(payload)
                return
            messagebox.showinfo("提示", "当前阶段无需提交。")
            return

        if mode == "2v2":
            side = self.my_side
            if side not in ("A", "B"):
                messagebox.showwarning("提示", "当前身份异常，无法提交 BP。")
                return
            captain = sanitize_text(match.get("teams", {}).get(side, {}).get("captain"), 20)
            if captain and self.nickname != captain:
                messagebox.showinfo("提示", f"2v2 BP 仅允许发起人 {captain} 操作。")
                return

            selected = [k for k, v in self.bp_multi_vars.items() if v.get()]
            if state == "team_ban":
                payload = {
                    "type": "bp_submit",
                    "choices": selected,
                }
                self.send(payload)
                return
            if state == "team_pick":
                if len(selected) != 2:
                    messagebox.showwarning("提示", "请准确选择 2 个指挥官。")
                    return
                payload = {
                    "type": "bp_submit",
                    "choices": selected,
                }
                self.send(payload)
                return

            messagebox.showinfo("提示", "当前阶段无需提交。")
            return

    def send_chat(self) -> None:
        text = sanitize_text(self.chat_entry_var.get(), 200)
        if not text:
            return
        if self.current_match is None:
            messagebox.showinfo("提示", "当前没有可用聊天室。")
            return

        if self.send({"type": "chat_send", "text": text}):
            self.chat_entry_var.set("")

    def report_result(self, winner: str) -> None:
        if self.current_match is None:
            messagebox.showinfo("提示", "当前没有可汇报的比赛。")
            return

        state = sanitize_text(self.current_match.get("state"), 32)
        if state != "ready":
            messagebox.showinfo("提示", "比赛尚未完成 BP，不能汇报结果。")
            return

        if self.send({"type": "report_result", "winner": winner}):
            self.append_log("已提交比赛结果。")

    def update_countdown(self) -> None:
        if isinstance(self.match_deadline, float):
            left = int(self.match_deadline - time.time())
            if left < 0:
                left = 0
            self.countdown_var.set(f"倒计时: {left} 秒")
        else:
            self.countdown_var.set("倒计时: --")

        self.root.after(250, self.update_countdown)

    def update_buttons(self) -> None:
        has_auth = self.authenticated
        in_match = self.current_match is not None
        in_queue = bool(self.queued_mode)
        mode = self.mode_var.get()

        auth_buttons_enabled = self.connected and (not self.authenticated) and (not self.auth_pending)
        self.register_button.configure(state=tk.NORMAL if auth_buttons_enabled else tk.DISABLED)
        self.login_button.configure(state=tk.NORMAL if auth_buttons_enabled else tk.DISABLED)
        self.leaderboard_button.configure(state=tk.NORMAL if (self.connected and self.authenticated) else tk.DISABLED)

        idle = has_auth and not in_queue and not in_match
        self.start_queue_button.configure(state=tk.NORMAL if idle else tk.DISABLED)
        self.cancel_queue_button.configure(state=tk.NORMAL if in_queue else tk.DISABLED)

        mode_controls = tk.NORMAL if idle else tk.DISABLED
        self.mode_1v1.configure(state=mode_controls)
        self.mode_2v2.configure(state=mode_controls)

        commander_state = tk.NORMAL if (idle and mode == "1v1") else tk.DISABLED
        for check in self.commander_checks:
            check.configure(state=commander_state)

        teammate_state = tk.NORMAL if (idle and mode == "2v2") else tk.DISABLED
        self.teammate_entry.configure(state=teammate_state)

        bp_enabled = False
        if isinstance(self.current_match, dict):
            state = sanitize_text(self.current_match.get("state"), 32)
            mode_in_match = sanitize_text(self.current_match.get("mode"), 8)
            if mode_in_match == "2v2":
                side = self.my_side
                captain = ""
                if side in ("A", "B"):
                    captain = sanitize_text(self.current_match.get("teams", {}).get(side, {}).get("captain"), 20)
                bp_enabled = state in ("team_ban", "team_pick") and bool(captain) and self.nickname == captain
            else:
                bp_enabled = state in ("bp_ban", "bp_pick")
        self.bp_submit_button.configure(state=tk.NORMAL if bp_enabled else tk.DISABLED)

        chat_enabled = in_match
        self.chat_entry.configure(state=tk.NORMAL if chat_enabled else tk.DISABLED)
        self.chat_send_button.configure(state=tk.NORMAL if chat_enabled else tk.DISABLED)

        can_report = False
        if isinstance(self.current_match, dict):
            can_report = sanitize_text(self.current_match.get("state"), 32) == "ready"
        self.report_self_button.configure(state=tk.NORMAL if can_report else tk.DISABLED)
        self.report_opp_button.configure(state=tk.NORMAL if can_report else tk.DISABLED)

    def on_close(self) -> None:
        self.save_settings()
        if self.connected and self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.root.destroy()



def main() -> None:
    root = tk.Tk()
    LadderClientApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
