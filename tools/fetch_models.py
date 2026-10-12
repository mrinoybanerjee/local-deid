"""Fetch the pinned model files and verify every file against models/MANIFEST.json.

This is the only part of the project that uses the network. It runs at setup
time, never at app runtime. Each file comes from a fixed repository revision,
and its SHA-256 must match the manifest; a mismatch deletes the download and
fails. Run `--record` once to write hashes for new entries (trust on first use
from the pinned revision; LFS files are also checked against Hugging Face's
published LFS SHA-256).

    .venv/bin/python tools/fetch_models.py            # fetch + verify all
    .venv/bin/python tools/fetch_models.py --verify   # verify only, no network
    .venv/bin/python tools/fetch_models.py --only pf-mlx-8bit o200k-bpe   # the shipping models

Files marked `derived_by` are built locally from another pinned artifact
(tools/derive_gliner.py) and checked against the same manifest hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
MANIFEST = MODELS / "MANIFEST.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url) as resp, tmp.open("wb") as out:  # noqa: S310 (fixed https URLs)
        while block := resp.read(1 << 20):
            out.write(block)
    tmp.replace(dest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="verify local files only")
    ap.add_argument("--record", action="store_true", help="record missing hashes")
    ap.add_argument("--only", nargs="+", metavar="ARTIFACT", help="fetch or verify only these artifacts")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text())
    unknown = set(args.only or ()) - set(manifest["artifacts"])
    if unknown:
        print(f"unknown artifact: {', '.join(sorted(unknown))}")
        return 2
    ok = True
    changed = False
    for name, art in manifest["artifacts"].items():
        if args.only and name not in args.only:
            continue
        base = art.get("base_url") or f"https://huggingface.co/{art['repo']}/resolve/{art['revision']}"
        for rel, meta in art["files"].items():
            dest = MODELS / name / Path(rel).name
            if not dest.exists() and meta.get("derived_by"):
                print(f"DERIVE {name}/{dest.name}: run {meta['derived_by']} (see its docstring)")
                ok = False
                continue
            if not dest.exists():
                if args.verify:
                    print(f"MISSING {name}/{dest.name}")
                    ok = False
                    continue
                print(f"fetch  {name}/{rel}")
                download(f"{base}/{rel}", dest)
            digest = sha256_file(dest)
            want = meta.get("sha256")
            if want is None:
                if args.record:
                    meta["sha256"] = digest
                    changed = True
                    print(f"record {name}/{dest.name} {digest}")
                else:
                    print(f"NOHASH {name}/{dest.name} (run --record)")
                    ok = False
            elif digest != want:
                print(f"BAD    {name}/{dest.name}: {digest} != {want}")
                if not args.verify:
                    dest.unlink()
                ok = False
            elif meta.get("size") is not None and dest.stat().st_size != meta["size"]:
                print(f"BADSIZE {name}/{dest.name}")
                ok = False
            else:
                print(f"ok     {name}/{dest.name}")
    if changed:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
