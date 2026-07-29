"""Staged pipeline state machine + run manifest.

The stage order is enforced in code: a stage can only be entered when every
preceding stage has completed. This is what guarantees `report.md` cannot be
written before the deterministic checks and the LLM evaluation are done.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STAGES = [
    "INIT",
    "CASES_LOADED",
    "RULE_CHECKS_COMPLETE",
    "LLM_EVAL_COMPLETE",
    "SCORES_AGGREGATED",
    "REPORT_GENERATED",
    "VALIDATION_COMPLETE",
    "RESULTS_FINALISED",
]

MANIFEST_FILE = "run_manifest.json"


def utc_now() -> str:
    """ISO-8601 timestamp in UTC, second precision, with an explicit offset."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StageError(RuntimeError):
    """Raised when a stage is entered out of order."""


class Pipeline:
    """Tracks stage progression and writes a per-stage manifest to disk.

    The manifest doubles as the reliability feature (per-stage status tracking)
    and as the evidence `validate.py` uses to confirm the report was generated
    after the stages it depends on.
    """

    def __init__(self, outdir: Path, run_mode: str) -> None:
        self.outdir = Path(outdir)
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.run_mode = run_mode
        self.started_at = utc_now()
        self.completed: list[str] = []
        self.records: list[dict] = []
        self.enter("INIT")

    # -- stage control -----------------------------------------------------
    def enter(self, stage: str) -> None:
        if stage not in STAGES:
            raise StageError(f"unknown stage: {stage}")
        idx = STAGES.index(stage)
        expected = STAGES[:idx]
        missing = [s for s in expected if s not in self.completed]
        if missing:
            raise StageError(
                f"cannot enter {stage}: prior stages incomplete -> {missing}"
            )
        if stage in self.completed:
            raise StageError(f"stage already completed: {stage}")
        self._current = stage
        self._current_started = utc_now()

    def complete(self, stage: str, **details) -> None:
        if getattr(self, "_current", None) != stage:
            raise StageError(
                f"cannot complete {stage}: current stage is {getattr(self, '_current', None)}"
            )
        self.completed.append(stage)
        self.records.append(
            {
                "stage": stage,
                "status": "complete",
                "started_at": self._current_started,
                "completed_at": utc_now(),
                "details": details,
            }
        )
        self.write_manifest()

    def run(self, stage: str, **details) -> None:
        """Convenience for stages with no interleaved work."""
        self.enter(stage)
        self.complete(stage, **details)

    def has_completed(self, stage: str) -> bool:
        return stage in self.completed

    # -- persistence -------------------------------------------------------
    @property
    def manifest_path(self) -> Path:
        return self.outdir / MANIFEST_FILE

    def write_manifest(self) -> None:
        payload = {
            "pipeline_version": 1,
            "run_mode": self.run_mode,
            "started_at": self.started_at,
            "updated_at": utc_now(),
            "stage_order": STAGES,
            "completed_stages": list(self.completed),
            "stages": self.records,
        }
        self.manifest_path.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
