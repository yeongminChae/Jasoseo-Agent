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
    review = sub.add_parser("submission-review", help="Independent Submission Review; explicit model consent required")
    review.add_argument("--request", type=Path, required=True, help="Curated T0 JSON; no gold/prior AI/posthoc data")
    review.add_argument("--runner", choices=["stub", "codex", "openai"], default="codex")
    review.add_argument("--allow-external-model", action="store_true")
    review.add_argument("--model", default="gpt-4.1-mini", help="Model for OpenAI API runner only")
    review.add_argument("--codex-model", default=None, help="Optional explicitly selected Codex CLI model; requires availability in your account")
    review.add_argument("--timeout-seconds", type=int, default=60)
    review.add_argument("--output-dir", type=Path, default=Path("runs/submission-review"))
    application = sub.add_parser("application-review", help="Private multi-question T0 review; use --dry-run before model calls")
    application.add_argument("--request", type=Path, required=True, help="Private application JSON; keep inside gitignored private/")
    application.add_argument("--dry-run", action="store_true", help="Validate and display only counts/hashes; no model call")
    application.add_argument("--runner", choices=["codex"], default="codex", help="ChatGPT-authenticated Codex CLI, not paid API")
    application.add_argument("--allow-external-model", action="store_true", help="Explicit permission to send redacted content")
    application.add_argument("--allow-private-input", action="store_true", help="Separate acknowledgement for real applicant drafts")
    application.add_argument("--timeout-seconds", type=int, default=180)
    application.add_argument("--codex-model", default=None)
    application.add_argument("--output-dir", type=Path, help="New run directory, e.g. runs/private/imsec-20261105")
    improve = sub.add_parser("application-improve", help="Auto-triage V1 findings, propose V2, optionally run V2 review")
    improve.add_argument("--request", type=Path, required=True, help="Original V1 application under private/")
    improve.add_argument("--review-dir", type=Path, required=True, help="Completed V1 review under runs/")
    improve.add_argument("--output-dir", type=Path, required=True, help="Fresh improvement output folder under runs/")
    improve.add_argument("--v2-request", type=Path, required=True, help="New V2 application JSON under private/")
    improve.add_argument("--dry-run", action="store_true", help="Check all V1 reviews and hashes without any model calls")
    improve.add_argument("--recheck", action="store_true", help="After proposing V2, run fresh Analyst/Critic on it")
    improve.add_argument("--allow-external-model", action="store_true")
    improve.add_argument("--allow-private-input", action="store_true")
    improve.add_argument("--timeout-seconds", type=int, default=180)
    improve.add_argument("--codex-model", default=None)
    history = sub.add_parser("application-history-finish", help="Historical failure audit -> revision -> blind A/B -> final red-team")
    history.add_argument("--request", type=Path, required=True, help="Current private application JSON")
    history.add_argument("--history", type=Path, required=True, help="Private historical evidence packet JSON")
    history.add_argument("--output-dir", type=Path, required=True, help="New run directory below runs/")
    history.add_argument("--dry-run", action="store_true", help="Offline checks and hashes only")
    history.add_argument("--allow-external-model", action="store_true")
    history.add_argument("--allow-private-input", action="store_true")
    history.add_argument("--timeout-seconds", type=int, default=240)
    history.add_argument("--codex-model", default=None)
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
    if args.command == "application-history-finish":
        from .application_review import ApplicationDraft
        from .application_history_finish import preview as history_preview, run as history_run, _json, _sha
        try:
            cwd = Path.cwd()
            if not args.request.resolve().is_relative_to((cwd / "private").resolve()):
                raise ValueError("--request must be under gitignored private/")
            if not args.history.resolve().is_relative_to((cwd / "private").resolve()):
                raise ValueError("--history must be under gitignored private/")
            if not args.output_dir.resolve().is_relative_to((cwd / "runs").resolve()):
                raise ValueError("--output-dir must be under gitignored runs/")
            draft = ApplicationDraft.model_validate_json(args.request.read_text(encoding="utf-8"))
            packet = _json(args.history)
            request_sha, history_sha = _sha(args.request), _sha(args.history)
            preflight = history_preview(draft, packet, request_sha=request_sha, history_sha=history_sha)
            if args.dry_run:
                print(json.dumps(preflight, ensure_ascii=False, indent=2))
                return 0
            if not args.allow_external_model or not args.allow_private_input:
                raise ValueError("Requires BOTH --allow-external-model and --allow-private-input")
            if args.timeout_seconds <= 0:
                raise ValueError("--timeout-seconds must be positive")
            if args.output_dir.exists():
                raise ValueError("Output folder already exists; choose a fresh path")
            runner = CodexRunner(allow_external=True, timeout_seconds=args.timeout_seconds,
                                 model=args.codex_model)
            report = history_run(draft, packet, args.output_dir, runner,
                                 request_sha=request_sha, history_sha=history_sha)
            print(json.dumps({"status": report["status"],
                              "question_selections": [
                                  {"question_id": q["question_id"], "selected": q["selected"],
                                   "redteam": q["redteam_status"]} for q in report["questions"]
                              ],
                              "candidate_file": str(args.output_dir / "candidate.json"),
                              "report": str(args.output_dir / "summary.md")},
                             ensure_ascii=False, indent=2))
            return 0 if report["status"] == "CANDIDATE_HUMAN_FACT_CHECK_REQUIRED" else 2
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR [{type(exc).__name__}]: {exc}", file=sys.stderr)
            return 2
    if args.command == "application-improve":
        from .application_review import ApplicationDraft
        from .application_improve import preview_improvement, improve_application
        try:
            cwd = Path.cwd()
            if not args.request.resolve().is_relative_to((cwd / "private").resolve()):
                raise ValueError("--request must be under gitignored private/")
            if not args.v2_request.resolve().is_relative_to((cwd / "private").resolve()):
                raise ValueError("--v2-request must be under gitignored private/")
            for path in (args.review_dir, args.output_dir):
                if not path.resolve().is_relative_to((cwd / "runs").resolve()):
                    raise ValueError("--review-dir and --output-dir must be under gitignored runs/")
            original = ApplicationDraft.model_validate_json(args.request.read_text(encoding="utf-8"))
            # Offline preflight: reject wrong snapshot, failed roles or stale evidence BEFORE model calls.
            if args.dry_run:
                result = preview_improvement(original, args.review_dir)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            if not args.allow_external_model or not args.allow_private_input:
                raise ValueError("Improvement requires BOTH --allow-external-model and --allow-private-input")
            if args.timeout_seconds <= 0:
                raise ValueError("timeout must be positive")
            # Do not send any content until all preconditions have been checked.
            preview_improvement(original, args.review_dir)
            if args.output_dir.exists():
                raise ValueError("--output-dir already exists: select a new path to preserve evidence")
            if args.v2_request.exists():
                raise ValueError("--v2-request already exists: select a new path to preserve drafts")
            editor = CodexRunner(allow_external=True, timeout_seconds=args.timeout_seconds,
                                 model=args.codex_model)
            result = improve_application(original, args.review_dir, editor, args.output_dir,
                                         args.v2_request,
                                         recheck_runner=editor if args.recheck else None)
            print(json.dumps({
                "status": result["status"],
                "original_findings": result["original_findings"],
                "classified_findings": result["classified_findings"],
                "unique_issue_groups": result["unique_issue_groups"],
                "v2_request_file": result["v2_request_file"],
                "recheck_status": result["recheck_status"],
                "report": str(args.output_dir / "improvement.md"),
            }, ensure_ascii=False, indent=2))
            return 0 if result["recheck_status"] in {"COMPLETE", "NOT_REQUESTED"} else 2
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR [{type(exc).__name__}]: {exc}", file=sys.stderr)
            return 2
    if args.command == "application-review":
        from .application_review import ApplicationDraft, preview, run_application
        try:
            if not args.request.resolve().is_relative_to((Path.cwd() / "private").resolve()):
                raise ValueError("Application request must be saved under gitignored private/")
            draft = ApplicationDraft.model_validate_json(args.request.read_text(encoding="utf-8"))
            if args.dry_run:
                print(json.dumps(preview(draft), ensure_ascii=False, indent=2))
                return 0
            if not args.allow_external_model or not args.allow_private_input:
                raise ValueError("Real application requires BOTH --allow-external-model and --allow-private-input; start with --dry-run")
            if args.output_dir is None:
                raise ValueError("--output-dir is required; use a fresh directory under gitignored runs/")
            if not args.output_dir.resolve().is_relative_to((Path.cwd() / "runs").resolve()):
                raise ValueError("Output directory must be under gitignored runs/")
            if args.timeout_seconds <= 0:
                raise ValueError("timeout must be positive")
            runner = CodexRunner(allow_external=True, timeout_seconds=args.timeout_seconds, model=args.codex_model)
            result = run_application(draft, runner, args.output_dir)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "COMPLETE" else 2
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR [{type(exc).__name__}]: {exc}", file=sys.stderr)
            return 2
    if args.command == "submission-review":
        from .submission_review import (
            ReviewRequest, SubmissionStubRunner, build_review_graph,
            markdown_review, stub_analyst, stub_critic,
        )
        try:
            request = ReviewRequest.model_validate_json(args.request.read_text(encoding="utf-8"))
            if args.timeout_seconds <= 0:
                raise ValueError("timeout must be positive")
            if args.runner == "stub":
                if any(s.provenance != "synthetic" for s in request.sources):
                    raise ValueError("Stub accepts synthetic sources only")
                graph = build_review_graph(SubmissionStubRunner(), analyst=stub_analyst, critic=stub_critic)
            else:
                from .runner import OpenAIRunner
                runner = (CodexRunner(allow_external=args.allow_external_model, timeout_seconds=args.timeout_seconds, model=args.codex_model)
                          if args.runner == "codex" else OpenAIRunner(allow_external=args.allow_external_model,
                              model=args.model, timeout_seconds=args.timeout_seconds))
                graph = build_review_graph(runner)
            result = graph.invoke({"request": request})["result"]
            args.output_dir.mkdir(parents=True, exist_ok=True)
            # Fixed filenames: user-supplied request_id never becomes a path.
            jsonfile, mdfile = args.output_dir / "review.json", args.output_dir / "review.md"
            jsonfile.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            mdfile.write_text(markdown_review(result), encoding="utf-8")
            print(json.dumps({"status": result.status, "real_llm_executed": result.real_llm_executed,
                              "structural_checks_passed": result.verification.passed,
                              "report": str(jsonfile), "markdown_report": str(mdfile)}, ensure_ascii=False, indent=2))
            return 0 if result.status in {"COMPLETE", "STRUCTURAL_CHECKS_ONLY"} else 2
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
