"""
Live match tracking, invoked every 5 minutes by degen-live-tracker.timer.

Each run is a single poll pass (not a persistent daemon -- systemd timer
handles the 5-minute cadence): for every match currently in its live window,
fetch the latest state once, log new events, update its summary, and exit.

NOTE: _get_live_data() is a scaffold -- it needs to be wired up to a real
source (webapp/fotmob_lineup.py or an ESPN/FotMob endpoint) before this
produces real events. Until then each run finds zero live matches and exits.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
LIVE_DIR = DATA_DIR / "live"
LOGS_DIR = BASE_DIR / "logs"
LIVE_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOGS_DIR / 'live_tracker.log'),
        logging.StreamHandler()
    ]
)


def get_matches_in_live_window():
    """Return matches whose kickoff has passed but aren't marked finished
    yet. Scaffold -- wire this up to the real fixtures data source."""
    return []


def get_live_data(match_id):
    """Fetch current state for one match from the live data source.
    Scaffold -- wire this up to FotMob/ESPN. Returns None until then."""
    return None


def process_match(match):
    match_id = match['id']
    state_file = LIVE_DIR / f"{match_id}_state.json"
    summary_file = LIVE_DIR / f"{match_id}_summary.json"

    prev_state = {}
    if state_file.exists():
        with open(state_file) as f:
            prev_state = json.load(f)

    live_data = get_live_data(match_id)
    if live_data is None:
        logger.info(f"[{match_id}] no live data available this poll")
        return

    new_events = live_data.get('events', [])[len(prev_state.get('events', [])):]
    for event in new_events:
        logger.info(f"[{match_id}] {event.get('minute')}' {event.get('type')} -- {event.get('team')}")

    state = {
        'match_id': match_id,
        'polled_at': datetime.now().isoformat(),
        'status': live_data.get('status'),
        'minute': live_data.get('minute'),
        'score': live_data.get('score'),
        'events': live_data.get('events', []),
        'stats': live_data.get('stats', {}),
    }
    with open(state_file, 'w') as f:
        json.dump(state, f, indent=2)

    if live_data.get('status') == 'finished':
        with open(summary_file, 'w') as f:
            json.dump({**state, 'match': match, 'finished_at': datetime.now().isoformat()}, f, indent=2)
        state_file.unlink(missing_ok=True)
        logger.info(f"[{match_id}] match finished, summary written")


def main():
    matches = get_matches_in_live_window()
    if not matches:
        logger.info("No matches currently in their live window.")
        return

    logger.info(f"Polling {len(matches)} live match(es)...")
    for match in matches:
        try:
            process_match(match)
        except Exception:
            logger.exception(f"Error processing match {match.get('id')}")


if __name__ == '__main__':
    main()
