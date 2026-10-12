import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")


def test_meminfo_tracks_allocation_and_peak():
    from localdeid.sysmem import meminfo

    before = meminfo()
    assert before.footprint > 0 and before.footprint_peak >= before.footprint
    block = bytearray(300 * 1024 * 1024)
    block[:: 4096] = b"\x01" * len(block[:: 4096])  # touch every page
    after = meminfo()
    assert after.footprint - before.footprint > 250 * 1024 * 1024
    assert after.footprint_peak >= after.footprint
    del block
    assert meminfo().footprint_peak >= after.footprint


def test_peak_footprint_of_a_pid_matches_the_task_peak():
    import os
    import sys

    import pytest

    if sys.platform != "darwin":
        pytest.skip("macOS only")
    from localdeid.sysmem import meminfo, peak_footprint

    blob = bytearray(64 << 20)
    blob[::4096] = b"\1" * len(blob[::4096])  # touch 64 MiB
    other = peak_footprint(os.getpid())
    mine = meminfo().footprint_peak
    assert other is not None and abs(other - mine) < (16 << 20)
    assert peak_footprint(999_999_999) is None  # no such process
