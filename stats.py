import json
import time
import os

stats = dict()
addonpath = None


def load_stats(addon):
    global stats, addonpath
    addonpath = addon

    path = os.path.join(addon, "stats.json")

    try:
        with open(path, "r") as fp:
            stats = json.load(fp)
    except FileNotFoundError:
        stats = dict()


def get_stat(key: str) -> float:
    date = time.strftime("%d/%m/%Y")
    _ensure_date_entry(date)
    return stats[date][key]


def get_stats_data() -> dict:
    return stats


def _ensure_date_entry(date: str):
    """Ensure the stats dict has a usable entry for the given date."""
    if date not in stats:
        stats[date] = dict(
            assessments=0.0,
            words=0.0,
            avg_accuracy=0.0,
            avg_fluency=0.0,
            avg_pronunciation=0.0,
            pronunciation_time=0.0,
            history=[],
        )
    else:
        # Ensure history list exists for backwards compatibility
        stats[date].setdefault("history", [])


def update_stat(key: str, increment: float, set_value=False):

    date = time.strftime("%d/%m/%Y")
    _ensure_date_entry(date)

    if not set_value:
        stats[date][key] += increment
    else:
        stats[date][key] = increment


def log_assessment(entry: dict):
    """Log a single assessment entry into today's history list.

    Entry should be a serialisable dict (e.g. JSON-safe values) with enough
    context to later reconstruct progress (note/card ids, scores, timestamps).
    """
    date = time.strftime("%d/%m/%Y")
    _ensure_date_entry(date)

    # Keep a reasonable cap on history length to prevent huge files; trim older
    # entries beyond a threshold (e.g. 1000 per day).
    history = stats[date]["history"]
    history.append(entry)
    if len(history) > 2000:
        # keep only most recent 2000 entries
        stats[date]["history"] = history[-2000:]


def update_avg_stat(key: str, new_score: float, assessments: float):
    if assessments <= 0:
        new_avg = new_score
    else:
        new_avg = (get_stat(key) * (assessments - 1) + new_score) / assessments
    new_avg = round(new_avg, 2)

    update_stat(key, new_avg, set_value=True)


def save_stats():
    if not addonpath:
        print("Addon path not set; cannot save stats.")
        return
    
    with open(os.path.join(addonpath, "stats.json"), "w+") as fp:
        json.dump(stats, fp, indent=4)
