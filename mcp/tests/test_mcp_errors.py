from openjev_mcp import errors
from openjev_mcp.errors import ToolError, invalid_input

ALWAYS = {"code", "message", "http_status", "path", "retryable", "retry_after_s", "request_id"}


def test_to_dict_always_keys():
    d = ToolError("OJ_SERVER", "boom").to_dict()
    assert set(d) == ALWAYS
    assert d == {"code": "OJ_SERVER", "message": "boom", "http_status": None, "path": None,
                 "retryable": False, "retry_after_s": None, "request_id": None}


def test_hint_only_when_set():
    assert "hint" not in ToolError("OJ_AUTH", "m").to_dict()
    assert ToolError("OJ_AUTH", "m", hint="set the key").to_dict()["hint"] == "set the key"


def test_server_detail_only_when_set_including_none():
    assert "server_detail" not in ToolError("OJ_AUTH", "m").to_dict()
    assert ToolError("OJ_AUTH", "m", server_detail=None).to_dict()["server_detail"] is None
    assert ToolError("OJ_AUTH", "m", server_detail={"a": [1]}).to_dict()["server_detail"] == {"a": [1]}
    assert ToolError("OJ_AUTH", "m", server_detail="").to_dict()["server_detail"] == ""


def test_full_fields():
    e = ToolError("OJ_OVERLOADED", "busy", 529, path="questions.q", retryable=True, retry_after_s=1.0,
                  request_id="req_1")
    assert e.to_dict() == {"code": "OJ_OVERLOADED", "message": "busy", "http_status": 529, "path": "questions.q",
                           "retryable": True, "retry_after_s": 1.0, "request_id": "req_1"}


def test_invalid_input():
    e = invalid_input("state", "state is required", hint="pass a string")
    assert (e.code, e.path, e.message, e.hint, e.retryable) == ("OJ_INVALID_INPUT", "state", "state is required",
                                                               "pass a string", False)
    assert invalid_input(None, "x").hint is None


def test_is_exception():
    e = ToolError("OJ_INTERNAL", "internal error in openjev-mcp: ValueError")
    assert isinstance(e, Exception) and "OJ_INTERNAL" in str(e)
    assert e != ToolError("OJ_INTERNAL", "internal error in openjev-mcp: ValueError")   # eq=False


def test_code_sets():
    assert "OJ_INTERNAL" in errors.CODES and len(errors.CODES) == 19
    assert errors.RETRYABLE <= errors.CODES and errors.RETRY_ONCE <= errors.CODES
    assert not errors.RETRYABLE & errors.RETRY_ONCE
