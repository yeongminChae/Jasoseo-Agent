"""Human-adjudicated, offline comparison of an actual Submission Review run.

Never calls a model. The Gold set and human judgments are read after inference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


class EvaluationError(ValueError):
    pass


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvaluationError(f"Expected JSON object: {path}")
    return value


def _digest(request: dict) -> str:
    sources = request.get("sources")
    if not isinstance(sources, list):
        raise EvaluationError("Request sources must be a list")
    return hashlib.sha256(json.dumps(sources, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _findings(review: dict) -> dict[tuple[str, str], dict]:
    items: dict[tuple[str, str], dict] = {}
    for role in ("analyst", "critic"):
        for finding in review.get(role, {}).get("findings", []):
            key = (role, finding["finding_id"])
            if key in items:
                raise EvaluationError(f"Duplicate finding in {role}: {key[1]}")
            items[key] = finding
    return items


def _check_live_review(review: dict) -> None:
    if review.get("execution_mode") not in ("codex", "openai") or review.get("real_llm_executed") is not True:
        raise EvaluationError("Only real model runs can receive a quality score; Stub/Fake rejected")
    if set(review.get("successful_roles", [])) != {"analyst", "critic"} or review.get("failures"):
        raise EvaluationError("Both live roles must succeed before comparing their detection quality")
    if not review.get("verification", {}).get("passed", False):
        raise EvaluationError("Structural citation validation failed; quality scoring blocked")


def prepare(review: dict) -> dict:
    _check_live_review(review)
    return {
        "request_id": review["request_id"],
        "input_sha256": review["input_sha256"],
        "notes": "Human review required. Verdicts: match, additional_valid (defect), false_positive (unsupported defect claim), valid_non_defect (accurate coverage or caveat), out_of_scope (inapplicable context). Gold IDs only for match.",
        "judgments": [
            {"role": role, "finding_id": finding_id, "verdict": None, "gold_defect_id": None, "reason": ""}
            for role, finding_id in _findings(review)
        ],
    }


def evaluate(review: dict, request: dict, gold: dict, adjudication: dict) -> dict:
    _check_live_review(review)
    run_id = review.get("request_id")
    digest = _digest(request)
    if not (request.get("request_id") == run_id == gold.get("request_id") == adjudication.get("request_id")):
        raise EvaluationError("Request/Gold/review/adjudication IDs differ")
    if review.get("input_sha256") != digest or adjudication.get("input_sha256") != digest:
        raise EvaluationError("T0 input hash mismatch: wrong run or stale judgments")
    if gold.get("evaluation_only") is not True:
        raise EvaluationError("Gold must be evaluation_only")
    gold_ids = [d["defect_id"] for d in gold.get("expected_defects", [])]
    if len(gold_ids) != len(set(gold_ids)):
        raise EvaluationError("Duplicate Gold defect IDs")
    gold_set = set(gold_ids)
    findings = _findings(review)
    judged: dict[tuple[str, str], dict] = {}
    for item in adjudication.get("judgments", []):
        key = (item["role"], item["finding_id"])
        if key not in findings or key in judged:
            raise EvaluationError(f"Unknown or duplicate adjudication: {key}")
        verdict = item.get("verdict")
        defect = item.get("gold_defect_id")
        if verdict not in {"match", "additional_valid", "false_positive", "valid_non_defect", "out_of_scope"}:
            raise EvaluationError(f"Unjudged/invalid verdict: {key}")
        if verdict == "match" and defect not in gold_set:
            raise EvaluationError(f"Missing/unknown matched Gold ID: {key}")
        if verdict != "match" and defect is not None:
            raise EvaluationError(f"Non-match judgment must not reference Gold: {key}")
        if not str(item.get("reason", "")).strip():
            raise EvaluationError(f"Human evidence/rationale required: {key}")
        judged[key] = item
    if set(judged) != set(findings):
        raise EvaluationError(f"Missing judgments for {sorted(set(findings) - set(judged))}")

    def metrics(roles: set[str]) -> dict:
        chosen = [v for (role, _), v in judged.items() if role in roles]
        matched = sorted({x["gold_defect_id"] for x in chosen if x["verdict"] == "match"})
        valid_defect_findings = sum(x["verdict"] in {"match", "additional_valid"} for x in chosen)
        false_positives = sum(x["verdict"] == "false_positive" for x in chosen)
        defect_finding_count = valid_defect_findings + false_positives
        valid_non_defects = sum(x["verdict"] == "valid_non_defect" for x in chosen)
        out_of_scope = sum(x["verdict"] == "out_of_scope" for x in chosen)
        return {
            "gold_found": matched,
            "gold_missed": sorted(gold_set - set(matched)),
            "tp_gold": len(matched),
            "fn_gold": len(gold_set - set(matched)),
            "false_positive_findings": false_positives,
            "additional_valid_findings": sum(x["verdict"] == "additional_valid" for x in chosen),
            "valid_non_defect_findings": valid_non_defects,
            "out_of_scope_findings": out_of_scope,
            "gold_recall": round(len(matched) / len(gold_set), 4) if gold_set else None,
            "finding_precision": round(valid_defect_findings / defect_finding_count, 4) if defect_finding_count else None,
            "defect_finding_count": defect_finding_count,
            "finding_count": len(chosen),
        }

    return {
        "evaluation_type": "human_adjudicated_synthetic_case",
        "real_model_execution": True,
        "request_id": run_id,
        "input_sha256": digest,
        "model_mode": review["execution_mode"],
        "review_status": review["status"],
        "gold_defect_count": len(gold_set),
        "analyst": metrics({"analyst"}),
        "critic": metrics({"critic"}),
        "combined": metrics({"analyst", "critic"}),
        "limits": [
            "A small synthetic sample is not a general accuracy estimate or hiring outcome evidence.",
            "Gold completeness and correctness depend on human review.",
            "Defect-finding precision includes only matched/additional-valid defect findings and false-positive defect claims, excluding valid non-defect observations and out-of-scope statements.",
            "A null precision means no defect claim was adjudicated, not a perfect score.",
            "Analyst-only is a role-specific baseline, not a separately designed single-agent system.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline, human-adjudicated submission review evaluation")
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare", help="Create blank judgments from a completed live run")
    prep.add_argument("--review", type=Path, required=True)
    prep.add_argument("--out", type=Path, required=True)
    score = sub.add_parser("score", help="Score once every finding is human-adjudicated")
    for flag in ("review", "request", "gold", "adjudication", "out"):
        score.add_argument("--" + flag, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = (prepare(_load(args.review)) if args.action == "prepare" else
                  evaluate(_load(args.review), _load(args.request), _load(args.gold), _load(args.adjudication)))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"result": str(args.out), "action": args.action}, ensure_ascii=False))
        return 0
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.exit(2, f"EVAL_BLOCKED: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
