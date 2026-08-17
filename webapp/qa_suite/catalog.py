"""Catalogs and JSON contracts used by the QA suite."""

PUBLIC_SMOKE_PATHS = (
    "/",
    "/sports",
    "/sports/soccer",
    "/sports/baseball",
    "/sports/cricket",
    "/sports/tennis",
    "/sports/american-football",
    "/todays-picks",
    "/todays-picks/combos",
    "/todays-picks/soccer",
    "/todays-picks/baseball",
    "/todays-picks/american_football",
    "/premier_league/",
    "/mlb/fixtures",
    "/nfl/fixtures",
    "/nfl/results",
    "/tennis/fixtures",
    "/crypto",
    "/stocks",
    "/accuracy",
    "/about",
    "/faq",
    "/terms",
    "/privacy",
    "/feedback",
    "/sitemap.xml",
    "/robots.txt",
    "/llms.txt",
    "/ads.txt",
    "/healthz",
)

ADMIN_PATHS = (
    "/admin",
    "/results",
    "/todays-picks/digest",
    "/todays-picks/top3",
    "/todays-picks/digest.json",
    "/todays-picks/combos/results",
    "/premier_league/backtest",
)

RETIRED_PATHS = (
    "/dubai",
    "/dubai/",
    "/metals",
    "/metals/",
    "/contact",
)

NFL_PREDICT_CONTRACT = {
    "required": (
        "home", "away", "home_prob", "away_prob", "favourite",
        "elo_home", "elo_away", "predicted_total_points",
        "total_points_pick", "total_points_line", "spread_pick",
        "spread_home", "insights",
    ),
    "types": {
        "home": str,
        "away": str,
        "home_prob": (int, float),
        "away_prob": (int, float),
        "favourite": str,
        "elo_home": (int, float),
        "elo_away": (int, float),
        "predicted_total_points": (int, float),
        "insights": list,
    },
}

SOCCER_PREDICT_CONTRACT = {
    "required": ("Predicted_Result", "P_Home_Win", "Safer_Result_Pick"),
    "types": {
        "Predicted_Result": str,
        "P_Home_Win": (int, float, type(None)),
        "Safer_Result_Pick": str,
    },
}

MLB_PREDICT_CONTRACT = {
    "required": (
        "home", "away", "home_prob", "away_prob", "favourite",
        "predicted_total_runs", "insights",
    ),
    "types": {
        "home": str,
        "away": str,
        "home_prob": (int, float),
        "away_prob": (int, float),
        "favourite": str,
        "insights": list,
    },
}


def assert_contract(payload, spec):
    assert isinstance(payload, dict)
    missing = [k for k in spec["required"] if k not in payload]
    assert not missing, f"missing keys: {missing}"
    for key, expected in spec.get("types", {}).items():
        if key not in payload or payload[key] is None and type(None) in (
            expected if isinstance(expected, tuple) else (expected,)
        ):
            continue
        assert isinstance(payload[key], expected), (
            f"{key} type {type(payload[key]).__name__} not in {expected}"
        )
