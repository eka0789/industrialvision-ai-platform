"""Food Processing & Packaging Equipment PdM Schemas & Domain Thresholds (Fase 5: `schemas.py`).

Supports food manufacturing equipment types:
- `HEAT_SEALER`: Horizontal/vertical heat sealer jaws (normal `sealer_temp_c` 140–185°C)
- `FLOW_WRAPPER`: High-speed flow-wrap packaging machine (`pneumatic_pressure_bar` 5.5–7.0 bar)
- `CONVEYOR_DRIVE`: Packaging line belt drive (`conveyor_speed_mpm`, `motor_current_amp`, `vibration_mm_s`)
- `BAKING_OVEN`: Tunnel baking oven (`zone_temp_c`)
- `CHILLER_TUNNEL`: Cold-chain cooling tunnel (`zone_temp_c`, `ambient_humidity_pct`)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

FoodMachineType = Literal[
    "HEAT_SEALER",
    "FLOW_WRAPPER",
    "CONVEYOR_DRIVE",
    "BAKING_OVEN",
    "CHILLER_TUNNEL",
    "food_packaging",
    "baking_oven",
    "chiller",
    "conveyor",
    "press",
    "cnc",
    "robot",
    "generic",
]

FOOD_MACHINE_TYPES: tuple[str, ...] = (
    "HEAT_SEALER",
    "FLOW_WRAPPER",
    "CONVEYOR_DRIVE",
    "BAKING_OVEN",
    "CHILLER_TUNNEL",
)

# Normal operating ranges for food packaging lines
SEALER_TEMP_NORMAL_MIN_C = 140.0
SEALER_TEMP_NORMAL_MAX_C = 185.0
PNEUMATIC_PRESSURE_NORMAL_MIN_BAR = 5.5
PNEUMATIC_PRESSURE_NORMAL_MAX_BAR = 7.0
VIBRATION_CRITICAL_MM_S = 4.5
MOTOR_CURRENT_CRITICAL_AMP = 14.0
AMBIENT_HUMIDITY_CRITICAL_PCT = 80.0


class FoodSensorReadingInput(BaseModel):
    """Unified sensor reading payload supporting both Food Packaging PdM and legacy AI4I fields."""

    # Legacy AI4I 2020 fields (auto-populated with nominal defaults when omitted in Food PdM mode)
    air_temperature_k: float = Field(default=300.0, ge=250, le=340)
    process_temperature_k: float = Field(default=310.0, ge=250, le=360)
    rotational_speed_rpm: float = Field(default=1500.0, ge=0, le=4000)
    torque_nm: float = Field(default=40.0, ge=0, le=500)
    tool_wear_min: float = Field(default=50.0, ge=0, le=300)

    # Food Processing & Packaging Equipment telemetry (Fase 5)
    sealer_temp_c: float | None = Field(default=None, ge=20.0, le=350.0)
    pneumatic_pressure_bar: float | None = Field(default=None, ge=0.0, le=16.0)
    conveyor_speed_mpm: float | None = Field(default=None, ge=0.0, le=500.0)
    motor_current_amp: float | None = Field(default=None, ge=0.0, le=100.0)
    motor_current_a: float | None = Field(default=None, ge=0.0, le=100.0)
    vibration_mm_s: float | None = Field(default=None, ge=0.0, le=50.0)
    ambient_humidity_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    zone_temp_c: float | None = Field(default=None, ge=-45.0, le=350.0)
    recorded_at: datetime | None = None

    @model_validator(mode="after")
    def sync_motor_current_aliases(self) -> "FoodSensorReadingInput":
        if self.motor_current_amp is not None and self.motor_current_a is None:
            self.motor_current_a = self.motor_current_amp
        elif self.motor_current_a is not None and self.motor_current_amp is None:
            self.motor_current_amp = self.motor_current_a
        return self

    def effective_motor_current(self) -> float | None:
        if self.motor_current_amp is not None:
            return float(self.motor_current_amp)
        if self.motor_current_a is not None:
            return float(self.motor_current_a)
        return None


def evaluate_food_equipment_anomalies(
    food_metrics: dict[str, Any] | None = None,
    **kwargs: Any,
) -> list[str]:
    """Evaluate domain-specific failure modes for food packaging & processing machines."""
    metrics = dict(food_metrics or {})
    metrics.update(kwargs)
    modes: list[str] = []
    sealer_c = metrics.get("sealer_temp_c")
    pressure_bar = metrics.get("pneumatic_pressure_bar")
    motor_amp = metrics.get("motor_current_amp")
    if motor_amp is None:
        motor_amp = metrics.get("motor_current_a")
    vib_mm_s = metrics.get("vibration_mm_s")
    humidity_pct = metrics.get("ambient_humidity_pct")

    if sealer_c is not None:
        if float(sealer_c) < SEALER_TEMP_NORMAL_MIN_C:
            modes.append("SEAL_TEMP_LOW")
        elif float(sealer_c) > SEALER_TEMP_NORMAL_MAX_C:
            modes.append("SEAL_TEMP_HIGH")
    if pressure_bar is not None and float(pressure_bar) < PNEUMATIC_PRESSURE_NORMAL_MIN_BAR:
        modes.append("PNEUMATIC_DROP")
    if motor_amp is not None and float(motor_amp) > MOTOR_CURRENT_CRITICAL_AMP:
        modes.append("CONVEYOR_OVERLOAD")
    if vib_mm_s is not None and float(vib_mm_s) > VIBRATION_CRITICAL_MM_S:
        modes.append("BEARING_VIBRATION_HIGH")
    if humidity_pct is not None and float(humidity_pct) > AMBIENT_HUMIDITY_CRITICAL_PCT:
        modes.append("HIGH_AMBIENT_HUMIDITY")
    return modes
