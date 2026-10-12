"""GLiNER organization detector: input building, decoding, parity with the
`gliner` package (fixture from tools/gliner_parity.py), and the engine filter."""

import importlib.util
import itertools
import json
import os
import re
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from localdeid import pipeline
from localdeid.gliner_onnx import CHUNK_WORDS, LABEL_KIND, LABELS, MAX_CHUNK_TOKENS, OrgSpan, chunks, greedy_flat, span_inputs
from localdeid.pf.artifacts import ArtifactError
from localdeid.pipeline import ORG_MODEL_KIND, Settings, deidentify

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
FIXTURE = ROOT / "tests" / "fixtures" / "gliner_parity.json"
HAS_MODEL = (all(importlib.util.find_spec(m) for m in ("onnxruntime", "tokenizers"))
             and (MODELS / "gliner-x-base-q8" / "model_quantized.onnx").exists() and not os.environ.get("SKIP_SLOW"))
needs_model = pytest.mark.skipif(not HAS_MODEL, reason="onnxruntime, tokenizers, or GLiNER model missing, or SKIP_SLOW")


# ----------------------------------------------------------------- pure parts


def test_label_kinds_agree():
    assert LABEL_KIND == ORG_MODEL_KIND
    assert set(LABEL_KIND) == set(LABELS)


@settings(max_examples=300, deadline=None)
@given(st.text(alphabet=st.sampled_from(list("ab Á-_.!?\n\t,;:'​\x07😀")), max_size=400))
def test_chunk_offsets_are_exact(text):
    last_end = -1
    for base, offs in chunks(text):
        assert 1 <= len(offs) <= MAX_CHUNK_TOKENS
        assert offs[0][0] == 0
        for a, b in offs:
            word = text[base + a:base + b]
            assert word and not re.search(r"\s", word)
            assert re.fullmatch(r"\w+(?:[-_]\w+)*|\S", word)
            assert base + a >= last_end  # in order, no overlap, across chunks too
            last_end = base + b
        assert "\n" not in text[base:base + offs[-1][1]]


def test_chunks_split_long_lines():
    text = " ".join(["w"] * (CHUNK_WORDS * 2 + 5))
    sizes = [len(o) for _, o in chunks(text)]
    assert sizes == [CHUNK_WORDS, CHUNK_WORDS, 5]
    dashes = "-" * (MAX_CHUNK_TOKENS + 10)  # one whitespace word, many splitter words
    assert [len(o) for _, o in chunks(dashes)] == [MAX_CHUNK_TOKENS, 10]


def test_chunks_of_empty_and_punctuation_only_text():
    assert chunks("") == []
    assert chunks("\n\n...!?\n") == []


@pytest.mark.parametrize("n,w", [(1, 12), (3, 2), (5, 12), (20, 12)])
def test_span_inputs_match_package_definition(n, w):
    idx, mask = span_inputs(n, w)
    want = [(s, s + k) for s in range(n) for k in range(w)]
    assert [tuple(r) for r in idx.tolist()] == want
    assert mask.tolist() == [e <= n - 1 for _, e in want]


def test_greedy_flat_keeps_best_and_drops_overlaps():
    cands = [(0.7, 0, 2, 0), (0.9, 2, 3, 1), (0.8, 4, 4, 0), (0.6, 5, 6, 2)]
    assert greedy_flat(cands) == [(0.9, 2, 3, 1), (0.8, 4, 4, 0), (0.6, 5, 6, 2)]


def test_greedy_flat_same_span_two_labels_keeps_one():
    assert greedy_flat([(0.7, 1, 2, 0), (0.71, 1, 2, 3)]) == [(0.71, 1, 2, 3)]


def test_greedy_flat_nested_and_ties():
    assert greedy_flat([(0.8, 0, 5, 0), (0.9, 2, 3, 0)]) == [(0.9, 2, 3, 0)]
    # equal scores: the earlier (start, width, class) candidate wins, as in the package
    assert greedy_flat([(0.5, 1, 1, 0), (0.5, 1, 2, 0)]) == [(0.5, 1, 1, 0)]
    assert greedy_flat([]) == []


@settings(max_examples=200, deadline=None)
@given(st.lists(st.tuples(st.floats(0.01, 1.0), st.integers(0, 15), st.integers(0, 11), st.integers(0, 7)),
                max_size=30))
def test_greedy_flat_output_never_overlaps(raw):
    cands = [(p, s, s + k, c) for p, s, k, c in raw]
    kept = greedy_flat(cands)
    for (_, s1, e1, _), (_, s2, e2, _) in itertools.combinations(kept, 2):
        assert e1 < s2 or e2 < s1
    assert [k[1] for k in kept] == sorted(k[1] for k in kept)
    if cands:
        assert max(cands)[0] == max(kept)[0]  # the best candidate always survives


# ------------------------------------------------------------- engine filter


class FakeOrgs:
    def __init__(self, spans):
        self.spans = spans

    def __call__(self, text):
        return [OrgSpan(text.index(s), text.index(s) + len(s), label, score) for s, label, score in self.spans]


NEUTRAL = "Mom likes Quillfeather Bindery a lot."


def run(text, spans):
    return deidentify(text, Settings(), org_detect=FakeOrgs(spans))


def hidden(r):
    return {r.source[i.start:i.end]: i.kind for i in r.items if i.hidden}


def test_model_org_becomes_tag_with_mapped_kind():
    assert hidden(run(NEUTRAL, [])) == {}  # the rules alone do not catch it
    r = run(NEUTRAL, [("Quillfeather Bindery", "company", 0.8)])
    assert hidden(r) == {"Quillfeather Bindery": "ORG"}
    assert "[ORG_1]" in r.output and not r.blocked
    r = run(NEUTRAL, [("Quillfeather Bindery", "daycare", 0.7)])
    assert hidden(r) == {"Quillfeather Bindery": "DAYCARE"}


def test_model_org_below_threshold_or_unknown_label_is_dropped():
    r = run(NEUTRAL, [("Quillfeather Bindery", "company", 0.59)])
    assert hidden(r) == {}
    r = run(NEUTRAL, [("Quillfeather Bindery", "person", 0.9)])
    assert hidden(r) == {}


@pytest.mark.parametrize("text,span", [
    ("Results on the Preschool Language Scales were low.", "Preschool Language Scales"),
    ("She was referred to Physical Therapy last week.", "Physical Therapy"),
    ("Goals target Total Communication at home.", "Total Communication"),
    ("Speech Therapy continues weekly.", "Speech Therapy"),
    ("Scored 85 on the CELF-5 today.", "CELF-5"),
])
def test_model_org_on_clinical_content_is_dropped(text, span):
    r = run(text, [(span, "organization", 0.95)])
    assert hidden(r) == {}
    assert r.output == r.source


def test_model_org_filter_unit():
    from localdeid.protect import Protected

    t = "Seen at Maplecrest Pediatrics and the clinic."
    assert pipeline._model_org_ok(t, 8, 28, [])
    assert not pipeline._model_org_ok(t, 8, 28, [Protected(10, 12, "term")])
    assert not pipeline._model_org_ok(t, 33, 44, [])  # "the clinic": generic words only
    assert not pipeline._model_org_ok("Seen 123 x", 5, 8, [])  # no letters


# ------------------------------------------------------------- real model


@pytest.fixture(scope="module")
def model():
    from localdeid.gliner_onnx import GlinerOnnx

    return GlinerOnnx(MODELS)


@needs_model
def test_parity_with_gliner_package(model):
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert tuple(fx["labels"]) == LABELS and len(fx["chunks"]) >= 50
    for ch in fx["chunks"]:
        text = ch["text"]
        got = model.detect(text, fx["threshold"])
        assert [(s.start, s.end, s.label) for s in got] == [tuple(x[:3]) for x in ch["spans"]], text
        for s, x in zip(got, ch["spans"], strict=True):
            assert abs(s.score - x[3]) < 1e-5


@needs_model
def test_tokenless_words_do_not_shift_offsets(model):
    text = "Mateo va a Little​ Sprouts Learning Center en Durham."
    got = model.detect(text, 0.5)
    assert any(text[s.start:s.end] == "Little​ Sprouts Learning Center" for s in got)
    for s in got:
        assert text[s.start:s.end].strip() == text[s.start:s.end]
    assert model.detect("​ \x07 ﻿", 0.1) == []


@needs_model
def test_model_runs_offline_on_cpu_only(model):
    assert model.session.get_providers() == ["CPUExecutionProvider"]
    assert os.environ.get("ORT_DISABLE_TELEMETRY") == "1"


@needs_model
def test_spans_are_valid_on_long_and_odd_text(model):
    text = ("Mamá trabaja en Acme Logistics. " * 300) + "\n" + "!" * 1000 + " Hope Valley Baptist Church\n"
    for s in model.detect(text, 0.5):
        assert 0 <= s.start < s.end <= len(text) and s.label in LABELS and 0.5 < s.score <= 1.0


def test_load_refuses_unverified_files(monkeypatch):
    import localdeid.gliner_onnx as g

    def bad(root, artifact):
        raise ArtifactError(f"{artifact}: hash differs from manifest")

    monkeypatch.setattr(g, "verify", bad)
    with pytest.raises(ArtifactError):
        g.GlinerOnnx(MODELS)


@pytest.mark.parametrize("text,span,ok", [
    ("Standardized testing was done.", "Standardized testing", False),
    ("Results:\nLanguage sample collected.", "Language sample", False),
    ("He attends daycare weekly.", "daycare", False),
    ("Daycare teacher said so.", "Daycare teacher", False),
    ("Seen by the Bilingual Speech-Language Pathologist today.", "Bilingual Speech-Language Pathologist", False),
    ("Mother's Cell: 555-0100", "Mother's Cell", False),
    ("[ ] Home  [ ] Daycare", "Home  [ ] Daycare", False),
    ("Dx: Trastorno del Desarrollo del Lenguaje.", "Trastorno del Desarrollo del Lenguaje", False),
    ("Mom works at Dollar General downtown.", "Dollar General", True),
    ("Referred by Western Piedmont CDSA in May.", "Western Piedmont CDSA", True),
    ("Employed by Carolina Pediatric Therapy Services since 2020.", "Carolina Pediatric Therapy Services", True),
    ("She visits WIC monthly.", "WIC", True),
    ("Attends Casa Mariposa Preschool daily.", "Casa Mariposa Preschool", True),
])
def test_model_org_filter_cases(text, span, ok):
    s = text.index(span)
    assert pipeline._model_org_ok(text, s, s + len(span), []) is ok


def test_model_org_span_is_judged_per_table_cell():
    text = "Teacher\tDaycare\nMs. Lopez\tQuillfeather Kids Daycare\n"
    s = text.index("Lopez")
    e = text.index("Daycare\n", s) + len("Daycare")
    r = deidentify(text, Settings(), org_detect=lambda t: [OrgSpan(s, e, "daycare", 0.9)])
    assert "Quillfeather" not in r.output and not r.failures


HAS_SRC_TOKENIZER = (MODELS / "gliner-x-base-src" / "tokenizer.json").exists()


@needs_model
@pytest.mark.skipif(not HAS_SRC_TOKENIZER, reason="gliner-x-base-src/tokenizer.json missing")
def test_sentencepiece_matches_hf_tokenizer(model):
    from tokenizers import Tokenizer

    hf = Tokenizer.from_file(str(MODELS / "gliner-x-base-src" / "tokenizer.json"))
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    texts = [c["text"] for c in fx["chunks"]] + [
        "a ​ b", "x \x07 y \x00 z", "﻿ 😀 ­", "ﬁ ｆｕｌｌ Ⅳ é é", "İstanbul ß Ǆ 𝔘𝔫𝔦 ",
        "Niño, José-María O'Brien 3;2 8/10 [ʃ] /s/", "  \t ", "Guardería Los Pinitos — CDSA de Durham",
    ]
    for text in texts:
        for base, offs in chunks(text):
            words = model.prompt + [text[base + a:base + b] for a, b in offs]
            e = hf.encode(words, is_pretokenized=True, add_special_tokens=True)
            assert model.tokenize(words) == (e.ids, list(e.word_ids)), text


@needs_model
def test_oversized_input_is_bounded(model, monkeypatch):
    from localdeid import gliner_onnx

    seen = []
    real = model.predict_words

    def spy(words, threshold):
        seen.append(len(model.tokenize(model.prompt + words)[0]))
        return real(words, threshold)

    monkeypatch.setattr(model, "predict_words", spy)
    text = "Mom works at Acme Logistics. " + "xq" * 5000 + " and " + "palabra " * 300
    spans = model.detect(text, 0.5)
    assert seen and max(seen) <= gliner_onnx.MAX_MODEL_TOKENS
    assert all(0 <= s.start < s.end <= len(text) for s in spans)
    assert any(text[s.start:s.end] == "Acme Logistics" for s in spans)


@needs_model
def test_windows_overlap_so_names_survive_a_split(model):
    filler = " ".join(["abcdefghijklmnop"] * 55)
    text = filler + " mother is hired by Bright Horizons " + " ".join(["abcdefghijklmnop"] * 59)
    windows = model.bounded(text.split())
    assert len(windows) > 1 and all(b > a for a, b in windows)
    assert all(windows[i + 1][0] < windows[i][1] for i in range(len(windows) - 1))  # overlap
    assert any(text[s.start:s.end] == "Bright Horizons" for s in model.detect(text, 0.5))


@needs_model
def test_short_chunk_is_one_window(model):
    words = "Mom works at Acme Logistics in Durham .".split()
    assert model.bounded(words) == [(0, len(words))]


@needs_model
def test_oversized_word_is_a_window_barrier(model):
    words = ["word"] * 60 + ["xq" * 5000] + ["word"] * 59
    windows = model.bounded(words)
    assert all(not (a <= 60 < b) for a, b in windows)  # no window holds the oversized word
    assert len(windows) <= 4
