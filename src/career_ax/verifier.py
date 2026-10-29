"""Machine-checkable assertions; never equate citation existence with truth."""
from __future__ import annotations
from .models import Collection, Assessment, Verification
from .evidence import Registry

def verify(reg: Registry, collection: Collection, analyst: Assessment, critic: Assessment) -> Verification:
    errors: list[str] = []
    collected = {(e.source_id, e.line, e.quote) for e in collection.evidence}
    source_levels = set()
    for e in collection.evidence:
        if not reg.verify_ref(e):
            errors.append(f"Collector invalid quote {e.source_id}:{e.line}")
        else:
            source_levels.add(reg.specs[e.source_id].provenance)
    ids: set[str] = set()
    valid_ids: list[str] = []
    for role, assessment in (("analyst", analyst), ("critic", critic)):
        for f in assessment.findings:
            valid = True
            if f.finding_id in ids:
                errors.append(f"Duplicate finding ID: {f.finding_id}")
                valid = False
            ids.add(f.finding_id)
            if not f.evidence:
                errors.append(f"{role}:{f.finding_id} missing citation")
                valid = False
            for ref in f.evidence:
                if not reg.verify_ref(ref) or (ref.source_id, ref.line, ref.quote) not in collected:
                    errors.append(f"{role}:{f.finding_id} invalid/non-collected citation")
                    valid = False
            if valid:
                valid_ids.append(f.finding_id)
    if not collection.evidence:
        errors.append("No evidence retrieved")
    if source_levels & {"primary", "synthetic"}:
        level = "PRIMARY_OR_SYNTHETIC"
    elif source_levels:
        level = "SECONDARY_ONLY"
    else:
        level = "NO_EVIDENCE"
    return Verification(passed=not errors, errors=errors, valid_finding_ids=valid_ids, source_level=level)


def followup_is_linked(question: str, prior_statements: list[str]) -> bool:
    """Cheap structural guard against entirely unrelated follow-up questions."""
    import re
    tokens = set(re.findall(r"[가-힣a-zA-Z0-9]{2,}", question.lower()))
    statements = " ".join(prior_statements).lower()
    return any(t in statements for t in tokens if len(t) >= 2 and t not in {"어떻게", "무엇인가", "있는가", "위해", "조사", "검증"})
