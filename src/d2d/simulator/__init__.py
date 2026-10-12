"""Stage 1: household simulator with labeled scenario injectors."""

from d2d.simulator.cdc import generate_cdc
from d2d.simulator.engine import SimulationResult, simulate
from d2d.simulator.writer import write_cdc, write_ground_truth, write_snapshots

__all__ = [
    "SimulationResult",
    "generate_cdc",
    "simulate",
    "write_cdc",
    "write_ground_truth",
    "write_snapshots",
]
