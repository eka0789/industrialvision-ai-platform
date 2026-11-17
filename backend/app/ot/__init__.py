"""Operational Technology (OT) Bridge for conveyor photoelectric triggers & PLC rejectors."""

from app.ot.adapter import (
    BaseOTAdapter,
    MQTTIndustrialAdapter,
    ModbusTCPAdapter,
    OTAdapter,
    SimulatedOTAdapter,
    create_ot_adapter,
    execute_hardware_trigger,
    get_ot_adapter,
    get_simulated_ot_adapter,
)

__all__ = [
    "BaseOTAdapter",
    "MQTTIndustrialAdapter",
    "ModbusTCPAdapter",
    "OTAdapter",
    "SimulatedOTAdapter",
    "create_ot_adapter",
    "execute_hardware_trigger",
    "get_ot_adapter",
    "get_simulated_ot_adapter",
]
