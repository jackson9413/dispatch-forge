"""Brief parsing and data models for dispatch-forge.

A dispatch brief is plain text. Every meaningful line starts with a section
tag in square brackets:

    [Job] ID: J1 | Customer: Rivera | Address: 12 Oak St | Window: 09:00-11:00 | Duration: 45m | Skills: hvac | Priority: high

Recognised sections: [Job], [Resource], [Depot], [Travel], [Settings].
Lines that start with '#' are comments. Anything else is ignored with a
warning so a malformed brief never crashes the planner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

PRIORITY_RANK = {
    "emergency": 4,
    "urgent": 4,
    "high": 3,
    "medium": 2,
    "normal": 2,
    "low": 1,
}

PRIORITY_ALIASES = {
    "emergency": "emergency",
    "emerg": "emergency",
    "urgent": "emergency",
    "critical": "emergency",
    "high": "high",
    "hi": "high",
    "medium": "normal",
    "normal": "normal",
    "standard": "normal",
    "regular": "normal",
    "low": "low",
    "routine": "low",
}


def norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").strip().lower()).strip()


def clean(value: str) -> str:
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1].strip()
    return value


def parse_clock(token: str) -> Optional[int]:
    """Parse a wall-clock time into minutes after midnight."""
    text = (token or "").strip().lower().replace(".", "")
    text = re.sub(r"\s+", "", text)
    if not text:
        return None
    if text in ("noon", "midday"):
        return 720
    if text == "midnight":
        return 0
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm|a|p)?$", text)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2) or 0)
        suffix = match.group(3)
    else:
        match = re.match(r"^(\d{3,4})$", text)
        if not match:
            return None
        digits = match.group(1)
        hours = int(digits[:-2])
        minutes = int(digits[-2:])
        suffix = None
    if suffix:
        if suffix.startswith("p") and hours < 12:
            hours += 12
        elif suffix.startswith("a") and hours == 12:
            hours = 0
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


DURATION_UNITS = {
    "h": 60, "hr": 60, "hrs": 60, "hour": 60, "hours": 60,
    "m": 1, "min": 1, "mins": 1, "minute": 1, "minutes": 1,
    "d": 480, "day": 480, "days": 480,
}


def parse_duration(token: str) -> Optional[int]:
    """Parse '45m', '1h30m', '2 hours', '1.5h' into minutes."""
    text = (token or "").strip().lower().replace(",", "")
    if not text:
        return None
    total = 0.0
    found = False
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*([a-z]*)", text):
        amount = float(match.group(1))
        unit = match.group(2) or "m"
        factor = DURATION_UNITS.get(unit)
        if factor is None:
            continue
        total += amount * factor
        found = True
    if not found:
        return None
    return int(round(total))


def parse_window(text: str) -> Tuple[Optional[int], Optional[int], str]:
    """Parse a customer window into (start, end, shape)."""
    raw = (text or "").strip()
    if not raw:
        return (None, None, "empty")
    low = raw.lower()
    if low.startswith("after "):
        return (parse_clock(raw[6:]), None, "open_start")
    if low.startswith("by ") or low.startswith("before "):
        return (None, parse_clock(raw.split(" ", 1)[1]), "open_end")
    parts = re.split(r"\s*(?:-|\u2013|\u2014|\bto\b)\s*", raw, maxsplit=1)
    if len(parts) == 2:
        return (parse_clock(parts[0]), parse_clock(parts[1]), "range")
    point = parse_clock(raw)
    return (point, None, "point")


def parse_money(text: str) -> float:
    value = (text or "").strip().replace(",", "").replace("$", "")
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group(0)) if match else 0.0


def parse_skills(text: str) -> List[str]:
    parts = re.split(r"[,;/|+]", text or "")
    return [p.strip().lower() for p in parts if p.strip()]


def parse_coords(text: str) -> Tuple[Optional[float], Optional[float]]:
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)", text or "")
    if not match:
        return (None, None)
    return (float(match.group(1)), float(match.group(2)))


@dataclass
class Location:
    address: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None

    @property
    def known(self) -> bool:
        return bool(self.address) or self.lat is not None

    @property
    def has_coords(self) -> bool:
        return self.lat is not None and self.lon is not None


@dataclass
class Job:
    id: str
    customer: str = ""
    location: Location = field(default_factory=Location)
    window_start: Optional[int] = None
    window_end: Optional[int] = None
    duration: Optional[int] = None
    skills: List[str] = field(default_factory=list)
    priority: str = "normal"
    revenue: float = 0.0
    phone: str = ""
    notes: str = ""
    line_no: int = 0

    @property
    def priority_rank(self) -> int:
        return PRIORITY_RANK.get(self.priority, 2)

    def window_text(self) -> str:
        if self.window_start is not None and self.window_end is not None:
            return fmt_clock(self.window_start) + " - " + fmt_clock(self.window_end)
        if self.window_start is not None:
            return "from " + fmt_clock(self.window_start)
        if self.window_end is not None:
            return "by " + fmt_clock(self.window_end)
        return "no window"


@dataclass
class Resource:
    id: str
    name: str = ""
    skills: List[str] = field(default_factory=list)
    shift_start: int = 480
    shift_end: int = 1020
    base: Location = field(default_factory=Location)
    notes: str = ""
    line_no: int = 0

    @property
    def label(self) -> str:
        return self.name or self.id


@dataclass
class Settings:
    day: str = ""
    shift_start: int = 480
    shift_end: int = 1020
    depot: Location = field(default_factory=Location)
    depot_name: str = "Depot"
    speed_mph: float = 32.0
    road_factor: float = 1.3
    travel_buffer: int = 5
    late_cost_per_min: float = 2.0
    return_to_depot: bool = True
    mode: str = "team"
    assumed_travel_min: int = 30


@dataclass
class Brief:
    jobs: List[Job] = field(default_factory=list)
    resources: List[Resource] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)
    travel_overrides: Dict[Tuple[str, str], float] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def fmt_clock(minutes: Optional[int]) -> str:
    if minutes is None:
        return "--:--"
    minutes = int(minutes) % (24 * 60)
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def fmt_duration(minutes: Optional[float]) -> str:
    if minutes is None:
        return "--"
    minutes = int(round(minutes))
    if minutes < 60:
        return str(minutes) + "m"
    hours, rest = divmod(minutes, 60)
    return "%dh%02dm" % (hours, rest)


SECTION_ALIASES = {
    "job": "job", "jobs": "job", "task": "job", "visit": "job", "call": "job",
    "service call": "job", "work order": "job",
    "resource": "resource", "resources": "resource", "tech": "resource",
    "worker": "resource", "employee": "resource", "driver": "resource",
    "crew": "resource", "person": "resource",
    "depot": "depot", "base": "depot", "yard": "depot",
    "travel": "travel", "travel times": "travel", "matrix": "travel",
    "settings": "settings", "config": "settings", "options": "settings",
}

JOB_FIELDS = {
    "id": ("id", "job id", "job", "ref", "reference", "ticket", "ticket id", "call id"),
    "customer": ("customer", "client", "account", "name", "who", "for"),
    "address": ("address", "addr", "site", "street", "where"),
    "loc": ("loc", "coordinates", "coords", "gps"),
    "lat": ("lat", "latitude"),
    "lon": ("lon", "lng", "long", "longitude"),
    "window": ("window", "appointment window", "time window", "arrival window",
               "availability", "when", "appointment"),
    "duration": ("duration", "service time", "onsite", "on site", "length",
                 "time needed", "job time", "time"),
    "skills": ("skills", "skill", "requires", "requirements", "required", "tags"),
    "priority": ("priority", "urgency", "prio"),
    "revenue": ("revenue", "value", "price", "fee", "charge", "amount", "job value"),
    "phone": ("phone", "tel", "telephone", "contact", "mobile", "contact number"),
    "notes": ("notes", "note", "comments", "comment", "detail", "details"),
}

RESOURCE_FIELDS = {
    "id": ("id", "resource id", "tech id", "worker id"),
    "name": ("name", "tech", "worker", "employee", "who"),
    "skills": ("skills", "skill", "qualified", "certifications", "tags"),
    "shift": ("shift", "shift hours", "hours", "working hours", "availability"),
    "base": ("base", "start", "start location", "home base"),
    "loc": ("loc", "coordinates", "coords"),
    "lat": ("lat", "latitude"),
    "lon": ("lon", "lng", "long", "longitude"),
    "notes": ("notes", "note", "comments"),
}

SETTINGS_FIELDS = {
    "day": ("day", "date"),
    "shift": ("shift", "shift hours", "hours", "work hours"),
    "depot": ("depot", "depot address", "yard", "start"),
    "loc": ("loc", "coordinates"),
    "lat": ("lat", "latitude"),
    "lon": ("lon", "lng", "longitude"),
    "speed": ("speed", "speed mph", "avg speed", "average speed"),
    "road_factor": ("road factor", "detour factor"),
    "buffer": ("buffer", "travel buffer", "setup buffer", "parking buffer"),
    "late_cost": ("late cost", "cost per late minute", "late penalty"),
    "return": ("return to depot", "return to base", "round trip"),
    "mode": ("mode",),
    "assumed_travel": ("assumed travel", "default travel", "fallback travel"),
}

TRAVEL_FIELDS = {
    "from": ("from", "origin", "a", "source"),
    "to": ("to", "destination", "b", "target"),
    "minutes": ("minutes", "mins", "min", "time"),
}


def split_fields(rest: str) -> List[Tuple[str, str]]:
    pairs = []
    for chunk in (rest or "").split("|"):
        if ":" in chunk:
            key, value = chunk.split(":", 1)
            pairs.append((norm_key(key), clean(value)))
        else:
            value = clean(chunk)
            if value:
                pairs.append(("", value))
    return pairs


def collect(fields: List[Tuple[str, str]], spec: Dict[str, Tuple[str, ...]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in fields:
        if not key:
            continue
        for target, aliases in spec.items():
            if key in aliases:
                if target not in out or not out[target]:
                    out[target] = value
                break
    return out


def slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip()).strip("-")
    return cleaned or "x"


def build_job(fields, index, line_no, warnings) -> Job:
    values = collect(fields, JOB_FIELDS)
    job_id = values.get("id") or ("J%d" % index)
    customer = values.get("customer", "")
    window_start, window_end, _shape = parse_window(values.get("window", ""))
    location = Location(address=values.get("address", ""))
    if values.get("loc"):
        lat, lon = parse_coords(values["loc"])
        location.lat, location.lon = lat, lon
    if values.get("lat"):
        try:
            location.lat = float(values["lat"])
        except ValueError:
            warnings.append("job %s: unreadable latitude" % job_id)
    if values.get("lon"):
        try:
            location.lon = float(values["lon"])
        except ValueError:
            warnings.append("job %s: unreadable longitude" % job_id)
    duration = parse_duration(values.get("duration", ""))
    if values.get("duration") and duration is None:
        warnings.append("job %s: unreadable duration '%s'" % (job_id, values["duration"]))
    priority_raw = (values.get("priority") or "normal").strip().lower()
    priority = PRIORITY_ALIASES.get(priority_raw, priority_raw if priority_raw in PRIORITY_RANK else "normal")
    return Job(
        id=job_id,
        customer=customer,
        location=location,
        window_start=window_start,
        window_end=window_end,
        duration=duration,
        skills=parse_skills(values.get("skills", "")),
        priority=priority,
        revenue=parse_money(values.get("revenue", "")),
        phone=values.get("phone", ""),
        notes=values.get("notes", ""),
        line_no=line_no,
    )


def build_resource(fields, index, line_no, warnings) -> Resource:
    values = collect(fields, RESOURCE_FIELDS)
    name = values.get("name", "")
    res_id = values.get("id") or (slug(name).lower() or ("R%d" % index))
    shift_start, shift_end = 480, 1020
    if values.get("shift"):
        start, end, _shape = parse_window(values["shift"])
        if start is not None:
            shift_start = start
        if end is not None:
            shift_end = end
        if shift_end <= shift_start:
            warnings.append("resource %s: shift end is not after shift start" % res_id)
            shift_end = shift_start + 480
    base = Location(address=values.get("base", ""))
    if values.get("loc"):
        lat, lon = parse_coords(values["loc"])
        base.lat, base.lon = lat, lon
    if values.get("lat"):
        try:
            base.lat = float(values["lat"])
        except ValueError:
            warnings.append("resource %s: unreadable latitude" % res_id)
    if values.get("lon"):
        try:
            base.lon = float(values["lon"])
        except ValueError:
            warnings.append("resource %s: unreadable longitude" % res_id)
    return Resource(
        id=res_id,
        name=name or res_id,
        skills=parse_skills(values.get("skills", "")),
        shift_start=shift_start,
        shift_end=shift_end,
        base=base,
        notes=values.get("notes", ""),
        line_no=line_no,
    )


def apply_settings(fields, settings: Settings, warnings) -> None:
    values = collect(fields, SETTINGS_FIELDS)
    if values.get("day"):
        settings.day = values["day"]
    if values.get("shift"):
        start, end, _shape = parse_window(values["shift"])
        if start is not None:
            settings.shift_start = start
        if end is not None:
            settings.shift_end = end
    if values.get("depot"):
        settings.depot.address = values["depot"]
    if values.get("loc"):
        lat, lon = parse_coords(values["loc"])
        settings.depot.lat, settings.depot.lon = lat, lon
    if values.get("lat"):
        try:
            settings.depot.lat = float(values["lat"])
        except ValueError:
            warnings.append("settings: unreadable latitude")
    if values.get("lon"):
        try:
            settings.depot.lon = float(values["lon"])
        except ValueError:
            warnings.append("settings: unreadable longitude")
    if values.get("speed"):
        digits = re.sub(r"[^0-9.]", "", values["speed"])
        try:
            settings.speed_mph = float(digits) if digits else settings.speed_mph
        except ValueError:
            warnings.append("settings: unreadable speed")
    if values.get("road_factor"):
        digits = re.sub(r"[^0-9.]", "", values["road_factor"])
        try:
            settings.road_factor = float(digits) if digits else settings.road_factor
        except ValueError:
            warnings.append("settings: unreadable road factor")
    if values.get("buffer"):
        minutes = parse_duration(values["buffer"])
        if minutes is not None:
            settings.travel_buffer = minutes
    if values.get("late_cost"):
        settings.late_cost_per_min = parse_money(values["late_cost"])
    if values.get("return"):
        settings.return_to_depot = values["return"].strip().lower() not in ("no", "false", "off", "0")
    if values.get("mode"):
        mode = values["mode"].strip().lower()
        settings.mode = "personal" if mode.startswith("person") or mode == "solo" else "team"
    if values.get("assumed_travel"):
        minutes = parse_duration(values["assumed_travel"])
        if minutes is not None:
            settings.assumed_travel_min = minutes


def apply_travel(fields, overrides, warnings) -> None:
    values = collect(fields, TRAVEL_FIELDS)
    origin = (values.get("from") or "").strip().lower()
    target = (values.get("to") or "").strip().lower()
    minutes = parse_duration(values.get("minutes", ""))
    if not origin or not target or minutes is None:
        warnings.append("travel line needs From, To and Minutes")
        return
    overrides[(origin, target)] = float(minutes)
    overrides[(target, origin)] = float(minutes)


def parse_brief(text: str) -> Brief:
    warnings: List[str] = []
    jobs: List[Job] = []
    resources: List[Resource] = []
    settings = Settings()
    overrides: Dict[Tuple[str, str], float] = {}

    for line_no, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^\[([^\]]+)\]\s*(.*)$", line)
        if not match:
            warnings.append("line %d: missing [Section] tag, ignored" % line_no)
            continue
        section = norm_key(match.group(1))
        kind = SECTION_ALIASES.get(section)
        if kind is None:
            warnings.append("line %d: unknown section [%s], ignored" % (line_no, match.group(1)))
            continue
        fields = split_fields(match.group(2))
        if kind == "job":
            jobs.append(build_job(fields, len(jobs) + 1, line_no, warnings))
        elif kind == "resource":
            resources.append(build_resource(fields, len(resources) + 1, line_no, warnings))
        elif kind == "depot":
            bare = [value for key, value in fields if not key]
            if bare and not settings.depot.address:
                settings.depot.address = bare[0]
            apply_settings(fields, settings, warnings)
        elif kind == "travel":
            apply_travel(fields, overrides, warnings)
        else:
            apply_settings(fields, settings, warnings)

    if not resources:
        settings.mode = "personal"
        resources.append(Resource(
            id="R1",
            name="Me",
            skills=[],
            shift_start=settings.shift_start,
            shift_end=settings.shift_end,
            base=settings.depot,
        ))
    else:
        for resource in resources:
            if not resource.base.known:
                resource.base = settings.depot

    seen = {}
    for job in jobs:
        key = job.id.strip().lower()
        if key in seen:
            warnings.append("duplicate job id %s on line %d" % (job.id, job.line_no))
        seen[key] = job.line_no

    return Brief(jobs=jobs, resources=resources, settings=settings,
                 travel_overrides=overrides, warnings=warnings)
