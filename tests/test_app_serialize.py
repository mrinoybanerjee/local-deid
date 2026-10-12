"""Engine objects survive the worker pipe and the vault unchanged."""

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from localdeid.app.serialize import (FormatError, restored_from_dict, restored_to_dict, result_from_dict,
                                     result_to_dict, settings_from_dict, settings_to_dict)
from localdeid.pipeline import Settings, deidentify
from localdeid.restore import copy_block, restore
from localdeid.roster import Child, Related

NOTE = ('Name: Mateo Ruiz   DOB: 03/14/2021\nMother: Lucía Ruiz (919) 555-0142\n'
        'Mateo produced /k/ in 8/10 trials at Little Sprouts Daycare. Su mamá dijo "sol".')
KIDS = [Child("Mateo", ["Ruiz"], ["Teo"], [Related("Sol", "sister")], ["Little Sprouts"], ["sol"])]


def _roundtrip(d):
    return json.loads(json.dumps(d, ensure_ascii=False))


def test_result_roundtrip_is_exact():
    r = deidentify(NOTE, Settings(children=KIDS), ref="K7F2")
    copy_block(r)  # adds a copied context
    back = result_from_dict(_roundtrip(result_to_dict(r)))
    assert back == r
    assert restore(copy_block(back), back).text == r.source


def test_settings_roundtrip_is_exact():
    s = Settings(children=KIDS, extra_labels={"Tutor": "PARENT"}, keep_names=["Valeria López"],
                 employer_names=["Carolina Speech"])
    assert settings_from_dict(_roundtrip(settings_to_dict(s))) == s


def test_restored_roundtrip():
    r = deidentify(NOTE, Settings(children=KIDS), ref="K7F2")
    rs = restore(copy_block(r) + "\n[CHILD_9]", r)
    back = restored_from_dict(_roundtrip(restored_to_dict(rs)))
    assert back == rs and back.blocked


@pytest.mark.parametrize("bad", [None, [], {"source": "x"}, {"items": "no"}])
def test_bad_result_data_fails_loudly(bad):
    with pytest.raises((FormatError, KeyError)):
        result_from_dict(bad)


def test_unknown_field_is_rejected():
    d = result_to_dict(deidentify("Hi Mateo.", ref="K7F2"))
    d["evil"] = 1
    with pytest.raises(FormatError):
        result_from_dict(d)


@settings(max_examples=60, deadline=None)
@given(st.text(alphabet=st.sampled_from(list("ab MateoRuiz 0123/-:\n.'\"[]⟦⟧ñé")), max_size=120))
def test_any_text_result_roundtrips(text):
    r = deidentify(text, ref="AB12")
    assert result_from_dict(_roundtrip(result_to_dict(r))) == r
