"""Tolerant loader for CloudFormation and SAM templates.

Stage 2 of docs/PLAN.md, flagged there as the highest-risk component. The risk
is not parsing YAML, it is that CFN YAML is not really YAML: it carries custom
short-form tags (!Ref, !GetAtt, !Sub, !If, ...) that a stock SafeLoader rejects
outright.

The rule this module follows: never crash. A template we cannot fully resolve
should still yield the resources we can see, with a warning. Unresolvable
references become warnings, never exceptions. See Appendix A of the plan.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# CFN short-form tags, mapped to the long form they stand for.
INTRINSIC_TAGS = {
    "!Ref": "Ref",
    "!GetAtt": "Fn::GetAtt",
    "!Sub": "Fn::Sub",
    "!Join": "Fn::Join",
    "!Select": "Fn::Select",
    "!Split": "Fn::Split",
    "!FindInMap": "Fn::FindInMap",
    "!If": "Fn::If",
    "!Not": "Fn::Not",
    "!Equals": "Fn::Equals",
    "!And": "Fn::And",
    "!Or": "Fn::Or",
    "!Base64": "Fn::Base64",
    "!Cidr": "Fn::Cidr",
    "!ImportValue": "Fn::ImportValue",
    "!GetAZs": "Fn::GetAZs",
    "!Transform": "Fn::Transform",
    "!Condition": "Condition",
}


class CfnLoader(yaml.SafeLoader):
    """SafeLoader that understands CFN short-form intrinsics."""


def _intrinsic_constructor(long_name: str):
    def construct(loader: yaml.Loader, node: yaml.Node) -> dict[str, Any]:
        if isinstance(node, yaml.ScalarNode):
            value: Any = loader.construct_scalar(node)
            # !GetAtt Foo.Bar is sugar for {"Fn::GetAtt": ["Foo", "Bar"]}
            if long_name == "Fn::GetAtt" and isinstance(value, str):
                value = value.split(".", 1)
        elif isinstance(node, yaml.SequenceNode):
            value = loader.construct_sequence(node, deep=True)
        else:
            value = loader.construct_mapping(node, deep=True)
        return {long_name: value}

    return construct


for tag, long_form in INTRINSIC_TAGS.items():
    CfnLoader.add_constructor(tag, _intrinsic_constructor(long_form))


def _unknown_tag(loader: yaml.Loader, tag_suffix: str, node: yaml.Node) -> Any:
    """Never crash on a tag we have not seen before."""
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_mapping(node, deep=True)


CfnLoader.add_multi_constructor("!", _unknown_tag)


@dataclass
class LoadedTemplate:
    path: str
    body: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    ok: bool = True

    @property
    def resources(self) -> dict[str, dict[str, Any]]:
        res = self.body.get("Resources")
        return res if isinstance(res, dict) else {}

    @property
    def is_sam(self) -> bool:
        transform = self.body.get("Transform")
        if isinstance(transform, str):
            return "Serverless" in transform
        if isinstance(transform, list):
            return any("Serverless" in str(t) for t in transform)
        return False

    def resource_type(self, logical_id: str) -> str | None:
        r = self.resources.get(logical_id)
        if isinstance(r, dict):
            t = r.get("Type")
            return t if isinstance(t, str) else None
        return None

    def iter_resources(self):
        """Yield (logical_id, type, properties) for well-formed resources only."""
        for logical_id, spec in self.resources.items():
            if not isinstance(spec, dict):
                continue
            rtype = spec.get("Type")
            if not isinstance(rtype, str):
                continue
            props = spec.get("Properties")
            yield logical_id, rtype, props if isinstance(props, dict) else {}


class TemplateParseError(Exception):
    """Raised only when there is genuinely nothing usable in the input."""

    def __init__(self, message: str, line: int | None = None, column: int | None = None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column

    def as_api_error(self) -> dict[str, Any]:
        """Shape for the 400 response the API contract promises."""
        return {
            "error": "parse_error",
            "message": self.message,
            "line": self.line,
            "column": self.column,
        }


def loads(text: str, path: str = "<string>") -> LoadedTemplate:
    """Load a template from text. Tries JSON first, then CFN-tolerant YAML."""
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            body = json.loads(text)
            return _validated(body, path)
        except json.JSONDecodeError as exc:
            raise TemplateParseError(
                f"Invalid JSON: {exc.msg}", line=exc.lineno, column=exc.colno
            ) from exc

    try:
        body = yaml.load(text, Loader=CfnLoader)  # noqa: S506 - CfnLoader is a SafeLoader
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark
        raise TemplateParseError(
            f"Invalid YAML: {exc.problem or 'could not parse'}",
            line=(mark.line + 1) if mark else None,
            column=(mark.column + 1) if mark else None,
        ) from exc
    except yaml.YAMLError as exc:
        raise TemplateParseError(f"Invalid YAML: {exc}") from exc

    return _validated(body, path)


def load_file(path: str | Path) -> LoadedTemplate:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise TemplateParseError(f"Could not read {p}: {exc}") from exc
    return loads(text, path=str(p))


def _validated(body: Any, path: str) -> LoadedTemplate:
    if body is None:
        raise TemplateParseError("Template is empty.")
    if not isinstance(body, dict):
        raise TemplateParseError("Template root must be a mapping.")

    tpl = LoadedTemplate(path=path, body=body)
    if "Resources" not in body:
        tpl.warnings.append("Template has no Resources section.")
        tpl.ok = False
    elif not isinstance(body["Resources"], dict):
        tpl.warnings.append("Resources is not a mapping; ignoring it.")
        tpl.ok = False
    else:
        for logical_id, spec in body["Resources"].items():
            if not isinstance(spec, dict):
                tpl.warnings.append(f"Resource {logical_id} is not a mapping; skipped.")
            elif not isinstance(spec.get("Type"), str):
                tpl.warnings.append(f"Resource {logical_id} has no Type; skipped.")
    return tpl


# --------------------------------------------------------------------------
# reference resolution
# --------------------------------------------------------------------------


def referenced_logical_ids(value: Any) -> set[str]:
    """Collect every logical ID an intrinsic expression points at.

    Walks arbitrarily nested structures, because a policy resource or an
    environment variable can bury a !GetAtt several levels down. Pseudo
    parameters (AWS::Region and friends) are not logical IDs and are dropped.
    """
    found: set[str] = set()
    _walk_refs(value, found)
    return {f for f in found if not f.startswith("AWS::")}


def _walk_refs(value: Any, out: set[str]) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            if key == "Ref" and isinstance(inner, str):
                out.add(inner)
            elif key == "Fn::GetAtt":
                if isinstance(inner, str):
                    out.add(inner.split(".", 1)[0])
                elif isinstance(inner, list) and inner and isinstance(inner[0], str):
                    out.add(inner[0])
            elif key == "Fn::Sub":
                _walk_sub(inner, out)
            else:
                _walk_refs(inner, out)
    elif isinstance(value, list):
        for item in value:
            _walk_refs(item, out)


def _walk_sub(value: Any, out: set[str]) -> None:
    """!Sub embeds references as ${Logical} or ${Logical.Attr} inside a string."""
    import re

    if isinstance(value, str):
        for m in re.finditer(r"\$\{([^}]+)\}", value):
            token = m.group(1).strip()
            if token.startswith("!"):
                continue
            out.add(token.split(".", 1)[0])
    elif isinstance(value, list):
        if value and isinstance(value[0], str):
            _walk_sub(value[0], out)
        for item in value[1:]:
            _walk_refs(item, out)
    else:
        _walk_refs(value, out)
