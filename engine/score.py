"""Scorecard for dispatch-forge.

Five axes, each 0-100:

  feasibility  - do the visits land inside the customer windows
  coverage     - is every job crewed at all
  efficiency   - how much of the day is work instead of driving
  resilience   - what happens when one resource disappears
  data quality - how much of the brief is real information

Structural caps stop a day from scoring well by skipping the hard part: an
unstaffed emergency caps coverage at 30 and the whole review at 45.
"""

from __future__ import annotations

from typing import Dict, List

PROFILES: Dict[str, Dict[str, float]] = {
    "general": {"feasibility": 0.30, "coverage": 0.25, "efficiency": 0.15,
                "resilience": 0.15, "data_quality": 0.15},
    "deadline_critical": {"feasibility": 0.40, "coverage": 0.25, "efficiency": 0.10,
                           "resilience": 0.10, "data_quality": 0.15},
    "cost_conscious": {"feasibility": 0.20, "coverage": 0.20, "efficiency": 0.30,
                       "resilience": 0.15, "data_quality": 0.15},
    "team_day": {"feasibility": 0.25, "coverage": 0.25, "efficiency": 0.15,
                 "resilience": 0.25, "data_quality": 0.10},
    "customer_first": {"feasibility": 0.35, "coverage": 0.30, "efficiency": 0.10,
                       "resilience": 0.10, "data_quality": 0.15},
}

VERDICTS = [
    (90, "dispatch_ready", "The day can be sent to the crew as planned."),
    (78, "mostly_ready", "Runnable, with a few things worth fixing before it goes out."),
    (65, "workable_with_watch", "Workable, but something will bite during the day."),
    (50, "needs_attention", "Several things need attention before this sheet is usable."),
    (35, "fragile", "This plan will fail in ordinary conditions."),
    (0, "not_dispatchable", "This brief cannot be dispatched as it stands."),
]

PRIORITY_WEIGHT = {"emergency": 4, "high": 3, "normal": 2, "low": 1}


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _feasibility(metrics) -> float:
    if metrics["assigned"] <= 0:
        return 0.0
    score = 100.0 * metrics["on_time"] / metrics["assigned"]
    score -= min(20.0, metrics["overtime_minutes"] / 3.0)
    score -= min(15.0, metrics["late_minutes"] / 10.0)
    return clamp(score)


def _coverage(metrics) -> float:
    if metrics["jobs"] <= 0:
        return 0.0
    return clamp(100.0 * metrics["coverage_pct"])


def _efficiency(metrics, plan) -> float:
    if metrics["assigned"] <= 0:
        return 0.0
    score = 100.0
    travel_share = metrics["travel_share_pct"] / 100.0
    score -= min(55.0, max(0.0, (travel_share - 0.20) / 0.35) * 55.0)
    utilizations = [route["totals"]["utilization"] for route in plan["routes"]
                    if route["totals"]["jobs"] > 0]
    if utilizations:
        busiest = max(utilizations)
        score -= min(25.0, max(0.0, (busiest - 0.85) / 0.35) * 25.0)
        spread = max(utilizations) - min(utilizations)
        score -= min(15.0, spread * 15.0)
    if metrics["capacity_minutes"]:
        idle_share = metrics["idle_minutes"] / max(1.0, metrics["capacity_minutes"])
        score -= min(12.0, max(0.0, (idle_share - 0.15) / 0.45) * 12.0)
    return clamp(score)


def _resilience(metrics, resilience, brief) -> float:
    score = 100.0
    if brief.jobs:
        worst = max((item["impact_pct"] for item in resilience), default=0.0)
        score -= min(60.0, worst * 0.6)
    holders = {}
    for resource in brief.resources:
        for skill in resource.skills:
            holders.setdefault(skill.lower(), set()).add(resource.id)
    fragile_jobs = 0
    for job in brief.jobs:
        needed = [s.lower() for s in job.skills]
        if needed and all(len(holders.get(skill, set())) <= 1 for skill in needed):
            fragile_jobs += 1
    if brief.jobs:
        fragile_share = fragile_jobs / len(brief.jobs)
        score -= min(20.0, fragile_share * 25.0)
    return clamp(score)


def _data_quality(brief, travel) -> float:
    if not brief.jobs:
        return 0.0
    total = 0.0
    for job in brief.jobs:
        score = 0.0
        if job.location.known:
            score += 20
        if job.duration is not None:
            score += 20
        if job.window_start is not None or job.window_end is not None:
            score += 20
        if job.customer:
            score += 10
        if job.phone:
            score += 10
        if job.location.has_coords or job.id.lower() in travel.overrides:
            score += 20
        total += score
    return clamp(total / len(brief.jobs))


def verdict_for(score: float) -> str:
    for threshold, name, _note in VERDICTS:
        if score >= threshold:
            return name
    return "not_dispatchable"


def score_plan(brief, plan, metrics, resilience, findings, travel, profile="general") -> Dict:
    weights = PROFILES.get(profile, PROFILES["general"])
    axes = [
        {"key": "feasibility", "label": "Window feasibility",
         "score": round(_feasibility(metrics), 1),
         "note": "%d of %d scheduled visits land inside their window." % (
             metrics["on_time"], metrics["assigned"])},
        {"key": "coverage", "label": "Coverage",
         "score": round(_coverage(metrics), 1),
         "note": "%d of %d jobs have a crew (%d%% by priority weight)." % (
             metrics["assigned"], metrics["jobs"], round(metrics["coverage_pct"]))},
        {"key": "efficiency", "label": "Route efficiency",
         "score": round(_efficiency(metrics, plan), 1),
         "note": "%d%% of the day is driving, %d%% is on-site work." % (
             round(metrics["travel_share_pct"]), round(metrics["service_share_pct"]))},
        {"key": "resilience", "label": "Resilience",
         "score": round(_resilience(metrics, resilience, brief), 1),
         "note": "Worst single absence leaves %d job(s) without a crew." % (
             max((item["unassigned"] for item in resilience), default=0))},
        {"key": "data_quality", "label": "Brief quality",
         "score": round(_data_quality(brief, travel), 1),
         "note": "%d job(s) are missing a duration or a location." % (
             sum(1 for job in brief.jobs
                 if job.duration is None or not job.location.known))},
    ]

    caps: List[Dict] = []
    by_severity = {}
    for item in findings:
        by_severity.setdefault(item["severity"], []).append(item)

    def cap(axis_key, value, reason):
        for axis in axes:
            if axis["key"] == axis_key:
                if value < axis["score"]:
                    caps.append({"axis": axis_key, "capped_at": value,
                                 "from": axis["score"], "reason": reason})
                    axis["score"] = value
                return

    emergency_unassigned = [f for f in by_severity.get("critical", [])
                            if f["rule"] == "unassigned_job"]
    unassigned = [f for f in findings if f["rule"] == "unassigned_job"]
    if emergency_unassigned:
        cap("coverage", 30, "an emergency job has no crew")
    elif unassigned:
        cap("coverage", 55, "%d job(s) have no crew" % len(unassigned))
    if metrics["late_jobs"] > 0:
        cap("feasibility", 78, "%d visit(s) miss their window" % metrics["late_jobs"])
    if metrics["assigned"] and metrics["late_jobs"] / max(1, metrics["assigned"]) > 0.25:
        cap("feasibility", 55, "more than a quarter of the visits miss their window")
    if metrics["overtime_minutes"] > 60:
        cap("feasibility", 60, "the crew works more than an hour past the shift")
    if brief.jobs:
        missing = sum(1 for job in brief.jobs if job.duration is None)
        if missing / len(brief.jobs) > 0.30:
            cap("data_quality", 35, "more than 30% of jobs have no service time")
    if any(item["impact_pct"] >= 50 for item in resilience):
        cap("resilience", 40, "losing one resource breaks half the day")

    total = 0.0
    for axis in axes:
        total += axis["score"] * weights[axis["key"]]
    overall = clamp(total)

    if emergency_unassigned:
        overall = min(overall, 45)
        caps.append({"axis": "overall", "capped_at": 45, "from": round(overall, 1),
                     "reason": "an emergency job has no crew"})
    elif unassigned:
        overall = min(overall, 62)
        caps.append({"axis": "overall", "capped_at": 62, "from": round(overall, 1),
                     "reason": "%d job(s) have no crew" % len(unassigned)})
    if by_severity.get("critical") and not emergency_unassigned:
        overall = min(overall, 55)
        caps.append({"axis": "overall", "capped_at": 55, "from": round(overall, 1),
                     "reason": "a critical finding is unresolved"})

    return {
        "axes": axes,
        "weights": weights,
        "profile": profile if profile in PROFILES else "general",
        "overall": round(overall, 1),
        "verdict": verdict_for(overall),
        "verdict_note": next(note for _threshold, name, note in VERDICTS
                              if verdict_for(overall) == name),
        "caps": caps,
    }
