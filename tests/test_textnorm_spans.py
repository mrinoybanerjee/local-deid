import unicodedata

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from localdeid.protect import Protected
from localdeid.spans import Candidate, merge, snap, protected_mask
from localdeid.textnorm import InvalidText, fold, normalize_paste

TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=120)


def test_normalize_paste_nfc_and_reports_change():
    nfd = "José Niño"
    text, changed = normalize_paste(nfd)
    assert text == "José Niño" and changed
    assert normalize_paste("José") == ("José", False)


def test_normalize_paste_refuses_lone_surrogates():
    with pytest.raises(InvalidText):
        normalize_paste("ok \ud800 bad")


@given(TEXT)
def test_fold_map_is_monotonic_and_complete(text):
    f = fold(text)
    assert len(f.src) == len(f.text) + 1
    assert all(a <= b for a, b in zip(f.src, f.src[1:]))
    assert f.src[-1] == len(text)


@given(TEXT, st.data())
def test_fold_to_source_covers_the_folded_chars(text, data):
    f = fold(text)
    if not f.text:
        return
    s = data.draw(st.integers(0, len(f.text) - 1))
    e = data.draw(st.integers(s + 1, len(f.text)))
    a, b = f.to_source(s, e)
    assert 0 <= a < b <= len(text)
    # folding the source span reproduces (at least) the folded slice
    assert f.text[s:e] in fold(text[a:b]).text


def test_fold_examples():
    f = fold("MARÍA José Straße")
    assert f.text == "maria jose strasse"
    i = f.text.index("strasse")
    assert f.to_source(i, i + 7) == (len("MARÍA José "), len("MARÍA José Straße"))


def test_snap_extends_to_words_but_not_into_protected():
    text = "Mateo's tat"
    mask = protected_mask(len(text), [Protected(8, 11, "quoted")])
    assert snap(text, 1, 3, mask) == (0, 5)  # stops at the apostrophe
    assert snap(text, 9, 10, mask) == (9, 10)  # never grows into protected chars


def C(s, e, kind="PERSON", source="model", sure=True):
    return Candidate(s, e, kind, source, sure)


def test_union_keeps_both_tails():
    text = "Lucía María Rodríguez López"
    ents = merge(text, [C(0, 11, source="model"), C(6, 27, source="gazetteer")], [])
    covered = "".join(text[e.start:e.end] for e in ents)
    assert "Lucía" in covered and "López" in covered
    assert min(e.start for e in ents) == 0 and max(e.end for e in ents) == 27


def test_priority_splits_typed_entities():
    text = "Mateo Ramírez"
    ents = merge(text, [C(0, 13, "PERSON", "model"), C(0, 5, "CHILD", "roster"),
                        C(6, 13, "SURNAME", "roster")], [])
    assert [(text[e.start:e.end], e.kind) for e in ents] == [("Mateo", "CHILD"), ("Ramírez", "SURNAME")]


def test_protect_trims_model_span_and_drops_rule_inside():
    text = "Rossetti score 8/10 trials"
    prot = [Protected(0, 8, "term"), Protected(15, 19, "score")]
    ents = merge(text, [C(0, 14, "PERSON", "model"), C(15, 19, "DATE", "rules")], prot)
    assert [(text[e.start:e.end]) for e in ents] == ["score"]


def test_roster_over_protect_becomes_check_item():
    text = 'Practiced "sol" with Sol.'
    prot = [Protected(11, 14, "quoted")]
    ents = merge(text, [C(11, 14, "SIBLING", "roster"), C(21, 24, "SIBLING", "roster")], prot)
    by = {text[e.start:e.end]: e for e in ents}
    assert by["sol"].protected_overlap and not by["sol"].sure
    assert by["Sol"].sure and not by["Sol"].protected_overlap


def test_whitespace_only_pieces_are_not_entities():
    text = "Ana - Ruiz"
    ents = merge(text, [C(0, 10, "PERSON", "model"), C(0, 3, "CHILD", "roster"),
                        C(6, 10, "SURNAME", "roster")], [])
    assert all(text[e.start:e.end].strip(" -") == text[e.start:e.end] for e in ents)


def test_merge_rejects_bad_candidates():
    with pytest.raises(ValueError):
        merge("abc", [C(0, 9)], [])
    with pytest.raises(ValueError):
        merge("abc", [Candidate(0, 1, "PERSON", "nope")], [])


@settings(max_examples=200, deadline=None)
@given(TEXT, st.lists(st.tuples(st.integers(0, 120), st.integers(1, 40),
                                st.sampled_from(["model", "rules", "roster", "gazetteer", "fields"])),
                      max_size=8),
       st.lists(st.tuples(st.integers(0, 120), st.integers(1, 10)), max_size=4))
def test_merge_invariants(text, raw, raw_prot):
    n = len(text)
    if n == 0:
        return
    cands = [C(min(s, n - 1), min(n, min(s, n - 1) + ln), "PERSON", src) for s, ln, src in raw]
    prot = [Protected(min(s, n - 1), min(n, min(s, n - 1) + ln), "term") for s, ln in raw_prot]
    ents = merge(text, cands, prot)
    mask = protected_mask(n, prot)
    last = -1
    for e in ents:
        assert 0 <= e.start < e.end <= n
        assert e.start >= last  # sorted, non-overlapping
        last = e.end
        if "roster" not in e.sources:
            assert not any(mask[e.start:e.end])  # protect is a hard mask
        assert text[e.start].isalnum() or unicodedata.category(text[e.start]).startswith("M")
