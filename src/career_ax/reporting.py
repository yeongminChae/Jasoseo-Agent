"""Human-readable view of the machine trace. Never assert semantic proof."""
from __future__ import annotations
from .models import FinalReport

def markdown_report(report: FinalReport) -> str:
    lines=["# Career Intelligence AX Pilot — Run Report", "",
           f"- Run ID: `{report.run_id}`", f"- Status: `{report.status.value}`",
           f"- Runner: `{report.runner}`", f"- Actual LLM calls: {'YES' if report.is_real_llm else 'NO (deterministic MockRunner)'}",
           f"- Role calls/attempts: {report.calls_used}",
           "- USD usage: **unknown / not metered**", "- **Evidence verification checks exact citations only. It is not proof that AI conclusions are true.**", "",
           "## Research goal", "", report.goal, ""]
    for c in report.cycles:
        lines.extend([f"## Cycle {c.index}", "",f"**Question:** {c.question}", "",
                      f"**Based on earlier finding IDs:** {', '.join(c.based_on_finding_ids) or 'initial question'}", "",
                      f"**Citation gate:** {'PASS' if c.verification.passed else 'FAIL'} · `{c.verification.source_level}`", "",
                      "**Retrieved evidence:**", ""])
        for ref in c.collection.evidence:
            lines.append(f"- `{ref.source_id}:{ref.line}` — {ref.quote.replace(chr(10),' ')[:130]}")
        if not c.collection.evidence:
            lines.append("- No evidence")
        for name,assess in (("Analyst",c.analyst),("Critic",c.critic)):
            lines.extend(["",f"**{name}:**",""])
            if not assess.findings:
                lines.append("- No findings")
            for f in assess.findings:
                accepted = "CITATION_VERIFIED" if f.finding_id in c.accepted_finding_ids else "NOT_PROMOTED"
                lines.append(f"- `{f.finding_id}` [{f.kind}; {accepted}] {f.statement} — {f.limits}")
            for u in assess.unresolved:
                lines.append(f"- Unresolved: {u}")
        lines.extend(["",f"**Research Lead:** `{c.decision.action.value}` — {c.decision.reason}"])
        if c.next_question:
            lines.append(f"- Proposed follow-up: {c.next_question}")
        for err in c.verification.errors:
            lines.append(f"- Gate error: {err}")
        lines.append("")
    lines.extend(["## Remaining limitations",""])
    lines += [f"- {x}" for x in report.notes] or ["- No additional notes; inspect sources and rerun with adjudicated labels for substantive claims."]
    return "\n".join(lines).rstrip()+"\n"
