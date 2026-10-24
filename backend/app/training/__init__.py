"""Reproducible training and evaluation tooling (T21).

Design rules:

- every run records seed, config snapshot, environment, and dataset identity
  (validation-report fingerprint + identified split);
- checkpoints and logs live inside the run directory;
- metrics are written and readable **only** for a completed run on an
  identified dataset split — there is no path that reports metrics from an
  incomplete or unidentified run.
"""

from app.training.runs import RunError, TrainingRun

__all__ = ["RunError", "TrainingRun"]
