"""Engine objects <-> plain JSON data, for the worker pipe and the vault.

Every field round-trips exactly: `from_dict(to_dict(r)) == r` for Result,
Settings, and Restored (tests/test_app_serialize.py). Unknown keys are
rejected, so a corrupted vault file or a confused worker fails loudly.
"""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

from ..pipeline import CheckFailure, Instance, Item, Result, Settings
from ..protect import Protected
from ..restore import Issue, Restored
from ..roster import Child, Related


class FormatError(ValueError):
    pass


def _build(cls, data: dict[str, Any]):
    if not isinstance(data, dict):
        raise FormatError(f"{cls.__name__}: expected an object")
    names = {f.name for f in fields(cls)}
    extra = set(data) - names
    if extra:
        raise FormatError(f"{cls.__name__}: unknown fields {sorted(extra)}")
    return cls(**data)


def result_to_dict(r: Result) -> dict[str, Any]:
    d = asdict(r)
    d["protected"] = [asdict(p) for p in r.protected]
    return d


def result_from_dict(d: dict[str, Any]) -> Result:
    if not isinstance(d, dict):
        raise FormatError("Result: expected an object")
    d = dict(d)
    try:
        d["items"] = [_item(x) for x in d["items"]]
        d["protected"] = [_build(Protected, x) for x in d["protected"]]
        d["instances"] = [_build(Instance, x) for x in d["instances"]]
        d["failures"] = [_build(CheckFailure, x) for x in d["failures"]]
        d["canonical"] = dict(d["canonical"])
        d["copied_contexts"] = list(d.get("copied_contexts", []))
    except FormatError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise FormatError(f"Result: {exc}") from exc
    return _build(Result, d)


def _item(x: dict[str, Any]) -> Item:
    x = dict(x)
    x["sources"] = tuple(x.get("sources", ()))
    x["notes"] = tuple(x.get("notes", ()))
    return _build(Item, x)


def settings_to_dict(s: Settings) -> dict[str, Any]:
    return asdict(s)


def settings_from_dict(d: dict[str, Any]) -> Settings:
    if not isinstance(d, dict):
        raise FormatError("Settings: expected an object")
    d = dict(d)
    d["children"] = [child_from_dict(c) for c in d.get("children", [])]
    for k in ("keep_names", "employer_names"):
        d[k] = [str(v) for v in d.get(k, [])]
    d["extra_labels"] = {str(k): str(v) for k, v in dict(d.get("extra_labels", {})).items()}
    return _build(Settings, d)


def child_from_dict(d: dict[str, Any]) -> Child:
    if not isinstance(d, dict):
        raise FormatError("Child: expected an object")
    d = dict(d)
    d["related"] = [_build(Related, r) for r in d.get("related", [])]
    return _build(Child, d)


def restored_to_dict(r: Restored) -> dict[str, Any]:
    return {"text": r.text, "issues": [asdict(i) for i in r.issues], "spans": [list(s) for s in r.spans],
            "blocked": r.blocked}


def restored_from_dict(d: dict[str, Any]) -> Restored:
    return Restored(d["text"], [_build(Issue, i) for i in d["issues"]], [tuple(s) for s in d["spans"]])
