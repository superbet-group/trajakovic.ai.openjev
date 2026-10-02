"""claude_live gate, markers and session fixtures. Never part of `mise run test`: collection needs OPENJEV_CLAUDE_LIVE=1 (or OJ_CLAUDE_SELFTEST=1 for the no-model self-tests)."""
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
LIVE = os.environ.get("OPENJEV_CLAUDE_LIVE") == "1"
SELFTEST = os.environ.get("OJ_CLAUDE_SELFTEST") == "1"

for p in (str(HERE), str(HERE.parents[0] / "live")):
    if p not in sys.path:
        sys.path.insert(0, p)

if not (LIVE or SELFTEST):
    collect_ignore_glob = ["test_*.py"]
elif not LIVE:
    collect_ignore_glob = [f.name for f in HERE.glob("test_*.py") if not f.name.startswith("test_cl_selftest")]

MARKERS = ("claude_live: live Claude Code suite (spends subscription usage; needs OPENJEV_CLAUDE_LIVE=1)", "strong: strong-tier model case",
           "hooks: Claude Code hook case", "skills: skills plugin case", "slow: timeout above 180 s",
           *(f"g{i:02d}: catalogue group {i}" for i in range(1, 11)))


def pytest_configure(config):
    for m in MARKERS:
        config.addinivalue_line("markers", m)


def pytest_collection_modifyitems(config, items):
    for item in items:
        if HERE not in Path(str(item.path)).parents:
            continue
        item.add_marker(pytest.mark.claude_live)
        case = getattr(getattr(item, "callspec", None), "params", {}).get("case")
        if case is None or not hasattr(case, "group"):
            continue
        item.add_marker(getattr(pytest.mark, case.group))
        for name, on in (("strong", case.tier == "strong"), ("hooks", bool(case.hooks)), ("skills", case.skills), ("slow", case.timeout_s > 180)):
            if on:
                item.add_marker(getattr(pytest.mark, name))
        if case.xfail:
            item.add_marker(pytest.mark.xfail(reason=case.xfail, strict=False))


@pytest.fixture(scope="session")
def live_env():
    import shutil
    import urllib.request
    import cl_env
    try:
        urllib.request.urlopen(cl_env.BASE_URL + "/health", timeout=5).read()
    except Exception as e:
        pytest.skip(f"OpenJev not reachable at {cl_env.BASE_URL}: {e}; start it yourself, the suite never starts or stops it")
    if not shutil.which(cl_env.CLAUDE_BIN):
        pytest.skip(f"{cl_env.CLAUDE_BIN} not found on PATH (OJ_CLAUDE_BIN)")
    return cl_env.BASE_URL


@pytest.fixture(scope="session")
def work(live_env):
    import shutil
    import cl_env
    import cl_results
    w = (cl_results.run_root() / f"work-{os.getpid()}").resolve()
    (w / "data").mkdir(parents=True, exist_ok=True)
    srcs = [cl_env.REPO / "tests/data/hotdog.jpg", *sorted((cl_env.REPO / "docs/mcp-skill-spec/tests/data").glob("ui22_*.png"))]
    for s in srcs:
        if s.exists():
            shutil.copy2(s, w / "data" / s.name)
    return w


@pytest.fixture(scope="session")
def instances(work):
    from cl_servers import Instances
    inst = Instances(work)
    yield inst
    inst.stop_all()


@pytest.fixture(scope="session")
def mcp(instances):
    return instances.get


@pytest.fixture
def case_ctx(live_env, work, instances):
    import shutil
    import cl_env
    import cl_results
    from cl_cases import Ctx

    def factory(case, attempt=1):
        tag = f"{case.id}-a{attempt}"
        cwd = (work / "cwd" / tag)
        shutil.rmtree(cwd, ignore_errors=True)
        cwd.mkdir(parents=True)
        run_dir = cl_results.run_root() / "runs" / tag
        shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True)
        return Ctx(case=case, attempt=attempt, run_id=cl_env.run_id(), work=work, cwd=cwd.resolve(), run_dir=run_dir, data=work / "data",
                   fixtures=cl_env.FIXTURES, repo=cl_env.REPO, base_url=live_env, instances=instances)
    return factory
