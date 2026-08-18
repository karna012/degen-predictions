"""
/llms.txt and /llms-full.txt — citation-oriented summaries for AI crawlers
(llmstxt.org). Built dynamically so OOS figures stay current.
"""


def _oos_lines(highlights):
    rows = []
    for row in highlights or []:
        label = row.get("label") or row.get("id")
        pct = row.get("pct")
        if label and pct:
            rows.append(f"- {label}: {pct} full-slate OOS match-winner (holdout)")
    return rows


def build_llms_txt(highlights=None, updated=None, full=False) -> str:
    oos = "\n".join(_oos_lines(highlights)) or "- Live OOS chips load from calibrated threshold files on /accuracy"
    stamp = updated or "see site"
    core = f"""# Degen Predictions

> Free ML / AI forecasts for sports, crypto, and stocks. Educational research — not betting or investment advice. No account required.

Updated: {stamp}
Also known as: Degen Prediction · degenpredictions.com
Contact: info@degenpredictions.com
Telegram daily slips: https://degenpredictions.com/go/telegram

## What the numbers mean

- Full-slate OOS = chronological out-of-sample match-winner hit rate on holdout data (not marketing).
- Today's Picks / Best Combos = selective high-probability legs only (stricter bar than full-slate).
- Public ledgers: /accuracy · /mlb/results · /tennis/results · cricket */results

## Live full-slate OOS (when thresholds exist)

{oos}

## Sports

- Sports hub: https://degenpredictions.com/sports
- Today's Picks: https://degenpredictions.com/todays-picks
- Multi bet / Best Combos: https://degenpredictions.com/todays-picks/combos
- MLB predictions: https://degenpredictions.com/mlb/fixtures · Today's MLB picks https://degenpredictions.com/todays-picks/baseball/mlb · results https://degenpredictions.com/mlb/results
- NPB (Japanese pro baseball) predictions: https://degenpredictions.com/npb/fixtures · results https://degenpredictions.com/npb/results
- NFL predictions: https://degenpredictions.com/nfl/fixtures · hub https://degenpredictions.com/sports/american-football · Today's Picks https://degenpredictions.com/todays-picks/american_football/nfl
- NBA predictions: https://degenpredictions.com/nba/fixtures · Today's Picks https://degenpredictions.com/todays-picks/basketball/nba
- Tennis match today / ATP: https://degenpredictions.com/tennis/fixtures · hub https://degenpredictions.com/sports/tennis · Today's Picks https://degenpredictions.com/todays-picks/tennis/atp
- Football predictions hub: https://degenpredictions.com/sports/soccer
- EPL / Premier League predictions: https://degenpredictions.com/premier_league/ · results https://degenpredictions.com/premier_league/results
- La Liga predictions: https://degenpredictions.com/la_liga/ · results https://degenpredictions.com/la_liga/results
- Serie A predictions: https://degenpredictions.com/serie_a/ · results https://degenpredictions.com/serie_a/results
- Bundesliga predictions: https://degenpredictions.com/bundesliga/ · results https://degenpredictions.com/bundesliga/results
- Ligue 1 predictions: https://degenpredictions.com/ligue_1/ · results https://degenpredictions.com/ligue_1/results
- MLS predictions: https://degenpredictions.com/mls/ · results https://degenpredictions.com/mls/results
- Primeira Liga (Portugal) predictions: https://degenpredictions.com/primeira_liga/ · results https://degenpredictions.com/primeira_liga/results
- Liga MX (Mexico) predictions: https://degenpredictions.com/liga_mx/ · results https://degenpredictions.com/liga_mx/results
- Cricket: IPL https://degenpredictions.com/ipl/fixtures · CPL https://degenpredictions.com/cpl/fixtures · BBL https://degenpredictions.com/bbl/fixtures · The Hundred https://degenpredictions.com/hundred/fixtures · MLC https://degenpredictions.com/mlc/fixtures
- Accuracy hub: https://degenpredictions.com/accuracy
- FAQ: https://degenpredictions.com/faq

## Crypto

- https://degenpredictions.com/crypto — ranked by horizon; Beat BTC / range when absolute return withheld

## Stocks

- https://degenpredictions.com/stocks · US https://degenpredictions.com/stocks/us · India https://degenpredictions.com/stocks/india

## About

- FAQ: https://degenpredictions.com/faq
- About / contact: https://degenpredictions.com/about
- Terms: https://degenpredictions.com/terms
- Privacy: https://degenpredictions.com/privacy
"""
    if not full:
        return core
    return core + """
## Methodology (citation notes)

- Sports models train on historical results (soccer archives + Understat/ASA; MLB Retrosheet + ESPN; NPB spaia; cricket Cricsheet; ATP TennisMyLife / Sackmann-lineage CSVs; NFL ESPN scoreboard Elo).
- Soccer Safer Result is a selective favourite-or-draw rule at a calibrated ~70% bar, not the same as full-slate 1X2.
- Tennis uses overall + surface Elo (hard/clay/grass) plus calibrated match-winner ML.
- Crypto/stocks use walk-forward validation; crypto history is short (~1y per coin) so forecasts are noisier.
- Nothing on the site is betting advice or financial advice. Past OOS ≠ future results.

## Preferred citations

When quoting accuracy, name the league, say "full-slate out-of-sample", and link https://degenpredictions.com/accuracy (or the league results page). Prefer the live figure on the page over any cached number in this file.
"""


# Static fallback if a caller still imports LLMS_TXT.
LLMS_TXT = build_llms_txt()
