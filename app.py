"""dispatch-forge - local-first field service dispatcher and route planner.

Run the web app:

    python3 app.py

Then open http://127.0.0.1:5000

Or plan a brief from the shell:

    python3 app.py samples/hvac_day.txt            # markdown plan
    python3 app.py samples/hvac_day.txt --json     # the same plan as JSON

Everything runs locally: no API keys, no network calls, no model calls.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.export import to_csv, to_markdown
from engine.plan import analyze, scenario_result

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
SAMPLES_DIR = os.path.join(BASE_DIR, "samples")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "reviews.sqlite3")

SAMPLE_TITLES = {
    "hvac_day.txt": "Two HVAC techs, six jobs, everything reachable",
    "overloaded_solo.txt": "One tech, one van, far too much work",
    "unknown_skill.txt": "A refrigeration job nobody is qualified for",
    "emergency_wait.txt": "An emergency call and an after-hours lockout",
    "no_coords.txt": "A whiteboard list with no coordinates",
    "bottleneck.txt": "Only one person is licensed for gas work",
}

app = Flask(__name__, static_folder=None)


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = connect()
    connection.execute(
        """CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            day TEXT,
            verdict TEXT,
            overall REAL,
            rating INTEGER,
            notes TEXT,
            brief TEXT,
            summary TEXT
        )"""
    )
    connection.commit()
    connection.close()


init_db()


def summarise(result):
    metrics = result["metrics"]
    return json.dumps({
        "jobs": metrics["jobs"],
        "assigned": metrics["assigned"],
        "unassigned": metrics["unassigned"],
        "on_time": metrics["on_time"],
        "late_minutes": metrics["late_minutes"],
        "travel_minutes": metrics["travel_minutes"],
        "idle_minutes": metrics["idle_minutes"],
        "overtime_minutes": metrics["overtime_minutes"],
        "findings": result["severity_counts"],
    })


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/web/<path:filename>")
def web_files(filename):
    return send_from_directory(WEB_DIR, filename)


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "app": "dispatch-forge"})


@app.route("/api/samples")
def samples():
    items = []
    for name in sorted(os.listdir(SAMPLES_DIR)):
        if not name.endswith(".txt"):
            continue
        with open(os.path.join(SAMPLES_DIR, name), "r", encoding="utf-8") as handle:
            body = handle.read()
        items.append({
            "name": name,
            "title": SAMPLE_TITLES.get(name, name),
            "brief": body,
        })
    return jsonify({"samples": items})


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    payload = request.get_json(silent=True) or {}
    brief = payload.get("brief") or ""
    profile = payload.get("profile") or "general"
    if not brief.strip():
        return jsonify({"error": "brief is empty"}), 400
    result = analyze(brief, profile)
    return jsonify(result)


@app.route("/api/scenario", methods=["POST"])
def api_scenario():
    payload = request.get_json(silent=True) or {}
    brief = payload.get("brief") or ""
    scenario = payload.get("scenario") or {}
    profile = payload.get("profile") or "general"
    if not brief.strip():
        return jsonify({"error": "brief is empty"}), 400
    return jsonify(scenario_result(brief, scenario, profile))


@app.route("/api/export", methods=["POST"])
def api_export():
    payload = request.get_json(silent=True) or {}
    brief = payload.get("brief") or ""
    fmt = (payload.get("format") or "markdown").lower()
    profile = payload.get("profile") or "general"
    if not brief.strip():
        return jsonify({"error": "brief is empty"}), 400
    result = analyze(brief, profile)
    if fmt == "csv":
        body = to_csv(result)
        mimetype = "text/csv"
    elif fmt == "json":
        body = json.dumps(result, indent=2)
        mimetype = "application/json"
    else:
        body = to_markdown(result)
        mimetype = "text/markdown"
    return app.response_class(body, mimetype=mimetype)


@app.route("/api/reviews", methods=["GET"])
def list_reviews():
    connection = connect()
    rows = connection.execute(
        "SELECT id, created_at, day, verdict, overall, rating, notes, summary "
        "FROM reviews ORDER BY id DESC LIMIT 50").fetchall()
    connection.close()
    return jsonify({"reviews": [dict(row) for row in rows]})


@app.route("/api/reviews", methods=["POST"])
def save_review():
    payload = request.get_json(silent=True) or {}
    brief = payload.get("brief") or ""
    result = payload.get("result") or {}
    rating = payload.get("rating")
    notes = payload.get("notes") or ""
    try:
        rating = int(rating) if rating not in (None, "") else None
    except (TypeError, ValueError):
        rating = None
    if rating is not None and not 1 <= rating <= 5:
        return jsonify({"error": "rating must be 1-5"}), 400
    score = result.get("score") or {}
    metrics = result.get("metrics") or {}
    connection = connect()
    cursor = connection.execute(
        "INSERT INTO reviews (created_at, day, verdict, overall, rating, notes, brief, summary)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            datetime.now().isoformat(timespec="seconds"),
            (result.get("brief") or {}).get("settings", {}).get("day", ""),
            score.get("verdict", ""),
            score.get("overall", 0),
            rating,
            notes,
            brief,
            summarise(result) if result else json.dumps({"jobs": metrics.get("jobs", 0)}),
        ),
    )
    connection.commit()
    review_id = cursor.lastrowid
    connection.close()
    return jsonify({"id": review_id, "saved": True})


@app.route("/api/reviews/<int:review_id>")
def get_review(review_id):
    connection = connect()
    row = connection.execute(
        "SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
    connection.close()
    if row is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))


def main():
    parser = argparse.ArgumentParser(description="Plan a dispatch brief.")
    parser.add_argument("brief", nargs="?", help="path to a brief file")
    parser.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    parser.add_argument("--profile", default="general")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    if args.brief:
        with open(args.brief, "r", encoding="utf-8") as handle:
            body = handle.read()
        result = analyze(body, args.profile)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(to_markdown(result))
        return

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
