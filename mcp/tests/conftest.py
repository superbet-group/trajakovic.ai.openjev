import os

import pytest


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False, help="run tests marked live (real model)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live") or os.environ.get("OPENJEV_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: pass --live or set OPENJEV_LIVE=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def anyio_backend():
    return "asyncio"
