from __future__ import annotations

import json
import os

from mcp import types

from openjev_mcp.envelope import TEXT_ANNOTATIONS, error_result, file_link, success_result
from openjev_mcp.errors import ToolError


def test_success_text_matches_structured():
    structured = {"decision": "yes", "p": 0.9995, "label": "é", "meta": {"requests": 1}}
    result = success_result(structured)
    assert result["isError"] is False
    assert result["structuredContent"] is structured
    assert len(result["content"]) == 1
    block = result["content"][0]
    assert block["type"] == "text"
    assert block["annotations"] == TEXT_ANNOTATIONS == {"audience": ["assistant"]}
    assert json.loads(block["text"]) == structured
    assert " " not in block["text"].replace("é", "")


def test_error_shape():
    err = ToolError("OJ_TIMEOUT", "too slow", http_status=504, retryable=True, request_id="r1")
    result = error_result(err)
    assert result["isError"] is True
    assert "structuredContent" not in result
    assert len(result["content"]) == 1
    assert json.loads(result["content"][0]["text"]) == {"error": err.to_dict()}
    assert result["content"][0]["annotations"] == {"audience": ["assistant"]}


def test_results_validate_as_sdk_call_tool_result():
    ok = types.CallToolResult.model_validate(success_result({"a": 1}))
    assert ok.is_error is False and ok.structured_content == {"a": 1}
    assert ok.content[0].annotations.audience == ["assistant"]
    bad = types.CallToolResult.model_validate(error_result(ToolError("OJ_INTERNAL", "boom")))
    assert bad.is_error is True and bad.structured_content is None
    assert json.loads(bad.content[0].text)["error"]["code"] == "OJ_INTERNAL"


def test_links_follow_the_text_block(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("x")
    result = success_result({"a": 1}, [file_link(str(f), "text/csv")])
    assert [b["type"] for b in result["content"]] == ["text", "resource_link"]
    assert result["content"][1]["uri"] == "file://" + os.path.realpath(f)
    assert success_result({"a": 1})["content"] == success_result({"a": 1}, [])["content"]
