"""Carbon hotkeys: registration, dispatch, other keys pass, unregister."""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")


@pytest.fixture
def hotkeys():
    from AppKit import NSApplication

    from localdeid.app.hotkeys import HotKeys

    NSApplication.sharedApplication()
    h = HotKeys()
    yield h
    h.close()


def test_registered_keys_fire_and_others_pass(hotkeys):
    hits = []
    assert hotkeys.register({2: lambda: hits.append("D"), 15: lambda: hits.append("R")}) == []
    assert hotkeys.simulate(2) == 0 and hotkeys.simulate(15) == 0
    assert hotkeys.simulate(3) == -9874  # eventNotHandledErr: not ours
    assert hits == ["D", "R"]


def test_an_exception_in_an_action_does_not_reach_carbon(hotkeys):
    def boom():
        raise RuntimeError("x")

    assert hotkeys.register({2: boom}) == []
    assert hotkeys.simulate(2) == 0


def test_close_unregisters(hotkeys):
    hits = []
    hotkeys.register({2: lambda: hits.append(1)})
    hotkeys.close()
    assert hotkeys.simulate(2) == -9874 and hits == []
