"""Script (screenplay) markdown ↔ ShotSpec round-trip."""

from __future__ import annotations

from mtv.script import parse_script, render_script


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
