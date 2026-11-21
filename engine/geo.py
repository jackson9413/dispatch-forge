"""Travel-time model for dispatch-forge.

Travel minutes between two stops come from one of three bases, in order of
trust:

  override  - the operator typed a real drive time into the brief
  haversine - straight-line distance inflated by a road factor, divided by
               an average speed
  assumed   - neither is known, so a fallback assumption is used and the
               planner says so out loud instead of pretending it is exact
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

EARTH_RADIUS_MILES = 3958.7613


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_MILES * math.asin(min(1.0, math.sqrt(a)))


def norm_id(value: str) -> str:
    return (value or "").strip().lower()


class TravelModel:
    """Travel minutes between named locations, memoised per leg."""

    def __init__(self, settings, overrides: Optional[Dict[Tuple[str, str], float]] = None):
        self.settings = settings
        self.overrides: Dict[Tuple[str, str], float] = {}
        for (origin, target), minutes in (overrides or {}).items():
            self.overrides[(norm_id(origin), norm_id(target))] = float(minutes)
            self.overrides[(norm_id(target), norm_id(origin))] = float(minutes)
        self._cache: Dict[Tuple[str, str], Tuple[float, str]] = {}
        self.assumed_pairs = 0
        self.override_pairs = 0
        self.haversine_pairs = 0

    def minutes(self, origin, target) -> Tuple[float, str]:
        """origin and target are (id, Location) pairs."""
        key = (norm_id(origin[0]), norm_id(target[0]))
        if key in self._cache:
            return self._cache[key]
        value = self._compute(origin, target)
        self._cache[key] = value
        return value

    def _compute(self, origin, target) -> Tuple[float, str]:
        origin_id, origin_loc = origin
        target_id, target_loc = target
        if origin_id == target_id:
            return (0.0, "same")
        override = self.overrides.get((norm_id(origin_id), norm_id(target_id)))
        if override is not None:
            self.override_pairs += 1
            return (override, "override")
        if origin_loc.has_coords and target_loc.has_coords:
            miles = haversine_miles(origin_loc.lat, origin_loc.lon,
                                   target_loc.lat, target_loc.lon)
            minutes = (miles * self.settings.road_factor) / max(1.0, self.settings.speed_mph) * 60.0
            self.haversine_pairs += 1
            return (minutes, "haversine")
        self.assumed_pairs += 1
        return (float(self.settings.assumed_travel_min), "assumed")

    def miles(self, origin, target) -> Optional[float]:
        origin_id, origin_loc = origin
        target_id, target_loc = target
        if origin_id == target_id:
            return 0.0
        if origin_loc.has_coords and target_loc.has_coords:
            return haversine_miles(origin_loc.lat, origin_loc.lon,
                                   target_loc.lat, target_loc.lon)
        return None

    def stats(self) -> Dict[str, int]:
        return {
            "override_pairs": self.override_pairs,
            "haversine_pairs": self.haversine_pairs,
            "assumed_pairs": self.assumed_pairs,
        }
