"""Derive the runtime GLiNER files from the pinned source model (setup time only).

The Hugging Face file `onnx/model_quantized.onnx` embeds its 0.3 GB of int8
weights in the graph, so ONNX Runtime copies them into process memory
(0.62 GiB footprint). Saved with the weights as one external data file, the
same graph lets ONNX Runtime memory-map them: file-backed pages that the OS
can reclaim, and the worker footprint for GLiNER falls to about 0.01 GiB.
The conversion changes the container only, not a single weight; it is
byte-for-byte reproducible with onnx 1.23.2, and both outputs are pinned in
models/MANIFEST.json (artifact gliner-x-base-q8).

    uv run --no-project --python 3.12 --with onnx==1.23.2 python tools/derive_gliner.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from localdeid.pf.artifacts import verify  # noqa: E402


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    import onnx

    if onnx.__version__ != "1.23.2":
        print(f"onnx {onnx.__version__}: use onnx==1.23.2 so the output matches the manifest")
        return 1
    manifest = json.loads((ROOT / "models" / "MANIFEST.json").read_text())
    files = manifest["artifacts"]["gliner-x-base-q8"]["files"]
    names = ("model_quantized.onnx", "model_quantized.onnx_data")
    out = ROOT / "models" / "gliner-x-base-q8"
    if all((out / n).is_file() and sha256(out / n) == files[n]["sha256"] for n in names):
        print("ok     gliner-x-base-q8 already derived")
        return 0
    src = verify(ROOT / "models", "gliner-x-base-src")
    out.mkdir(parents=True, exist_ok=True)
    # Write into a fresh folder: onnx appends external tensors to an existing data file.
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        model = onnx.load(str(src / "model_quantized.onnx"))
        onnx.save_model(model, str(Path(tmp) / names[0]), save_as_external_data=True,
                        all_tensors_to_one_file=True, location=names[1], size_threshold=1024)
        for n in names:
            got = sha256(Path(tmp) / n)
            if got != files[n]["sha256"]:
                print(f"BAD    gliner-x-base-q8/{n}: {got}; nothing replaced")
                return 1
        for n in names:
            os.replace(Path(tmp) / n, out / n)
            print(f"ok     gliner-x-base-q8/{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
