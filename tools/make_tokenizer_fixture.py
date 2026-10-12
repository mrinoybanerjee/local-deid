"""Write tests/fixtures/o200k_ids.json: reference token ids from tiktoken o200k_base.

Dev-only (tiktoken downloads its BPE file). Run with:
    uv run --no-project --with tiktoken python tools/make_tokenizer_fixture.py
"""
import json
from pathlib import Path

import tiktoken

CASES = [
    "", " ", "\n\n", "Mateo Ramírez (DOB 03/14/2021) was seen at Little Acorns.",
    "Su mamá, Lucía, dijo que el 5 de marzo llamó al 919-555-0142.",
    "niño ÑANDÚ pingüino acción", "Niño (NFD) vs Niño (NFC)", "café con leche",
    "zero​width‍joiner", "“smart quotes” ‘and’ — dashes …", "emoji 😀👩‍👩‍👧 flags 🇲🇽🇺🇸",
    "CRLF line\r\nnext\rold-mac", "tabs\tin\ttables\t92507\t8/10", "/k/ /ɾ/ [ˈpeɾo] θ ʃ ʒ",
    "<|endoftext|> <|start|>x<|end|> <|reserved_200000|>", "[CHILD_1] [PARENT_2]'s [niño_1]",
    "a" * 300, "١٢٣ 中文 日本語 한국어", "퟿\U0010fffd",
]

enc = tiktoken.get_encoding("o200k_base")
out = [{"text": t, "ids": enc.encode(t, disallowed_special=())} for t in CASES]
Path("tests/fixtures/o200k_ids.json").write_text(json.dumps(out, ensure_ascii=False, indent=0) + "\n")
print(len(out), "cases")
