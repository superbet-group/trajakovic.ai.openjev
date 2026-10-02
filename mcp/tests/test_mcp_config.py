import os

import pytest

from openjev_mcp.config import Config, ConfigError, load_config


def test_defaults(tmp_path):
    c = load_config({}, cwd=str(tmp_path))
    assert c.base_url == "http://127.0.0.1:8080"
    assert (c.model, c.timeout_ms, c.retries, c.max_inflight_batch) == ("openjev-latest", 30000, 2, 4)
    assert (c.transport, c.host, c.port) == ("http", "127.0.0.1", 8100)
    assert (c.band_no_at, c.band_yes_at) == (0.2, 0.8)
    assert c.toolsets == "all" and c.log_path is None and not c.log_states and not c.debug
    assert c.max_body_bytes == 4 * 1024 * 1024
    assert c.allowed_hosts == () and c.allowed_origins == ()
    assert c.is_loopback


def test_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("OPENJEV_MCP_PORT", "8123")
    assert load_config().port == 8123


def test_model_env_is_openjev_mcp_model_only(tmp_path):
    assert load_config({"OPENJEV_MODEL": "other"}, cwd=str(tmp_path)).model == "openjev-latest"
    assert load_config({"OPENJEV_MCP_MODEL": "m1", "OPENJEV_MODEL": "other"}, cwd=str(tmp_path)).model == "m1"


def test_ui_url_ignored(tmp_path):
    c = load_config({"OPENJEV_URL": "http://elsewhere:1"}, cwd=str(tmp_path))
    assert c.base_url == "http://127.0.0.1:8080"


def test_base_url_stripped_and_credentials(tmp_path):
    c = load_config({"OPENJEV_BASE_URL": "http://h:9/", "OPENJEV_API_KEY": "k", "OPENJEV_ORIGIN_SECRET": "o",
                     "OPENJEV_MCP_TOKEN": "t"}, cwd=str(tmp_path))
    assert (c.base_url, c.api_key, c.origin_secret, c.token) == ("http://h:9", "k", "o", "t")


def test_kwargs_beat_env(tmp_path):
    env = {"OPENJEV_MCP_TRANSPORT": "http", "OPENJEV_MCP_HOST": "localhost", "OPENJEV_MCP_PORT": "9000"}
    c = load_config(env, transport="stdio", host="::1", port=9001, cwd=str(tmp_path))
    assert (c.transport, c.host, c.port) == ("stdio", "::1", 9001)


def test_max_inflight_per_transport(tmp_path):
    cwd = str(tmp_path)
    assert load_config({}, transport="stdio", cwd=cwd).max_inflight == 1
    assert load_config({}, transport="http", cwd=cwd).max_inflight == 4
    assert load_config({"OPENJEV_MCP_TRANSPORT": "stdio"}, cwd=cwd).max_inflight == 1
    assert load_config({"OPENJEV_MCP_MAX_INFLIGHT": "7"}, transport="stdio", cwd=cwd).max_inflight == 7


def test_band_parsed(tmp_path):
    c = load_config({"OPENJEV_MCP_BAND": "0.1, 0.9"}, cwd=str(tmp_path))
    assert (c.band_no_at, c.band_yes_at) == (0.1, 0.9)


def test_roots_order(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    c = load_config({"OPENJEV_MCP_ROOTS": f"{a}:{b}"}, cwd=str(tmp_path))
    assert c.roots == (os.path.realpath(tmp_path), os.path.realpath(a), os.path.realpath(b))
    assert load_config({}, cwd=str(tmp_path)).roots == (os.path.realpath(tmp_path),)


def test_lists_and_flags(tmp_path):
    c = load_config({"OPENJEV_MCP_ALLOWED_HOSTS": "a:*, b:80,", "OPENJEV_MCP_ALLOWED_ORIGINS": "http://x",
                     "OPENJEV_MCP_LOG": "/tmp/l.jsonl", "OPENJEV_MCP_LOG_STATES": "1", "OPENJEV_MCP_DEBUG": "1",
                     "OPENJEV_MCP_TOOLSETS": "core"}, cwd=str(tmp_path))
    assert c.allowed_hosts == ("a:*", "b:80") and c.allowed_origins == ("http://x",)
    assert c.log_path == "/tmp/l.jsonl" and c.log_states and c.debug and c.toolsets == "core"


def test_is_loopback():
    assert all(Config(host=h).is_loopback for h in ("127.0.0.1", "localhost", "::1"))
    assert not Config(host="0.0.0.0").is_loopback


@pytest.mark.parametrize("env,name", [
    ({"OPENJEV_MCP_PORT": "x"}, "OPENJEV_MCP_PORT"),
    ({"OPENJEV_MCP_PORT": "70000"}, "OPENJEV_MCP_PORT"),
    ({"OPENJEV_MCP_TIMEOUT_MS": "10"}, "OPENJEV_MCP_TIMEOUT_MS"),
    ({"OPENJEV_MCP_TIMEOUT_MS": "600001"}, "OPENJEV_MCP_TIMEOUT_MS"),
    ({"OPENJEV_MCP_MAX_INFLIGHT": "0"}, "OPENJEV_MCP_MAX_INFLIGHT"),
    ({"OPENJEV_MCP_MAX_INFLIGHT_BATCH": "5"}, "OPENJEV_MCP_MAX_INFLIGHT_BATCH"),
    ({"OPENJEV_MCP_RETRIES": "-1"}, "OPENJEV_MCP_RETRIES"),
    ({"OPENJEV_MCP_TOOLSETS": "some"}, "OPENJEV_MCP_TOOLSETS"),
    ({"OPENJEV_MCP_TRANSPORT": "ws"}, "OPENJEV_MCP_TRANSPORT"),
    ({"OPENJEV_MCP_BAND": "0.8,0.2"}, "OPENJEV_MCP_BAND"),
    ({"OPENJEV_MCP_BAND": "0.5"}, "OPENJEV_MCP_BAND"),
    ({"OPENJEV_MCP_BAND": "0.2,1.5"}, "OPENJEV_MCP_BAND"),
    ({"OPENJEV_MCP_BAND": "a,b"}, "OPENJEV_MCP_BAND"),
    ({"OPENJEV_MCP_LOG_STATES": "maybe"}, "OPENJEV_MCP_LOG_STATES"),
    ({"OPENJEV_MCP_DEBUG": "2"}, "OPENJEV_MCP_DEBUG"),
    ({"OPENJEV_MCP_MAX_BODY_BYTES": "0"}, "OPENJEV_MCP_MAX_BODY_BYTES"),
])
def test_config_error_names_variable(env, name, tmp_path):
    with pytest.raises(ConfigError, match=name):
        load_config(env, cwd=str(tmp_path))


def test_repr_hides_secrets():
    text = repr(Config(api_key="sk-secret", origin_secret="org-secret", token="tok-secret"))
    assert "secret" not in text
    assert "base_url" in text


def test_phase23_defaults(tmp_path):
    c = load_config({}, cwd=str(tmp_path))
    assert (c.recipes_dir, c.routing, c.fetch, c.tasks, c.chat_model) == (None, True, False, False, "diffusiongemma-26b")
    assert c.audit_dir == os.path.join(os.path.realpath(tmp_path), "openjev-audits")


def test_phase23_values(tmp_path):
    (tmp_path / "rec").mkdir()
    env = {"OPENJEV_MCP_RECIPES": str(tmp_path / "rec"), "OPENJEV_MCP_ROUTING": "off", "OPENJEV_MCP_FETCH": "on",
           "OPENJEV_MCP_TASKS": "1", "OPENJEV_MCP_AUDIT_DIR": "out/a", "OPENJEV_MCP_CHAT_MODEL": "m"}
    c = load_config(env, cwd=str(tmp_path))
    assert c.recipes_dir == os.path.realpath(tmp_path / "rec")
    assert (c.routing, c.fetch, c.tasks, c.chat_model) == (False, True, True, "m")
    assert c.audit_dir == os.path.join(os.path.realpath(tmp_path), "out", "a")
    assert load_config({"OPENJEV_MCP_AUDIT_DIR": str(tmp_path / "x")}, cwd=str(tmp_path)).audit_dir == str(tmp_path / "x")


@pytest.mark.parametrize("env,name", [
    ({"OPENJEV_MCP_RECIPES": "/nonexistent-dir-xyz"}, "OPENJEV_MCP_RECIPES"),
    ({"OPENJEV_MCP_ROUTING": "maybe"}, "OPENJEV_MCP_ROUTING"),
    ({"OPENJEV_MCP_FETCH": "2"}, "OPENJEV_MCP_FETCH"),
    ({"OPENJEV_MCP_TASKS": "x"}, "OPENJEV_MCP_TASKS"),
])
def test_phase23_bad_values(env, name, tmp_path):
    with pytest.raises(ConfigError, match=name):
        load_config(env, cwd=str(tmp_path))
