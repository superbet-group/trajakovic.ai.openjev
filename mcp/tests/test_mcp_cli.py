"""`openjev check` and `openjev filter`: exit codes, stdout and stderr over stub engines, plus the process."""
from __future__ import annotations

import io
import json
import os
import subprocess

import httpx
import pytest
import stubs

from openjev_mcp import cli

HERE = os.path.dirname(__file__)
OPENJEV = os.path.join(stubs.REPO, ".venv", "bin", "openjev")
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith(("OPENJEV_", "ANTHROPIC_"))}
GATE_INPUTS = {"task": "Fix off-by-one", "timeline": ["1. Edit src/pager.py"], "final_message": "Fixed, ready to merge."}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("OPENJEV_MCP_RETRIES", "0")


def served(fn):
    engine = stubs.StubEngine(answers=lambda qs, state, opts: {q: fn(q, s) for q, s in qs.items()})
    return engine, stubs.asgi_transport(stubs.openjev_app(engine=engine))


def gate_engine(verified, claims, nxt):
    def fn(q, spec):
        if q in ("verified", "claims"):
            return {"type": "noul", "noul": verified if q == "verified" else claims}
        labels = list(spec["criteria"])
        return {"type": "choice", "choice": nxt, "confidence": 0.9,
                "probabilities": {k: (0.9 if k == nxt else 0.05) for k in labels}}
    return served(fn)


def run(monkeypatch, capsys, argv, stdin="", transport=None):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = cli.main(argv, transport=transport)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def inputs_file(tmp_path, data=GATE_INPUTS):
    p = tmp_path / "in.json"
    p.write_text(json.dumps(data))
    return str(p)


# ---- check ----

def test_check_least_severe_decision_exits_0(monkeypatch, capsys, tmp_path):
    _, transport = gate_engine(0.98, 0.9, "allow_stop")
    code, out, _ = run(monkeypatch, capsys, ["check", "done_gate", "--inputs", inputs_file(tmp_path)], transport=transport)
    body = json.loads(out)
    assert code == 0 and body["recipe"] == "done_gate" and body["decision"] == "allow_stop" and body["degraded"] is False


def test_check_other_decision_exits_1(monkeypatch, capsys, tmp_path):
    _, transport = gate_engine(0.02, 0.99, "continue")
    code, out, _ = run(monkeypatch, capsys, ["check", "done_gate", "--inputs", inputs_file(tmp_path)], transport=transport)
    assert code == 1 and json.loads(out)["decision"] == "block"


def test_check_rule_decided_makes_no_request(monkeypatch, capsys, tmp_path):
    transport = stubs.fail_on_request_transport()
    data = {"task": "t", "command": "rm -rf ~", "context": ""}
    code, out, _ = run(monkeypatch, capsys, ["check", "command_gate", "--inputs", inputs_file(tmp_path, data)], transport=transport)
    assert code == 1 and json.loads(out)["decision"] == "deny" and transport.requests == []


def test_check_profile_and_unattended_reach_the_recipe(monkeypatch, capsys, tmp_path):
    engine, transport = gate_engine(0.98, 0.9, "allow_stop")
    data = {"text": "plain page", "source": "WebFetch result"}
    code, out, _ = run(monkeypatch, capsys, ["check", "injection_screen", "--inputs", inputs_file(tmp_path, data),
                                             "--profile", "exec", "--unattended"], transport=transport)
    assert json.loads(out)["recipe"] == "injection_screen"
    code, _, err = run(monkeypatch, capsys, ["check", "injection_screen", "--inputs", inputs_file(tmp_path, data),
                                             "--profile", "nope"], transport=transport)
    assert code == 2 and "profile" in err


def test_check_degraded_exits_2_but_still_prints(monkeypatch, capsys, tmp_path):
    transport = stubs.fault_transport(exc=httpx.ConnectError("refused"))
    code, out, _ = run(monkeypatch, capsys, ["check", "done_gate", "--inputs", inputs_file(tmp_path)], transport=transport)
    assert code == 2 and json.loads(out)["degraded"] is True


@pytest.mark.parametrize("argv,stdin,needle", [
    (["check", "no_such_recipe"], "", "no_such_recipe"),
    (["check", "done_gate", "--inputs", "/nonexistent.json"], "", "nonexistent"),
    (["check", "done_gate", "--inputs", "-"], "{nope", "Expecting"),
    (["check", "done_gate", "--inputs", "-"], "[1]", "JSON object"),
    (["check", "done_gate", "--inputs", "-"], '{"task": "x"}', "timeline")])
def test_check_errors_exit_2(monkeypatch, capsys, argv, stdin, needle):
    code, out, err = run(monkeypatch, capsys, argv, stdin, transport=stubs.fail_on_request_transport())
    assert code == 2 and out == "" and needle in err


def test_check_inputs_from_stdin(monkeypatch, capsys):
    _, transport = gate_engine(0.98, 0.9, "allow_stop")
    code, out, _ = run(monkeypatch, capsys, ["check", "done_gate", "--inputs", "-"], json.dumps(GATE_INPUTS), transport)
    assert code == 0 and json.loads(out)["decision"] == "allow_stop"


# ---- filter ----

LOG = "ok line\nERROR disk full\n\nretry succeeded\n"


def line_engine(hot):
    return served(lambda q, spec: {"type": "noul", "noul": 0.95 if q in hot else 0.03})


def test_filter_keeps_lines_and_exits_1(monkeypatch, capsys):
    engine, transport = line_engine({"L2"})
    code, out, _ = run(monkeypatch, capsys, ["filter", "--gt", "0.7", "real error needing action"], LOG, transport)
    assert (code, out) == (1, "ERROR disk full\n")
    assert "L1" in engine.calls[0]["state"] and "retry succeeded" in engine.calls[0]["state"]
    assert "real error needing action" in json.dumps(engine.calls[0]["questions"])


def test_filter_nothing_kept_exits_0(monkeypatch, capsys):
    _, transport = line_engine(set())
    assert run(monkeypatch, capsys, ["filter", "real error"], LOG, transport)[:2] == (0, "")


def test_filter_threshold_decides(monkeypatch, capsys):
    _, transport = served(lambda q, spec: {"type": "noul", "noul": 0.75})
    assert run(monkeypatch, capsys, ["filter", "--gt", "0.9", "x"], "a\nb\n", transport)[0] == 0
    assert run(monkeypatch, capsys, ["filter", "--gt", "0.5", "x"], "a\nb\n", transport)[:2] == (1, "a\nb\n")


def test_filter_task_and_explicit_id_criterion(monkeypatch, capsys):
    engine, transport = line_engine({"L1"})
    run(monkeypatch, capsys, ["filter", "--task", "triage logs", "Is {id} a failure?"], "x\n", transport)
    assert engine.calls[0]["state"].startswith("TASK: triage logs")
    assert "Is L1 a failure?" in json.dumps(engine.calls[0]["questions"])


def test_filter_empty_stdin_exits_0_without_a_request(monkeypatch, capsys):
    transport = stubs.fail_on_request_transport()
    assert run(monkeypatch, capsys, ["filter", "x"], "\n  \n", transport)[:2] == (0, "")
    assert transport.requests == []


def test_filter_error_exits_2(monkeypatch, capsys):
    transport = stubs.fault_transport(exc=httpx.ConnectError("refused"))
    code, out, err = run(monkeypatch, capsys, ["filter", "x"], "a\n", transport)
    assert code == 2 and out == "" and "OJ_UNREACHABLE" in err


def test_filter_low_gt_keeps_the_drop_band_valid(monkeypatch, capsys):
    _, transport = line_engine({"L1"})
    assert run(monkeypatch, capsys, ["filter", "--gt", "0.1", "x"], "a\n", transport)[:2] == (1, "a\n")


# ---- misc and the process ----

def test_version_and_help(monkeypatch, capsys):
    assert run(monkeypatch, capsys, ["--version"])[:2] == (0, f"openjev-mcp {cli.__version__}\n")
    assert run(monkeypatch, capsys, ["version"])[0] == 0
    code, out, _ = run(monkeypatch, capsys, [])
    assert code == 0 and "check" in out and "filter" in out


def test_process_exit_codes():
    dead = {**CLEAN_ENV, "OPENJEV_BASE_URL": "http://127.0.0.1:9", "OPENJEV_MCP_RETRIES": "0"}
    res = subprocess.run([OPENJEV, "filter", "x"], input="a\n", capture_output=True, text=True, env=dead, timeout=30)
    assert res.returncode == 2 and "OJ_UNREACHABLE" in res.stderr
    res = subprocess.run([OPENJEV, "check", "command_gate", "--inputs", "-"], capture_output=True, text=True, env=dead,
                         input=json.dumps({"task": "t", "command": "git status", "context": ""}), timeout=30)
    assert res.returncode == 0 and json.loads(res.stdout)["decision"] == "allow"
    res = subprocess.run([OPENJEV, "check"], capture_output=True, text=True, env=dead)
    assert res.returncode == 2                                      # argparse usage error


def test_cli_import_is_light():
    code = ("import sys; import openjev_mcp.cli; "
            "bad = [m for m in ('mcp', 'httpx', 'openjev_mcp.http') if m in sys.modules]; sys.exit(1 if bad else 0)")
    assert subprocess.run([os.path.join(stubs.REPO, ".venv", "bin", "python"), "-c", code]).returncode == 0
