import math
from typing import Dict, List, Optional, Tuple

from football_agent.domain.models import H2HStats, MarketPrediction, Odds


def compute_season_progress(played_rounds: int, total_rounds: int) -> float:
    return min(played_rounds / total_rounds, 1.0)


def compute_weights(g: float) -> Tuple[float, float, float]:
    w_M, w_F, w_C = g, 0.5, 1 - g
    S = w_M + w_F + w_C
    return w_M / S, w_F / S, w_C / S


def compute_rating(
    M: float,
    F: float,
    C: float,
    eliminated: bool,
    opponent_is_fighting: bool,
    wM: float,
    wF: float,
    wC: float,
) -> float:
    if eliminated and opponent_is_fighting:
        F = min(F + 0.05, 1.0)
        C = min(C + 0.05, 1.0)
    return wM * M + wF * F + wC * C


def compute_h2h_bias(h2h: H2HStats) -> float:
    """
    Возвращает bias ∈ [-0.3, 0.3].
    Положительное значение → хозяева текущего матча исторически сильнее в этой паре.
    Отрицательное → гости исторически сильнее.
    Сглаживается при малой выборке через alpha.
    """
    if h2h.total_matches == 0:
        return 0.0

    total = h2h.total_matches
    decisive = h2h.home_wins + h2h.away_wins

    if decisive == 0:
        win_bias = 0.0
    else:
        # 0.5 = паритет, >0.5 = хозяева чаще побеждали в этой паре
        home_win_rate = h2h.home_wins / decisive
        win_bias = home_win_rate - 0.5  # ∈ [-0.5, 0.5]

    # Много ничьих → пара равная → снижаем доверие к win_bias
    draw_rate = h2h.draws / total
    draw_dampening = 1.0 - draw_rate * 0.5  # ∈ [0.5, 1.0]

    raw_bias = win_bias * draw_dampening

    # При малой выборке bias стремится к 0
    # 5 матчей → alpha=0.5, 10+ матчей → alpha=1.0
    alpha = min(total / 10.0, 1.0)
    smoothed_bias = raw_bias * alpha

    return max(-0.3, min(0.3, smoothed_bias))


def compute_market_probabilities(
    R_home: float,
    R_away: float,
    avg_goals_home: float,
    avg_goals_away: float,
    h2h: H2HStats,
) -> Dict[str, float]:
    """
    Порядок блоков внутри функции:
    1. Базовый softmax 1X2
    2. H2H коррекция логитов (если total_matches >= 3)
    3. Пересчёт двойных шансов
    4. BTTS с учётом H2H
    """

    def clip(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))

    diff = R_home - R_away
    k = 3.0

    # --- Блок 1: Базовый softmax ---
    exp_h = math.exp(k * diff)
    exp_a = math.exp(-k * diff)
    exp_d = math.exp(k * (1 - abs(diff)) - k * 0.5)
    Z = exp_h + exp_a + exp_d

    P_home = exp_h / Z
    P_away = exp_a / Z
    P_draw = exp_d / Z

    # --- Блок 2: H2H коррекция 1X2 ---
    # Применяем только при достаточной выборке (минимум 3 матча)
    if h2h.total_matches >= 3:
        h2h_bias = compute_h2h_bias(h2h)

        # Вес H2H растёт с выборкой: 3 матча → ~0.10, 10+ матчей → ~0.25
        h2h_weight = 0.10 + 0.15 * min(h2h.total_matches / 10.0, 1.0)

        # Корректируем логиты: bias>0 усиливает хозяев, bias<0 усиливает гостей
        logit_h = k * diff + h2h_bias * h2h_weight * k
        logit_a = -k * diff - h2h_bias * h2h_weight * k
        logit_d = k * (1 - abs(diff + h2h_bias * h2h_weight)) - k * 0.5

        exp_h_adj = math.exp(logit_h)
        exp_a_adj = math.exp(logit_a)
        exp_d_adj = math.exp(logit_d)
        Z_adj = exp_h_adj + exp_a_adj + exp_d_adj

        P_home = exp_h_adj / Z_adj
        P_away = exp_a_adj / Z_adj
        P_draw = exp_d_adj / Z_adj

    # --- Блок 3: Двойные шансы (пересчёт после возможной H2H коррекции) ---
    P_1X = P_home + P_draw
    P_X2 = P_away + P_draw

    # --- Блок 4: BTTS ---
    lam = avg_goals_home + avg_goals_away
    base = 0.5
    if avg_goals_home >= 1.0 and avg_goals_away >= 1.0:
        base += 0.15
    if lam >= 2.5:
        base += 0.10
    if lam >= 3.0:
        base += 0.05

    # Коррекция BTTS через H2H если достаточно матчей
    if h2h.total_matches >= 3:
        # вес H2H растёт с количеством матчей, но ограничен сверху
        h2h_w = min(h2h.total_matches / 10.0, 0.4)
        base = base * (1 - h2h_w) + h2h.btts_rate * h2h_w

    P_btts = clip(base, 0.30, 0.90)

    return {
        "HOME_WIN": round(P_home, 4),
        "AWAY_WIN": round(P_away, 4),
        "HOME_NOT_LOSE": round(P_1X, 4),
        "AWAY_NOT_LOSE": round(P_X2, 4),
        "BTTS_YES": round(P_btts, 4),
    }


MARKET_LABELS: Dict[str, str] = {
    "HOME_WIN": "П1",
    "AWAY_WIN": "П2",
    "HOME_NOT_LOSE": "1X (хозяева не проиграют)",
    "AWAY_NOT_LOSE": "X2 (гости не проиграют)",
    "BTTS_YES": "Обе забьют — Да",
}


def _odds_for_market(odds: Optional[Odds], market: str) -> Optional[float]:
    if not odds:
        return None
    if market == "HOME_WIN":
        return odds.home_win
    if market == "AWAY_WIN":
        return odds.away_win
    if market == "HOME_NOT_LOSE":
        return odds.home_not_lose
    if market == "AWAY_NOT_LOSE":
        return odds.away_not_lose
    if market == "BTTS_YES":
        return odds.btts_yes
    return None


def select_best_markets(
    probabilities: Dict[str, float],
    odds: Optional[Odds] = None,
    top_n: int = 3,
) -> List[MarketPrediction]:
    """
    Преобразует вероятности (и опционально odds) в список MarketPrediction,
    отсортированный по probability по убыванию.
    """
    preds: List[MarketPrediction] = []
    for market, p in probabilities.items():
        preds.append(
            MarketPrediction(
                market=market,
                probability=float(p),
                odds=_odds_for_market(odds, market),
                label=MARKET_LABELS.get(market, market),
            )
        )

    preds.sort(key=lambda x: x.probability, reverse=True)
    return preds[: max(1, int(top_n))]

