from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, BinaryIO

REPLAY_VERDICT_UNBOUND = "unbound"
REPLAY_VERDICT_ME_WIN = "me_win"
REPLAY_VERDICT_OPPONENT_WIN = "opponent_win"
REPLAY_VERDICT_NOT_FOUND = "not_found"
STRICT_REPLAY_VERDICT_VALID = "valid"
STRICT_REPLAY_VERDICT_INVALID = "invalid"
STRICT_REPLAY_VERDICT_MANUAL_REQUIRED = "manual_required"
UTC_PLUS_8 = timezone(timedelta(hours=8))
REPLAY_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def normalize_game_nickname(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def validate_game_nickname(value: str | None) -> str | None:
    candidate = normalize_game_nickname(value)
    if candidate is None:
        return "请填写游戏内昵称。"
    if len(candidate) > 32:
        return "游戏内昵称长度不能超过 32 个字符。"
    return None


def replay_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def replay_datetime_text(value: Any) -> str | None:
    normalized = replay_datetime_value(value)
    if normalized is not None:
        return normalized.strftime(f"{REPLAY_TIME_FORMAT} UTC+8")
    if value is None:
        return None
    return str(value)


def replay_datetime_value(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).astimezone(UTC_PLUS_8)
        return value.astimezone(UTC_PLUS_8)
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        for suffix, source_timezone in (("UTC+8", UTC_PLUS_8), ("UTC", timezone.utc)):
            if candidate.endswith(f" {suffix}"):
                raw_value = candidate[: -(len(suffix) + 1)]
                try:
                    parsed = datetime.strptime(raw_value, REPLAY_TIME_FORMAT)
                except ValueError:
                    return None
                return parsed.replace(tzinfo=source_timezone).astimezone(UTC_PLUS_8)
        try:
            return datetime.strptime(candidate, REPLAY_TIME_FORMAT).replace(tzinfo=UTC_PLUS_8)
        except ValueError:
            return None
    return None


def replay_map_match_key(value: str | None) -> str | None:
    candidate = replay_text(value)
    if candidate is None:
        return None
    compact = re.sub(r"\s+", " ", candidate.strip())
    return compact.casefold() or None


def nickname_match_key(value: str | None) -> str | None:
    candidate = normalize_game_nickname(value)
    if candidate is None:
        return None
    return candidate.casefold()


def player_matches_bound_nickname(player_name: str | None, bound_game_nickname: str | None) -> bool:
    player_key = nickname_match_key(player_name)
    nickname_key = nickname_match_key(bound_game_nickname)
    return bool(player_key and nickname_key and player_key == nickname_key)


def team_contains_nickname(team: dict[str, Any], bound_game_nickname: str | None) -> bool:
    return any(
        player_matches_bound_nickname(player.get("name"), bound_game_nickname)
        for player in team.get("players", [])
    )


def build_nickname_verdict(teams: list[dict[str, Any]], bound_game_nickname: str | None) -> dict[str, Any]:
    normalized_nickname = normalize_game_nickname(bound_game_nickname)
    if normalized_nickname is None:
        return {
            "status": REPLAY_VERDICT_UNBOUND,
            "label": "未绑定游戏内昵称",
            "detail": "当前账号尚未绑定游戏内昵称，因此无法按昵称判定 replay 胜负。",
            "bound_game_nickname": None,
        }

    for team in teams:
        if not team["matches_bound_game_nickname"]:
            continue
        if team["is_winner"]:
            return {
                "status": REPLAY_VERDICT_ME_WIN,
                "label": "我方胜利",
                "detail": f"绑定昵称 {normalized_nickname} 位于胜者队伍中。",
                "bound_game_nickname": normalized_nickname,
            }
        return {
            "status": REPLAY_VERDICT_OPPONENT_WIN,
            "label": "对方胜利",
            "detail": f"绑定昵称 {normalized_nickname} 位于败者队伍中。",
            "bound_game_nickname": normalized_nickname,
        }

    return {
        "status": REPLAY_VERDICT_NOT_FOUND,
        "label": "未匹配到绑定昵称",
        "detail": f"没有在本局 replay 的玩家名单中找到绑定昵称 {normalized_nickname}。",
        "bound_game_nickname": normalized_nickname,
    }


def build_replay_team(team: Any, bound_game_nickname: str | None) -> dict[str, Any]:
    players = []
    team_is_winner = replay_text(getattr(team, "result", None)) == "Win"
    matches_bound_game_nickname = False

    for player in getattr(team, "players", []) or []:
        player_name = replay_text(getattr(player, "name", None)) or "未知选手"
        result = replay_text(getattr(player, "result", None)) or "未知"
        player_matches = player_matches_bound_nickname(player_name, bound_game_nickname)
        if result == "Win":
            team_is_winner = True
        if player_matches:
            matches_bound_game_nickname = True
        players.append(
            {
                "name": player_name,
                "pick_race": replay_text(getattr(player, "pick_race", None)) or "未知",
                "play_race": replay_text(getattr(player, "play_race", None)) or replay_text(getattr(player, "pick_race", None)) or "未知",
                "result": result,
                "matches_bound_game_nickname": player_matches,
            }
        )

    return {
        "label": f"Team {getattr(team, 'number', '?')}",
        "lineup": replay_text(getattr(team, "lineup", None)),
        "result": replay_text(getattr(team, "result", None)) or ("Win" if team_is_winner else "未知"),
        "is_winner": team_is_winner,
        "matches_bound_game_nickname": matches_bound_game_nickname,
        "players": players,
    }


def build_strict_match_verdict(
    teams: list[dict[str, Any]],
    player1_game_nickname: str | None,
    player2_game_nickname: str | None,
    player1_label: str = "选手 1",
    player2_label: str = "选手 2",
) -> dict[str, Any]:
    normalized_player1 = normalize_game_nickname(player1_game_nickname)
    normalized_player2 = normalize_game_nickname(player2_game_nickname)

    if normalized_player1 is None or normalized_player2 is None:
        missing_labels = []
        if normalized_player1 is None:
            missing_labels.append(player1_label)
        if normalized_player2 is None:
            missing_labels.append(player2_label)
        return {
            "status": STRICT_REPLAY_VERDICT_INVALID,
            "label": "昵称未绑定完整",
            "detail": f"{ '、'.join(missing_labels) } 尚未绑定游戏内昵称，当前 replay 不能用于自动判定胜负。",
            "winner_slot": None,
        }

    if nickname_match_key(normalized_player1) == nickname_match_key(normalized_player2):
        return {
            "status": STRICT_REPLAY_VERDICT_MANUAL_REQUIRED,
            "label": "昵称冲突",
            "detail": f"{player1_label} 与 {player2_label} 绑定了相同的游戏内昵称，当前 replay 需由玩家手动选择本局赢家。",
            "winner_slot": None,
        }

    winner_teams = [team for team in teams if team.get("is_winner")]
    loser_teams = [team for team in teams if not team.get("is_winner")]
    if len(winner_teams) != 1 or len(loser_teams) != 1:
        return {
            "status": STRICT_REPLAY_VERDICT_INVALID,
            "label": "胜负队伍未识别",
            "detail": "当前 replay 没有稳定识别出唯一胜者队伍和唯一败者队伍，无法自动判定。",
            "winner_slot": None,
        }

    winner_team = winner_teams[0]
    loser_team = loser_teams[0]
    player1_in_winner = team_contains_nickname(winner_team, normalized_player1)
    player1_in_loser = team_contains_nickname(loser_team, normalized_player1)
    player2_in_winner = team_contains_nickname(winner_team, normalized_player2)
    player2_in_loser = team_contains_nickname(loser_team, normalized_player2)

    if player1_in_winner and player2_in_loser and not player1_in_loser and not player2_in_winner:
        return {
            "status": STRICT_REPLAY_VERDICT_VALID,
            "label": f"{player1_label} 胜利",
            "detail": f"{player1_label} 的绑定昵称位于胜者队伍，且 {player2_label} 的绑定昵称位于败者队伍。",
            "winner_slot": "player1",
        }
    if player2_in_winner and player1_in_loser and not player2_in_loser and not player1_in_winner:
        return {
            "status": STRICT_REPLAY_VERDICT_VALID,
            "label": f"{player2_label} 胜利",
            "detail": f"{player2_label} 的绑定昵称位于胜者队伍，且 {player1_label} 的绑定昵称位于败者队伍。",
            "winner_slot": "player2",
        }

    return {
        "status": STRICT_REPLAY_VERDICT_INVALID,
        "label": "匹配关系无效",
        "detail": (
            f"replay 中未同时满足“{player1_label} 在胜者队伍且 {player2_label} 在败者队伍”"
            f"或“{player2_label} 在胜者队伍且 {player1_label} 在败者队伍”的严格条件。"
        ),
        "winner_slot": None,
    }


def build_strict_team_match_verdict(
    teams: list[dict[str, Any]],
    team_a_game_nicknames: list[str | None],
    team_b_game_nicknames: list[str | None],
    team_a_label: str = "队伍 A",
    team_b_label: str = "队伍 B",
) -> dict[str, Any]:
    normalized_team_a = [normalize_game_nickname(value) for value in team_a_game_nicknames]
    normalized_team_b = [normalize_game_nickname(value) for value in team_b_game_nicknames]

    missing_labels = []
    for index, value in enumerate(normalized_team_a, start=1):
        if value is None:
            missing_labels.append(f"{team_a_label} 选手 {index}")
    for index, value in enumerate(normalized_team_b, start=1):
        if value is None:
            missing_labels.append(f"{team_b_label} 选手 {index}")
    if missing_labels:
        return {
            "status": STRICT_REPLAY_VERDICT_INVALID,
            "label": "昵称未绑定完整",
            "detail": f"{ '、'.join(missing_labels) } 尚未绑定游戏内昵称，当前 replay 不能用于自动判定胜负。",
            "winner_side": None,
        }

    team_a_keys = [nickname_match_key(value) for value in normalized_team_a if value is not None]
    team_b_keys = [nickname_match_key(value) for value in normalized_team_b if value is not None]
    if len(team_a_keys) != len(set(team_a_keys)) or len(team_b_keys) != len(set(team_b_keys)):
        return {
            "status": STRICT_REPLAY_VERDICT_INVALID,
            "label": "队内昵称冲突",
            "detail": f"{team_a_label} 或 {team_b_label} 内部存在重复绑定昵称，当前 replay 不能用于自动判定胜负。",
            "winner_side": None,
        }
    if set(team_a_keys) & set(team_b_keys):
        return {
            "status": STRICT_REPLAY_VERDICT_INVALID,
            "label": "两队昵称冲突",
            "detail": f"{team_a_label} 与 {team_b_label} 存在重复绑定昵称，当前 replay 不能用于自动判定胜负。",
            "winner_side": None,
        }

    winner_teams = [team for team in teams if team.get("is_winner")]
    loser_teams = [team for team in teams if not team.get("is_winner")]
    if len(winner_teams) != 1 or len(loser_teams) != 1:
        return {
            "status": STRICT_REPLAY_VERDICT_INVALID,
            "label": "胜负队伍未识别",
            "detail": "当前 replay 没有稳定识别出唯一胜者队伍和唯一败者队伍，无法自动判定。",
            "winner_side": None,
        }

    winner_team = winner_teams[0]
    loser_team = loser_teams[0]
    team_a_all_in_winner = all(team_contains_nickname(winner_team, value) for value in normalized_team_a)
    team_a_any_in_winner = any(team_contains_nickname(winner_team, value) for value in normalized_team_a)
    team_a_all_in_loser = all(team_contains_nickname(loser_team, value) for value in normalized_team_a)
    team_a_any_in_loser = any(team_contains_nickname(loser_team, value) for value in normalized_team_a)
    team_b_all_in_winner = all(team_contains_nickname(winner_team, value) for value in normalized_team_b)
    team_b_any_in_winner = any(team_contains_nickname(winner_team, value) for value in normalized_team_b)
    team_b_all_in_loser = all(team_contains_nickname(loser_team, value) for value in normalized_team_b)
    team_b_any_in_loser = any(team_contains_nickname(loser_team, value) for value in normalized_team_b)

    if team_a_all_in_winner and not team_a_any_in_loser and team_b_all_in_loser and not team_b_any_in_winner:
        return {
            "status": STRICT_REPLAY_VERDICT_VALID,
            "label": f"{team_a_label} 胜利",
            "detail": f"{team_a_label} 的绑定昵称全部位于胜者队伍，且 {team_b_label} 的绑定昵称全部位于败者队伍。",
            "winner_side": "A",
        }
    if team_b_all_in_winner and not team_b_any_in_loser and team_a_all_in_loser and not team_a_any_in_winner:
        return {
            "status": STRICT_REPLAY_VERDICT_VALID,
            "label": f"{team_b_label} 胜利",
            "detail": f"{team_b_label} 的绑定昵称全部位于胜者队伍，且 {team_a_label} 的绑定昵称全部位于败者队伍。",
            "winner_side": "B",
        }

    return {
        "status": STRICT_REPLAY_VERDICT_INVALID,
        "label": "匹配关系无效",
        "detail": (
            f"replay 中未同时满足“{team_a_label} 的绑定昵称全部在胜者队伍且 {team_b_label} 的绑定昵称全部在败者队伍”"
            f"或“{team_b_label} 的绑定昵称全部在胜者队伍且 {team_a_label} 的绑定昵称全部在败者队伍”的严格条件。"
        ),
        "winner_side": None,
    }


def inspect_replay(source: BinaryIO, filename: str, bound_game_nickname: str | None = None) -> dict[str, Any]:
    cleaned_filename = (filename or "").strip()
    if not cleaned_filename:
        raise ValueError("请先选择一个 replay 文件。")
    if not cleaned_filename.lower().endswith(".sc2replay"):
        raise ValueError("请上传 .SC2Replay 文件。")

    try:
        import sc2reader
    except ModuleNotFoundError as exc:
        raise RuntimeError("服务器当前未安装 sc2reader，请先安装依赖后再测试 replay 解析。") from exc

    source.seek(0)
    replay = sc2reader.load_replay(source, load_level=2)
    replay_start_time = replay_datetime_value(getattr(replay, "start_time", None))
    replay_end_time = replay_datetime_value(getattr(replay, "end_time", None))

    teams = [build_replay_team(team, bound_game_nickname) for team in (getattr(replay, "teams", []) or [])]
    winner_labels = [team["lineup"] or team["label"] for team in teams if team["is_winner"]]
    observer_names = [
        replay_text(getattr(observer, "name", None)) or "未知观察者"
        for observer in (getattr(replay, "observers", []) or [])
    ]

    return {
        "filename": cleaned_filename,
        "map_name": replay_text(getattr(replay, "map_name", None)) or "未知地图",
        "expansion": replay_text(getattr(replay, "expansion", None)) or "未知",
        "release_string": replay_text(getattr(replay, "release_string", None)) or "未知",
        "category": replay_text(getattr(replay, "category", None)) or "未知",
        "game_type": replay_text(getattr(replay, "real_type", None)) or replay_text(getattr(replay, "type", None)) or "未知",
        "speed": replay_text(getattr(replay, "speed", None)) or "未知",
        "region": replay_text(getattr(replay, "region", None)) or "未知",
        "start_time": replay_datetime_text(replay_start_time) or "未知",
        "end_time": replay_datetime_text(replay_end_time) or "未知",
        "start_time_dt": replay_start_time,
        "end_time_dt": replay_end_time,
        "game_length": replay_text(getattr(replay, "game_length", None)) or "未知",
        "real_length": replay_text(getattr(replay, "real_length", None)) or "未知",
        "winner_summary": " / ".join(winner_labels) if winner_labels else "未识别",
        "observer_names": observer_names,
        "teams": teams,
        "nickname_verdict": build_nickname_verdict(teams, bound_game_nickname),
    }