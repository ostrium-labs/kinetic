"""QA results: one QaCheck per gate, collected into a QaReport (qa/report.json)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ostrium_kinetic import canonical

QaStatus = Literal["pass", "warn", "fail"]

_MARK: dict[str, str] = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}


class QaCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str  # e.g. "frames.count", "annotations.parity", "delivery.color"
    shot_id: str | None = None  # None for film-level checks
    status: QaStatus
    detail: str
    data: dict[str, Any] = Field(default_factory=dict)  # JSON values only


def check(id: str, shot_id: str | None, status: QaStatus, detail: str, **data: Any) -> QaCheck:
    return QaCheck(id=id, shot_id=shot_id, status=status, detail=detail, data=data)


class QaReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    film_id: str
    profile: str
    checks: list[QaCheck] = Field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """1 if any check failed, else 0 (warnings do not block)."""
        return 1 if any(c.status == "fail" for c in self.checks) else 0

    def counts(self) -> dict[QaStatus, int]:
        tally = Counter(c.status for c in self.checks)
        return {"pass": tally["pass"], "warn": tally["warn"], "fail": tally["fail"]}

    def summary(self) -> str:
        """One line, e.g. 'QA FAIL: 12 pass, 1 warn, 2 fail'."""
        n = self.counts()
        verdict = "FAIL" if n["fail"] else ("PASS with warnings" if n["warn"] else "PASS")
        return f"QA {verdict}: {n['pass']} pass, {n['warn']} warn, {n['fail']} fail"

    def render_text(self) -> str:
        """Human-readable report: failures first, then warnings, then passes."""
        order = {"fail": 0, "warn": 1, "pass": 2}
        lines = [f"{self.film_id} [{self.profile}]"]
        for c in sorted(self.checks, key=lambda c: order[c.status]):
            where = f"{c.shot_id} " if c.shot_id else ""
            lines.append(f"  {_MARK[c.status]}  {where}{c.id}: {c.detail}")
        lines.append(self.summary())
        return "\n".join(lines) + "\n"

    def write(self, path: Path) -> Path:
        """Canonical JSON with an LF newline."""
        canonical.write_text_lf(path, canonical.dumps(self.model_dump(mode="json")) + "\n")
        return path

    @classmethod
    def load(cls, path: Path) -> QaReport:
        return cls.model_validate_json(path.read_text("utf-8"))
