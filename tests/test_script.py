"""Script (screenplay) markdown ↔ ShotSpec round-trip."""

from __future__ import annotations

import json

from muvid.script import parse_script, render_script
from tests.ascii_locale_support import needs_ascii_locale, run_under_ascii_locale


SAMPLE = """## [intro] 0.00 → 12.50

### s01 | 0.00-12.50 | image_to_video
**env**: park_bench  **camera**: slow push-in
A wide of the empty park bench at golden hour. Leaves drifting.

## [verse 1] 12.50 → 35.00

### s02 | 12.50-22.00 | lipsync
**env**: park_bench  **chars**: maya
Medium close on Maya. She begins to sing, looking off-camera.

### s03 | 22.00-35.00 | image_to_video
**env**: park_bench  **chars**: maya, charlie
Push in to a tight close-up.
"""


def test_parse_extracts_sections_and_shots():
    sections, shots = parse_script(SAMPLE)
    assert [s.id for s in sections] == ["intro", "verse_1"]
    assert [sh.id for sh in shots] == ["s01", "s02", "s03"]


def test_parse_handles_multiple_kv_pairs_on_one_line():
    """Regression: an earlier version greedily matched the first **key**
    only and stuffed the rest into the value."""
    _, shots = parse_script(SAMPLE)
    s01 = shots[0]
    assert s01.environment == "park_bench"
    assert s01.camera == "slow push-in"


def test_parse_handles_chars_list():
    _, shots = parse_script(SAMPLE)
    s03 = shots[2]
    assert s03.characters == ("maya", "charlie")
    assert s03.environment == "park_bench"


def test_unknown_strategy_falls_back_to_image_to_video():
    md = "### sX | 0.0-1.0 | quantum_render\nfoo\n"
    _, shots = parse_script(md)
    assert shots[0].render_strategy == "image_to_video"


def test_round_trip_preserves_topology():
    sections, shots = parse_script(SAMPLE)
    rendered = render_script(sections, shots)
    secs2, shots2 = parse_script(rendered)
    assert [s.id for s in secs2] == [s.id for s in sections]
    assert [sh.id for sh in shots2] == [sh.id for sh in shots]
    assert shots2[2].characters == shots[2].characters


# --- UTF-8 on disk, regardless of the ambient locale ----------------------

#: Prose direction with the accents and typographic marks a script really has.
NON_ASCII_DESCRIPTION = "Plano medio de Renée en el café — «mira a cámara»."

_PROJECT_ROUND_TRIP = """
import json, sys
from muvid.project import MusicVideoProject
from muvid.schema import SectionSpec, ShotSpec
from muvid.script import parse_and_apply, write_script

root, description = sys.argv[1], json.loads(sys.argv[2])
proj = MusicVideoProject.init(root, title="prueba")
proj.upsert_section(SectionSpec(id="verso_1", start_s=0.0, end_s=12.5, label="verso 1"))
proj.upsert_shot(
    ShotSpec(
        id="s01",
        start_s=0.0,
        end_s=12.5,
        section_id="verso_1",
        description=description,
    )
)
write_script(proj)
parse_and_apply(proj)
# ensure_ascii keeps stdout readable under the ASCII codec too.
print(json.dumps(proj.read_spec().shots[0].description))
"""


@needs_ascii_locale
def test_script_md_round_trips_non_ascii_under_ascii_locale(tmp_path):
    """``script.md`` is UTF-8 by contract, not by ambient locale.

    Under ``LC_ALL=C`` — a container with no locale, cron, a systemd unit —
    an unqualified ``write_text`` picks the ASCII codec, so a shot direction
    carrying an accent cannot be written out at all, and one written earlier
    in a UTF-8 locale cannot be read back.
    """
    proc = run_under_ascii_locale(
        _PROJECT_ROUND_TRIP,
        str(tmp_path / "proj"),
        json.dumps(NON_ASCII_DESCRIPTION),
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == NON_ASCII_DESCRIPTION
    written = (tmp_path / "proj" / "script" / "script.md").read_text(encoding="utf-8")
    assert NON_ASCII_DESCRIPTION in written
