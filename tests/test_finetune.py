"""Fine-tune support (DECISIONS 26): gold-to-token labels, windows, LoRA parity,
adapter loading, and the organization class in the engine."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from localdeid.evaldata import Doc, GoldSpan

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
sys.path.insert(0, str(ROOT / "tools"))
ft = pytest.importorskip("finetune_pf")

HAS_MLX = importlib.util.find_spec("mlx") is not None and (MODELS / "pf-mlx-8bit" / "weights.safetensors").exists()
needs_mlx = pytest.mark.skipif(not HAS_MLX, reason="mlx or the MLX model is missing")


@pytest.fixture(scope="module")
def tok():
    from localdeid.pf.tokenize import PFTokenizer

    return PFTokenizer(MODELS / "o200k-bpe" / "o200k_base.tiktoken")


def _decode(text, tok, labels, id2label):
    """Entities (type, surface) from token labels, using the token offsets."""
    enc = tok.encode(text)
    out, cur = [], None
    for t, lab in enumerate(labels):
        name = id2label[int(lab)]
        if name == "O":
            continue
        tag, ent = name[0], name[2:]
        if tag in "BS":
            cur = [ent, int(enc.starts[t]), int(enc.ends[t])]
            out.append(cur)
        else:
            cur[2] = int(enc.ends[t])
    return [(e, text[s:x].strip()) for e, s, x in out]


def test_labels_cover_each_span_and_keep_neighbours_apart(tok):
    id2label, label2id = ft.label_space()
    text = "Ana Sol went to Little Sprouts Daycare on 03/14/2025 at 3:00 pm. Score 8/10."
    spans = [GoldSpan(0, 3, "CHILD"), GoldSpan(4, 7, "SIBLING"), GoldSpan(16, 38, "DAYCARE"),
             GoldSpan(42, 52, "DATE"), GoldSpan(56, 63, "TIME"), GoldSpan(71, 75, "KEEP")]
    ids, labels = ft.token_labels(Doc("t", text, spans), tok, label2id)
    assert len(ids) == len(labels)
    assert _decode(text, tok, labels, id2label) == [
        ("private_person", "Ana"), ("private_person", "Sol"),
        ("private_organization", "Little Sprouts Daycare"), ("private_date", "03/14/2025")]


def test_every_gold_type_maps_and_org_kinds_share_one_class():
    from localdeid.evaldata import GOLD_TYPES

    for t in GOLD_TYPES:
        if t not in ("KEEP", "TIME"):
            assert t in ft.GOLD_TO_PF, t
    assert {ft.GOLD_TO_PF[t] for t in ("AGENCY", "DAYCARE", "EMPLOYER", "HOSPITAL", "ORG", "SCHOOL")} == {
        "private_organization"}


def test_label_space_extends_the_base_and_keeps_its_order():
    import json

    id2label, label2id = ft.label_space()
    base = json.loads((MODELS / "pf-mlx-8bit" / "config.json").read_text())["id2label"]
    assert len(id2label) == len(base) + 4
    assert all(id2label[int(k)] == v for k, v in base.items())
    assert [id2label[i] for i in range(len(base), len(base) + 4)] == [
        "B-private_organization", "I-private_organization", "E-private_organization", "S-private_organization"]


def test_windows_cover_every_token():
    ids = np.arange(5000)
    labels = np.arange(5000)
    ws = ft.windows(ids, labels, 2048, 256)
    seen = np.zeros(5000, dtype=bool)
    for a, b in ws:
        assert len(a) <= 2048 and (a == b).all()
        seen[a] = True
    assert seen.all()
    assert len(ft.windows(ids[:100], labels[:100], 2048, 256)) == 1


@needs_mlx
def test_lora_with_zero_b_matches_the_base_model():
    import mlx.core as mx

    from localdeid.pf.backends import MlxBackend
    from localdeid.pf.lora import attach

    ids = mx.array(np.arange(1000, 1300, dtype=np.int32)[None, :])
    m = MlxBackend(MODELS / "pf-mlx-8bit").model
    base = np.array(m(ids).astype(mx.float32))
    attach(m, rank=4, alpha=8, targets=["attn.qkv", "attn.out"], num_labels=37, seed=1)
    tuned = np.array(m(ids).astype(mx.float32))
    assert tuned.shape[-1] == 37
    # B starts at zero and the head copies the base rows, so the base logits agree up to
    # bf16 rounding of the base output (8 significant bits: 1/128 relative, plus a little).
    assert (np.abs(tuned[..., :33] - base) <= 0.02 + np.abs(base) / 64).all()
    assert (tuned[..., :33].argmax(-1) == base.argmax(-1)).mean() > 0.99


@needs_mlx
def test_adapter_file_round_trip_and_refusals(tmp_path):
    import mlx.core as mx

    from localdeid.pf.backends import MlxBackend
    from localdeid.pf.lora import adapter_state, attach, load_adapters

    targets = ["attn.qkv", "attn.out"]
    m = MlxBackend(MODELS / "pf-mlx-8bit").model
    attach(m, rank=4, alpha=8, targets=targets, num_labels=37, seed=2)
    state = adapter_state(m, targets)
    state = {k: v + 0.01 for k, v in state.items()}
    mx.save_safetensors(str(tmp_path / "adapters.safetensors"), state)
    cfg = {"num_labels": 37, "_lora": {"rank": 4, "alpha": 8, "targets": targets}}
    m2 = MlxBackend(MODELS / "pf-mlx-8bit").model
    load_adapters(m2, tmp_path / "adapters.safetensors", cfg)
    got = adapter_state(m2, targets)
    assert all(np.allclose(np.array(got[k]), np.array(state[k])) for k in state)
    bad = dict(state)
    bad.pop("unembedding.weight")
    mx.save_safetensors(str(tmp_path / "bad.safetensors"), bad)
    with pytest.raises(ValueError, match="keys differ"):
        load_adapters(MlxBackend(MODELS / "pf-mlx-8bit").model, tmp_path / "bad.safetensors", cfg)
    with pytest.raises(ValueError, match="shape"):
        load_adapters(MlxBackend(MODELS / "pf-mlx-8bit").model, tmp_path / "adapters.safetensors",
                      {"num_labels": 37, "_lora": {"rank": 8, "alpha": 8, "targets": targets}})


def test_model_organization_goes_through_the_org_filter():
    from localdeid.pf.model import PFSpan
    from localdeid.pipeline import Settings, deidentify

    text = "Visit at Maplecrest Pediatric Partners.\nPLS-5 Spanish given today."

    def pf(t):
        a = t.index("Maplecrest")
        b = t.index("PLS-5")
        return [PFSpan(a, a + len("Maplecrest Pediatric Partners"), "private_organization", 0.9),
                PFSpan(b, b + len("PLS-5 Spanish"), "private_organization", 0.9)]

    r = deidentify(text, Settings(), pf_detect=pf)
    assert "Maplecrest" not in r.output
    assert "PLS-5 Spanish" in r.output  # a test name is clinical, never an organization
    assert not r.blocked


def test_a_confident_lowercase_model_organization_is_kept():
    from localdeid.pf.model import PFSpan
    from localdeid.pipeline import Settings, deidentify

    text = "pt seen at sunnybrook elementary today. sunnybrook elementary staff present."

    def pf(score):
        return lambda t: [PFSpan(m, m + len("sunnybrook elementary"), "private_organization", score)
                          for m in (t.index("sunnybrook"), t.rindex("sunnybrook"))]

    assert "sunnybrook" not in deidentify(text, Settings(), pf_detect=pf(0.95)).output
    assert "sunnybrook" in deidentify(text, Settings(), pf_detect=pf(0.3)).output  # unsure: needs a capital


@pytest.mark.parametrize("text, name", [
    ("Sunnybrook elementary sent the records today.", "Sunnybrook elementary"),
    ("Mom works at 3M.", "3M"),
    ("Mom works at H&M.", "H&M"),
    ("Dad works at AT&T now.", "AT&T"),
])
def test_confident_model_organizations_in_hard_shapes_are_kept(text, name):
    from localdeid.pf.model import PFSpan
    from localdeid.pipeline import Settings, deidentify

    a = text.index(name)
    r = deidentify(text, Settings(), pf_detect=lambda t: [PFSpan(a, a + len(name), "private_organization", 0.95)])
    assert name not in r.output and not r.blocked


def test_label_repair_leaves_word_names_alone():
    from localdeid.evaldata import Doc, GoldSpan, sanitize

    text = "Mark attended today. Mark each correct response. Will continue next week."
    doc, _ = sanitize(Doc("t", text, [GoldSpan(0, 4, "CHILD")], {}))
    assert [text[s.start:s.end] for s in doc.identifiers] == ["Mark"]


@pytest.mark.parametrize("text, word", [
    ("Birth history: NICU stay.", "NICU"),
    ("Referred by her PCP last month.", "PCP"),
    ("Dx: SPEECH SOUND DISORDER noted.", "SPEECH SOUND DISORDER"),
])
def test_confident_model_spans_on_clinical_acronyms_stay_visible(text, word):
    from localdeid.pf.model import PFSpan
    from localdeid.pipeline import Settings, deidentify

    a = text.index(word)
    r = deidentify(text, Settings(), pf_detect=lambda t: [PFSpan(a, a + len(word), "private_organization", 0.95)])
    assert word in r.output


@pytest.mark.parametrize("text, name", [
    ("He attends School 23 now.", "School 23"),
    ("Asiste a la Escuela 23.", "Escuela 23"),
    ("Va a la Guardería 12 por la mañana.", "Guardería 12"),
])
def test_confident_numbered_organizations_are_hidden(text, name):
    from localdeid.pf.model import PFSpan
    from localdeid.pipeline import Settings, deidentify

    a = text.index(name)
    r = deidentify(text, Settings(), pf_detect=lambda t: [PFSpan(a, a + len(name), "private_organization", 0.95)])
    assert name not in r.output


@pytest.mark.parametrize("text, words", [
    ("See Table 2 for scores.", "Table 2"),
    ("Language sample 2 was collected.", "sample 2"),
    ("Part 2 of the test was given.", "Part 2"),
    ("He sat in Room 2.", "Room 2"),
])
def test_confident_model_spans_on_numbered_clinical_text_stay_visible(text, words):
    from localdeid.pf.model import PFSpan
    from localdeid.pipeline import Settings, deidentify

    a = text.index(words)
    r = deidentify(text, Settings(), pf_detect=lambda t: [PFSpan(a, a + len(words), "private_organization", 0.95)])
    assert words in r.output


def test_ampersand_specialties_stay_visible_but_ampersand_names_hide():
    from localdeid.pf.model import PFSpan
    from localdeid.pipeline import Settings, deidentify

    def run(text, word):
        a = text.index(word)
        return deidentify(text, Settings(),
                          pf_detect=lambda t: [PFSpan(a, a + len(word), "private_organization", 0.95)]).output

    assert "ENT&GI" in run("Referrals: ENT&GI.", "ENT&GI")
    assert "P&G" not in run("Dad works at P&G.", "P&G")


def test_judge_rejects_cached_reports_that_do_not_match(tmp_path):
    from localdeid.evaldata import Doc, GoldSpan, docs_digest, dump_jsonl

    docs = [Doc("d1", "Ana said hi.", [GoldSpan(0, 3, "CHILD")], {"language": "en", "doc_type": "daily_note"})]
    path = tmp_path / "devtest.jsonl"
    dump_jsonl(docs, path)
    import json as _json

    manifest = _json.loads((MODELS / "MANIFEST.json").read_text())["artifacts"]["pf-mlx-8bit"]["files"]
    from localdeid.evaldata import code_digest

    good = {"model": True, "backend": "mlx_8bit", "docs_sha256": docs_digest([ft_sanitize(d) for d in docs]),
            "code_sha256": code_digest(),
            "artifact_sha256": {f: m["sha256"] for f, m in sorted(manifest.items())}}
    judge = pytest.importorskip("judge_ft")
    judge._check_cached(path, good, "mlx_8bit")  # matches: no exit
    for bad in ({**good, "model": False}, {**good, "backend": "mlx_8bit_ft1"},
                {**good, "docs_sha256": "0" * 64}, {**good, "artifact_sha256": {}}, {**good, "code_sha256": "x"}):
        with pytest.raises(SystemExit):
            judge._check_cached(path, bad, "mlx_8bit")
    changed = [Doc("d1", "Ana said bye.", [GoldSpan(0, 3, "CHILD")], docs[0].meta)]
    assert docs_digest(changed) != docs_digest(docs)  # same id, other text: another digest


def ft_sanitize(d):
    from localdeid.evaldata import sanitize

    return sanitize(d)[0]


def test_code_digest_covers_the_evaluation_runner(monkeypatch):
    from pathlib import Path

    from localdeid import evaldata

    before = evaldata.code_digest()
    runner = Path(evaldata.__file__).resolve().parents[2] / "tools" / "run_eval.py"
    real = Path.read_bytes

    def patched(self):
        data = real(self)
        return data + b"# changed" if self == runner else data

    monkeypatch.setattr(Path, "read_bytes", patched)
    assert evaldata.code_digest() != before
