"""Cut pattern READMEs into short passages for evidence retrieval.

Offline, like the rest of indexer/. The passages are bundled into the Lambda as
data/readme_chunks.json, so answering a question never touches the 800 MB
corpus or the network.

Most of a serverless-patterns README is boilerplate that every pattern shares:
requirements, deployment steps, cleanup, the cost disclaimer, the licence.
Keeping it would make every pattern look relevant to every question, so only
the prose that describes what the pattern does survives.

    python -m indexer.readme_chunks
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from indexer.census import CORPUS

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "readme_chunks.json"
INDEX = ROOT / "data" / "pattern_index.json"

MAX_CHUNK = 600       # characters per passage
MAX_PER_PATTERN = 6   # passages kept per pattern
MIN_CHUNK = 60        # shorter fragments carry no information

# Sections whose heading matches are deployment mechanics, not architecture.
_SKIP_HEADING = re.compile(
    r"requirement|prerequisite|deploy|install|clean ?up|delet|licen|copyright|"
    r"useful command|getting started|build|setup|set up|resources|test|usage|"
    r"observ|monitor|troubleshoot|example event|sample",
    re.I,
)
_SKIP_PARAGRAPH = re.compile(
    r"^(important: this application|learn more about this pattern|copyright|"
    r"spdx-license|note: )",
    re.I,
)
_STEP_VERB = re.compile(
    r"\b(cd|sam|aws|npm|cdk|git|click|choose|select|open|navigate|run|log in|"
    r"copy|paste|enter|type)\b",
    re.I,
)
_CODE_FENCE = re.compile(r"```.*?```", re.S)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML = re.compile(r"<[^>]+>")
_URL = re.compile(r"https?://\S+")
_WS = re.compile(r"\s+")


def clean_markdown(text: str) -> str:
    text = _CODE_FENCE.sub(" ", text)
    text = _IMAGE.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _HTML.sub(" ", text)
    text = _URL.sub(" ", text)
    text = re.sub(r"[`*_>#|]", " ", text)
    return _WS.sub(" ", text).strip()


def passages(readme: str) -> list[str]:
    """Descriptive paragraphs, boilerplate sections removed, in document order."""
    out: list[str] = []
    skipping = False
    block: list[str] = []

    def flush() -> None:
        if not block:
            return
        para = clean_markdown(" ".join(block))
        block.clear()
        if len(para) < MIN_CHUNK or _SKIP_PARAGRAPH.match(para):
            return
        while para:
            if len(para) <= MAX_CHUNK:
                out.append(para)
                break
            cut = para.rfind(". ", 0, MAX_CHUNK)
            cut = cut + 1 if cut > MIN_CHUNK else MAX_CHUNK
            out.append(para[:cut].strip())
            para = para[cut:].strip()

    for line in _CODE_FENCE.sub("\n", readme).splitlines():
        heading = re.match(r"^\s*#{1,6}\s*(.*)", line)
        if heading:
            flush()
            skipping = bool(_SKIP_HEADING.search(heading.group(1)))
            continue
        if skipping:
            continue
        if not line.strip():
            flush()
            continue
        # Numbered and bulleted steps are almost always instructions: CLI
        # commands or console clicks, never a description of the design.
        if re.match(r"^\s*(\d+\.|[*-])\s", line) and _STEP_VERB.search(line):
            continue
        block.append(line.strip())
    flush()
    return out[:MAX_PER_PATTERN]


def build(corpus: Path = CORPUS, index_path: Path = INDEX) -> dict[str, list[str]]:
    """Passages for every pattern the edge index knows about."""
    pattern_ids = sorted(json.loads(index_path.read_text(encoding="utf-8"))["patterns"])
    chunks: dict[str, list[str]] = {}
    for pid in pattern_ids:
        readme = corpus / pid / "README.md"
        if not readme.exists():
            continue
        found = passages(readme.read_text(encoding="utf-8", errors="replace"))
        if found:
            chunks[pid] = found
    return chunks


def main() -> None:
    chunks = build()
    OUT.write_text(json.dumps(chunks, separators=(",", ":"), ensure_ascii=False),
                   encoding="utf-8")
    n = sum(len(v) for v in chunks.values())
    print(f"readme passages : {n} from {len(chunks)} patterns, "
          f"{OUT.stat().st_size / 1024:.0f} KB -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
