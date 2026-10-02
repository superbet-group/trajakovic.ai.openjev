"""ask_image: questions about 1-8 images, loaded and re-encoded locally (spec 2.12)."""
from __future__ import annotations

from typing import Any

from openjev_mcp.images import MAX_SIDE_PX, load_images
from openjev_mcp.schemas import INPUT_SCHEMAS, OUTPUT_SCHEMAS, register_tool_schemas
from openjev_mcp.tools import ToolContext, ToolSpec
from openjev_mcp.tools.read import _audit, _band, run_read

IMAGE_ITEM: dict[str, Any] = {"oneOf": [
    {"type": "object", "required": ["path"], "additionalProperties": False, "properties": {
        "path": {"type": "string", "description": "image file inside the allowed roots (.png .jpg .jpeg .webp .gif)"}}},
    {"type": "object", "required": ["url"], "additionalProperties": False, "properties": {
        "url": {"type": "string", "format": "uri",
                "description": "https only; refused unless OPENJEV_MCP_FETCH=on; public addresses only"}}},
    {"type": "object", "required": ["data_url"], "additionalProperties": False, "properties": {
        "data_url": {"type": "string", "pattern": "^data:image/"}}},
    {"type": "object", "required": ["base64", "content_type"], "additionalProperties": False, "properties": {
        "base64": {"type": "string"}, "content_type": {"type": "string"}}}]}
IMAGES = {"type": "array", "minItems": 1, "maxItems": 8, "items": IMAGE_ITEM}
MAX_SIDE = {"type": "integer", "minimum": 64, "maximum": 8192, "default": MAX_SIDE_PX,
            "description": "longest side after downscaling; images are re-encoded as image/png or image/jpeg"}
IMAGE_INFO = {"type": "array", "items": {"type": "object", "required": ["source", "sent_as", "bytes", "reencoded"], "properties": {
    "source": {"type": "string"}, "sent_as": {"enum": ["image/png", "image/jpeg"]}, "bytes": {"type": "integer"},
    "reencoded": {"type": "boolean"}}}}

_INPUT: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["images", "questions"],
    "properties": {
        "images": IMAGES,
        "state": {"type": "string", "default": "Screenshot.",
                  "description": "What the images are, the task, and their order for multi-image requests"},
        "questions": {"$ref": "#/$defs/QuestionSet"},
        "options": {"type": "object", "additionalProperties": False, "properties": {
            "model": {"type": "string"}, "samples": {"type": "integer", "minimum": 1, "maximum": 32},
            "steps": {"type": "integer", "minimum": 1, "maximum": 8}, "timeout_ms": {"type": "integer"}}},
        "max_side_px": MAX_SIDE,
        "thresholds": {"$ref": "#/$defs/Band"},
    },
}

_OUTPUT: dict[str, Any] = {
    "type": "object", "required": ["answers", "images", "meta"],
    "properties": {
        "answers": {"type": "object", "additionalProperties": {"$ref": "#/$defs/Answer"}},
        "images": IMAGE_INFO,
        "meta": {"$ref": "#/$defs/Meta"},
    },
}

DESCRIPTION = ("Ask questions about 1-8 images (screenshots, photos) plus a short text state. Loads files, data URLs "
               "and (only with OPENJEV_MCP_FETCH=on) public https URLs, downscales them and re-encodes them as PNG or "
               "JPEG locally. think and sequential are not available for images; prefer ask with a text tree when you "
               "have one.")


async def ask_image(ctx: ToolContext, args: dict) -> dict:
    loaded = await load_images(args["images"], ctx.config, max_side_px=args.get("max_side_px", MAX_SIDE_PX))
    out = await run_read(ctx, state=args.get("state", "Screenshot."), questions=args["questions"],
                         options=args.get("options"), band=_band(ctx, args.get("thresholds")),
                         images=[i.data_url for i in loaded])
    _audit(ctx, "ask_image", out)
    return {"answers": out.answers, "images": [i.info() for i in loaded], "meta": out.meta}


def register(config) -> ToolSpec:
    register_tool_schemas("ask_image", _INPUT, _OUTPUT)
    annotations = {"title": "Ask about images", "readOnlyHint": True, "idempotentHint": False,
                   "openWorldHint": bool(config.fetch)}
    return ToolSpec("ask_image", "Ask about images", DESCRIPTION, INPUT_SCHEMAS["ask_image"],
                    OUTPUT_SCHEMAS["ask_image"], annotations, ask_image)
