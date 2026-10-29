"""Local CLI with explicit model-data egress consent."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from .evidence import EvidenceError, Registry
from .runner import MockRunner, CodexRunner, AgentRuntimeError
from .store import Store
from .workflow import Workflow
from .reporting import markdown_report
from .eval_dev import execute_suite

DEMO = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic_question_swap" / "manifest.json"

def parser():
    p=argparse.ArgumentParser(prog="career-ax", description="Evidence-gated autonomous research AX Pilot")
    sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("doctor",help="Check local environment; no external calls")
    review = sub.add_parser("submission-review", help="Submission Review skeleton; synthetic Stub only")
    review.add_argument("--request", type=Path, required=True, help="Curated T0 JSON; no gold/prior AI/posthoc data")
    review.add_argument("--runner", choices=["stub", "unimplemented"], default="unimplemented")
    review.add_argument("--output-dir", type=Path, default=Path("runs/submission-review"))
    ev=sub.add_parser("eval-dev",help="Run synthetic-only Mock smoke suite (NOT real AI quality Eval)")
    ev.add_argument("--output",type=Path,default=Path("runs/mock_smoke.json"))
    inv=sub.add_parser("inventory",help="Inspect verified manifest inputs")
    inv.add_argument("--manifest",type=Path,default=DEMO)
    inv.add_argument("--scope",choices=["replay_t0","audit","research"],default="audit")
    for name in ("run","resume"):
        q=sub.add_parser(name,help="Start or resume research; Mock is local only")
        q.add_argument("--manifest",type=Path,default=DEMO)
        q.add_argument("--scope",choices=["replay_t0","audit","research"],default="audit")
        q.add_argument("--runner",choices=["mock","codex"],default="mock")
        q.add_argument("--allow-external-model",action="store_true",help="Explicitly approve transmitting snippets to Codex")
        q.add_argument("--db",type=Path,default=Path("data/research.sqlite"),help="SQLite DB path (default storage)")
        q.add_argument("--storage", choices=["sqlite", "postgres"], default="sqlite",help="Persistence backend; PostgreSQL DSN via CAREER_AX_DATABASE_URL")
        q.add_argument("--output-dir",type=Path,default=Path("runs"))
        q.add_argument("--max-cycles",type=int,default=3)
        q.add_argument("--max-calls",type=int,default=20)
        q.add_argument("--max-retries",type=int,default=1)
        if name=="run":q.add_argument("--goal",required=True)
        else:q.add_argument("--run-id",required=True)
    return p

def main(argv=None):
    args=parser().parse_args(argv)
    if args.command == "submission-review":
        from .submission_review import (
            ReviewRequest, SubmissionStubRunner, build_review_graph,
            markdown_review, stub_analyst, stub_critic,
        )
        try:
            request = ReviewRequest.model_validate_json(args.request.read_text(encoding="utf-8"))
            if args.runner != "stub":
                raise NotImplementedError("Analyst/Critic execution TODO; use --runner stub for synthetic wiring checks")
            result = build_review_graph(
                SubmissionStubRunner(), analyst=stub_analyst, critic=stub_critic,
            ).invoke({"request": request})["result"]
            args.output_dir.mkdir(parents=True, exist_ok=True)
            # Fixed filenames: user-supplied request_id never becomes a path.
            jsonfile, mdfile = args.output_dir / "review.json", args.output_dir / "review.md"
            jsonfile.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            mdfile.write_text(markdown_review(result), encoding="utf-8")
            print(json.dumps({"status": result.status, "real_llm_executed": result.real_llm_executed,
                              "structural_checks_passed": result.verification.passed,
                              "report": str(jsonfile), "markdown_report": str(mdfile)}, ensure_ascii=False, indent=2))
            return 0 if result.verification.passed else 2
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR [{type(exc).__name__}]: {exc}", file=sys.stderr)
            return 2
    if args.command=="doctor":
        print(json.dumps({"python":sys.version.split()[0],"codex_cli_found":bool(shutil.which("codex")),
                          "default_mode":"mock","model_cost_metered":False,
                          "postgres_driver_available":__import__("importlib.util",fromlist=["find_spec"]).find_spec("psycopg") is not None,
                          "postgres_dsn_configured":bool(os.environ.get("CAREER_AX_DATABASE_URL"))},indent=2))
        return 0
    if args.command=="eval-dev":
        suite=execute_suite(DEMO.parent.parent)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(suite,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(suite,ensure_ascii=False,indent=2))
        return 0 if suite["passed"]==suite["total"] else 1
    try:
        reg=Registry.from_manifest(args.manifest,scope=args.scope)
        if args.command=="inventory":
            print(json.dumps(reg.inventory(),ensure_ascii=False,indent=2))
            return 0
        runner=MockRunner() if args.runner=="mock" else CodexRunner(allow_external=args.allow_external_model)
        if args.storage == "postgres":
            from .store_postgres import PostgresStore
            store=PostgresStore(os.environ.get("CAREER_AX_DATABASE_URL", ""))
        else:
            store=Store(args.db)
        workflow=Workflow(reg,store,runner,max_cycles=args.max_cycles,max_calls=args.max_calls,max_retries=args.max_retries)
        report=workflow.execute(getattr(args,"goal",None),run_id=getattr(args,"run_id",None))
        outfile=args.output_dir / f"{report.run_id}.json"
        store.export(report.run_id,outfile)
        mdfile=args.output_dir / f"{report.run_id}.md"
        mdfile.write_text(markdown_report(report),encoding="utf-8")
        print(json.dumps({"run_id":report.run_id,"status":report.status.value,"mode":report.runner,
                          "real_llm_executed":report.is_real_llm,"cycles":len(report.cycles),
                          "questions":[c.question for c in report.cycles],
                          "calls_used":report.calls_used,"cost_usd":None,
                          "report":str(outfile),"markdown_report":str(mdfile)},ensure_ascii=False,indent=2))
        return 0 if report.status.value in {"COMPLETE","PARTIAL_COMPLETE"} else 2
    except (OSError,ValueError,KeyError,RuntimeError,EvidenceError,AgentRuntimeError) as exc:
        print(f"ERROR [{type(exc).__name__}]: {exc}",file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
