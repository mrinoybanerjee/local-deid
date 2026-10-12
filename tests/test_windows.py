import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from localdeid.pf.windows import plan_windows, windowed_logits


@given(n=st.integers(0, 20000), window=st.integers(1, 5000), overlap=st.integers(0, 2400))
def test_plan_tiles_exactly_with_enough_context(n, window, overlap):
    if 2 * overlap >= window:
        with pytest.raises(ValueError):
            plan_windows(n, window, overlap)
        return
    plan = plan_windows(n, window, overlap)
    if n == 0:
        assert plan == []
        return
    pos = 0
    for w in plan:
        assert 0 <= w.start <= w.keep_start < w.keep_end <= w.end <= n
        assert w.end - w.start <= window
        assert w.keep_start == pos  # cores are contiguous, in order, no gaps
        pos = w.keep_end
        # every kept token has `overlap` context on each side, or touches a doc edge
        assert w.keep_start - w.start >= overlap or w.start == 0
        assert w.end - w.keep_end >= overlap or w.end == n
    assert pos == n
    if n > window:
        assert all(w.end - w.start == window for w in plan)


def test_invalid_args():
    for args in [(10, 0, 0), (10, 8, 4), (10, 8, -1)]:
        with pytest.raises(ValueError):
            plan_windows(*args)


def test_windowed_logits_equals_local_model():
    # A model whose output at i depends only on tokens within +-R must be
    # reproduced exactly when overlap >= R.
    rng = np.random.default_rng(1)
    ids = rng.integers(0, 1000, size=5000)
    radius = 7

    def local_model(x):
        padded = np.pad(x.astype(np.float64), radius)
        out = np.stack([padded[i : i + 2 * radius + 1] @ np.arange(1, 2 * radius + 2)
                        for i in range(len(x))])
        return np.stack([out, -out], axis=1)

    full = local_model(ids)
    for window, overlap in [(64, 7), (100, 30), (5000, 0), (9999, 7)]:
        got = windowed_logits(ids, local_model, window, overlap)
        assert np.array_equal(got, full), (window, overlap)
    short = windowed_logits(ids, local_model, 64, 3)  # too little overlap: differs
    assert not np.array_equal(short, full)


def test_windowed_logits_rejects_bad_forward():
    with pytest.raises(ValueError):
        windowed_logits(np.arange(10), lambda x: np.zeros((len(x) - 1, 2)), 4, 1)
    assert windowed_logits(np.arange(0), lambda x: x, 4, 1).shape[0] == 0
