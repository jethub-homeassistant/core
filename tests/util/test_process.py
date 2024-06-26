"""Test process util."""

import os
import subprocess

import pytest

from homeassistant.util import process


async def test_kill_process() -> None:
    """Test killing a process."""
    sleeper = subprocess.Popen(  # noqa: S602 # shell by design
        "sleep 1000",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid = sleeper.pid

    assert os.kill(pid, 0) is None

    process.kill_subprocess(sleeper)

    with pytest.raises(OSError):
        os.kill(pid, 0)
