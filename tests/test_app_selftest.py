"""Self-test: it checks the files of the backend that actually runs."""

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("backend, want", [
    ("mlx_8bit", {"o200k-bpe", "pf-mlx-8bit"}),
    ("onnx_q4", {"o200k-bpe", "pf-onnx-q4"}),
    ("mlx_8bit_ft1", {"o200k-bpe", "pf-mlx-8bit-ft1", "pf-mlx-8bit"}),
])
def test_hash_check_follows_the_active_backend(monkeypatch, tmp_path, backend, want):
    from localdeid.app import selftest
    from localdeid.pf import artifacts

    seen = []
    monkeypatch.setattr(artifacts, "verify", lambda root, art: seen.append(art) or tmp_path)
    engine = SimpleNamespace(pf=SimpleNamespace(backend=SimpleNamespace(name=backend), detect=lambda t: []),
                             gl=None)
    out = selftest.run(tmp_path, engine)
    check = next(c for c in out["checks"] if c["name"] == "model files")
    assert check["ok"] and set(seen) == want


def test_a_changed_file_fails_the_check(monkeypatch, tmp_path):
    from localdeid.app import selftest
    from localdeid.pf import artifacts

    def bad(root, art):
        raise artifacts.ArtifactError(f"{art}: hash differs")

    monkeypatch.setattr(artifacts, "verify", bad)
    out = selftest.run(tmp_path, SimpleNamespace(pf=None, gl=None))
    assert out["ok"] is False and not out["checks"][0]["ok"]
