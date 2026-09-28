from __future__ import annotations

from dataclasses import dataclass

from .constants import INITIAL_RATING
from .constants import K_MAX
from .constants import K_MIN


@dataclass
class RatingValue:
    rating: float = INITIAL_RATING
    games: int = 0


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def dynamic_k(games: int) -> float:
    clamped_games = games if games >= 0 else 0
    q = max(0.0, 1.0 - min(clamped_games, 50) / 50.0)
    return K_MIN + (K_MAX - K_MIN) * q


def update_elo(a: RatingValue, b: RatingValue, score_a: float) -> tuple[float, float]:
    exp_a = expected_score(a.rating, b.rating)
    exp_b = 1.0 - exp_a

    k_a = dynamic_k(a.games)
    k_b = dynamic_k(b.games)

    delta_a = k_a * (score_a - exp_a)
    delta_b = k_b * ((1.0 - score_a) - exp_b)

    a.rating += delta_a
    b.rating += delta_b
    a.games += 1
    b.games += 1

    return delta_a, delta_b
