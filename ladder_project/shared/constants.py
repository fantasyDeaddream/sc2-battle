from __future__ import annotations

DEFAULT_HOST = "8.136.159.63"
DEFAULT_PORT = 47070
BUFFER_SIZE = 4096
BP_SECONDS = 60

MATCH_BASE_WINDOW = 50
MATCH_EXPAND_STEP = 25
MATCH_EXPAND_SECONDS = 10
MATCH_MAX_WINDOW = 300

K_MAX = 40.0
K_MIN = 20.0
INITIAL_RATING = 1500.0

COMMANDER_POOL = [
    "雷诺",
    "凯瑞甘",
    "阿塔尼斯",
    "斯旺",
    "扎加拉",
    "阿巴瑟",
    "霍纳与汉",
    "菲尼克斯",
    "阿拉纳克",
    "斯台特曼",
    "蒙斯克",
    "泰凯斯",
    "沃拉尊",
    "泽拉图",
    "德哈卡",
    "凯拉克斯",
    "诺娃",
    "斯托科夫",
]
COMMANDER_SET = set(COMMANDER_POOL)

MAP_POOL_1V1 = [
    "10000 Feet LE",
    "Celestial Enclave LE",
    "Mothership LE",
    "Old Republic LE",
    "Ruby Rock LE",
    "Taito Citadel LE",
    "Tourmaline LE",
    "White Rabbit LE",
    "Winter Madness LE",
]

MAP_POOL_2V2 = [
    "Fields of Death CE",
    "Gemgarden LE",
    "New Bed of Chaos LE",
    "Reclamation LE",
    "Rhoskallian LE",
    "Rohana LE",
    "Rust Bucket LE",
    "Undercurrent LE",
    "Yellowjacket",
]
