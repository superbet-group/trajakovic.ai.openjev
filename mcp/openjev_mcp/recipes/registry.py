"""Recipe registry: built-in recipes plus OPENJEV_MCP_RECIPES/*.json under one loader (spec 2.18).
A file that fails to load is skipped with a warning (status shows it), never half-loaded; an extra recipe may not
shadow a built-in id or an earlier extra one. Cached per process so every list is identical."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from importlib import resources
from typing import TYPE_CHECKING

from .engine import Recipe, RecipeError, load_recipe

if TYPE_CHECKING:
    from ..config import Config

MAX_FILE_BYTES = 1 << 20


@dataclass(frozen=True)
class Registry:
    recipes: dict[str, Recipe]
    docs: dict[str, dict]
    warnings: tuple[str, ...] = field(default=())

    def ids(self) -> list[str]:
        return list(self.recipes)

    def get(self, recipe_id: str) -> Recipe:
        try:
            return self.recipes[recipe_id]
        except KeyError:
            raise RecipeError(f"recipe {recipe_id}: no such recipe") from None

    def document(self, recipe_id: str) -> dict:
        self.get(recipe_id)
        return self.docs[recipe_id]

    def index(self) -> list[dict]:
        out = []
        for rid, r in self.recipes.items():
            props = r.input_schema.get("properties", {})
            out.append({"id": rid, "title": r.title, "description": r.description, "decisions": list(r.decisions),
                        "input": {"required": list(r.input_schema.get("required", [])),
                                  "properties": {k: v.get("type") if isinstance(v, dict) else None
                                                 for k, v in props.items()}},
                        "variant_of": self.docs[rid].get("variant_of")})
        return out


def _builtin_files() -> list[tuple[str, str]]:
    root = resources.files("openjev_mcp.recipes").joinpath("builtin")
    return sorted((p.name, p.read_text(encoding="utf-8")) for p in root.iterdir() if p.name.endswith(".json"))


def _extra_files(directory: str) -> list[tuple[str, str]]:
    out = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not name.endswith(".json") or name.startswith(".") or not os.path.isfile(path):
            continue
        if os.path.getsize(path) > MAX_FILE_BYTES:
            out.append((name, ""))   # empty text: reported as invalid JSON
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            out.append((name, f.read()))
    return out


def _parse(name: str, text: str) -> tuple[Recipe, dict]:
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise RecipeError(f"invalid JSON ({e})") from e
    return load_recipe(doc), doc


def _build(recipes_dir: str | None) -> Registry:
    recipes: dict[str, Recipe] = {}
    docs: dict[str, dict] = {}
    warnings: list[str] = []
    sources: list[tuple[str, str, bool]] = [(n, t, True) for n, t in _builtin_files()]
    if recipes_dir:
        try:
            sources += [(n, t, False) for n, t in _extra_files(recipes_dir)]
        except OSError as e:
            warnings.append(f"recipes dir {recipes_dir} unreadable: {e}")
    for name, text, builtin in sources:
        try:
            recipe, doc = _parse(name, text)
            if recipe.id in recipes:
                raise RecipeError(f"id {recipe.id!r} is already defined (a recipe may not shadow another)")
        except RecipeError as e:
            warnings.append(f"recipe file {name} skipped: {e}")
            continue
        recipes[recipe.id], docs[recipe.id] = recipe, doc
    return Registry(recipes, docs, tuple(warnings))


_CACHE: dict[str | None, Registry] = {}


def load_all(config: Config) -> Registry:
    key = config.recipes_dir
    if key not in _CACHE:
        _CACHE[key] = _build(key)
    return _CACHE[key]


def clear_cache() -> None:
    _CACHE.clear()
