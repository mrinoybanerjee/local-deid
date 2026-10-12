import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from localdeid.pf.tokenize import PFTokenizer

ROOT = Path(__file__).resolve().parent.parent
TOK = ROOT / "models" / "o200k-bpe" / "o200k_base.tiktoken"
HF_TOK = ROOT / "models" / "pf-onnx-q4" / "tokenizer.json"
pytestmark = pytest.mark.skipif(not TOK.exists(), reason="models not fetched")


@pytest.fixture(scope="module")
def tok():
    return PFTokenizer(TOK)


@pytest.fixture(scope="module")
def hf():
    """The Hugging Face tokenizer shipped with the model: an independent offset reference."""
    tokenizers = pytest.importorskip("tokenizers")
    if not HF_TOK.exists():
        pytest.skip("HF tokenizer.json not fetched")
    t = tokenizers.Tokenizer.from_file(str(HF_TOK))
    t.encode_special_tokens = True
    return t


def test_ids_match_tiktoken_o200k(tok):
    cases = json.loads((ROOT / "tests" / "fixtures" / "o200k_ids.json").read_text())
    for case in cases:
        assert tok.encode(case["text"]).ids.tolist() == case["ids"], case["text"]


def test_special_token_text_is_plain_text(tok):
    enc = tok.encode("x <|endoftext|> y")
    assert 199999 not in enc.ids.tolist()
    assert all(i < 199998 for i in enc.ids.tolist())


def check_offsets(text, enc):
    assert len(enc.ids) == len(enc.starts) == len(enc.ends)
    prev_start = 0
    for s, e in zip(enc.starts.tolist(), enc.ends.tolist()):
        assert 0 <= s <= e <= len(text)
        assert s >= prev_start  # monotonic
        prev_start = s
    # The tokens' character ranges cover every non-empty input completely.
    covered = set()
    for s, e in zip(enc.starts.tolist(), enc.ends.tolist()):
        covered.update(range(s, e))
    assert covered == set(range(len(text)))


@pytest.mark.parametrize("text", [
    "", "a", "😀", "Ñ", "Ñ", "a b", "x​y", "a\r\nb", "👩‍👩‍👧 hola", "  lead", "trail  ",
])
def test_offsets_edge_cases(tok, text):
    check_offsets(text, tok.encode(text))


@settings(max_examples=300, deadline=None)
@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=200))
def test_offsets_property(tok, text):
    check_offsets(text, tok.encode(text))


@settings(max_examples=300, deadline=None)
@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=200))
def test_matches_hf_tokenizer(tok, hf, text):
    enc = tok.encode(text)
    ref = hf.encode(text, add_special_tokens=False)
    assert enc.ids.tolist() == ref.ids
    assert list(zip(enc.starts.tolist(), enc.ends.tolist())) == [tuple(o) for o in ref.offsets]


@pytest.mark.parametrize("cache_dir", [None, ""])
def test_wrong_bpe_file_is_refused(tmp_path, monkeypatch, cache_dir):
    # tiktoken skips its own hash check when TIKTOKEN_CACHE_DIR is empty; ours must not.
    if cache_dir is not None:
        monkeypatch.setenv("TIKTOKEN_CACHE_DIR", cache_dir)
    bad = tmp_path / "o200k_base.tiktoken"
    bad.write_bytes(TOK.read_bytes().replace(b"IQ== 0", b"Ig== 0", 1))  # one rank changed
    with pytest.raises(ValueError):
        PFTokenizer(bad)
