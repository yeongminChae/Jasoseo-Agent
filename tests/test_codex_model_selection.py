"""Codex model-selection tests never invoke an external model."""
import json
from pathlib import Path
from types import SimpleNamespace

from career_ax.models import Assessment
from career_ax.runner import CodexRunner, _codex_strict_output_schema


def test_codex_requested_model_is_passed_to_cli_without_external_call(monkeypatch):
    import shutil
    import subprocess

    monkeypatch.setattr(shutil, "which", lambda program: "/fake/codex")
    seen = []

    def fake_subprocess_run(cmd, *, input, text, cwd, capture_output, timeout, check):
        seen.append(cmd)
        assert input and "analyst" in input
        schema_path = Path(cmd[cmd.index("--output-schema") + 1])
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema["required"] == ["findings", "unresolved"]
        assert schema["additionalProperties"] is False
        assert schema["$defs"]["Finding"]["required"] == list(schema["$defs"]["Finding"]["properties"])
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


def test_strict_output_schema_recursive_required_and_no_defaults():
    schema = _codex_strict_output_schema(Assessment)
    original = Assessment.model_json_schema()

    def check(node):
        if isinstance(node, list):
            for item in node:
                check(item)
        elif isinstance(node, dict):
            assert "default" not in node
            if isinstance(node.get("properties"), dict):
                assert node["additionalProperties"] is False
                assert node["required"] == list(node["properties"])
            for child in node.values():
                check(child)

    check(schema)
    assert schema["$defs"]["Finding"]["required"] == list(schema["$defs"]["Finding"]["properties"])
    assert schema["$defs"]["EvidenceRef"]["required"] == list(schema["$defs"]["EvidenceRef"]["properties"])
    assert original.get("required", []) != schema["required"]
    # The model is still subject to local Pydantic validation, not only schema enforcement.
    assert Assessment.model_validate({"findings": [], "unresolved": []}).findings == []
