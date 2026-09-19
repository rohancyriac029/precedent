"""Corpus census: what is actually in the serverless-patterns repo.

Stage 1 of docs/PLAN.md. This runs before any parser work, because the
vocabulary should cover what the corpus actually contains rather than what we
assume it contains. Gate 1 requires its output in eval/results/census.json.

Nothing here needs AWS or a network connection once the corpus is cloned.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from core.cfn_loader import TemplateParseError, load_file

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus" / "serverless-patterns"
OUT = ROOT / "eval" / "results" / "census.json"

SAM_CFN_NAMES = {"template.yaml", "template.yml", "template.json"}
CDK_MARKERS = ("cdk.json",)
TF_SUFFIX = ".tf"
SLS_NAMES = {"serverless.yml", "serverless.yaml"}

# NOTE: an earlier version regex-scanned the whole file for `Type:`, which also
# matched the Parameters section and reported CloudFormation *parameter* types
# such as AWS::EC2::VPC::Id as if they were resources. We parse properly now.


@dataclass
class Census:
    patterns_total: int = 0
    frameworks: Counter = field(default_factory=Counter)
    resource_types: Counter = field(default_factory=Counter)
    sam_cfn_patterns: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    unparseable: list[str] = field(default_factory=list)

    def to_dict(self, top_n: int = 40) -> dict:
        return {
            "patterns_total": self.patterns_total,
            "frameworks": dict(self.frameworks.most_common()),
            "sam_cfn_pattern_count": len(self.sam_cfn_patterns),
            "distinct_resource_types": len(self.resource_types),
            "top_resource_types": self.resource_types.most_common(top_n),
            "resource_instances_total": sum(self.resource_types.values()),
            "unreadable_files": self.unreadable[:20],
            "unreadable_count": len(self.unreadable),
            "unparseable_templates": self.unparseable[:20],
            "unparseable_count": len(self.unparseable),
        }


def pattern_dirs(corpus: Path) -> Iterable[Path]:
    """Top-level directories are patterns. Skip repo plumbing."""
    for p in sorted(corpus.iterdir()):
        if not p.is_dir():
            continue
        if p.name.startswith(".") or p.name in {"_config", "docs", "assets"}:
            continue
        yield p


def classify(pattern: Path) -> set[str]:
    """A pattern can ship more than one framework, so return a set."""
    found: set[str] = set()
    for path in pattern.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if name in SAM_CFN_NAMES:
            found.add("sam_cfn")
        elif name in CDK_MARKERS:
            found.add("cdk")
        elif name in SLS_NAMES:
            found.add("serverless_framework")
        elif path.suffix == TF_SUFFIX:
            found.add("terraform")
    return found or {"other"}


def sam_cfn_templates(pattern: Path) -> Iterable[Path]:
    for path in pattern.rglob("*"):
        if path.is_file() and path.name in SAM_CFN_NAMES:
            yield path


def run(corpus: Path = CORPUS) -> Census:
    if not corpus.exists():
        raise SystemExit(
            f"corpus not found at {corpus}\nRun: python tasks.py corpus"
        )

    c = Census()
    for pattern in pattern_dirs(corpus):
        c.patterns_total += 1
        kinds = classify(pattern)
        for k in kinds:
            c.frameworks[k] += 1

        if "sam_cfn" not in kinds:
            continue
        c.sam_cfn_patterns.append(pattern.name)

        for tpl in sam_cfn_templates(pattern):
            try:
                loaded = load_file(tpl)
            except TemplateParseError:
                c.unparseable.append(str(tpl.relative_to(corpus)))
                continue
            except (UnicodeDecodeError, OSError):
                c.unreadable.append(str(tpl.relative_to(corpus)))
                continue
            for _logical_id, rtype, _props in loaded.iter_resources():
                c.resource_types[rtype] += 1
    return c


def coverage(census: Census, vocab) -> dict[str, float]:
    """Two different numbers, because raw coverage is misleading on its own.

    `mapped` is the fraction of resource instances that become a service node.
    `classified` adds the ones we deliberately exclude as plumbing.

    Gate 1 allows either >=90% coverage OR an explicit exclusion list, so
    `classified` is the number that actually decides the gate, and `mapped` is
    reported alongside it so nobody mistakes one for the other.
    """
    total = sum(census.resource_types.values())
    if not total:
        return {"mapped": 0.0, "classified": 0.0, "total_instances": 0}
    mapped = sum(
        n for rt, n in census.resource_types.items() if vocab.service_for_resource_type(rt) != "unknown"
    )
    classified = sum(
        n for rt, n in census.resource_types.items() if vocab.is_classified(rt)
    )
    return {
        "mapped": mapped / total,
        "classified": classified / total,
        "total_instances": total,
    }


def main() -> None:
    from core.vocabulary import load as load_vocab

    c = run()
    vocab = load_vocab()
    cov = coverage(c, vocab)

    commit_file = ROOT / "data" / "corpus_commit.txt"
    commit = commit_file.read_text(encoding="utf-8").strip() if commit_file.exists() else ""

    unclassified = [
        (rt, n) for rt, n in c.resource_types.most_common() if not vocab.is_classified(rt)
    ]

    data = c.to_dict()
    data["corpus_commit"] = commit
    data["coverage_mapped"] = round(cov["mapped"], 4)
    data["coverage_classified"] = round(cov["classified"], 4)
    data["vocabulary_services"] = len(vocab.ids)
    data["unclassified_top"] = unclassified[:30]
    data["unclassified_instances"] = sum(n for _, n in unclassified)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")

    print(f"corpus commit           : {commit[:12]}")
    print(f"patterns total          : {c.patterns_total}")
    print(f"  with SAM/CFN template : {len(c.sam_cfn_patterns)}")
    for name, n in c.frameworks.most_common():
        print(f"  {name:22s}: {n}")
    print(f"templates unparseable   : {len(c.unparseable)}")
    print(f"distinct resource types : {len(c.resource_types)}")
    print(f"resource instances      : {cov['total_instances']}")
    print()
    print(f"mapped to a service     : {cov['mapped']:.1%}")
    print(f"classified (map+ignore) : {cov['classified']:.1%}   <- Gate 1 number, wants >= 90%")
    print()
    if unclassified:
        print(f"still unclassified: {len(unclassified)} types, "
              f"{data['unclassified_instances']} instances")
        for rt, n in unclassified[:20]:
            print(f"    {rt:48s} {n}")
    else:
        print("every resource type is either mapped or explicitly excluded")
    print(f"\nwritten to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
