"""Low-rank adapters and the extended label head for the fine-tuned Privacy Filter
(DECISIONS 26). Training (`tools/finetune_pf.py`) and inference (`MlxBackend`)
share this file, so the tuned model runs the same code it was trained with.

The base 8-bit weights stay unchanged and keep their pinned hash. The tuned
artifact adds `adapters.safetensors` (LoRA matrices and the new head) and a
`config.json` with the extended label set and the adapter settings.
"""

from __future__ import annotations

import math
from pathlib import Path

import mlx.core as mx
import mlx.nn as nn

ORG_LABELS = ("B-private_organization", "I-private_organization", "E-private_organization",
              "S-private_organization")


class LoRALinear(nn.Module):
    """y = base(x) + scale * (x A) B. The base layer (quantized or not) stays frozen."""

    def __init__(self, base: nn.Module, rank: int, alpha: float, *, seed_key: mx.array | None = None):
        super().__init__()
        self.base = base
        self.base.freeze()
        if hasattr(base, "scales"):
            in_f = base.scales.shape[-1] * base.group_size
        else:
            in_f = base.weight.shape[1]
        out_f = base.weight.shape[0]
        self.a = mx.random.normal((in_f, rank), key=seed_key) * (1.0 / math.sqrt(in_f))
        self.b = mx.zeros((rank, out_f))
        self.scale = float(alpha) / float(rank)

    def __call__(self, x: mx.array) -> mx.array:
        y = self.base(x)
        low = (x.astype(mx.float32) @ self.a) @ self.b
        return y + (low * self.scale).astype(y.dtype)


def _get(model: nn.Module, path: str) -> nn.Module:
    obj = model
    for part in path.split("."):
        obj = obj[int(part)] if part.isdigit() else getattr(obj, part)
    return obj


def _set(model: nn.Module, path: str, value: nn.Module) -> None:
    parent, last = path.rsplit(".", 1)
    setattr(_get(model, parent), last, value)


def target_paths(model: nn.Module, targets: list[str]) -> list[str]:
    return [f"block.{i}.{t}" for i in range(len(model.block)) for t in targets]


def attach(model: nn.Module, *, rank: int, alpha: float, targets: list[str], num_labels: int,
           seed: int = 0) -> None:
    """Freeze the model, wrap the target layers in LoRA, and replace the head with a
    float head of `num_labels` rows whose first rows copy the base head."""
    model.freeze()
    key = mx.random.key(seed)
    for path in target_paths(model, targets):
        key, sub = mx.random.split(key)
        _set(model, path, LoRALinear(_get(model, path), rank, alpha, seed_key=sub))
    old = model.unembedding
    if hasattr(old, "scales"):
        w = mx.dequantize(old.weight, old.scales, old.biases, group_size=old.group_size, bits=old.bits)
    else:
        w = old.weight
    w = w.astype(mx.float32)
    if num_labels < w.shape[0]:
        raise ValueError("the new head cannot have fewer labels than the base head")
    key, sub = mx.random.split(key)
    extra = mx.random.normal((num_labels - w.shape[0], w.shape[1]), key=sub) * 0.01
    head = nn.Linear(w.shape[1], num_labels, bias=False)
    head.weight = mx.concatenate([w, extra], axis=0)
    model.unembedding = head


def adapter_state(model: nn.Module, targets: list[str]) -> dict[str, mx.array]:
    out = {}
    for path in target_paths(model, targets):
        layer = _get(model, path)
        out[f"{path}.a"], out[f"{path}.b"] = layer.a, layer.b
    out["unembedding.weight"] = model.unembedding.weight
    return out


def load_adapters(model: nn.Module, adapter_file: str | Path, cfg: dict) -> None:
    """Rebuild the tuned model from the base model and `adapters.safetensors`."""
    lora = cfg["_lora"]
    targets = list(lora["targets"])
    attach(model, rank=int(lora["rank"]), alpha=float(lora["alpha"]), targets=targets,
           num_labels=int(cfg["num_labels"]))
    weights = dict(mx.load(str(adapter_file)))
    want = set(adapter_state(model, targets))
    if set(weights) != want:
        raise ValueError(f"adapter file keys differ: missing {sorted(want - set(weights))[:3]}, "
                         f"extra {sorted(set(weights) - want)[:3]}")
    for k, v in weights.items():
        path, name = k.rsplit(".", 1)
        layer = _get(model, path)
        if getattr(layer, name).shape != v.shape:
            raise ValueError(f"{k}: shape {v.shape} differs from {getattr(layer, name).shape}")
        setattr(layer, name, v.astype(mx.float32))
    model.freeze()
