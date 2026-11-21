"""Test suite for dispatch-forge.

Run from the project root:

    python3 tests/test_dispatch.py
"""

from __future__ import annotations

import json
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from engine.export import to_csv, to_markdown
from engine.geo import haversine_miles
from engine.models import (parse_brief, parse_clock, parse_duration,
                           parse_window)
from engine.plan import analyze, apply_scenario, scenario_result
from engine.schedule import build_plan
from engine.geo import TravelModel


SAMPLES = os.path.join(BASE_DIR, "samples")


def sample(name):
    with open(os.path.join(SAMPLES, name), "r", encoding="utf-8") as handle:
        return handle.read()


class ParsingTests(unittest.TestCase):
    def test_parse_clock(self):
        self.assertEqual(parse_clock("9:00"), 540)
        self.assertEqual(parse_clock("1pm"), 780)
        self.assertEqual(parse_clock("13:45"), 825)
        self.assertEqual(parse_clock("0900"), 540)
        self.assertEqual(parse_clock("noon"), 720)
        self.assertIsNone(parse_clock("half past"))

    def test_parse_duration(self):
        self.assertEqual(parse_duration("45m"), 45)
        self.assertEqual(parse_duration("1h30m"), 90)
        self.assertEqual(parse_duration("2 hours"), 120)
        self.assertEqual(parse_duration("1.5h"), 90)
        self.assertIsNone(parse_duration("soon"))

    def test_parse_window(self):
        self.assertEqual(parse_window("09:00-11:00"), (540, 660, "range"))
        self.assertEqual(parse_window("after 8am"), (480, None, "open_start"))
        self.assertEqual(parse_window("by 5pm"), (None, 1020, "open_end"))
        self.assertEqual(parse_window("10:00"), (600, None, "point"))

    def test_brief_parses_jobs_and_resources(self):
        brief = parse_brief(sample("hvac_day.txt"))
        self.assertEqual(len(brief.jobs), 6)
        self.assertEqual(len(brief.resources), 2)
        self.assertEqual(brief.settings.day, "2026-11-24")
        self.assertEqual(brief.settings.shift_start, 480)
        self.assertEqual(brief.settings.shift_end, 1020)
        self.assertEqual(brief.jobs[0].customer, "Rivera")
        self.assertEqual(brief.jobs[0].duration, 45)
        self.assertEqual(brief.jobs[1].priority, "high")

    def test_no_resources_falls_back_to_personal_mode(self):
        brief = parse_brief(
            "[Settings] Shift: 09:00-17:00\n"
            "[Job] ID: P1 | Customer: Solo | Address: 1 Main | Window: 10:00-11:00 | Duration: 30m\n")
        self.assertEqual(brief.settings.mode, "personal")
        self.assertEqual(len(brief.resources), 1)
        self.assertEqual(brief.resources[0].shift_start, 540)

    def test_malformed_lines_are_warned_not_fatal(self):
        brief = parse_brief("not a section line\n[Unknown] x\n[Job] ID: A | Customer: B | Duration: 30m\n")
        self.assertEqual(len(brief.jobs), 1)
        self.assertTrue(brief.warnings)

    def test_haversine_is_symmetric_and_positive(self):
        first = haversine_miles(39.7817, -89.6501, 39.7890, -89.6400)
        second = haversine_miles(39.7890, -89.6400, 39.7817, -89.6501)
        self.assertAlmostEqual(first, second, places=6)
        self.assertGreater(first, 0.0)


class EngineTests(unittest.TestCase):
    def test_plan_is_deterministic(self):
        text = sample("hvac_day.txt")
        first = analyze(text)
        second = analyze(text)
        self.assertEqual(json.dumps(first["schedule"]["routes"], sort_keys=True),
                         json.dumps(second["schedule"]["routes"], sort_keys=True))

    def test_control_sample_scores_high_and_is_clean(self):
        result = analyze(sample("hvac_day.txt"))
        self.assertGreaterEqual(result["score"]["overall"], 85)
        self.assertEqual(result["metrics"]["unassigned"], 0)
        self.assertEqual(result["metrics"]["late_jobs"], 0)
        self.assertEqual(result["score"]["verdict"], "dispatch_ready")

    def test_unknown_skill_leaves_jobs_unassigned(self):
        result = analyze(sample("unknown_skill.txt"))
        unassigned = {item["id"] for item in result["schedule"]["unassigned"]}
        self.assertEqual(unassigned, {"R1", "R4"})
        reasons = result["schedule"]["unassigned"][0]["reasons"]
        self.assertIn("no_skill", reasons)
        self.assertLessEqual(result["score"]["overall"], 70)

    def test_overloaded_day_is_flagged(self):
        result = analyze(sample("overloaded_solo.txt"))
        self.assertGreater(result["metrics"]["late_jobs"], 0)
        self.assertGreater(result["metrics"]["overtime_minutes"], 0)
        rules = {item["rule"] for item in result["findings"]}
        self.assertIn("capacity_overflow", rules)
        self.assertIn("shift_overrun", rules)

    def test_after_hours_job_cannot_be_staffed(self):
        result = analyze(sample("emergency_wait.txt"))
        unassigned = {item["id"] for item in result["schedule"]["unassigned"]}
        self.assertEqual(unassigned, {"E5"})

    def test_missing_data_is_reported(self):
        result = analyze(sample("no_coords.txt"))
        rules = {item["rule"] for item in result["findings"]}
        self.assertIn("assumed_travel", rules)
        self.assertIn("missing_address", rules)
        self.assertLess(result["score"]["axes"][4]["score"], 80)

    def test_single_point_of_failure_is_detected(self):
        result = analyze(sample("bottleneck.txt"))
        rules = {item["rule"] for item in result["findings"]}
        self.assertIn("single_point_of_failure", rules)
        self.assertIn("single_skill_holder", rules)

    def test_every_finding_is_fully_populated(self):
        for name in sorted(os.listdir(SAMPLES)):
            if not name.endswith(".txt"):
                continue
            result = analyze(sample(name))
            for item in result["findings"]:
                self.assertIn(item["severity"],
                              {"critical", "high", "medium", "low", "info"})
                self.assertTrue(item["rule"])
                self.assertTrue(item["evidence"])
                self.assertTrue(item["why"])
                self.assertTrue(item["fix"])

    def test_scores_stay_in_range(self):
        for name in sorted(os.listdir(SAMPLES)):
            if not name.endswith(".txt"):
                continue
            result = analyze(sample(name))
            self.assertGreaterEqual(result["score"]["overall"], 0)
            self.assertLessEqual(result["score"]["overall"], 100)
            for axis in result["score"]["axes"]:
                self.assertGreaterEqual(axis["score"], 0)
                self.assertLessEqual(axis["score"], 100)

    def test_travel_override_is_used(self):
        brief = parse_brief(
            "[Settings] Shift: 08:00-17:00\n"
            "[Resource] Name: A | Shift: 08:00-17:00\n"
            "[Job] ID: X | Address: 1 Main | Window: 09:00-11:00 | Duration: 30m\n"
            "[Job] ID: Y | Address: 2 Main | Window: 12:00-14:00 | Duration: 30m\n"
            "[Travel] From: X | To: Y | Minutes: 25\n")
        travel = TravelModel(brief.settings, brief.travel_overrides)
        minutes, basis = travel.minutes(("X", brief.jobs[0].location),
                                        ("Y", brief.jobs[1].location))
        self.assertEqual(basis, "override")
        self.assertEqual(minutes, 25)

    def test_skill_mismatch_blocks_assignment(self):
        brief = parse_brief(
            "[Settings] Shift: 08:00-17:00\n"
            "[Resource] Name: A | Skills: hvac | Shift: 08:00-17:00\n"
            "[Job] ID: X | Address: 1 Main | Window: 09:00-11:00 | Duration: 30m | Skills: gas\n")
        travel = TravelModel(brief.settings, {})
        plan = build_plan(brief.jobs, brief.resources, brief.settings, travel)
        self.assertEqual(len(plan["unassigned"]), 1)
        self.assertEqual(plan["unassigned"][0]["reasons"], ["no_skill"])

    def test_resilience_covers_every_resource(self):
        result = analyze(sample("hvac_day.txt"))
        self.assertEqual(len(result["resilience"]), 2)
        for item in result["resilience"]:
            self.assertIn("impact_pct", item)

    def test_scenario_add_resource_covers_a_missing_skill(self):
        text = sample("unknown_skill.txt")
        before = analyze(text)
        self.assertEqual(before["metrics"]["unassigned"], 2)
        after = scenario_result(text, {
            "op": "add_resource",
            "resource": {"id": "cool", "name": "Cool Tech", "skills": "refrigeration",
                         "shift": "08:00-17:00"},
        })
        self.assertEqual(after["after"]["metrics"]["unassigned"], 0)
        self.assertGreater(after["score_delta"], 0)

    def test_scenario_extend_shift_cuts_overtime(self):
        text = sample("overloaded_solo.txt")
        before = analyze(text)
        self.assertGreater(before["metrics"]["overtime_minutes"], 0)
        after = scenario_result(text, {"op": "extend_shift", "minutes": 90})
        self.assertLess(after["after"]["metrics"]["overtime_minutes"],
                        before["metrics"]["overtime_minutes"])

    def test_scenario_remove_job_cuts_the_load(self):
        text = sample("overloaded_solo.txt")
        before = analyze(text)
        after = scenario_result(text, {"op": "remove_job", "job_id": "A7"})
        self.assertEqual(after["after"]["metrics"]["jobs"], before["metrics"]["jobs"] - 1)

    def test_apply_scenario_does_not_mutate_the_original(self):
        brief = parse_brief(sample("hvac_day.txt"))
        before = len(brief.jobs)
        apply_scenario(brief, {"op": "remove_job", "job_id": "J1"})
        self.assertEqual(len(brief.jobs), before)


class ExportTests(unittest.TestCase):
    def test_markdown_export_has_the_plan(self):
        result = analyze(sample("hvac_day.txt"))
        text = to_markdown(result)
        self.assertIn("Verdict", text)
        self.assertIn("Scorecard", text)
        self.assertIn("Routes", text)
        self.assertIn("J1", text)

    def test_csv_export_has_a_row_per_job(self):
        result = analyze(sample("hvac_day.txt"))
        text = to_csv(result)
        lines = [line for line in text.strip().splitlines() if line]
        self.assertEqual(len(lines), result["metrics"]["jobs"] + 1)
        self.assertTrue(lines[0].startswith("job_id,"))


class ApiTests(unittest.TestCase):
    def setUp(self):
        from app import app as flask_app
        flask_app.config["TESTING"] = True
        self.client = flask_app.test_client()

    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_samples_are_served(self):
        response = self.client.get("/api/samples")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.get_json()["samples"]), 6)

    def test_analyze_returns_the_full_result(self):
        response = self.client.post("/api/analyze", json={"brief": sample("hvac_day.txt")})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        for key in ("metrics", "findings", "score", "schedule", "jobs_view", "resilience"):
            self.assertIn(key, data)
        self.assertEqual(data["metrics"]["jobs"], 6)

    def test_analyze_rejects_an_empty_brief(self):
        response = self.client.post("/api/analyze", json={"brief": "   "})
        self.assertEqual(response.status_code, 400)

    def test_scenario_endpoint(self):
        response = self.client.post("/api/scenario", json={
            "brief": sample("unknown_skill.txt"),
            "scenario": {"op": "add_resource",
                         "resource": {"id": "cool", "name": "Cool", "skills": "refrigeration",
                                      "shift": "08:00-17:00"}},
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["after"]["metrics"]["unassigned"], 0)

    def test_export_endpoint(self):
        response = self.client.post("/api/export", json={"brief": sample("hvac_day.txt"),
                                                          "format": "markdown"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Verdict", response.get_data(as_text=True))

    def test_review_round_trip(self):
        result = analyze(sample("hvac_day.txt"))
        response = self.client.post("/api/reviews", json={
            "brief": sample("hvac_day.txt"),
            "result": result,
            "rating": 4,
            "notes": "test review",
        })
        self.assertEqual(response.status_code, 200)
        review_id = response.get_json()["id"]
        listing = self.client.get("/api/reviews").get_json()["reviews"]
        self.assertTrue(any(item["id"] == review_id for item in listing))

    def test_review_rejects_a_bad_rating(self):
        result = analyze(sample("hvac_day.txt"))
        response = self.client.post("/api/reviews", json={
            "brief": "x", "result": result, "rating": 9})
        self.assertEqual(response.status_code, 400)

    def test_frontend_assets_exist(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/web/app.js").status_code, 200)
        self.assertEqual(self.client.get("/web/styles.css").status_code, 200)


class FrontendContractTests(unittest.TestCase):
    def test_frontend_calls_the_api_routes_the_backend_exposes(self):
        with open(os.path.join(BASE_DIR, "web", "app.js"), "r", encoding="utf-8") as handle:
            script = handle.read()
        for route in ("/api/analyze", "/api/scenario", "/api/export", "/api/reviews", "/api/samples"):
            self.assertIn(route, script)

    def test_frontend_reads_the_fields_the_backend_returns(self):
        with open(os.path.join(BASE_DIR, "web", "app.js"), "r", encoding="utf-8") as handle:
            script = handle.read()
        for field in ("metrics", "findings", "score", "jobs_view", "resilience",
                      "schedule", "idle_minutes", "late_minutes", "coverage_pct"):
            self.assertIn(field, script)

    def test_every_element_the_script_reads_exists_in_the_page(self):
        import re
        with open(os.path.join(BASE_DIR, "web", "app.js"), "r", encoding="utf-8") as handle:
            script = handle.read()
        with open(os.path.join(BASE_DIR, "web", "index.html"), "r", encoding="utf-8") as handle:
            page = handle.read()
        wanted = set(re.findall(r"el\('([^']+)'\)", script))
        self.assertTrue(wanted)
        for element_id in sorted(wanted):
            # ids live either in the static page or in markup the script builds
            static = 'id="' + element_id + '"' in page
            dynamic = 'id="' + element_id + '"' in script
            self.assertTrue(static or dynamic,
                            "no element with id=" + element_id + " in index.html or app.js")

    def test_frontend_escapes_dynamic_text(self):
        with open(os.path.join(BASE_DIR, "web", "app.js"), "r", encoding="utf-8") as handle:
            script = handle.read()
        self.assertIn("function esc(", script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
