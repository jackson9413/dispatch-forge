"""Audit rules for dispatch-forge.

Every finding carries the evidence that triggered it, why it matters and a
concrete fix, because a dispatcher arguing with a customer needs the number
in front of them, not a vibe.
"""

from __future__ import annotations

from typing import Dict, List

from .models import fmt_clock, fmt_duration

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

REASON_TEXT = {
    "no_skill": "no resource on shift holds the required skill",
    "window_ends_before_shift": "the customer window closes before anyone starts",
    "window_starts_after_shift": "the customer window opens after the shift ends",
    "no_feasible_slot": "no sequence slot keeps the schedule inside the shift",
    "no_resources": "there are no resources on this brief",
}


def finding(rule, severity, axis, title, evidence, why, fix, jobs=None) -> Dict:
    return {
        "rule": rule,
        "severity": severity,
        "axis": axis,
        "title": title,
        "evidence": evidence,
        "why": why,
        "fix": fix,
        "jobs": list(jobs or []),
    }


def audit(brief, plan, metrics, travel, resilience) -> List[Dict]:
    findings: List[Dict] = []
    jobs_by_id = {job.id: job for job in brief.jobs}

    # --- coverage -------------------------------------------------------
    for item in plan["unassigned"]:
        job = item["job"]
        reasons = item["reasons"]
        severity = "critical" if job.priority == "emergency" else (
            "high" if job.priority == "high" else "medium")
        detail = "; ".join(REASON_TEXT.get(reason, reason) for reason in reasons)
        findings.append(finding(
            "unassigned_job", severity, "coverage",
            "Job %s cannot be staffed" % job.id,
            "%s (%s) is unassigned: %s" % (
                job.id, job.customer or "no customer named", detail),
            "An unassigned job is revenue that never gets earned and a customer who "
            "gets no service, so it has to be resolved before the sheet goes out.",
            "Add a qualified resource, move the window, or hand the job to a partner "
            "crew and tell the customer before the window opens.",
            [job.id],
        ))

    # --- feasibility ----------------------------------------------------
    for route in plan["routes"]:
        for entry in route["entries"]:
            if entry["late_minutes"] > 0:
                priority = entry["priority"]
                severity = "critical" if priority == "emergency" else (
                    "high" if priority == "high" else "medium")
                window = fmt_clock(entry["window_start"]) + "-" + fmt_clock(entry["window_end"])
                findings.append(finding(
                    "window_missed", severity, "feasibility",
                    "Job %s arrives after its window closes" % entry["job_id"],
                    "%s starts at %s, %s late against the %s window (%s on %s)." % (
                        entry["job_id"], fmt_clock(entry["start"]),
                        fmt_duration(entry["late_minutes"]), window,
                        entry["customer"] or "no customer named",
                        route["resource"]["name"] or route["resource"]["id"]),
                    "A late arrival on a closed window is usually a refusal, a bad "
                    "review or a callback, so the cost is more than the delay.",
                    "Move the job earlier, reorder the route to reach it sooner, or "
                    "call the customer and rebook the window.",
                    [entry["job_id"]],
                ))

    for route in plan["routes"]:
        totals = route["totals"]
        if totals["overtime_minutes"] > 0:
            severity = "high" if totals["overtime_minutes"] > 60 else "medium"
            findings.append(finding(
                "shift_overrun", severity, "feasibility",
                "%s works past the shift end" % (route["resource"]["name"] or route["resource"]["id"]),
                "The route finishes at %s against a %s shift end (%s over)." % (
                    fmt_clock(totals["ends_at"]), fmt_clock(route["resource"]["shift_end"]),
                    fmt_duration(totals["overtime_minutes"])),
                "Work past the shift end is unpaid or unpaid-adjacent time, and it "
                "pushes the last customer of the day into tomorrow's problem.",
                "Extend the shift deliberately, move one job to another resource, or "
                "drop the lowest priority job to a later day.",
            ))

    if brief.jobs and brief.resources:
        earliest_shift = min(resource.shift_start for resource in brief.resources)
        early_jobs = [job for job in brief.jobs
                      if job.window_start is not None and job.window_start < earliest_shift]
        if early_jobs:
            findings.append(finding(
                "early_window_unstaffed", "high", "feasibility",
                "%d job(s) expect someone before the first shift starts" % len(early_jobs),
                "Jobs %s open at or before %s but the earliest shift starts at %s." % (
                    ", ".join(job.id for job in early_jobs),
                    fmt_clock(min(job.window_start for job in early_jobs)),
                    fmt_clock(earliest_shift)),
                "A customer waiting outside an empty van is the worst kind of late, "
                "because the delay was decided before the day started.",
                "Start someone earlier, move the job to a later window, or call the "
                "customer and confirm a new time.",
                [job.id for job in early_jobs],
            ))

    # --- efficiency -----------------------------------------------------
    if metrics["assigned"] > 0:
        travel_share = metrics["travel_share_pct"]
        if travel_share > 35:
            severity = "high" if travel_share > 55 else "medium"
            findings.append(finding(
                "travel_heavy", severity, "efficiency",
                "Driving eats %d%% of the day" % round(travel_share),
                "%s of driving against %s of on-site work and %s of idle time." % (
                    fmt_duration(metrics["travel_minutes"]),
                    fmt_duration(metrics["service_minutes"]),
                    fmt_duration(metrics["idle_minutes"])),
                "Travel that is not work is unpaid margin, and on a fuel-heavy day it "
                "is the line item that decides whether the day made money.",
                "Cluster the stops geographically, or add a [Travel] line with real "
                "drive times so the route can be sequenced on true distances.",
            ))

    for route in plan["routes"]:
        totals = route["totals"]
        if totals["idle_minutes"] >= 20:
            share = 100.0 * totals["idle_minutes"] / max(1, totals["span_minutes"])
            severity = "medium" if share > 40 else "low"
            findings.append(finding(
                "idle_gap", severity, "efficiency",
                "%s has %s of idle time" % (route["resource"]["name"] or route["resource"]["id"],
                                            fmt_duration(totals["idle_minutes"])),
                "%s idle against %s of work and %s of driving in a %s shift." % (
                    fmt_duration(totals["idle_minutes"]),
                    fmt_duration(totals["service_minutes"]),
                    fmt_duration(totals["travel_minutes"]),
                    fmt_duration(totals["span_minutes"])),
                "Idle time is paid time that earns nothing, and it usually means the "
                "window was wider than the route needed.",
                "Start the visit later so the crew is not standing at the door, or "
                "pull a waiting job forward to use the gap.",
            ))

    for route in plan["routes"]:
        totals = route["totals"]
        if totals["jobs"] >= 8:
            findings.append(finding(
                "heavy_day", "medium", "efficiency",
                "%s is carrying %d jobs" % (route["resource"]["name"] or route["resource"]["id"],
                                             totals["jobs"]),
                "%d jobs, %s of work plus %s of travel." % (
                    totals["jobs"], fmt_duration(totals["service_minutes"]),
                    fmt_duration(totals["travel_minutes"])),
                "A long list of stops is where mistakes, missed notes and unhappy "
                "customers cluster, especially in the last hour of the day.",
                "Split the list, move a job to another resource, or move the last job "
                "to tomorrow.",
            ))

    if metrics["assigned"] > 0 and metrics["capacity_minutes"] > 0:
        demand = metrics["service_minutes"] + metrics["travel_minutes"]
        if demand > metrics["capacity_minutes"]:
            findings.append(finding(
                "capacity_overflow", "high", "coverage",
                "The day needs more hours than the shift has",
                "%s of work and travel against %s of shift time across %d resources." % (
                    fmt_duration(demand), fmt_duration(metrics["capacity_minutes"]),
                    len(brief.resources)),
                "Total demand above total capacity means the overflow has to go "
                "somewhere: overtime, another crew, or tomorrow.",
                "Add a resource, extend a shift, or move the lowest priority jobs to "
                "another day.",
            ))

    # --- resilience -----------------------------------------------------
    if resilience:
        worst = max(resilience, key=lambda item: (item["impact_pct"], item["resource"]))
        fragile = [item for item in resilience if item["impact_pct"] >= 40]
        if worst["impact_pct"] >= 40:
            findings.append(finding(
                "single_point_of_failure", "high", "resilience",
                "One resource holds up the day",
                "If %s calls in sick, %d of %d jobs lose their crew and %s of extra "
                "lateness appears." % (
                    worst.get("name") or worst["resource"], worst["unassigned"],
                    len(brief.jobs), fmt_duration(worst["late_minutes"])),
                "A day that collapses on one absence is a day that will collapse, and "
                "the customer finds out before you do.",
                "Cross-train a second resource on the specialist skills, or keep a "
                "named backup crew for the skills only one person holds.",
            ))
        if len(fragile) > 1:
            findings.append(finding(
                "fragile_roster", "medium", "resilience",
                "%d of %d resources are single points of failure" % (
                    len(fragile), len(resilience)),
                "Losing %s would each leave at least 40%% of the day uncrewed." % ", ".join(
                    item.get("name") or item["resource"] for item in fragile),
                "A roster where every body is a bottleneck is a roster that cannot "
                "absorb a bad morning.",
                "Cross-train, or deliberately keep one resource free of specialist "
                "work as cover.",
            ))

    skill_holders = {}
    for resource in brief.resources:
        for skill in resource.skills:
            skill_holders.setdefault(skill.lower(), []).append(resource)
    for skill, holders in sorted(skill_holders.items()):
        if len(holders) != 1:
            continue
        needing = [job for job in brief.jobs if skill in [s.lower() for s in job.skills]]
        if len(needing) >= 2:
            findings.append(finding(
                "single_skill_holder", "medium", "resilience",
                "Only %s knows '%s'" % (holders[0].name or holders[0].id, skill),
                "%d jobs need '%s' and only %s holds it." % (
                    len(needing), skill, holders[0].name or holders[0].id),
                "Specialist skills concentrated on one person are the most common "
                "cause of a short, expensive day.",
                "Pair the specialist with a second qualified resource, or price the "
                "risk into the day.",
                [job.id for job in needing],
            ))

    # --- routing quality ------------------------------------------------
    for route in plan["routes"]:
        entries = route["entries"]
        for entry in entries:
            if entry["slack_minutes"] == 0:
                findings.append(finding(
                    "zero_slack", "medium", "feasibility",
                    "Job %s has no slack left" % entry["job_id"],
                    "%s starts at %s, exactly at the edge of its window." % (
                        entry["job_id"], fmt_clock(entry["start"])),
                    "Zero slack means one traffic jam turns into a missed window, "
                    "which is exactly the moment the day goes wrong.",
                    "Give the route more buffer, or move the job earlier in the day.",
                    [entry["job_id"]],
                ))

        for index in range(1, len(entries)):
            entry = entries[index]
            previous = entries[index - 1]
            if entry["priority"] != "emergency":
                continue
            window_start = entry["window_start"]
            if window_start is None or previous["end"] > window_start:
                findings.append(finding(
                    "emergency_not_first", "high", "feasibility",
                    "An emergency job is not first on %s" % (
                        route["resource"]["name"] or route["resource"]["id"]),
                    "%s runs from %s to %s and the emergency %s starts at %s." % (
                        previous["job_id"], fmt_clock(previous["start"]),
                        fmt_clock(previous["end"]), entry["job_id"],
                        fmt_clock(entry["start"])),
                    "An emergency that waits behind a routine job is the one call "
                    "that makes the whole sheet untrustworthy.",
                    "Put the emergency first and re-time whatever it displaces.",
                    [previous["job_id"], entry["job_id"]],
                ))

        for index in range(len(entries) - 1):
            first = entries[index]
            second = entries[index + 1]
            first_job = jobs_by_id.get(first["job_id"])
            second_job = jobs_by_id.get(second["job_id"])
            if not (first_job and second_job):
                continue
            if first_job.priority_rank >= second_job.priority_rank:
                continue
            if second["window_end"] is None or first["start"] is None:
                continue
            if second["window_end"] <= first["start"]:
                findings.append(finding(
                    "priority_inversion", "medium", "feasibility",
                    "A higher priority job was passed over",
                    "%s (%s) is served at %s while %s (%s) had a window that closed "
                    "at %s." % (
                        second["job_id"], second["priority"], fmt_clock(second["start"]),
                        first["job_id"], first["priority"], fmt_clock(second["window_end"])),
                    "When two jobs want the same slot the higher priority should win "
                    "it, otherwise the day decides for itself.",
                    "Swap the two visits, or move the lower priority job to a later "
                    "window.",
                    [first["job_id"], second["job_id"]],
                ))

    addresses = {}
    for job in brief.jobs:
        key = (job.location.address or "").strip().lower()
        if key:
            addresses.setdefault(key, []).append(job.id)
    for key, ids in sorted(addresses.items()):
        if len(ids) >= 2:
            findings.append(finding(
                "duplicate_address", "medium", "efficiency",
                "%d jobs share one address" % len(ids),
                "Jobs %s are all at '%s'." % (", ".join(ids), key),
                "Two visits to the same door are usually one visit with two tasks, "
                "and the second one costs a full trip.",
                "Combine them into a single job with the longer duration.",
                ids,
            ))

    # --- data quality ---------------------------------------------------
    missing_duration = [job.id for job in brief.jobs if job.duration is None]
    if missing_duration:
        share = 100.0 * len(missing_duration) / max(1, len(brief.jobs))
        findings.append(finding(
            "missing_duration", "high" if share > 30 else "medium", "data_quality",
            "%d job(s) have no service time" % len(missing_duration),
            "Jobs %s have no Duration." % ", ".join(missing_duration),
            "Without a service time the planner guesses how long the crew is tied "
            "up, so every downstream arrival time is a guess.",
            "Add a Duration to each job, even a rough one.",
            missing_duration,
        ))

    missing_address = [job.id for job in brief.jobs if not job.location.known]
    if missing_address:
        findings.append(finding(
            "missing_address", "high", "data_quality",
            "%d job(s) have no location" % len(missing_address),
            "Jobs %s have no address and no coordinates." % ", ".join(missing_address),
            "The planner cannot route to a place it cannot find, so the leg time is "
            "assumed rather than measured.",
            "Add an address or a coordinate pair for every job.",
            missing_address,
        ))

    missing_window = [job.id for job in brief.jobs
                      if job.window_start is None and job.window_end is None]
    if missing_window:
        findings.append(finding(
            "missing_window", "medium", "data_quality",
            "%d job(s) have no customer window" % len(missing_window),
            "Jobs %s have no Window." % ", ".join(missing_window),
            "A job without a window is scheduled wherever the route happens to reach "
            "it, which usually means the customer is treated as a fittable-in job.",
            "Give each job a window, even a wide one.",
            missing_window,
        ))

    missing_phone = [job.id for job in brief.jobs if not job.phone]
    if len(missing_phone) > len(brief.jobs) / 2:
        findings.append(finding(
            "missing_phone", "low", "data_quality",
            "Most jobs have no contact number",
            "%d of %d jobs have no Phone." % (len(missing_phone), len(brief.jobs)),
            "Without a number the crew cannot warn the customer they are running "
            "late, which is the cheapest way to keep a window.",
            "Add a contact number for each job.",
            missing_phone,
        ))

    stats = travel.stats()
    if stats["assumed_pairs"] > 0:
        findings.append(finding(
            "assumed_travel", "medium", "data_quality",
            "%d leg(s) use an assumed drive time" % stats["assumed_pairs"],
            "No coordinates and no [Travel] line for those legs, so %s minutes per "
            "leg was assumed." % brief.settings.assumed_travel_min,
            "Assumed drive times are the quietest source of wrong arrival times, "
            "because they look exact once they are in the plan.",
            "Add coordinates or explicit [Travel] lines for the legs that matter.",
        ))

    if metrics["resources_idle"] > 0 and metrics["resources_used"] > 0:
        findings.append(finding(
            "idle_resource", "info", "efficiency",
            "%d resource(s) finish with nothing" % metrics["resources_idle"],
            "On this day %d of %d resources were used." % (
                metrics["resources_used"], len(brief.resources)),
            "Unused capacity is not a problem, but it is often a sign that one crew "
            "is carrying work that two crews could share.",
            "Rebalance the assignment, or use the spare capacity for the jobs that "
            "currently have no crew.",
        ))

    revenue_total = metrics["revenue_total"]
    if revenue_total > 0 and len(brief.resources) > 1:
        for route in plan["routes"]:
            share = 100.0 * route["totals"]["revenue"] / revenue_total
            if share > 80:
                findings.append(finding(
                    "revenue_concentration", "info", "coverage",
                    "One crew carries %d%% of the day's revenue" % round(share),
                    "%s holds %.0f of %.0f in scheduled revenue." % (
                        route["resource"]["name"] or route["resource"]["id"],
                        route["totals"]["revenue"], revenue_total),
                    "Concentrated revenue is concentrated risk: one bad day for that "
                    "crew is a bad day for the business.",
                    "Spread the high value work across crews where the skills allow it.",
                ))

    findings.sort(key=lambda item: (SEVERITY_ORDER.get(item["severity"], 9),
                                    item["rule"], item["title"]))
    return findings
