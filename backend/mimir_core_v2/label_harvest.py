"""Turn in-app corrections into a replayable evaluation set.

Every time someone changes a verdict in the app, `save_manual_status` writes
`user_status` and `manual_status_override` into that session's JSON, right
beside the `local_evidence` the decision was made from. That is a complete
labelled example: the inputs, what Mimir concluded, and what a human said.

Nothing has ever read them back. On the machine this was written on there were
53 sessions and 4,837 incidents, and the model card was still asking for a
locked evaluation set that "does not exist" -- while the raw material for one
accumulated, unharvested, in files the app writes on every scan.

The output is deliberately replayable rather than a summary. Each row carries
the evidence dict, so a rule change can be measured by re-running
`resolve_severity` over the set and comparing against the human column. That
is the difference between "this feels better" and a before/after -- and the
reason three earlier tuning passes had to be made on 19 selection-biased
labels instead.

Two things are counted but never treated as labels:

  agreements   A user who changes nothing has not agreed; they may not have
               looked. Only an explicit correction is evidence. Where the app
               records an explicit confirmation, it arrives here as one.
  notes        Free text is context for a human reading the row, not a label.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

SEVERITIES = ("IGNORE", "REVIEW", "IMPORTANT")


@dataclass
class LabelRow:
    session_id: str
    incident_id: str
    event_group_id: str
    source_category: str
    mimir_severity: str
    human_severity: str
    agreed: bool
    note: str
    local_evidence: dict
    ai_evidence: dict | None

    def as_json(self) -> dict:
        return {
            "session_id": self.session_id,
            "incident_id": self.incident_id,
            "event_group_id": self.event_group_id,
            "source_category": self.source_category,
            "mimir_severity": self.mimir_severity,
            "human_severity": self.human_severity,
            "agreed": self.agreed,
            "note": self.note,
            "local_evidence": self.local_evidence,
            "ai_evidence": self.ai_evidence,
        }


@dataclass
class HarvestReport:
    sessions_seen: int = 0
    sessions_with_labels: int = 0
    incidents_seen: int = 0
    labels: list[LabelRow] = field(default_factory=list)
    skipped_no_evidence: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def corrections(self) -> int:
        return sum(1 for row in self.labels if not row.agreed)

    @property
    def agreements(self) -> int:
        return sum(1 for row in self.labels if row.agreed)


def _clean_severity(value: object) -> str:
    text = str(value or "").strip().upper()
    return text if text in SEVERITIES else ""


def harvest_sessions(roots: Iterable[Path]) -> HarvestReport:
    """Read every session under each root and collect its human verdicts."""

    report = HarvestReport()
    seen_incidents: set[tuple[str, str]] = set()

    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for session_file in sorted(root.rglob("session.json")):
            try:
                session = json.loads(session_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                report.warnings.append(f"Could not read {session_file}: {exc}")
                continue

            incidents = session.get("incidents")
            if not isinstance(incidents, list) or not incidents:
                continue

            report.sessions_seen += 1
            session_id = str(session.get("session_id") or session_file.parent.name)
            found_here = 0

            for incident in incidents:
                if not isinstance(incident, dict):
                    continue
                report.incidents_seen += 1

                # Keyed on user_status alone, not on manual_status_override.
                # The override flag means "differs from Mimir", so requiring it
                # would collect only the disagreements -- and a set with no
                # agreements in it cannot show that a change which removed
                # false positives did not remove the true ones too.
                human = _clean_severity(incident.get("user_status"))
                if not human:
                    continue

                # Rescanning the same footage produces a new session with the
                # same event group. Counting both would weight that footage
                # twice in any measurement taken from this set.
                key = (
                    str(incident.get("event_group_id") or incident.get("id") or ""),
                    human,
                )
                if key in seen_incidents:
                    continue
                seen_incidents.add(key)

                evidence = incident.get("local_evidence")
                if not isinstance(evidence, dict) or not evidence:
                    # Without the evidence the row cannot be replayed, which is
                    # the whole point of collecting it.
                    report.skipped_no_evidence += 1
                    continue

                mimir = _clean_severity(
                    incident.get("final_severity") or incident.get("severity")
                )
                report.labels.append(
                    LabelRow(
                        session_id=session_id,
                        incident_id=str(incident.get("id") or ""),
                        event_group_id=str(incident.get("event_group_id") or ""),
                        source_category=str(incident.get("source_category") or ""),
                        mimir_severity=mimir,
                        human_severity=human,
                        agreed=mimir == human,
                        note=str(incident.get("user_note") or ""),
                        local_evidence=evidence,
                        ai_evidence=incident.get("ai_evidence")
                        if isinstance(incident.get("ai_evidence"), dict)
                        else None,
                    )
                )
                found_here += 1

            if found_here:
                report.sessions_with_labels += 1

    return report


def write_label_set(report: HarvestReport, output: Path) -> Path:
    """Write the set as JSONL, one replayable row per line."""

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in report.labels:
            handle.write(json.dumps(row.as_json(), separators=(",", ":")) + "\n")
    return output


def evaluate(report: HarvestReport, resolver) -> dict:
    """Replay the set through a severity resolver and score it.

    `resolver` takes (local_evidence, ai_evidence) and returns the session
    dict shape, so this works against the real resolve_severity and against a
    stub in tests.

    The headline number is agreement on corrected rows only. Rows where a
    human confirmed what Mimir already said are easy and would flatter any
    ruleset; the corrections are where a change has to prove itself.
    """

    matrix: dict[tuple[str, str], int] = {}
    corrected_total = 0
    corrected_now_right = 0

    for row in report.labels:
        resolved = resolver(row.local_evidence, row.ai_evidence)
        now = _clean_severity(
            resolved.get("final_severity") or resolved.get("severity")
        )
        matrix[(now, row.human_severity)] = matrix.get((now, row.human_severity), 0) + 1
        if not row.agreed:
            corrected_total += 1
            if now == row.human_severity:
                corrected_now_right += 1

    return {
        "rows": len(report.labels),
        "corrected_rows": corrected_total,
        "corrected_now_matching_human": corrected_now_right,
        "corrected_agreement": (
            corrected_now_right / corrected_total if corrected_total else None
        ),
        "confusion": {f"{said}->{human}": count for (said, human), count in sorted(matrix.items())},
    }
