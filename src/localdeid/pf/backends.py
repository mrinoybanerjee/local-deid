"""Privacy Filter inference backends. Each maps token ids [t] to logits [t, labels]
(33 for the base model, 37 for the fine-tuned one).

Only local, offline execution is allowed: ONNX Runtime is restricted to the
CPU execution provider (the wheel also ships a network-capable Azure
provider), and MLX loads weights from a local file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

import numpy as np

from .artifacts import verify


class Backend(Protocol):
    name: str
    id2label: dict[int, str]

    def logits(self, ids: np.ndarray) -> np.ndarray: ...


ALLOWED_ORT_PROVIDERS = ("CPUExecutionProvider",)


class OnnxBackend:
    def __init__(
        self, model_dir: str | Path, filename: str = "model_q4.onnx", *,
        threads: int | None = None, name: str = "onnx_q4", arena: bool = True,
    ):
        self.name = name
        # ONNX Runtime's macOS wheel starts native (OneDS) telemetry unless told
        # not to before import; provider limits do not cover it.
        os.environ["ORT_DISABLE_TELEMETRY"] = "1"
        import onnxruntime as ort

        ort.disable_telemetry_events()

        model_dir = Path(model_dir)
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if threads:
            opts.intra_op_num_threads = threads
        if not arena:  # return activation memory to the OS after each run
            opts.enable_cpu_mem_arena = False
            opts.enable_mem_pattern = False
        self.session = ort.InferenceSession(
            str(model_dir / filename), sess_options=opts,
            providers=list(ALLOWED_ORT_PROVIDERS),
        )
        used = tuple(self.session.get_providers())
        if used != ALLOWED_ORT_PROVIDERS:
            raise RuntimeError(f"unexpected ONNX Runtime providers: {used}")
        cfg = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        self.id2label = {int(k): v for k, v in cfg["id2label"].items()}

    def logits(self, ids: np.ndarray) -> np.ndarray:
        x = np.ascontiguousarray(ids, dtype=np.int64)[None, :]
        (out,) = self.session.run(["logits"], {"input_ids": x, "attention_mask": np.ones_like(x)})
        return np.asarray(out[0], dtype=np.float32)


class MlxBackend:
    name = "mlx_8bit"

    def __init__(self, model_dir: str | Path, *, clear_cache: bool = False, adapter_dir: str | Path | None = None,
                 name: str = "mlx_8bit"):
        """`adapter_dir`: a fine-tuned artifact (DECISIONS 26) whose LoRA adapters and
        extended head are applied on top of the base weights in `model_dir`."""
        import mlx.core as mx
        import mlx.nn as nn

        from localdeid.vendor.openmed_privacy_filter import (
            OpenAIPrivacyFilterForTokenClassification,
        )

        model_dir = Path(model_dir)
        cfg = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        quant = cfg.get("_mlx_quantization") or {}
        weights = dict(mx.load(str(model_dir / "weights.safetensors")))
        model = OpenAIPrivacyFilterForTokenClassification(cfg)
        if any(k.endswith(".scales") for k in weights):
            nn.quantize(
                model,
                group_size=int(quant.get("group_size", 64)),
                bits=int(quant.get("bits", 8)),
                mode=str(quant.get("mode", "affine")),
                class_predicate=lambda path, m: hasattr(m, "to_quantized")
                and f"{path}.scales" in weights,
            )
        model.load_weights(list(weights.items()), strict=True)
        if adapter_dir is not None:
            from .lora import load_adapters

            cfg = json.loads((Path(adapter_dir) / "config.json").read_text(encoding="utf-8"))
            load_adapters(model, Path(adapter_dir) / "adapters.safetensors", cfg)
        self.name = name
        model.eval()
        mx.eval(model.parameters())
        self._mx = mx
        self.model = model
        self.clear_cache = clear_cache
        if clear_cache:
            mx.set_cache_limit(0)  # free buffers as soon as they are released
        self.id2label = {int(k): v for k, v in cfg["id2label"].items()}

    def logits(self, ids: np.ndarray) -> np.ndarray:
        mx = self._mx
        x = mx.array(np.ascontiguousarray(ids, dtype=np.int32)[None, :])
        out = self.model(x).astype(mx.float32)
        mx.eval(out)
        result = np.array(out[0], dtype=np.float32)
        del out
        if self.clear_cache:
            mx.clear_cache()
        return result


MODEL_DIRS = {"onnx_q4": "pf-onnx-q4", "mlx_8bit": "pf-mlx-8bit", "onnx_fp32_ref": "pf-onnx-fp32-ref",
              "mlx_8bit_ft1": "pf-mlx-8bit-ft1"}
ADAPTER_BASE = {"mlx_8bit_ft1": "mlx_8bit"}  # fine-tuned artifact -> the base it adapts


def load_backend(
    name: str, models_root: str | Path, *, threads: int | None = None, clear_cache: bool = False,
    arena: bool = True,
) -> Backend:
    """Load a backend by name, after verifying its files and the tokenizer file
    against models/MANIFEST.json. `onnx_fp32_ref` is the dev-only reference."""
    if name not in MODEL_DIRS:
        raise ValueError(f"unknown backend {name!r}")
    model_dir = verify(models_root, MODEL_DIRS[name])
    verify(models_root, "o200k-bpe")
    if name == "onnx_q4":
        return OnnxBackend(model_dir, "model_q4.onnx", threads=threads, name=name, arena=arena)
    if name == "onnx_fp32_ref":
        return OnnxBackend(model_dir, "model.onnx", threads=threads, name=name)
    if name in ADAPTER_BASE:
        base_dir = verify(models_root, MODEL_DIRS[ADAPTER_BASE[name]])
        return MlxBackend(base_dir, clear_cache=clear_cache, adapter_dir=model_dir, name=name)
    return MlxBackend(model_dir, clear_cache=clear_cache)
