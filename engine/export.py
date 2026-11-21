"""Text exports for dispatch-forge."""

from __future__ import annotations

import csv
import io
from typing import Dict, List

from .models import fmt_clock, fmt_duration


def to_markdown(result: Dict) -> str:
    lines: List[str] = []
    score = result["score"]
    metrics = result["metrics"]
    brief = result["brief"]

    lines.append("# Dispatch plan - %s" % (brief["settings"]["day"] or "unscheduled day"))
    lines.append("")
    lines.append("**Verdict:** %s (%s / 100)  " % (score["verdict"], score["overall"]))
    lines.append("**Profile:** %s  " % score["profile"])
    lines.append("**Mode:** %s" % brief["settings"]["mode"])
    lines.append("")
    lines.append("## Scorecard")
    lines.append("")
    lines.append("| Axis | Score | Note |")
    lines.append("| --- | --- | --- |")
    for axis in score["axes"]:
        lines.append("| %s | %.1f | %s |" % (axis["label"], axis["score"], axis["note"]))
    lines.append("")
    if score["caps"]:
        lines.append("Caps applied:")
        for cap in score["caps"]:
            lines.append("- %s: %.1f -> %.1f (%s)" % (
                cap["axis"], cap["from"], cap["capped_at"], cap["reason"]))
        lines.append("")

    lines.append("## Day summary")
    lines.append("")
    lines.append("- Jobs: %d (%d crewed, %d unassigned)" % (
        metrics["jobs"], metrics["assigned"], metrics["unassigned"]))
    lines.append("- On time: %d visits, %s late" % (
        metrics["on_time"], fmt_duration(metrics["late_minutes"])))
    lines.append("- Driving: %s (%d%% of the day)" % (
        fmt_duration(metrics["travel_minutes"]), round(metrics["travel_share_pct"])))
    lines.append("- On site: %s | Idle: %s | Overtime: %s" % (
        fmt_duration(metrics["service_minutes"]), fmt_duration(metrics["idle_minutes"]),
        fmt_duration(metrics["overtime_minutes"])))
    lines.append("- Scheduled revenue: $%.2f (unassigned: $%.2f)" % (
        metrics["revenue_scheduled"], metrics["revenue_unassigned"]))
    lines.append("")

    lines.append("## Routes")
    for route in result["schedule"]["routes"]:
        resource = route["resource"]
        lines.append("")
        lines.append("### %s (%s) - %s to %s" % (
            resource["name"] or resource["id"], resource["id"],
            fmt_clock(resource["shift_start"]), fmt_clock(resource["shift_end"])))
        lines.append("")
        if not route["entries"]:
            lines.append("_No jobs assigned._")
            continue
        lines.append("| # | Job | Arrive | Window | Late | Work | Idle | Leg | Customer |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for index, entry in enumerate(route["entries"], start=1):
            window = ""
            if entry["window_start"] is not None or entry["window_end"] is not None:
                window = "%s-%s" % (fmt_clock(entry["window_start"]), fmt_clock(entry["window_end"]))
            lines.append("| %d | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                index, entry["job_id"], fmt_clock(entry["start"]), window or "none",
                fmt_duration(entry["late_minutes"]), fmt_duration(entry["duration"]),
                fmt_duration(entry["idle_minutes"]), fmt_duration(entry["leg_minutes"]),
                entry["customer"] or "-"))
        totals = route["totals"]
        lines.append("")
        lines.append("_%d jobs, %s driving, %s on site, %s late, %.0f%% utilisation_" % (
            totals["jobs"], fmt_duration(totals["travel_minutes"]),
            fmt_duration(totals["service_minutes"]), fmt_duration(totals["late_minutes"]),
            totals["utilization"] * 100))
    lines.append("")

    if result["schedule"]["unassigned"]:
        lines.append("## Unassigned jobs")
        for item in result["schedule"]["unassigned"]:
            lines.append("- **%s** (%s): %s" % (
                item["id"], item["priority"], ", ".join(item["reasons"])))
        lines.append("")

    lines.append("## Findings")
    for item in result["findings"]:
        lines.append("")
        lines.append("### [%s] %s" % (item["severity"].upper(), item["title"]))
        lines.append("")
        lines.append("- **Evidence:** %s" % item["evidence"])
        lines.append("- **Why it matters:** %s" % item["why"])
        lines.append("- **Fix:** %s" % item["fix"])
    lines.append("")

    lines.append("## If one resource calls in sick")
    lines.append("")
    lines.append("| Resource | Jobs left uncrewed | Extra lateness | Impact |")
    lines.append("| --- | --- | --- | --- |")
    for item in result["resilience"]:
        lines.append("| %s | %d | %s | %d%% |" % (
            item.get("name") or item["resource"], item["unassigned"],
            fmt_duration(item["late_minutes"]), item["impact_pct"]))
    lines.append("")
    return "\n".join(lines)


def to_csv(result: Dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["job_id", "customer", "priority", "status", "resource",
                     "window_start", "window_end", "arrive", "leave", "idle_min",
                     "late_min", "leg_min", "revenue", "address"])
    for row in result["jobs_view"]:
        writer.writerow([
            row["id"], row["customer"], row["priority"], row["status"],
            row["resource_name"] or "",
            fmt_clock(row["window_start"]), fmt_clock(row["window_end"]),
            fmt_clock(row["start"]) if row["start"] is not None else "",
            fmt_clock(row["end"]) if row["end"] is not None else "",
            row["idle_minutes"], row["late_minutes"], row["leg_minutes"],
            row["revenue"], row["address"],
        ])
    return buffer.getvalue()
