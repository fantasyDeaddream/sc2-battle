from __future__ import annotations

import random
from typing import Any

BP_STEP_SECONDS = 40

BP_MODE_NONE = "none"
BP_MODE_MAP_POOL = "map_pool"
BP_MODE_COMMANDER_DRAFT = "commander_draft"
BP_MODE_COMMANDER_CONQUEST = "commander_conquest_1v1"
BP_MODE_COMMANDER_CONQUEST_2V2 = "commander_conquest_2v2"
BP_MODE_LADDER_1V1 = "ladder_1v1_bp"
BP_MODE_EVO = "evo_bp"

EVO_CHOICE_BOTH = "bw_plus_sc2"
EVO_CHOICE_LOSER_BW_WINNER_SC2 = "loser_bw_winner_sc2"
EVO_CHOICE_LOSER_SC2_WINNER_BW = "loser_sc2_winner_bw"

COMMANDER_POOL_SHARED = "shared"
COMMANDER_POOL_SEPARATE = "separate"

COMMANDER_BAN_GLOBAL = "global"
COMMANDER_BAN_OPPONENT_ONLY = "opponent_only"

BP_MODE_LABELS = {
	BP_MODE_NONE: "不启用 BP",
	BP_MODE_MAP_POOL: "地图池 BP",
	BP_MODE_COMMANDER_DRAFT: "随机地图 + 指挥官 BP",
	BP_MODE_COMMANDER_CONQUEST: "指挥官1v1征服者",
	BP_MODE_COMMANDER_CONQUEST_2V2: "指挥官2v2征服者",
	BP_MODE_LADDER_1V1: "天梯 1v1 BP",
	BP_MODE_EVO: "EVO BP",
}

EVO_CHOICE_LABELS = {
	EVO_CHOICE_BOTH: "BW + SC2",
	EVO_CHOICE_LOSER_BW_WINNER_SC2: "败者 BW & 胜者 SC2",
	EVO_CHOICE_LOSER_SC2_WINNER_BW: "败者 SC2 & 胜者 BW",
}

COMMANDER_POOL_MODE_LABELS = {
	COMMANDER_POOL_SHARED: "双方共享指挥官池",
	COMMANDER_POOL_SEPARATE: "双方独立指挥官池",
}

COMMANDER_BAN_MODE_LABELS = {
	COMMANDER_BAN_GLOBAL: "Ban 全局生效",
	COMMANDER_BAN_OPPONENT_ONLY: "Ban 仅限制对方",
}

MAP_BP_PATTERNS = {
	3: {
		"ban_order": "ABBAAB",
		"map_order": "BAC",
	},
	5: {
		"ban_order": "ABBA",
		"map_order": "BABAC",
	},
	7: {
		"ban_order": "AB",
		"map_order": "BABABAC",
	},
}

COMMANDER_SCORES = {
	"泽拉图": 5,
	"诺娃": 5,
	"阿塔尼斯": 4,
	"阿拉纳克": 4,
	"扎加拉": 4,
	"斯旺": 4,
	"蒙斯克": 3,
	"阿巴瑟": 3,
	"菲尼克斯": 3,
	"霍纳与汉": 3,
	"沃拉尊": 3,
	"雷诺": 2,
	"凯瑞甘": 2,
	"凯拉克斯": 2,
	"德哈卡": 2,
	"泰凯斯": 2,
	"斯托科夫": 1,
	"斯台特曼": 1,
}

COMMANDER_STEP_ORDER = (
	("score_limit", "A"),
	("ban", "A"),
	("ban", "B"),
	("pick", "A"),
	("pick", "B"),
	("pick", "B"),
	("pick", "A"),
)

COMMANDER_SCORE_LIMIT_CHOICES = (5, 6, 7)
ZERO_SCORE_STUKOV_NAME = "斯托科夫 (0)"


def normalize_bp_mode(value: str | None) -> str:
	return value if value in BP_MODE_LABELS else BP_MODE_NONE


def normalize_commander_pool_mode(value: str | None) -> str:
	return value if value in COMMANDER_POOL_MODE_LABELS else COMMANDER_POOL_SHARED


def normalize_commander_ban_mode(value: str | None) -> str:
	return value if value in COMMANDER_BAN_MODE_LABELS else COMMANDER_BAN_GLOBAL


def bp_mode_enabled(value: str | None) -> bool:
	return normalize_bp_mode(value) != BP_MODE_NONE


def normalize_evo_choice(value: str | None) -> str:
	return value if value in EVO_CHOICE_LABELS else EVO_CHOICE_BOTH


def evo_choice_options() -> list[dict[str, str]]:
	return [
		{"value": value, "label": label}
		for value, label in EVO_CHOICE_LABELS.items()
	]


def commander_catalog() -> list[dict[str, Any]]:
	return [
		{"name": name, "score": score}
		for name, score in sorted(COMMANDER_SCORES.items(), key=lambda item: (-item[1], item[0]))
	]


def commander_score(name: str) -> int:
	if name == ZERO_SCORE_STUKOV_NAME:
		return 0
	return int(COMMANDER_SCORES[name])


def commander_option_label(name: str) -> str:
	if name == ZERO_SCORE_STUKOV_NAME:
		return ZERO_SCORE_STUKOV_NAME
	return f"{name} ({commander_score(name)})"


def commander_other_actor(actor: str) -> str:
	return "B" if actor == "A" else "A"


def side_assignments_for_game(
	game_number: int,
	player1_registration_id: int | None,
	player2_registration_id: int | None,
) -> dict[str, int | None]:
	if game_number % 2 == 1:
		return {"A": player1_registration_id, "B": player2_registration_id}
	return {"A": player2_registration_id, "B": player1_registration_id}


def map_bp_required_map_count(best_of: int) -> int:
	pattern = MAP_BP_PATTERNS.get(best_of)
	if pattern is None:
		raise ValueError(f"Unsupported BO for map BP: {best_of}")
	return len(pattern["ban_order"]) + len(pattern["map_order"])


def build_map_bp_state(best_of: int, map_pool: list[str]) -> dict[str, Any]:
	pattern = MAP_BP_PATTERNS.get(best_of)
	if pattern is None:
		raise ValueError(f"Unsupported BO for map BP: {best_of}")

	required_count = map_bp_required_map_count(best_of)
	if len(map_pool) < required_count:
		raise ValueError(f"Map pool must contain at least {required_count} maps for BO{best_of} map BP")

	return {
		"mode": BP_MODE_MAP_POOL,
		"best_of": best_of,
		"available_maps": list(map_pool[:required_count]),
		"ban_order": pattern["ban_order"],
		"map_order": pattern["map_order"],
		"step_index": 0,
		"actions": [],
		"games": [],
		"completed": False,
	}


def current_map_bp_step(state: dict[str, Any]) -> dict[str, Any] | None:
	step_index = int(state.get("step_index", 0))
	ban_order = state.get("ban_order", "")
	map_order = state.get("map_order", "")
	if step_index < len(ban_order):
		return {
			"type": "ban",
			"actor": ban_order[step_index],
			"label": f"{ban_order[step_index]} 禁用地图",
		}

	pick_index = step_index - len(ban_order)
	if pick_index >= len(map_order):
		return None

	actor = map_order[pick_index]
	return {
		"type": "decider" if actor == "C" else "pick",
		"actor": actor,
		"game_number": len(state.get("games", [])) + 1,
		"label": "系统保留决胜图" if actor == "C" else f"{actor} 选择地图",
	}


def finalize_map_bp_if_needed(state: dict[str, Any]) -> dict[str, Any]:
	step = current_map_bp_step(state)
	if step is None:
		state["completed"] = True
		return state

	if step["type"] == "decider":
		remaining = list(state.get("available_maps", []))
		if len(remaining) != 1:
			raise ValueError("Decider map requires exactly one remaining map")
		map_name = remaining[0]
		state["available_maps"] = []
		state.setdefault("actions", []).append(
			{
				"type": "decider",
				"actor": "C",
				"map_name": map_name,
				"game_number": step["game_number"],
			}
		)
		state.setdefault("games", []).append(
			{
				"game_number": step["game_number"],
				"map_name": map_name,
				"actor": "C",
			}
		)
		state["step_index"] = int(state.get("step_index", 0)) + 1
		state["completed"] = current_map_bp_step(state) is None
	return state


def apply_map_bp_action(state: dict[str, Any], actor: str, map_name: str) -> dict[str, Any]:
	step = current_map_bp_step(state)
	if step is None:
		raise ValueError("Map BP is already complete")
	if step["type"] == "decider":
		raise ValueError("Decider map is chosen automatically")
	if actor != step["actor"]:
		raise ValueError("It is not this actor's turn")

	available_maps = list(state.get("available_maps", []))
	if map_name not in available_maps:
		raise ValueError("Selected map is not available")

	available_maps.remove(map_name)
	state["available_maps"] = available_maps
	state.setdefault("actions", []).append(
		{
			"type": step["type"],
			"actor": actor,
			"map_name": map_name,
			"game_number": step.get("game_number"),
		}
	)
	if step["type"] == "pick":
		state.setdefault("games", []).append(
			{
				"game_number": step["game_number"],
				"map_name": map_name,
				"actor": actor,
			}
		)

	state["step_index"] = int(state.get("step_index", 0)) + 1
	return finalize_map_bp_if_needed(state)


def build_commander_game_state(
	game_number: int,
	best_of: int,
	map_pool: list[str],
	used_maps: list[str],
	player1_registration_id: int | None,
	player2_registration_id: int | None,
	pool_mode: str = COMMANDER_POOL_SHARED,
	ban_mode: str = COMMANDER_BAN_GLOBAL,
) -> dict[str, Any]:
	available_maps = [map_name for map_name in map_pool if map_name not in set(used_maps)]
	if not available_maps:
		raise ValueError("Commander BP requires enough distinct maps for each game")

	assignments = side_assignments_for_game(game_number, player1_registration_id, player2_registration_id)
	picker = random.Random(f"{best_of}:{game_number}:{'|'.join(map_pool)}:{player1_registration_id}:{player2_registration_id}")
	selected_map = picker.choice(available_maps)
	return {
		"mode": BP_MODE_COMMANDER_DRAFT,
		"game_number": game_number,
		"best_of": best_of,
		"map_name": selected_map,
		"side_assignments": assignments,
		"pool_mode": normalize_commander_pool_mode(pool_mode),
		"ban_mode": normalize_commander_ban_mode(ban_mode),
		"score_limit": None,
		"bans": {},
		"picks": {"A": [], "B": []},
		"pending_choice": None,
		"step_index": 0,
		"completed": False,
	}


def current_commander_step(state: dict[str, Any]) -> dict[str, Any] | None:
	step_index = int(state.get("step_index", 0))
	if step_index >= len(COMMANDER_STEP_ORDER):
		return None
	action_type, actor = COMMANDER_STEP_ORDER[step_index]
	return {
		"type": action_type,
		"actor": actor,
	}


def pending_commander_action(state: dict[str, Any]) -> dict[str, Any] | None:
	pending = state.get("pending_choice")
	if not isinstance(pending, dict):
		return None
	step = current_commander_step(state)
	if step is None:
		return None
	if int(pending.get("step_index", -1)) != int(state.get("step_index", 0)):
		return None
	if pending.get("type") != step["type"] or pending.get("actor") != step["actor"]:
		return None
	return pending


def commander_banned_for_actor(state: dict[str, Any], actor: str) -> set[str]:
	bans = state.get("bans") or {}
	if normalize_commander_ban_mode(state.get("ban_mode")) == COMMANDER_BAN_GLOBAL:
		return set(bans.values())
	opponent = commander_other_actor(actor)
	return {bans[opponent]} if opponent in bans else set()


def commander_taken_for_actor(state: dict[str, Any], actor: str) -> set[str]:
	picks = state.get("picks") or {}
	if normalize_commander_pool_mode(state.get("pool_mode")) == COMMANDER_POOL_SHARED:
		return set(picks.get("A", [])) | set(picks.get("B", []))
	return set(picks.get(actor, []))


def commander_pick_entries_for_actor(state: dict[str, Any], actor: str) -> list[dict[str, Any]]:
	if state.get("score_limit") is None:
		return []

	banned = commander_banned_for_actor(state, actor)
	taken = commander_taken_for_actor(state, actor)
	remaining_budget = int(state["score_limit"]) - commander_pick_total(state, actor)
	entries = [
		entry
		for entry in commander_catalog()
		if entry["name"] not in banned and entry["name"] not in taken and int(entry["score"]) <= remaining_budget
	]
	if not entries and ZERO_SCORE_STUKOV_NAME not in set((state.get("picks") or {}).get(actor, [])):
		entries.append({"name": ZERO_SCORE_STUKOV_NAME, "score": 0})
	return entries


def commander_action_options(state: dict[str, Any]) -> list[dict[str, Any]]:
	step = current_commander_step(state)
	if step is None:
		return []

	if step["type"] == "score_limit":
		return [
			{"value": str(score_limit), "label": f"{score_limit} 分上限", "score": score_limit}
			for score_limit in COMMANDER_SCORE_LIMIT_CHOICES
		]

	if step["type"] == "ban":
		banned = set((state.get("bans") or {}).values())
		return [
			{
				"value": entry["name"],
				"label": commander_option_label(entry["name"]),
				"score": entry["score"],
			}
			for entry in commander_catalog()
			if entry["name"] not in banned
		]

	return [
		{
			"value": entry["name"],
			"label": commander_option_label(entry["name"]),
			"score": entry["score"],
		}
		for entry in commander_pick_entries_for_actor(state, step["actor"])
	]


def remaining_commander_pool(state: dict[str, Any], actor: str | None = None) -> list[str]:
	target_actor = actor
	if target_actor is None:
		step = current_commander_step(state)
		target_actor = step["actor"] if step is not None else "A"
	return [entry["name"] for entry in commander_pick_entries_for_actor(state, target_actor)]


def commander_pick_total(state: dict[str, Any], actor: str) -> int:
	return sum(commander_score(name) for name in state.get("picks", {}).get(actor, []))


def set_pending_commander_action(
	state: dict[str, Any],
	actor: str,
	action_value: str,
) -> dict[str, Any]:
	step = current_commander_step(state)
	if step is None:
		raise ValueError("Commander BP is already complete")
	if actor != step["actor"]:
		raise ValueError("It is not this actor's turn")

	options = commander_action_options(state)
	valid_values = {option["value"] for option in options}
	if action_value not in valid_values:
		raise ValueError("Selected option is not available")

	state["pending_choice"] = {
		"actor": actor,
		"type": step["type"],
		"step_index": int(state.get("step_index", 0)),
		"value": action_value,
	}
	return state


def apply_commander_action(
	state: dict[str, Any],
	actor: str,
	action_value: str,
) -> dict[str, Any]:
	step = current_commander_step(state)
	if step is None:
		raise ValueError("Commander BP is already complete")
	if actor != step["actor"]:
		raise ValueError("It is not this actor's turn")

	action_type = step["type"]
	if action_type == "score_limit":
		score_limit = int(action_value)
		if score_limit not in COMMANDER_SCORE_LIMIT_CHOICES:
			raise ValueError("Commander score limit must be between 5 and 7")
		state["score_limit"] = score_limit
	elif action_type == "ban":
		valid_values = {option["value"] for option in commander_action_options(state)}
		if action_value not in valid_values:
			raise ValueError("Selected commander is not available for ban")
		state.setdefault("bans", {})[actor] = action_value
	else:
		valid_values = {option["value"] for option in commander_action_options(state)}
		if action_value not in valid_values:
			raise ValueError("Selected commander is not available for pick")
		if state.get("score_limit") is None:
			raise ValueError("Commander BP requires a score limit before picks")

		next_total = commander_pick_total(state, actor) + commander_score(action_value)
		if next_total > int(state["score_limit"]):
			raise ValueError("Selected commanders exceed the configured score limit")
		state.setdefault("picks", {}).setdefault(actor, []).append(action_value)

	state["pending_choice"] = None
	state["step_index"] = int(state.get("step_index", 0)) + 1
	state["completed"] = current_commander_step(state) is None
	return state


def confirm_commander_action(state: dict[str, Any], actor: str) -> dict[str, Any]:
	pending = pending_commander_action(state)
	if pending is None or pending.get("actor") != actor:
		raise ValueError("当前没有可确认的预选操作")
	return apply_commander_action(state, actor, str(pending["value"]))


def auto_complete_commander_action(state: dict[str, Any]) -> dict[str, Any]:
	step = current_commander_step(state)
	if step is None:
		raise ValueError("Commander BP is already complete")
	options = commander_action_options(state)
	if not options:
		raise ValueError("No legal commander action is available")
	chosen_option = random.choice(options)
	return apply_commander_action(state, step["actor"], str(chosen_option["value"]))
