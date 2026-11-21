"""Assignment and sequencing engine for dispatch-forge.

The planner is deterministic: the same brief always produces the same
schedule, because every tie is broken on stable keys (job id, resource id,
position). That matters when a dispatcher compares two plans and asks
"what actually changed?".

Cost model used while searching:

    cost = travel minutes
         + 0.5 x waiting minutes (waiting is cheaper than driving, but it
           still means the day is not packed)
         + late minutes x late cost per minute x priority weight
         + overtime minutes
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .models import Job, Resource, Settings

LATE_WEIGHT = {
    "emergency": 3.0,
    "high": 2.0,
    "normal": 1.0,
    "low": 0.6,
}

WAIT_WEIGHT = 0.5
OVERTIME_WEIGHT = 1.0


def late_weight(job: Job) -> float:
    return LATE_WEIGHT.get(job.priority, 1.0)


def job_location(job: Job) -> Tuple[str, object]:
    return (job.id, job.location)


def resource_base(resource: Resource, settings: Settings) -> Tuple[str, object]:
    if resource.base.known:
        return (resource.id + ":base", resource.base)
    return ("depot", settings.depot)


def depot_location(settings: Settings) -> Tuple[str, object]:
    return ("depot", settings.depot)


def eligibility(job: Job, resource: Resource, settings: Settings) -> Tuple[bool, List[str]]:
    """Can this resource plausibly take this job at all?"""
    reasons: List[str] = []
    needed = set(skill.lower() for skill in job.skills)
    held = set(skill.lower() for skill in resource.skills)
    if needed - held:
        reasons.append("no_skill")
    if job.window_start is not None and job.window_end is not None:
        if job.window_end < resource.shift_start:
            reasons.append("window_ends_before_shift")
        elif job.window_start > resource.shift_end:
            reasons.append("window_starts_after_shift")
    elif job.window_start is not None and job.window_start > resource.shift_end:
        reasons.append("window_starts_after_shift")
    return (not reasons, reasons)


def evaluate_route(sequence: List[Job], resource: Resource, settings: Settings, travel) -> Dict:
    """Walk a route and produce the timeline plus totals."""
    entries: List[Dict] = []
    clock = resource.shift_start
    previous = resource_base(resource, settings)
    travel_minutes = 0.0
    wait_minutes = 0.0
    late_minutes = 0.0
    weighted_late = 0.0
    service_minutes = 0.0
    travel_miles = 0.0
    assumed_legs = 0

    for job in sequence:
        leg, basis = travel.minutes(previous, job_location(job))
        if basis == "assumed":
            assumed_legs += 1
        miles = travel.miles(previous, job_location(job))
        if miles is not None:
            travel_miles += miles
        arrive = clock + leg + settings.travel_buffer
        wait = 0
        if job.window_start is not None and arrive < job.window_start:
            wait = job.window_start - arrive
            arrive = job.window_start
        start = arrive
        late = 0
        if job.window_end is not None and start > job.window_end:
            late = start - job.window_end
        duration = job.duration or 0
        end = start + duration
        if job.window_end is not None:
            on_time = late <= 0
        else:
            on_time = True
        entries.append({
            "job_id": job.id,
            "customer": job.customer,
            "priority": job.priority,
            "skills": list(job.skills),
            "leg_minutes": round(leg, 1),
            "leg_basis": basis,
            "arrive": int(round(start)),
            "start": int(round(start)),
            "end": int(round(end)),
            "window_start": job.window_start,
            "window_end": job.window_end,
            "idle_minutes": round(wait, 1),
            "late_minutes": round(late, 1),
            "duration": duration,
            "revenue": job.revenue,
            "slack_minutes": (job.window_end - int(round(start))) if job.window_end is not None else None,
            "on_time": on_time,
            "address": job.location.address,
        })
        travel_minutes += leg
        wait_minutes += wait
        late_minutes += late
        weighted_late += late * late_weight(job)
        service_minutes += duration
        clock = end
        previous = job_location(job)

    if settings.return_to_depot and settings.depot.known:
        leg, _basis = travel.minutes(previous, depot_location(settings))
        travel_minutes += leg
        miles = travel.miles(previous, depot_location(settings))
        if miles is not None:
            travel_miles += miles
        clock += leg

    span = max(1, resource.shift_end - resource.shift_start)
    overtime = max(0.0, clock - resource.shift_end)
    productive = travel_minutes + service_minutes
    utilization = productive / span
    cost = (travel_minutes
            + WAIT_WEIGHT * wait_minutes
            + settings.late_cost_per_min * weighted_late
            + OVERTIME_WEIGHT * overtime)

    totals = {
        "jobs": len(sequence),
        "travel_minutes": round(travel_minutes, 1),
        "idle_minutes": round(wait_minutes, 1),
        "productive_minutes": round(travel_minutes + service_minutes, 1),
        "late_minutes": round(late_minutes, 1),
        "service_minutes": round(service_minutes, 1),
        "overtime_minutes": round(overtime, 1),
        "travel_miles": round(travel_miles, 1),
        "assumed_legs": assumed_legs,
        "span_minutes": span,
        "utilization": round(utilization, 4),
        "cost": round(cost, 2),
        "ends_at": int(round(clock)),
        "starts_at": resource.shift_start,
        "on_time": sum(1 for e in entries if e["on_time"]),
        "late_jobs": sum(1 for e in entries if e["late_minutes"] > 0),
        "revenue": round(sum(e["revenue"] for e in entries), 2),
    }
    return {"entries": entries, "totals": totals}


def route_cost(sequence: List[Job], resource: Resource, settings: Settings, travel) -> float:
    return evaluate_route(sequence, resource, settings, travel)["totals"]["cost"]


def plan_cost(assign, by_id, settings, travel) -> float:
    total = 0.0
    for rid, sequence in assign.items():
        total += route_cost(sequence, by_id[rid], settings, travel)
    return total


def _local_search(assign, by_id, settings, travel, rounds=6):
    """Try to improve the assignment and the order of every route."""
    for _round in range(rounds):
        improved = False

        for rid in sorted(assign.keys()):
            sequence = assign[rid]
            resource = by_id[rid]
            current = route_cost(sequence, resource, settings, travel)
            best_cost = current
            best_sequence = list(sequence)
            for i in range(len(sequence)):
                for j in range(len(sequence)):
                    if i == j:
                        continue
                    candidate = list(sequence)
                    job = candidate.pop(i)
                    candidate.insert(j, job)
                    cost = route_cost(candidate, resource, settings, travel)
                    if cost < best_cost - 1e-6:
                        best_cost = cost
                        best_sequence = candidate
            if best_cost < current - 1e-6:
                assign[rid] = best_sequence
                improved = True

        for rid in sorted(assign.keys()):
            for index in range(len(assign[rid])):
                job = assign[rid][index]
                source = by_id[rid]
                without = assign[rid][:index] + assign[rid][index + 1:]
                base_cost = plan_cost(assign, by_id, settings, travel)
                for target_id in sorted(by_id.keys()):
                    if target_id == rid:
                        continue
                    target = by_id[target_id]
                    ok, _reasons = eligibility(job, target, settings)
                    if not ok:
                        continue
                    for position in range(len(assign[target_id]) + 1):
                        candidate = assign[target_id][:position] + [job] + assign[target_id][position:]
                        trial = dict(assign)
                        trial[rid] = without
                        trial[target_id] = candidate
                        cost = plan_cost(trial, by_id, settings, travel)
                        if cost < base_cost - 1e-6:
                            assign[rid] = without
                            assign[target_id] = candidate
                            base_cost = cost
                            improved = True
                            break
                    if improved:
                        break
                if improved:
                    break
            if improved:
                break

        if not improved:
            break
    return assign


def build_plan(jobs: List[Job], resources: List[Resource], settings: Settings, travel,
               improve: bool = True) -> Dict:
    by_id = {resource.id: resource for resource in resources}
    order = sorted(
        jobs,
        key=lambda j: (-j.priority_rank,
                       j.window_start if j.window_start is not None else 10 ** 9,
                       str(j.id)),
    )
    assign: Dict[str, List[Job]] = {resource.id: [] for resource in resources}
    unassigned: List[Dict] = []

    for job in order:
        best = None
        for resource in resources:
            ok, _reasons = eligibility(job, resource, settings)
            if not ok:
                continue
            for position in range(len(assign[resource.id]) + 1):
                candidate = assign[resource.id][:position] + [job] + assign[resource.id][position:]
                cost = route_cost(candidate, resource, settings, travel)
                key = (round(cost, 4), str(resource.id), position)
                if best is None or key < best[0]:
                    best = (key, resource, candidate)
        if best is None:
            reasons: List[str] = []
            for resource in resources:
                ok, why = eligibility(job, resource, settings)
                if ok:
                    reasons.append("no_feasible_slot")
                else:
                    reasons.extend(why)
            unassigned.append({
                "job": job,
                "reasons": sorted(set(reasons)) or ["no_resources"],
            })
        else:
            assign[best[1].id] = best[2]

    if improve and resources:
        assign = _local_search(assign, by_id, settings, travel)

    routes = []
    for resource in resources:
        evaluation = evaluate_route(assign.get(resource.id, []), resource, settings, travel)
        routes.append({
            "resource": {
                "id": resource.id,
                "name": resource.name,
                "skills": list(resource.skills),
                "shift_start": resource.shift_start,
                "shift_end": resource.shift_end,
                "notes": resource.notes,
            },
            "entries": evaluation["entries"],
            "totals": evaluation["totals"],
        })

    return {
        "routes": routes,
        "unassigned": unassigned,
        "assign": {rid: [job.id for job in sequence] for rid, sequence in assign.items()},
    }
