"""o200k_base tokenizer with code-point offsets, loaded offline.

tiktoken reads the BPE ranks from a local, hash-pinned file
(models/o200k-bpe/o200k_base.tiktoken), so no network is used. It costs about
80 MB of memory against about 200 MB for the Hugging Face `tokenizers` object
of the same vocabulary (measured on the build host, 2026-10-10).

Offsets: each token's bytes are laid end to end over the UTF-8 encoding of
the text. A token that starts or ends inside a multi-byte character covers the
whole character, so offsets are monotonic and every character is covered.
Text that looks like a special token ("<|endoftext|>") is encoded as plain
text (disallowed_special=()).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

O200K_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
# From tiktoken_ext/openai_public.py (tiktoken 0.14.0, MIT license).
O200K_PAT_STR = "|".join([
    r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?""",
    r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?""",
    r"""\p{N}{1,3}""",
    r""" ?[^\s\p{L}\p{N}]+[\r\n/]*""",
    r"""\s*[\r\n]+""",
    r"""\s+(?!\S)""",
    r"""\s+""",
])
SPECIAL_TOKENS = {"<|endoftext|>": 199999, "<|endofprompt|>": 200018}


@dataclass(frozen=True)
class Encoding:
    ids: np.ndarray  # int64 [T]
    starts: np.ndarray  # int64 [T], code-point offsets into the input string
    ends: np.ndarray  # int64 [T]


class PFTokenizer:
    def __init__(self, bpe_file: str | Path):
        import tiktoken
        from tiktoken.load import load_tiktoken_bpe

        path = Path(bpe_file)
        # Verify the file ourselves: tiktoken skips its own expected_hash check
        # when its cache is disabled (TIKTOKEN_CACHE_DIR="").
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != O200K_SHA256:
            raise ValueError(f"o200k BPE file hash mismatch: {digest}")
        ranks = load_tiktoken_bpe(str(path), expected_hash=O200K_SHA256)
        self._enc = tiktoken.Encoding(
            name="o200k_base_local", pat_str=O200K_PAT_STR, mergeable_ranks=ranks,
            special_tokens=SPECIAL_TOKENS,
        )
        self._token_len = {}

    def _nbytes(self, tok: int) -> int:
        n = self._token_len.get(tok)
        if n is None:
            n = len(self._enc.decode_single_token_bytes(tok))
            self._token_len[tok] = n
        return n

    def encode(self, text: str) -> Encoding:
        ids_list = self._enc.encode_ordinary(text)
        n = len(ids_list)
        ids = np.asarray(ids_list, dtype=np.int64)
        if n == 0:
            z = np.zeros(0, dtype=np.int64)
            return Encoding(ids, z, z)
        # byte index -> code-point index
        char_of_byte = np.repeat(
            np.arange(len(text), dtype=np.int64),
            [len(ch.encode("utf-8", "surrogatepass")) for ch in text],
        )
        lens = np.fromiter((self._nbytes(t) for t in ids_list), dtype=np.int64, count=n)
        byte_end = np.cumsum(lens)
        byte_start = byte_end - lens
        if byte_end[-1] != len(char_of_byte):
            raise ValueError("token bytes do not cover the input")
        starts = char_of_byte[byte_start]
        ends = char_of_byte[byte_end - 1] + 1
        return Encoding(ids, starts, ends)
