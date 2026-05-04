"""Script (screenplay) markdown ↔ ShotSpec list.

The script is a human-friendly markdown anchored to the song timeline.
Each ``###`` header introduces a shot with a header line of the form::

    ### <id> | <start>-<end> | <strategy>

Then optional ``**key**: value`` lines (``env``, ``chars``, ``camera``,
``framing``) followed by a free-form prose description block. Sections
are introduced by ``## [<label>] <start> → <end>`` headers but are
optional (the project-level sections list is the SSOT for those).

The parse is intentionally lenient — we ignore anything we don't
understand — so the agent can write the file and the user can edit it
without learning a strict grammar.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Optional

from muvid.project import MusicVideoProject
from muvid.schema import ShotSpec, SectionSpec, RenderStrategy


_SECTION_HDR = re.compile(
    r"^##\s*\[(?P<label>[^\]]+)\]\s*"
    r"(?P<start>[0-9]+(?:\.[0-9]+)?)\s*[→\-–>]+\s*(?P<end>[0-9]+(?:\.[0-9]+)?)"
)
_SHOT_HDR = re.compile(
    r"^###\s*(?P<id>[a-zA-Z0-9_\-]+)\s*\|\s*"
    r"(?P<start>[0-9]+(?:\.[0-9]+)?)\s*[\-–]\s*"
    r"(?P<end>[0-9]+(?:\.[0-9]+)?)\s*"
    r"(?:\|\s*(?P<strategy>[a-zA-Z_]+))?"
)
_KV_LINE = re.compile(r"^\*\*(?P<key>[a-zA-Z_]+)\*\*\s*:\s*(?P<value>.+)$")
# Match *all* `**key**: value` pairs in a line (lookahead splits on the next
# bold marker so multiple kv pairs on one line are parsed independently).
_KV_PAIRS = re.compile(
    r"\*\*(?P<key>[a-zA-Z_]+)\*\*\s*:\s*(?P<value>.+?)(?=\s*\*\*[a-zA-Z_]+\*\*\s*:|$)"
)


_VALID_STRATEGIES = {"lipsync", "image_to_video", "text_to_video", "animation", "still"}


def parse_script(md: str) -> tuple[list[SectionSpec], list[ShotSpec]]:
    """Parse a script markdown string into (sections, shots).

    Sections are returned only when explicitly headed with ``## [label]``;
    otherwise it's an empty list and you should rely on the project's
    section list separately.
    """
    sections: list[SectionSpec] = []
    shots: list[ShotSpec] = []
    cur_section_id: str = ""
    cur_shot: dict | None = None

    def flush_shot():
        nonlocal cur_shot
        if cur_shot is None:
            return
        shots.append(_dict_to_shot(cur_shot))
        cur_shot = None

    for raw in md.splitlines():
        line = raw.rstrip()
        m_sec = _SECTION_HDR.match(line)
        if m_sec:
            flush_shot()
            label = m_sec.group("label").strip()
            sec_id = _slug(label)
            cur_section_id = sec_id
            sections.append(
                SectionSpec(
                    id=sec_id,
                    start_s=float(m_sec.group("start")),
                    end_s=float(m_sec.group("end")),
                    label=label,
                )
            )
            continue
        m_shot = _SHOT_HDR.match(line)
        if m_shot:
            flush_shot()
            strategy = (m_shot.group("strategy") or "image_to_video").strip()
            if strategy not in _VALID_STRATEGIES:
                strategy = "image_to_video"
            cur_shot = {
                "id": m_shot.group("id"),
                "start_s": float(m_shot.group("start")),
                "end_s": float(m_shot.group("end")),
                "section_id": cur_section_id,
                "render_strategy": strategy,
                "characters": [],
                "description_lines": [],
            }
            continue
        if cur_shot is None:
            continue
        # If the line is *only* `**key**: value [**key**: value]*` pairs,
        # absorb every pair on it. Otherwise treat it as description text.
        stripped = line.strip()
        kv_matches = list(_KV_PAIRS.finditer(stripped))
        # All non-pair characters should be whitespace for this to count as
        # a KV-only line.
        if kv_matches and _is_only_kv(stripped, kv_matches):
            for m_kv in kv_matches:
                key = m_kv.group("key").lower()
                value = m_kv.group("value").strip()
                if key in {"env", "environment"}:
                    cur_shot["environment"] = value
                elif key in {"chars", "characters"}:
                    cur_shot["characters"] = [
                        c.strip() for c in re.split(r"[,\s]+", value) if c.strip()
                    ]
                elif key == "camera":
                    cur_shot["camera"] = value
                elif key == "framing":
                    cur_shot["framing"] = value
                elif key == "notes":
                    cur_shot["notes"] = value
            continue
        if line.strip():
            cur_shot["description_lines"].append(line.strip())
    flush_shot()
    return sections, shots


def _is_only_kv(line: str, matches) -> bool:
    """True if ``line`` contains nothing but ``**key**: value`` pairs and
    whitespace separators between them."""
    if not matches:
        return False
    pos = 0
    for m in matches:
        if line[pos : m.start()].strip():
            return False
        pos = m.end()
    return not line[pos:].strip()


def _dict_to_shot(d: dict) -> ShotSpec:
    desc = " ".join(d.pop("description_lines", []) or []).strip()
    return ShotSpec(
        id=d["id"],
        start_s=d["start_s"],
        end_s=d["end_s"],
        section_id=d.get("section_id", ""),
        render_strategy=d.get("render_strategy", "image_to_video"),
        environment=d.get("environment", ""),
        characters=tuple(d.get("characters", ())),
        description=desc,
        camera=d.get("camera", ""),
        framing=d.get("framing", "medium"),
        notes=d.get("notes", ""),
    )


def render_script(sections: Iterable[SectionSpec], shots: Iterable[ShotSpec]) -> str:
    """Inverse of ``parse_script``. Writes the canonical markdown form."""
    sections = list(sections)
    shots = list(shots)
    by_section: dict[str, list[ShotSpec]] = {}
    orphan: list[ShotSpec] = []
    for s in shots:
        if s.section_id and s.section_id in {sec.id for sec in sections}:
            by_section.setdefault(s.section_id, []).append(s)
        else:
            orphan.append(s)

    lines: list[str] = []
    if sections:
        for sec in sections:
            lines.append(
                f"## [{sec.label or sec.id}] {sec.start_s:.2f} → {sec.end_s:.2f}"
            )
            lines.append("")
            for sh in by_section.get(sec.id, []):
                lines.extend(_shot_block(sh))
                lines.append("")
    for sh in orphan:
        lines.extend(_shot_block(sh))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _shot_block(sh: ShotSpec) -> list[str]:
    out = [f"### {sh.id} | {sh.start_s:.2f}-{sh.end_s:.2f} | {sh.render_strategy}"]
    kvs = []
    if sh.environment:
        kvs.append(f"**env**: {sh.environment}")
    if sh.characters:
        kvs.append(f"**chars**: {', '.join(sh.characters)}")
    if sh.camera:
        kvs.append(f"**camera**: {sh.camera}")
    if sh.framing and sh.framing != "medium":
        kvs.append(f"**framing**: {sh.framing}")
    if kvs:
        out.append("  ".join(kvs))
    if sh.description:
        out.append(sh.description)
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"


# --- file/project helpers -------------------------------------------------


def write_script(project: MusicVideoProject) -> Path:
    """Render the project's current sections+shots to ``script/script.md``."""
    spec = project.read_spec()
    md = render_script(spec.sections, spec.shots)
    target = project.root / "script" / "script.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md)
    return target


def parse_and_apply(project: MusicVideoProject, *, path: Path | None = None) -> None:
    """Parse ``script/script.md`` and upsert any sections/shots it defines.

    Existing sections/shots not present in the script are left alone.
    """
    path = path or (project.root / "script" / "script.md")
    if not path.exists():
        raise FileNotFoundError(path)
    sections, shots = parse_script(path.read_text())
    for sec in sections:
        project.upsert_section(sec)
    for sh in shots:
        project.upsert_shot(sh)
    project.log_decision(
        "parse_and_apply_script",
        n_sections=len(sections),
        n_shots=len(shots),
    )
