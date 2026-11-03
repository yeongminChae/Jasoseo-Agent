"""Codex model-selection tests never invoke an external model."""
import json
from pathlib import Path
from types import SimpleNamespace

from career_ax.models import Assessment
from career_ax.runner import CodexRunner


def test_codex_requested_model_is_passed_to_cli_without_external_call(monkeypatch):
    import shutil
    import subprocess

    monkeypatch.setattr(shutil, "which", lambda program: "/fake/codex")
    seen = []

    def fake_subprocess_run(cmd, *, input, text, cwd, capture_output, timeout, check):
        seen.append(cmd)
        assert input and "analyst" in input
        output = Path(cmd[cmd.index("--output-last-message") + 1])
        output.write_text(json.dumps({"findings": [], "unresolved": []}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    runner = CodexRunner(allow_external=True, model="synthetic-test-model")
    result = runner.run("analyst", {"request_id": "synthetic", "sources": [
        {"source_id": "S", "purpose": "submission", "phase": "t0_input",
         "provenance": "synthetic", "text": "가상"}
    ]}, Assessment)
    assert isinstance(result, Assessment)
    assert seen and seen[0][seen[0].index("--model") + 1] == "synthetic-test-model"
    assert seen[0][seen[0].index("--sandbox") + 1] == "read-only"
