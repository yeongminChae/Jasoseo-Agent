"""Read-only, hash-tracked local evidence; no PDF OCR or network fetches in pilot."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from .models import CandidateLine, EvidenceRef, Manifest, SourceSpec

class EvidenceError(ValueError):
    pass

class Registry:
    def __init__(self, manifest_path: Path, *, scope: str = "audit"):
        self.manifest_path = manifest_path.resolve(strict=True)
        self.root = self.manifest_path.parent
        if scope not in {"replay_t0", "audit", "research"}:
            raise EvidenceError("scope must be replay_t0, audit, or research")
        self.scope = scope
        self.manifest = Manifest.model_validate_json(self.manifest_path.read_text(encoding="utf-8"))
        if len({s.source_id for s in self.manifest.sources}) != len(self.manifest.sources):
            raise EvidenceError("Duplicate source ID")
        self.lines: dict[str, list[str]] = {}
        self.specs: dict[str, SourceSpec] = {}
        digest = hashlib.sha256()
        for s in self.manifest.sources:
            if s.phase == "posthoc" and scope != "research":
                continue  # Neither replay nor retrospective T0 audit sees later corrections.
            if s.phase == "t0_output" and scope == "replay_t0":
                continue  # ORIGINAL first-review input must exclude the historical model response.
            p = (self.root / s.path).resolve(strict=True)
            if not p.is_relative_to(self.root) or p.suffix.lower() not in {".md", ".txt"}:
                raise EvidenceError(f"Unsafe or unsupported source path: {s.source_id}")
            data = p.read_bytes()
            if len(data) > 600_000:
                raise EvidenceError(f"Source exceeds 600 KB pilot limit: {s.source_id}")
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise EvidenceError(f"UTF-8 required: {s.source_id}") from exc
            self.lines[s.source_id] = content.splitlines()
            self.specs[s.source_id] = s
            digest.update(s.model_dump_json().encode())
            digest.update(hashlib.sha256(data).digest())
        self.snapshot = digest.hexdigest()

    def verify_ref(self, ref: EvidenceRef) -> bool:
        src = self.lines.get(ref.source_id)
        return bool(src and 1 <= ref.line <= len(src) and ref.quote in src[ref.line - 1])

    def search(self, query: str, limit: int = 14) -> list[CandidateLine]:
        """Simple lexical retrieval; misses synonyms by design (not vector RAG)."""
        tokens = set(re.findall(r"[가-힣a-zA-Z0-9]{2,}", query.lower()))
        rows: list[tuple[int, CandidateLine]] = []
        for source_id, lines in self.lines.items():
            for i, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                lowered = line.lower()
                score = sum(1 for t in tokens if t in lowered)
                if score:
                    rows.append((score, CandidateLine(source_id=source_id, line=i, text=line[:300], provenance=self.specs[source_id].provenance)))
        rows.sort(key=lambda r: (-r[0], r[1].source_id, r[1].line))
        return [x[1] for x in rows[:limit]]

    def inventory(self) -> dict:
        return {"scope":self.scope, "source_snapshot":self.snapshot, "sources":[{"source_id":s.source_id,"provenance":s.provenance,"phase":s.phase,"lines":len(self.lines[s.source_id])} for s in self.specs.values()]}

    @classmethod
    def from_manifest(cls, path: str | Path, scope: str = "audit") -> "Registry":
        return cls(Path(path), scope=scope)
