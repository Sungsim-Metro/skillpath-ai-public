"""Build Markdown sources and metadata sidecars for the learning-program Knowledge Base."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import quote

from pipeline.search import COURSES, _read, _usable


def build_documents(catalog: Path, output: Path, provider: str) -> int:
    """Write one source per course to a dedicated staging directory."""
    files: dict[str, str] = {}
    for course in _read(catalog):
        course_id = course["course_id"]
        filename = f"{quote(course_id, safe='')}.md"
        if not course_id.strip() or filename in files:
            raise ValueError(f"Missing or duplicate course ID: {course_id!r}")
        sections = [f"# {course['title'].strip()}"]
        for field, heading in (
            ("objectives", "Learning objectives"), ("description", "Description"),
            ("audience", "Intended audience"), ("prerequisites", "Prerequisites"), ("aws_services", "AWS services"),
        ):
            if value := course.get(field, "").strip():
                sections.append(f"## {heading}\n\n{value}")
        files[filename] = "\n\n".join(sections) + "\n"
        metadata = {"type": "program", "course_id": course_id, "provider": provider,
                    "recommendable": _usable(course) and course.get("status", "ACTIVE") == "ACTIVE"}
        files[f"{filename}.metadata.json"] = json.dumps({"metadataAttributes": metadata}, ensure_ascii=False) + "\n"

    output.mkdir(parents=True, exist_ok=True)
    if any(path.name not in files for path in output.iterdir()):
        raise ValueError(f"Use a fresh staging directory to avoid publishing stale files: {output}")
    for name, content in files.items():
        (output / name).write_text(content, encoding="utf-8")
    return len(files) // 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=COURSES)
    parser.add_argument("--provider", default="aws-skill-builder")
    parser.add_argument("--output", type=Path, required=True, help="Local programs/ staging directory")
    args = parser.parse_args()
    count = build_documents(args.catalog, args.output, args.provider)
    print(f"{count} course documents and metadata sidecars in {args.output}")


if __name__ == "__main__":
    main()
