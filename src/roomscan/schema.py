"""Output contract (JSON). `roomscan schema` writes schema/output.schema.json from these models.

Plan frame: top-down, metres, axes aligned with the dominant wall directions, origin at
the minimum corner of the property. x grows to the right of the rendered plan, y grows
downward on the rendered plan. Every measurement carries a 90 % interval.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0.0"


class Measurement(BaseModel):
    value: float | None = Field(description="Point estimate (metres / square metres). null = not observed")
    ci90: tuple[float, float] | None = Field(description="90 % interval [low, high]")
    sigma: float | None = Field(description="1-sigma standard uncertainty after tier calibration")
    unit: Literal["m", "m2"] = "m"
    lower_bound: float | None = Field(default=None, description="Hard lower bound when value is not observed")
    note: str | None = None


class Opening(BaseModel):
    id: str
    type: Literal["door", "window", "opening"]
    wall_id: str
    room_id: str
    connects_to: str | None = Field(description="Neighbouring room id (doors / open passages)")
    width: Measurement
    height: Measurement
    sill_height: Measurement
    offset_along_wall: float = Field(description="Distance from wall start to opening start (m)")


class Wall(BaseModel):
    id: str
    start: tuple[float, float]
    end: tuple[float, float]
    length: Measurement
    height: Measurement
    area: Measurement = Field(description="Gross wall area minus openings")
    opening_ids: list[str]
    evidence_coverage: float = Field(description="Fraction of the wall length with direct surface evidence")


class Surface(BaseModel):
    id: str
    type: Literal["wall", "floor", "ceiling"]
    ref: str = Field(description="wall id for walls, room id for floor / ceiling")
    area: Measurement


class Room(BaseModel):
    id: str
    label: str
    polygon: list[tuple[float, float]]
    floor_area: Measurement
    perimeter: Measurement
    ceiling_height: Measurement
    ceiling_source: Literal["ceiling_plane", "wall_top", "none", "model"]
    walls: list[Wall]
    openings: list[Opening]
    surfaces: list[Surface]


class Adjacency(BaseModel):
    rooms: tuple[str, str]
    via: str | None = Field(description="Opening id joining the two rooms (null = shared wall only)")
    kind: Literal["door", "opening", "shared_wall"]


class DamageRegion(BaseModel):
    id: str
    surface_id: str
    room_id: str
    damage_class: Literal["water_stain", "crack", "mold", "peeling_paint", "hole", "other"]
    score: float = Field(description="Detector confidence 0..1")
    area: Measurement = Field(description="Metric extent on the surface (m2)")
    extent_u: Measurement = Field(description="Horizontal extent on the surface (m)")
    extent_v: Measurement = Field(description="Vertical extent on the surface (m)")
    center: tuple[float, float, float] = Field(description="Plan x, plan y, height above floor (m)")
    evidence_frames: list[int]


class ConcealedFlag(BaseModel):
    id: str
    surface_id: str
    room_id: str
    rule_id: str
    rule: str = Field(description="Human-readable rule that fired")
    triggered_by: list[str] = Field(description="Damage region ids that triggered the rule")
    risk: Literal["low", "medium", "high"]
    recommendation: str


class ScopeItem(BaseModel):
    id: str
    surface_id: str
    room_id: str
    code: str
    description: str
    quantity: Measurement
    unit: Literal["m2", "m", "each"]
    source: str = Field(description="damage id or flag id this item is keyed to")


class Property(BaseModel):
    footprint_area: Measurement = Field(description="Sum of room floor areas")
    bbox: Measurement = Field(description="Diagonal of the stitched plan bounding box")
    room_ids: list[str]
    adjacency: list[Adjacency]
    stitch_method: str
    drift_correction: dict


class CaptureInfo(BaseModel):
    id: str
    tier: Literal["lidar", "video", "photo"]
    source: str
    n_frames_used: int
    meta: dict = {}


class Output(BaseModel):
    schema_version: str = SCHEMA_VERSION
    units: str = "metres; areas in square metres"
    interval: str = "90 % (ci90); sigma = 1-sigma after per-tier calibration"
    capture: CaptureInfo
    property: Property
    rooms: list[Room]
    damage: list[DamageRegion]
    concealed_damage_flags: list[ConcealedFlag]
    scope: list[ScopeItem]
    warnings: list[str]
    timing_s: dict[str, float]
