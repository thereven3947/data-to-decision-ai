"""Stage 1: household simulator with labeled scenario injectors."""

from d2d.simulator.engine import SimulationResult, simulate
from d2d.simulator.writer import write_ground_truth, write_snapshots

__all__ = ["SimulationResult", "simulate", "write_ground_truth", "write_snapshots"]
