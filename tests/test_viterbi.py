import itertools
import json

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from localdeid.pf.viterbi import BIAS_KEYS, LabelSpace, ViterbiDecoder, load_biases, log_softmax

SMALL = LabelSpace.from_mapping(
    {0: "O", 1: "B-a", 2: "I-a", 3: "E-a", 4: "S-a", 5: "B-b", 6: "I-b", 7: "E-b", 8: "S-b"}
)


def valid_path(labels: LabelSpace, path) -> bool:
    prev = None
    for idx in path:
        tag, ent = labels.tag(idx), labels.entity(idx)
        if prev is None or labels.tag(prev) in (None, "E", "S"):
            if tag not in (None, "B", "S"):
                return False
        else:  # prev is B or I
            if tag not in ("I", "E") or ent != labels.entity(prev):
                return False
        prev = idx
    return labels.tag(path[-1]) in (None, "E", "S")


def path_score(dec: ViterbiDecoder, lp, path) -> float:
    s = dec.start[path[0]] + lp[0, path[0]]
    for t in range(1, len(path)):
        s += dec.trans[path[t - 1], path[t]] + lp[t, path[t]]
    return s + dec.end[path[-1]]


def brute_force(dec: ViterbiDecoder, lp):
    n = len(dec.labels)
    best, best_path = -np.inf, None
    for path in itertools.product(range(n), repeat=lp.shape[0]):
        if not valid_path(dec.labels, path):
            continue
        s = path_score(dec, lp, path)
        if s > best:
            best, best_path = s, list(path)
    return best_path, best


@settings(max_examples=60, deadline=None)
@given(
    t_len=st.integers(1, 4),
    seed=st.integers(0, 2**32 - 1),
    biases=st.lists(st.floats(-3, 3, allow_nan=False), min_size=6, max_size=6),
)
def test_matches_brute_force(t_len, seed, biases):
    rng = np.random.default_rng(seed)
    lp = log_softmax(rng.normal(0, 3, size=(t_len, len(SMALL))))
    dec = ViterbiDecoder(SMALL, dict(zip(BIAS_KEYS, biases)))
    got = dec.decode(lp)
    want, want_score = brute_force(dec, lp)
    assert valid_path(SMALL, got)
    # Ties can pick a different path with the same score.
    assert path_score(dec, lp, got) == pytest.approx(want_score, abs=1e-9)
    assert got == want or path_score(dec, lp, got) == pytest.approx(path_score(dec, lp, want))


def test_empty_and_shape_checks():
    dec = ViterbiDecoder(SMALL)
    assert dec.decode(np.zeros((0, len(SMALL)))) == []
    with pytest.raises(ValueError):
        dec.decode(np.zeros((3, 4)))
    with pytest.raises(ValueError):
        dec.decode(np.full((2, len(SMALL)), np.nan))


def test_orphan_inside_label_is_repaired():
    # Argmax would give I-a alone; the constrained decoder must not.
    lp = np.full((1, len(SMALL)), -10.0)
    lp[0, 2] = 0.0  # I-a
    lp[0, 4] = -1.0  # S-a
    assert ViterbiDecoder(SMALL).decode(lp) == [4]


def test_label_space_validation():
    with pytest.raises(ValueError):
        LabelSpace.from_mapping({0: "O", 2: "B-a"})
    with pytest.raises(ValueError):
        LabelSpace.from_mapping({0: "B-a", 1: "E-a"})
    with pytest.raises(ValueError):
        LabelSpace.from_mapping({0: "O", 1: "X-a"})


def test_bias_file_is_strict(tmp_path):
    p = tmp_path / "c.json"
    good = {"operating_points": {"default": {"biases": {k: 0 for k in BIAS_KEYS}}}}
    p.write_text(json.dumps(good))
    assert load_biases(p) == {k: 0.0 for k in BIAS_KEYS}
    bad = json.loads(json.dumps(good))
    bad["operating_points"]["default"]["biases"]["extra"] = 1
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        load_biases(p)
    bad = json.loads(json.dumps(good))
    bad["operating_points"]["default"]["biases"][BIAS_KEYS[0]] = True
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        load_biases(p)
    with pytest.raises(ValueError):
        ViterbiDecoder(SMALL, {"nope": 1.0})


def test_log_softmax_rows_sum_to_one():
    x = np.random.default_rng(0).normal(0, 50, size=(5, 9))
    assert np.allclose(np.exp(log_softmax(x)).sum(axis=1), 1.0)
