"""Deterministic Health Monitor PDF report (Block 3; ADR 0009/0014).

The document is rendered from the assembler's STRUCTURED OUTPUT only —
no model text anywhere, no recomputation. Sections are built first as
plain data (testable strings: the disclaimer is asserted present, the
frozen forbidden-terms list asserted absent), then laid out with
reportlab. Deviations read "vs your baseline"; the disclaimer block is
permanent.
"""

import io
from datetime import UTC, date, datetime
from typing import Final

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from somatriq_contracts.health_monitor import (
    HEALTH_MONITOR_DISCLAIMER,
    HealthMonitorResponse,
    VitalSummary,
)

# Frozen vocabulary the report must NEVER use (wellness, not diagnosis).
FORBIDDEN_TERMS: Final[tuple[str, ...]] = (
    "diagnos",
    "disease",
    "illness",
    "medical condition",
    "abnormal",
    "patholog",
)

_MARGIN = 54.0
_LINE = 16.0


def _vital_lines(vital: VitalSummary) -> list[str]:
    median = f"{vital.period_median:.4g}" if vital.period_median is not None else "—"
    baseline = f"{vital.baseline_median:.4g}" if vital.baseline_median is not None else "—"
    z = f"{vital.robust_z:+.2f}" if vital.robust_z is not None else "—"
    coverage = f"{vital.coverage * 100:.0f}%"
    return [
        f"{vital.label} ({vital.vital}) — {vital.status}",
        f"  period median {median} · baseline {baseline} · z {z} · "
        f"coverage {coverage} · n {vital.n_days} · source: {vital.source}",
        f"  {vital.note or ''}",
    ]


def report_sections(response: HealthMonitorResponse, generated_on: date) -> list[list[str]]:
    """The document as plain data — the testable, deterministic core."""
    sections: list[list[str]] = [
        ["Somatriq — Health Monitor report"],
        [
            f"Period: last {response.days} days · timezone {response.timezone}",
            f"Generated: {generated_on.isoformat()} (UTC) · "
            f"algorithm {response.vitals[0].algorithm_version if response.vitals else 'n/a'}",
        ],
    ]
    for vital in response.vitals:
        sections.append(_vital_lines(vital))
    if response.caveats:
        sections.append(["Caveats", *(f"  - {caveat}" for caveat in response.caveats)])
    sections.append(
        [
            "Disclaimer",
            f"  {HEALTH_MONITOR_DISCLAIMER}",
            "  Everything exportable · every value traceable to its source.",
        ]
    )
    return sections


def _assert_vocabulary(sections: list[list[str]]) -> None:
    """Honesty gate: the frozen forbidden terms never reach the document.

    The disclaimer itself is exempt — it is the sentence that SAYS this is
    not a diagnosis; the gate guards the data narrative, not the warning.
    """
    lowered = "\n".join("\n".join(section) for section in sections).lower()
    lowered = lowered.replace(HEALTH_MONITOR_DISCLAIMER.lower(), "")
    for term in FORBIDDEN_TERMS:
        if term in lowered:
            raise ValueError(f"forbidden term in health report: {term!r}")


def render_health_report(response: HealthMonitorResponse) -> bytes:
    """Lay the sections out on one A4 page stream (deterministic order)."""
    sections = report_sections(response, generated_on=datetime.now(UTC).date())
    _assert_vocabulary(sections)

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    cursor = height - _MARGIN

    for section in sections:
        for line in section:
            pdf.drawString(_MARGIN, cursor, line)
            cursor -= _LINE
        cursor -= _LINE / 2  # section spacing

    pdf.setTitle("Somatriq Health Monitor report")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
