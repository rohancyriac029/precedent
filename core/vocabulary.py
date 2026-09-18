"""Vocabulary loader.

Wraps data/vocabulary.yaml and exposes the lookups the rest of core needs.
Loaded once and cached; the file is small and never changes at runtime.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "vocabulary.yaml"

UNKNOWN = "unknown"


def _all_str(items: Any, field: str) -> list[str]:
    items = items or []
    bad = [i for i in items if not isinstance(i, str)]
    if bad:
        raise ValueError(
            f"vocabulary.yaml: ignored_resource_types.{field} must be strings, got "
            f"{bad!r}. A value ending in ':' needs quoting."
        )
    return list(items)


class Vocabulary:
    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw
        self.services: dict[str, dict[str, Any]] = raw.get("services", {})
        self.capability_classes: dict[str, list[str]] = raw.get("capability_classes", {})
        self.sdk_capable: set[str] = set(raw.get("sdk_capable", []))
        self.consume_relations: set[str] = set(raw.get("consume_relations", []))

        ignored = raw.get("ignored_resource_types") or {}
        # A YAML scalar ending in ":" parses as a mapping key, so an unquoted
        # "AWS::EC2::" silently becomes a dict. Fail loudly here instead of at
        # the first startswith() call, which is far from the cause.
        self.edge_constructs: set[str] = set(
            _all_str(raw.get("edge_constructs"), "edge_constructs")
        )
        self.sub_resource_types: set[str] = set(
            _all_str(raw.get("sub_resource_types"), "sub_resource_types")
        )
        self.ignored_exact: set[str] = set(_all_str(ignored.get("exact"), "exact"))
        self.ignored_prefixes: tuple[str, ...] = tuple(
            _all_str(ignored.get("prefixes"), "prefixes")
        )

        # resource type -> service id
        self._by_resource_type: dict[str, str] = {}
        for sid, spec in self.services.items():
            for rt in spec.get("resource_types") or []:
                self._by_resource_type[rt] = sid

        # role -> set of service ids
        self._by_role: dict[str, set[str]] = {}
        for sid, spec in self.services.items():
            for role in spec.get("roles") or []:
                self._by_role.setdefault(role, set()).add(sid)

    # ---- basic lookups ----------------------------------------------------

    @property
    def ids(self) -> list[str]:
        """Sorted service ids. This is the enum handed to the model."""
        return sorted(self.services)

    def enum_with_unknown(self) -> list[str]:
        return self.ids + [UNKNOWN]

    def has(self, service: str) -> bool:
        return service in self.services

    def title(self, service: str) -> str:
        return self.services.get(service, {}).get("title", service)

    def service_for_resource_type(self, resource_type: str) -> str:
        return self._by_resource_type.get(resource_type, UNKNOWN)

    @property
    def mapped_resource_types(self) -> set[str]:
        return set(self._by_resource_type)

    def is_ignored(self, resource_type: str) -> bool:
        """True for plumbing we deliberately do not model as a service.

        IAM glue, VPC networking, packaging meta. See the note at the bottom of
        data/vocabulary.yaml for why these are excluded rather than mapped.
        """
        if resource_type in self.ignored_exact:
            return True
        return resource_type.startswith(self.ignored_prefixes)

    def is_sub_resource(self, resource_type: str) -> bool:
        """Part of a parent service, never a node of its own."""
        return resource_type in self.sub_resource_types

    def is_node_type(self, resource_type: str) -> bool:
        """True only for resource types that become an architecture node."""
        return (
            self.service_for_resource_type(resource_type) != UNKNOWN
            and not self.is_sub_resource(resource_type)
            and not self.is_edge_construct(resource_type)
        )

    def is_edge_construct(self, resource_type: str) -> bool:
        """Read by the parser to discover an edge; never becomes a node."""
        return resource_type in self.edge_constructs

    def is_classified(self, resource_type: str) -> bool:
        """Mapped, edge-producing, or deliberately excluded. Either way, understood."""
        return (
            resource_type in self._by_resource_type
            or self.is_edge_construct(resource_type)
            or self.is_sub_resource(resource_type)
            or self.is_ignored(resource_type)
        )

    # ---- role queries used by the post-processor --------------------------

    def with_role(self, role: str) -> set[str]:
        return set(self._by_role.get(role, set()))

    def has_role(self, service: str, role: str) -> bool:
        return role in (self.services.get(service, {}).get("roles") or [])

    @property
    def call_only(self) -> set[str]:
        """Services that never originate an edge unless the design says so."""
        return self.with_role("call_only")

    @property
    def auth(self) -> set[str]:
        return self.with_role("auth")

    @property
    def api_front(self) -> set[str]:
        return self.with_role("api_front")

    @property
    def pull_sources(self) -> set[str]:
        """Pull-based event sources where the source originates the edge.

        SQS and Kinesis only. DynamoDB is deliberately excluded: see the note in
        data/vocabulary.yaml.
        """
        return self.with_role("pull_source")

    # ---- overlap check ----------------------------------------------------

    def capability_class_of(self, service: str) -> str | None:
        for cls, members in self.capability_classes.items():
            if service in members:
                return cls
        return None


@functools.lru_cache(maxsize=4)
def load(path: str | Path | None = None) -> Vocabulary:
    p = Path(path) if path else _DEFAULT_PATH
    with open(p, encoding="utf-8") as fh:
        return Vocabulary(yaml.safe_load(fh) or {})
