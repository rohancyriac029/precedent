#!/usr/bin/env python
"""Cross-platform task runner. Mirrors the Makefile for machines without `make`.

    python tasks.py test
    python tasks.py corpus
    python tasks.py --list
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT.parent / "frontend"   # backend/ and frontend/ are siblings
CORPUS_DIR = ROOT / "corpus" / "serverless-patterns"
CORPUS_REPO = "https://github.com/aws-samples/serverless-patterns"

def _sam() -> str:
    """Locate the SAM CLI.

    The Windows installer updates the system PATH, which existing shells do not
    pick up until they restart. Fall back to the known install locations so a
    fresh install works immediately.
    """
    found = shutil.which("sam")
    if found:
        return found
    candidates = [
        os.path.join("C:" + os.sep, "Program Files", "Amazon", "AWSSAMCLI", "bin", "sam.cmd"),
        os.path.join("C:" + os.sep, "Program Files (x86)", "Amazon", "AWSSAMCLI", "bin", "sam.cmd"),
        "/usr/local/bin/sam",
        "/opt/homebrew/bin/sam",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return "sam"  # let it fail with a clear message


def _npm() -> str:
    """Locate npm.

    On Windows npm is `npm.cmd`, and subprocess without a shell cannot resolve a
    .cmd file from a bare name, so `["npm", ...]` fails with FileNotFoundError.
    shutil.which honours PATHEXT and finds it.
    """
    return shutil.which("npm") or "npm"


TASKS: dict[str, list[list[str]]] = {
    "census": [[sys.executable, "-m", "indexer.census"]],
    "index": [[sys.executable, "-m", "indexer.build_index"]],
    "load": [[sys.executable, "-m", "indexer.load_ddb"]],
    "test": [[sys.executable, "-m", "pytest"]],
    "api": [
        [sys.executable, "-m", "uvicorn", "api.app:app",
         "--host", "127.0.0.1", "--port", "8000", "--reload"]
    ],
    "web": [[_npm(), "run", "dev"]],
    "build": [[_sam(), "build", "--template", "infra/template.yaml"]],
    "validate": [[_sam(), "validate", "--template", "infra/template.yaml", "--lint"]],
    "deploy": [[_sam(), "deploy", "--template", "infra/template.yaml"]],
    "eval": [[sys.executable, "-m", "eval.run_all"]],
    "demo-check": [[sys.executable, "-m", "eval.demo_check"]],
    "smoke": [[sys.executable, "-m", "eval.smoke"]],
}

CWD_OVERRIDE = {"web": FRONTEND}


LAMBDA_DEPS = ["pydantic", "pyyaml", "networkx", "httpx"]
LAMBDA_PLATFORM = "manylinux2014_aarch64"   # must match Architectures in template.yaml
LAMBDA_PY = "3.12"                          # must match Runtime in template.yaml


def task_package() -> int:
    """Assemble build/lambda for `sam build`.

    Two things make this non-obvious.

    CodeUri cannot point at the repo root: that would zip corpus/, which is over
    200 MB. A dedicated build directory keeps the package to what is needed.

    Dependencies must be Linux arm64 wheels, and we are building on Windows.
    Plain `pip install` would fetch win_amd64 wheels that cannot load in Lambda,
    and the failure appears at runtime as an opaque import error. Pinning
    --platform and --only-binary makes pip resolve the right wheels without
    needing Docker.
    """
    import shutil
    import subprocess

    build = ROOT / "build" / "lambda"
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)

    # handler modules and the extraction package
    shutil.copytree(ROOT / "extract", build / "extract",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for item in (ROOT / "lambda_src").iterdir():
        if item.is_file() and item.suffix == ".py":
            shutil.copy2(item, build / item.name)

    # the deterministic core, minus anything that pulls in heavy deps
    shutil.copytree(
        ROOT / "core",
        build / "core",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    # core/ reads these from ../data at import time. Missing any one of them
    # degrades the product silently: no aliases means every Mermaid label is
    # unknown, no rules means no UNSUPPORTED verdict ever fires.
    data_dir = build / "data"
    data_dir.mkdir(exist_ok=True)
    for name in ("vocabulary.yaml", "aliases.csv", "integration_rules.csv",
                 "pattern_index.json"):
        src_file = ROOT / "data" / name
        if not src_file.exists():
            print(f"missing data file: {name}", file=sys.stderr)
            return 1
        shutil.copy2(src_file, data_dir / name)

    cmd = [
        sys.executable, "-m", "pip", "install",
        "--platform", LAMBDA_PLATFORM,
        "--python-version", LAMBDA_PY,
        "--implementation", "cp",
        "--only-binary=:all:",
        "--upgrade",
        "--target", str(build),
        *LAMBDA_DEPS,
    ]
    print("$ " + " ".join(cmd))
    rc = subprocess.call(cmd)
    if rc != 0:
        print("dependency install failed", file=sys.stderr)
        return rc

    # Windows .pyc files would shadow the Linux modules at import time.
    for pyc in build.rglob("__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)

    # No requirements.txt on purpose. Its presence makes `sam build` take over
    # dependency handling, and its Python builder drops the compiled .so files
    # we just placed here, producing "No module named pydantic_core._pydantic_core"
    # at cold start. We deploy this directory as-is instead.

    total = sum(f.stat().st_size for f in build.rglob("*") if f.is_file())
    top = sorted(p.name for p in build.iterdir())
    print(f"\npackaged {len(top)} top-level entries, {total / 1e6:.1f} MB unzipped")
    print("   " + ", ".join(top[:12]))
    return 0


AMPLIFY_APP = "precedent"
AMPLIFY_BRANCH = "main"
STACK = "precedent"
REGION = "ap-south-1"


def task_web_deploy() -> int:
    """Build the frontend against the deployed API and publish it to Amplify.

    Manual deployment, no Git connection: the app and branch are created on
    first run and reused after that. Prints the public URL.
    """
    import io
    import time
    import urllib.request
    import zipfile

    import boto3

    cf = boto3.client("cloudformation", region_name=REGION)
    outputs = {o["OutputKey"]: o["OutputValue"]
               for o in cf.describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]}
    api = outputs["ApiUrl"]
    print(f"API: {api}")

    env = dict(os.environ, VITE_API_BASE=api)
    rc = subprocess.call([_npm(), "run", "build"], cwd=str(FRONTEND), env=env)
    if rc != 0:
        return rc
    dist = FRONTEND / "dist"

    # Forward-slash paths with index.html at the root. A zip made by Windows'
    # own tooling uses backslashes, and Amplify then serves nothing.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(dist.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(dist).as_posix())
    payload = buf.getvalue()
    print(f"bundle: {len(payload) / 1024:.0f} KB")

    amp = boto3.client("amplify", region_name=REGION)
    app = next((a for a in amp.list_apps()["apps"] if a["name"] == AMPLIFY_APP), None)
    if app is None:
        app = amp.create_app(name=AMPLIFY_APP, platform="WEB",
                             description="Precedent frontend")["app"]
        print(f"created Amplify app {app['appId']}")
    app_id = app["appId"]
    branches = [b["branchName"] for b in amp.list_branches(appId=app_id)["branches"]]
    if AMPLIFY_BRANCH not in branches:
        amp.create_branch(appId=app_id, branchName=AMPLIFY_BRANCH, stage="PRODUCTION")
        print(f"created branch {AMPLIFY_BRANCH}")

    dep = amp.create_deployment(appId=app_id, branchName=AMPLIFY_BRANCH)
    req = urllib.request.Request(dep["zipUploadUrl"], data=payload, method="PUT",
                                 headers={"Content-Type": "application/zip"})
    urllib.request.urlopen(req, timeout=120).read()
    amp.start_deployment(appId=app_id, branchName=AMPLIFY_BRANCH, jobId=dep["jobId"])

    for _ in range(90):
        status = amp.get_job(appId=app_id, branchName=AMPLIFY_BRANCH,
                             jobId=dep["jobId"])["job"]["summary"]["status"]
        if status in ("SUCCEED", "FAILED", "CANCELLED"):
            break
        time.sleep(2)
    print(f"deployment {dep['jobId']}: {status}")
    if status != "SUCCEED":
        return 1
    print(f"live at https://{AMPLIFY_BRANCH}.{app['defaultDomain']}")
    return 0


def task_corpus() -> int:
    """Clone the corpus at depth 1 and record the pinned commit."""
    if CORPUS_DIR.exists():
        print(f"corpus already present at {CORPUS_DIR}")
    else:
        CORPUS_DIR.parent.mkdir(parents=True, exist_ok=True)
        rc = subprocess.call(
            ["git", "clone", "--depth", "1", CORPUS_REPO, str(CORPUS_DIR)]
        )
        if rc != 0:
            return rc
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=CORPUS_DIR, text=True
    ).strip()
    out = ROOT / "data" / "corpus_commit.txt"
    out.write_text(commit + "\n", encoding="utf-8")
    print(f"corpus pinned at {commit}")
    print(f"written to {out.relative_to(ROOT)}")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"--list", "-l", "help", "--help"}:
        print("tasks:")
        for name in ["corpus", "package", "web-deploy", *TASKS]:
            print(f"  {name}")
        return 0

    name = argv[0]
    extra = argv[1:]

    if name == "corpus":
        return task_corpus()

    if name == "package":
        return task_package()

    if name == "web-deploy":
        return task_web_deploy()

    if name not in TASKS:
        print(f"unknown task: {name}", file=sys.stderr)
        print("run `python tasks.py --list` to see them", file=sys.stderr)
        return 2

    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(ROOT))
    cwd = CWD_OVERRIDE.get(name, ROOT)

    for cmd in TASKS[name]:
        full = cmd + extra
        print(f"$ {' '.join(full)}")
        rc = subprocess.call(full, cwd=str(cwd), env=env)
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
