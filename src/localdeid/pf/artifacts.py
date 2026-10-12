"""Verify model files against models/MANIFEST.json before any backend loads them.

AGENTS.md: model files are pinned by hash. The fetch-time check does not
protect later loads, so every load re-hashes every file of the artifact
(weights, configuration, tokenizer, calibration) and refuses a mismatch.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


class ArtifactError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def verify(models_root: str | Path, artifact: str) -> Path:
    """Check every file of `artifact`; return its directory. Raises ArtifactError."""
    root = Path(models_root)
    manifest_path = root / "MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        art = manifest["artifacts"][artifact]
    except (OSError, KeyError, ValueError) as exc:
        raise ArtifactError(f"no manifest entry for {artifact}: {exc}") from exc
    folder = root / artifact
    for rel, meta in art["files"].items():
        path = folder / Path(rel).name
        want = meta.get("sha256")
        if not want:
            raise ArtifactError(f"{artifact}/{path.name}: manifest has no hash")
        if not path.is_file():
            raise ArtifactError(f"{artifact}/{path.name}: missing")
        if meta.get("size") is not None and path.stat().st_size != meta["size"]:
            raise ArtifactError(f"{artifact}/{path.name}: size differs from manifest")
        if _sha256(path) != want:
            raise ArtifactError(f"{artifact}/{path.name}: hash differs from manifest")
    return folder
