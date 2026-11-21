"""Top level orchestration for dispatch-forge.

analyze() turns a pasted brief into a plan, a set of findings, a scorecard
and a set of what-if answers. Everything is local: no network calls, no API
keys, no model calls.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Dict, List, Optional

from .geo import TravelModel
from .models import (Brief, Job, Resource, build_job, build_resource,
                     fmt_clock, fmt_duration, norm_key, parse_brief,
                     parse_window)
from .rules import audit
from .schedule import build_plan
from .score import PROFILES, score_plan

PRIORITY_WEIGHT = {"emergency": 4, "high": 3, "normal": 2, "low": 1}


def _weight(job) -> int:
    return PRIORITY_WEIGHT.get(job.priority, 2)


def compute_metrics(brief: Brief, plan: Dict) -> Dict:
    assigned_ids = []
    on_time = 0
    late_jobs = 0
    late_minutes = 0.0
    travel_minutes = 0.0
    idle_minutes = 0.0
    service_minutes = 0.0
    overtime_minutes = 0.0
    travel_miles = 0.0
    revenue_scheduled = 0.0
    capacity = 0
    resources_used = 0
    resources_idle = 0
    cost = 0.0

    for route in plan["routes"]:
        totals = route["totals"]
        for entry in route["entries"]:
            assigned_ids.append(entry["job_id"])
            if entry["on_time"]:
                on_time += 1
            if entry["late_minutes"] > 0:
                late_jobs += 1
        late_minutes += totals["late_minutes"]
        travel_minutes += totals["travel_minutes"]
        idle_minutes += totals["idle_minutes"]
        service_minutes += totals["service_minutes"]
        overtime_minutes += totals["overtime_minutes"]
        travel_miles += totals["travel_miles"]
        revenue_scheduled += totals["revenue"]
        capacity += totals["span_minutes"]
        cost += totals["cost"]
        if totals["jobs"] > 0:
            resources_used += 1
        else:
            resources_idle += 1

    total_weight = sum(_weight(job) for job in brief.jobs)
    assigned_weight = 0
    for job in brief.jobs:
        if job.id in assigned_ids:
            assigned_weight += _weight(job)

    busy = travel_minutes + idle_minutes + service_minutes
    productive = travel_minutes + service_minutes
    travel_share = (100.0 * travel_minutes / busy) if busy > 0 else 0.0
    idle_share = (100.0 * idle_minutes / busy) if busy > 0 else 0.0
    service_share = (100.0 * service_minutes / busy) if busy > 0 else 0.0

    unassigned_jobs = [item["job"] for item in plan["unassigned"]]
    revenue_unassigned = sum(job.revenue for job in unassigned_jobs)

    return {
        "jobs": len(brief.jobs),
        "assigned": len(assigned_ids),
        "unassigned": len(unassigned_jobs),
        "on_time": on_time,
        "late_jobs": late_jobs,
        "late_minutes": round(late_minutes, 1),
        "travel_minutes": round(travel_minutes, 1),
        "idle_minutes": round(idle_minutes, 1),
        "service_minutes": round(service_minutes, 1),
        "overtime_minutes": round(overtime_minutes, 1),
        "travel_miles": round(travel_miles, 1),
        "capacity_minutes": capacity,
        "utilization_pct": round(100.0 * productive / capacity, 1) if capacity else 0.0,
        "travel_share_pct": round(travel_share, 1),
        "idle_share_pct": round(idle_share, 1),
        "service_share_pct": round(service_share, 1),
        "coverage_pct": round(100.0 * assigned_weight / total_weight, 1) if total_weight else 0.0,
        "on_time_pct": round(100.0 * on_time / len(assigned_ids), 1) if assigned_ids else 0.0,
        "cost": round(cost, 2),
        "revenue_scheduled": round(revenue_scheduled, 2),
        "revenue_unassigned": round(revenue_unassigned, 2),
        "revenue_total": round(revenue_scheduled + revenue_unassigned, 2),
        "resources_used": resources_used,
        "resources_idle": resources_idle,
        "late_cost": round(late_minutes * brief.settings.late_cost_per_min, 2),
    }


def failure_simulation(brief: Brief, settings) -> List[Dict]:
    """What happens to the day if each resource disappears?"""
    results = []
    for resource in brief.resources:
        others = [item for item in brief.resources if item.id != resource.id]
        if not others:
            results.append({
                "resource": resource.id,
                "name": resource.name,
                "unassigned": len(brief.jobs),
                "late_minutes": 0.0,
                "impact_pct": 100.0 if brief.jobs else 0.0,
            })
            continue
        travel = TravelModel(settings, brief.travel_overrides)
        trial = build_plan(brief.jobs, others, settings, travel, improve=False)
        unassigned = len(trial["unassigned"])
        late = sum(route["totals"]["late_minutes"] for route in trial["routes"])
        results.append({
            "resource": resource.id,
            "name": resource.name,
            "unassigned": unassigned,
            "late_minutes": round(late, 1),
            "impact_pct": round(100.0 * unassigned / max(1, len(brief.jobs)), 1),
        })
    return results


def brief_to_dict(brief: Brief) -> Dict:
    return {
        "jobs": [
            {
                "id": job.id,
                "customer": job.customer,
                "address": job.location.address,
                "lat": job.location.lat,
                "lon": job.location.lon,
                "window_start": job.window_start,
                "window_end": job.window_end,
                "window_text": job.window_text(),
                "duration": job.duration,
                "skills": job.skills,
                "priority": job.priority,
                "revenue": job.revenue,
                "phone": job.phone,
                "notes": job.notes,
            }
            for job in brief.jobs
        ],
        "resources": [
            {
                "id": resource.id,
                "name": resource.name,
                "skills": resource.skills,
                "shift_start": resource.shift_start,
                "shift_end": resource.shift_end,
                "base": resource.base.address,
                "notes": resource.notes,
            }
            for resource in brief.resources
        ],
        "settings": {
            "day": brief.settings.day,
            "mode": brief.settings.mode,
            "shift_start": brief.settings.shift_start,
            "shift_end": brief.settings.shift_end,
            "depot": brief.settings.depot.address,
            "speed_mph": brief.settings.speed_mph,
            "road_factor": brief.settings.road_factor,
            "travel_buffer": brief.settings.travel_buffer,
            "late_cost_per_min": brief.settings.late_cost_per_min,
            "return_to_depot": brief.settings.return_to_depot,
            "assumed_travel_min": brief.settings.assumed_travel_min,
        },
        "warnings": brief.warnings,
    }


def jobs_view(brief: Brief, plan: Dict) -> List[Dict]:
    rows = []
    seen = set()
    for route in plan["routes"]:
        for entry in route["entries"]:
            seen.add(entry["job_id"])
            rows.append({
                "id": entry["job_id"],
                "customer": entry["customer"],
                "priority": entry["priority"],
                "address": entry["address"],
                "skills": entry["skills"],
                "resource": route["resource"]["id"],
                "resource_name": route["resource"]["name"],
                "window_start": entry["window_start"],
                "window_end": entry["window_end"],
                "start": entry["start"],
                "end": entry["end"],
                "idle_minutes": entry["idle_minutes"],
                "late_minutes": entry["late_minutes"],
                "slack_minutes": entry["slack_minutes"],
                "leg_minutes": entry["leg_minutes"],
                "leg_basis": entry["leg_basis"],
                "on_time": entry["on_time"],
                "status": "on_time" if entry["on_time"] else "late",
                "revenue": entry["revenue"],
            })
    for item in plan["unassigned"]:
        job = item["job"]
        rows.append({
            "id": job.id,
            "customer": job.customer,
            "priority": job.priority,
            "address": job.location.address,
            "skills": job.skills,
            "resource": "",
            "resource_name": "",
            "window_start": job.window_start,
            "window_end": job.window_end,
            "start": None,
            "end": None,
            "idle_minutes": 0,
            "late_minutes": 0,
            "slack_minutes": None,
            "leg_minutes": 0,
            "leg_basis": "",
            "on_time": False,
            "status": "unassigned",
            "reasons": item["reasons"],
            "revenue": job.revenue,
        })
    rows.sort(key=lambda row: (0 if row["status"] == "late" else
                               1 if row["status"] == "unassigned" else 2,
                               str(row["id"])))
    return rows


def analyze(brief_text: str, profile: str = "general") -> Dict:
    brief = parse_brief(brief_text or "")
    settings = brief.settings
    travel = TravelModel(settings, brief.travel_overrides)
    plan = build_plan(brief.jobs, brief.resources, settings, travel)
    metrics = compute_metrics(brief, plan)
    resilience = failure_simulation(brief, settings)
    findings = audit(brief, plan, metrics, travel, resilience)
    score = score_plan(brief, plan, metrics, resilience, findings, travel, profile)

    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for item in findings:
        severity_counts[item["severity"]] = severity_counts.get(item["severity"], 0) + 1

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "mode": settings.mode,
            "day": settings.day,
            "profiles": sorted(PROFILES.keys()),
            "travel": travel.stats(),
        },
        "brief": brief_to_dict(brief),
        "schedule": {
            "routes": plan["routes"],
            "assign": plan["assign"],
            "unassigned": [
                {"id": item["job"].id, "customer": item["job"].customer,
                 "priority": item["job"].priority, "reasons": item["reasons"],
                 "revenue": item["job"].revenue}
                for item in plan["unassigned"]
            ],
        },
        "jobs_view": jobs_view(brief, plan),
        "metrics": metrics,
        "resilience": resilience,
        "findings": findings,
        "severity_counts": severity_counts,
        "score": score,
    }


def apply_scenario(brief: Brief, scenario: Optional[Dict]) -> Brief:
    """Apply a what-if change to a copy of the brief."""
    result = copy.deepcopy(brief)
    scenario = scenario or {}
    op = str(scenario.get("op") or "").strip().lower()

    if op == "add_resource":
        raw = scenario.get("resource") or {}
        fields = [(norm_key(str(key)), str(value)) for key, value in raw.items()]
        resource = build_resource(fields, len(result.resources) + 1, 0, result.warnings)
        if not resource.base.known:
            resource.base = result.settings.depot
        result.resources.append(resource)
    elif op == "remove_resource":
        target = str(scenario.get("resource_id") or "").strip().lower()
        result.resources = [r for r in result.resources if r.id.lower() != target]
    elif op == "remove_job":
        target = str(scenario.get("job_id") or "").strip().lower()
        result.jobs = [j for j in result.jobs if j.id.lower() != target]
    elif op == "add_job":
        raw = scenario.get("job") or {}
        fields = [(norm_key(str(key)), str(value)) for key, value in raw.items()]
        result.jobs.append(build_job(fields, len(result.jobs) + 1, 0, result.warnings))
    elif op == "extend_shift":
        try:
            minutes = int(scenario.get("minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        target = str(scenario.get("resource_id") or "").strip().lower()
        for resource in result.resources:
            if not target or resource.id.lower() == target:
                resource.shift_end += minutes
    elif op == "move_window":
        target = str(scenario.get("job_id") or "").strip().lower()
        start, end, _shape = parse_window(str(scenario.get("window") or ""))
        for job in result.jobs:
            if job.id.lower() == target:
                if start is not None:
                    job.window_start = start
                if end is not None:
                    job.window_end = end
    elif op == "set_speed":
        try:
            result.settings.speed_mph = float(scenario.get("speed_mph") or 0) or result.settings.speed_mph
        except (TypeError, ValueError):
            pass
    return result


METRIC_KEYS = ["jobs", "assigned", "unassigned", "on_time", "late_jobs",
               "late_minutes", "travel_minutes", "idle_minutes",
               "service_minutes", "overtime_minutes", "coverage_pct",
               "on_time_pct", "travel_share_pct", "idle_share_pct", "utilization_pct", "cost",
               "revenue_scheduled", "revenue_unassigned"]


def compare_metrics(base: Dict, other: Dict) -> List[Dict]:
    rows = []
    for key in METRIC_KEYS:
        before = base["metrics"].get(key, 0)
        after = other["metrics"].get(key, 0)
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            rows.append({
                "metric": key,
                "before": round(float(before), 2),
                "after": round(float(after), 2),
                "delta": round(float(after) - float(before), 2),
            })
    return rows


def scenario_result(brief_text: str, scenario: Optional[Dict], profile: str = "general") -> Dict:
    base = analyze(brief_text, profile)
    brief = parse_brief(brief_text or "")
    changed = apply_scenario(brief, scenario)
    travel = TravelModel(changed.settings, changed.travel_overrides)
    plan = build_plan(changed.jobs, changed.resources, changed.settings, travel)
    metrics = compute_metrics(changed, plan)
    resilience = failure_simulation(changed, changed.settings)
    findings = audit(changed, plan, metrics, travel, resilience)
    score = score_plan(changed, plan, metrics, resilience, findings, travel, profile)
    after = {
        "metrics": metrics,
        "score": score,
        "findings": findings,
        "schedule": {
            "routes": plan["routes"],
            "unassigned": [
                {"id": item["job"].id, "reasons": item["reasons"]}
                for item in plan["unassigned"]
            ],
        },
        "jobs_view": jobs_view(changed, plan),
    }
    return {
        "scenario": scenario or {},
        "before": {"metrics": base["metrics"], "score": base["score"]},
        "after": after,
        "delta": compare_metrics(base, after),
        "score_delta": round(score["overall"] - base["score"]["overall"], 1),
    }
