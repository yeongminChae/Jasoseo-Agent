from pathlib import Path
import json
import pytest
from career_ax.evidence import Registry, EvidenceError
from career_ax.models import EvidenceRef

DEMO = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic_question_swap" / "manifest.json"

def test_t0_never_sees_posthoc_source():
    reg=Registry(DEMO,scope="audit")
    assert "SYN_CORRECTION_POSTHOC" not in reg.lines
    assert all(x.source_id != "SYN_CORRECTION_POSTHOC" for x in reg.search("사후 정정"))
    assert "SYN_CORRECTION_POSTHOC" in Registry(DEMO,scope="research").lines
    assert "SYN_REVIEW_T0" not in Registry(DEMO,scope="replay_t0").lines
    assert "SYN_FORM_T0" in Registry(DEMO,scope="replay_t0").lines

def test_reference_exact_quote_and_line():
    reg=Registry(DEMO)
    assert reg.verify_ref(EvidenceRef(source_id="SYN_FORM_T0",line=2,quote="2번 문항 지원동기"))
    assert not reg.verify_ref(EvidenceRef(source_id="SYN_FORM_T0",line=3,quote="2번 문항 지원동기"))

def test_reject_path_escape(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret",encoding="utf-8")
    folder=tmp_path/"inputs"
    folder.mkdir()
    (folder/"manifest.json").write_text(json.dumps({"sources":[{"source_id":"x","path":"../outside.txt","provenance":"primary"}]}),encoding="utf-8")
    with pytest.raises(EvidenceError):Registry(folder/"manifest.json")

def test_snapshot_changes_with_bytes(tmp_path):
    src=tmp_path/"test.txt"
    src.write_text("초기 내용",encoding="utf-8")
    mf=tmp_path/"manifest.json"
    mf.write_text(json.dumps({"sources":[{"source_id":"one","path":"test.txt","provenance":"primary"}]}),encoding="utf-8")
    old=Registry(mf).snapshot
    src.write_text("수정된 내용",encoding="utf-8")
    assert old != Registry(mf).snapshot
