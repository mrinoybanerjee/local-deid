"""Span conversion unit tests, plus slow end-to-end checks on the real runtimes."""

import os
from pathlib import Path

import numpy as np
import pytest

from localdeid.pf.model import PFSpan, labels_to_spans
from localdeid.pf.viterbi import LabelSpace

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
LS = LabelSpace.from_mapping(
    {0: "O", 1: "B-p", 2: "I-p", 3: "E-p", 4: "S-p", 5: "B-d", 6: "I-d", 7: "E-d", 8: "S-d"}
)


def spans_for(text, tokens, path):
    starts = np.array([s for s, _ in tokens])
    ends = np.array([e for _, e in tokens])
    probs = np.full((len(path), len(LS)), 0.5)
    return labels_to_spans(text, path, probs, starts, ends, LS)


def test_multi_token_span_trims_spaces():
    text = "Hi Ana Ruiz ok"
    toks = [(0, 2), (2, 6), (6, 11), (11, 14)]  # o200k-style leading spaces
    assert spans_for(text, toks, [0, 1, 3, 0]) == [PFSpan(3, 11, "p", 0.5)]


def test_adjacent_entities_stay_separate():
    text = "AnaMay"
    toks = [(0, 3), (3, 6)]
    got = spans_for(text, toks, [4, 8])
    assert [(s.start, s.end, s.label) for s in got] == [(0, 3, "p"), (3, 6, "d")]


def test_split_character_tokens_merge():
    # Two byte-level tokens that both map to the same emoji character.
    text = "x😀y"
    toks = [(0, 1), (1, 2), (1, 2), (2, 3)]
    got = spans_for(text, toks, [0, 1, 3, 0])
    assert [(s.start, s.end) for s in got] == [(1, 2)]


def test_whitespace_only_span_dropped_and_orphans_handled():
    text = "a   b"
    toks = [(0, 1), (1, 4), (4, 5)]
    assert spans_for(text, toks, [0, 4, 0]) == []
    # orphan E (from an argmax fallback) becomes a one-token span
    got = spans_for(text, toks, [3, 0, 0])
    assert [(s.start, s.end) for s in got] == [(0, 1)]


def test_score_is_minimum_token_probability():
    text = "Ana Ruiz"
    starts, ends = np.array([0, 3]), np.array([3, 8])
    probs = np.full((2, len(LS)), 0.1)
    probs[0, 1], probs[1, 3] = 0.9, 0.6
    (span,) = labels_to_spans(text, [1, 3], probs, starts, ends, LS)
    assert span.score == pytest.approx(0.6)


def _available(module: str, path: Path) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None and path.exists() and not os.environ.get("SKIP_SLOW")


HAS_ONNX = _available("onnxruntime", MODELS / "pf-onnx-q4" / "model_q4.onnx")
HAS_MLX = _available("mlx", MODELS / "pf-mlx-8bit" / "weights.safetensors")
needs_onnx = pytest.mark.skipif(not HAS_ONNX, reason="onnxruntime or ONNX model missing, or SKIP_SLOW")
needs_mlx = pytest.mark.skipif(not HAS_MLX, reason="mlx or MLX model missing, or SKIP_SLOW")
BACKENDS = [pytest.param("onnx_q4", marks=needs_onnx), pytest.param("mlx_8bit", marks=needs_mlx)]


@needs_onnx
def test_onnx_uses_cpu_provider_only():
    from localdeid.pf.backends import ALLOWED_ORT_PROVIDERS, load_backend

    b = load_backend("onnx_q4", MODELS)
    assert tuple(b.session.get_providers()) == ALLOWED_ORT_PROVIDERS


@pytest.mark.parametrize("backend", BACKENDS)
def test_end_to_end_detects_planted_identifiers(backend):
    from localdeid.pf.backends import MODEL_DIRS, load_backend
    from localdeid.pf.model import PrivacyFilter

    pf = PrivacyFilter(load_backend(backend, MODELS), MODELS / MODEL_DIRS[backend])
    text = "Ana Ruiz llamó al 919-555-0142 el 03/14/2026. Email ana.ruiz@example.com."
    found = {text[s.start:s.end] for s in pf.detect(text)}
    assert {"919-555-0142", "03/14/2026", "ana.ruiz@example.com"} <= found
    assert any("Ana Ruiz" in f for f in found)
    assert pf.detect("") == []
    for s in pf.detect("😀 Niño José Núñez, 4;2 años"):  # offsets stay inside the text
        assert 0 <= s.start < s.end <= len("😀 Niño José Núñez, 4;2 años")


@needs_mlx
def test_windowed_equals_single_pass_mlx():
    """Overlap >= receptive field (1024) reproduces single-pass decoding up to
    numeric noise. The stitching itself is exact (test_windows.py). Changing
    the window changes tensor shapes, so bf16 rounding differs and can flip
    the top-4 expert routing on a few tokens: measured 99.79% token agreement
    on this document (2026-10-10). The bound below catches stitching bugs,
    which would shift whole regions."""
    from localdeid.pf.backends import MODEL_DIRS, load_backend
    from localdeid.pf.model import PrivacyFilter

    text = (ROOT / "harness" / "inputs" / "eval_mixed_5000.txt").read_text(encoding="utf-8")
    b = load_backend("mlx_8bit", MODELS)
    full = PrivacyFilter(b, MODELS / MODEL_DIRS["mlx_8bit"], window=1 << 30, overlap=0)
    win = PrivacyFilter(b, MODELS / MODEL_DIRS["mlx_8bit"], window=3072, overlap=1024)
    a, _ = full.token_logits(text)
    w, _ = win.token_logits(text)
    assert a.shape == w.shape and a.shape[0] > 3072
    assert (a.argmax(1) == w.argmax(1)).mean() > 0.995


def test_artifact_verification_refuses_changed_files(tmp_path):
    import json

    from localdeid.pf.artifacts import ArtifactError, verify

    (tmp_path / "art").mkdir()
    (tmp_path / "art" / "config.json").write_text('{"a": 1}')
    good = __import__("hashlib").sha256(b'{"a": 1}').hexdigest()
    manifest = {"artifacts": {"art": {"files": {"config.json": {"size": 8, "sha256": good}}}}}
    (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))
    assert verify(tmp_path, "art") == tmp_path / "art"
    (tmp_path / "art" / "config.json").write_text('{"a": 2}')  # same size, new content
    with pytest.raises(ArtifactError):
        verify(tmp_path, "art")
    (tmp_path / "art" / "config.json").unlink()
    with pytest.raises(ArtifactError):
        verify(tmp_path, "art")
    with pytest.raises(ArtifactError):
        verify(tmp_path, "nope")
    manifest["artifacts"]["art"]["files"]["config.json"].pop("sha256")
    (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))
    (tmp_path / "art" / "config.json").write_text('{"a": 1}')
    with pytest.raises(ArtifactError):
        verify(tmp_path, "art")


@needs_onnx
def test_onnx_telemetry_disabled_before_import():
    """A fresh process must set the opt-out before onnxruntime starts."""
    import subprocess
    import sys

    code = ("import os, sys\nsys.path.insert(0, 'src')\nfrom localdeid.pf.backends import load_backend\n"
            "b = load_backend('onnx_q4', 'models')\nprint(os.environ.get('ORT_DISABLE_TELEMETRY'))\n")
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "1"
    assert "telemetry" not in p.stderr.lower()


@pytest.mark.parametrize("backend", BACKENDS)
def test_backend_specific_default_windows(backend):
    from localdeid.pf.backends import MODEL_DIRS, load_backend
    from localdeid.pf.model import DEFAULT_WINDOWS, PrivacyFilter

    pf = PrivacyFilter(load_backend(backend, MODELS), MODELS / MODEL_DIRS[backend])
    assert (pf.window, pf.overlap) == DEFAULT_WINDOWS[backend]
    assert backend != "onnx_q4" or pf.window <= 2048  # dense attention: small windows only
