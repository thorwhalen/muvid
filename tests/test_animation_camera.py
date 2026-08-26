"""The animation renderer's camera must speak ``an``'s vocabulary.

muvid's ``ShotSpec.camera`` is free prose a director writes into the script
(``**camera**: slow push-in``). ``an``'s ``camera.move`` is a closed set of
named moves, and a name outside it is a hard refusal at BOTH validate and
compile — so a scene muvid synthesizes with a move ``an`` cannot name never
renders. That is muvid#44: this module emitted ``move: static``, which ``an``
has never implemented, and because nothing in the suite compiled the
synthesized ``scene.md`` the break was invisible until someone ran the
renderer, where it surfaced only as a silent fallback to the ``still``
strategy.

The tests that pin the vocabulary IMPORT ``an.ir.camera.CAMERA_MOVES`` rather
than re-typing it. A hand-copied vocabulary is exactly how ``static`` survived
here after ``an`` tightened the rule, so a copy in this file would reproduce
the bug it is meant to catch.

**These pins do not run in CI.** ``an`` is deliberately not a declared muvid
dependency (the import in ``muvid/renderers/animation.py`` is soft, falling
back to the ``still`` strategy), and muvid's CI installs only the ``ai,mcp``
extras — so every ``an``-importing test here SKIPS on the runner and is
verified on a developer machine that has ``an`` installed. The tests that need
no ``an`` run everywhere, and they are what keep an invalid literal from
reappearing unnoticed.

Skips are per-test rather than a module-level ``importorskip``: an
``importorskip`` at module scope aborts the import and the tests are never
collected at all, which is invisible in both the pass and the skip counts.
"""

from __future__ import annotations

import pytest

from muvid.renderers import RenderContext
from muvid.renderers.animation import (
    AN_CAMERA_MOVE_PHRASES,
    DFLT_AN_CAMERA_MOVE,
    _build_an_scene_md,
    an_camera_move,
)
from muvid.schema import ShotSpec

try:  # `an` is a soft dependency — see the module docstring.
    from an.ir.camera import CAMERA_MOVES as AN_CAMERA_MOVES

    HAS_AN = True
except ImportError:  # pragma: no cover - depends on the environment
    AN_CAMERA_MOVES = {}
    HAS_AN = False

needs_an = pytest.mark.skipif(not HAS_AN, reason="`an` is not installed")


def _scene_md(camera: str) -> str:
    """The exact ``scene.md`` the renderer hands to ``an``, for one direction."""
    shot = ShotSpec(
        id="s1",
        start_s=0.0,
        end_s=4.0,
        render_strategy="animation",
        characters=("alice",),
        environment="park",
        camera=camera,
        description="she sings",
    )
    ctx = RenderContext(
        project=None,
        shot=shot,
        shot_dir=None,
        audio_slice_path=None,
        character_image_paths={},
        environment_image_path=None,
        lyric_lines=[],
        global_style="",
    )
    return _build_an_scene_md(ctx)


def _emitted_move(camera: str) -> str:
    """Parse the move back out of the synthesized ``scene.md``."""
    lines = [L for L in _scene_md(camera).splitlines() if L.startswith("camera:")]
    assert len(lines) == 1, f"expected one camera line, got {lines}"
    # `camera: { move: push_in }`
    return lines[0].split("move:", 1)[1].strip(" }")


# --------------------------------------------------------------------------
# Runs everywhere (no `an` needed)
# --------------------------------------------------------------------------

# Directions a director plausibly writes, and how each must read at the
# boundary. `static` is the muvid#44 case: it is muvid's own prose spelling of
# "no move" and `an`'s is `hold`, which is precisely the translation that was
# missing.
CAMERA_DIRECTION_CASES = [
    ("", "hold"),
    ("static", "hold"),
    ("locked off", "hold"),
    ("handheld, drifting", "hold"),
    ("slow push-in", "push_in"),
    ("SLOW PUSH IN", "push_in"),
    ("dolly in on her face", "push_in"),
    ("zoom in hard", "zoom_in"),
    ("pull back to reveal the room", "pull_out"),
    ("zoom out", "zoom_out"),
    ("pan left across the crowd", "pan_left"),
    ("pan right", "pan_right"),
    ("tilt up to the sky", "tilt_up"),
    ("tilt down", "tilt_down"),
]


@pytest.mark.parametrize("direction,expected", CAMERA_DIRECTION_CASES)
def test_an_camera_move_translates_prose(direction, expected):
    assert an_camera_move(direction) == expected


@pytest.mark.parametrize("direction,expected", CAMERA_DIRECTION_CASES)
def test_emitted_scene_md_carries_the_translated_move(direction, expected):
    """The translation is not merely available — the template uses it."""
    assert _emitted_move(direction) == expected


def test_the_emitted_move_is_never_the_muvid_spelling():
    """The literal that broke muvid#44 must not reappear in the template."""
    for direction, _ in CAMERA_DIRECTION_CASES:
        assert _emitted_move(direction) != "static"


def test_first_phrase_in_the_table_wins():
    """A direction naming two moves resolves by table order, not dict order."""
    assert an_camera_move("push in, then pan left") == "push_in"


# --------------------------------------------------------------------------
# The pins against `an` itself — skipped when `an` is absent
# --------------------------------------------------------------------------


@needs_an
def test_every_translated_move_is_in_ans_vocabulary():
    """The whole table, pinned against the set `an` actually accepts.

    Imported from `an`, never re-typed — this is the test that goes red when
    `an` renames or retires a move, which is the drift muvid#44 was.
    """
    emitted = {move for _, move in AN_CAMERA_MOVE_PHRASES} | {DFLT_AN_CAMERA_MOVE}
    unknown = sorted(emitted - set(AN_CAMERA_MOVES))
    assert not unknown, (
        f"muvid emits camera moves {unknown} that `an` does not implement "
        f"(it has: {sorted(AN_CAMERA_MOVES)})."
    )


@needs_an
def test_the_fallback_move_is_ans_no_op():
    """`hold` is `an`'s spelling of "the camera does not move"."""
    assert DFLT_AN_CAMERA_MOVE in AN_CAMERA_MOVES
    assert AN_CAMERA_MOVES[DFLT_AN_CAMERA_MOVE](4.0) == []


@needs_an
@pytest.mark.parametrize("direction,_expected", CAMERA_DIRECTION_CASES)
def test_synthesized_scene_md_validates_against_an(direction, _expected):
    """The full round trip: muvid's `scene.md` → `an`'s IR → semantic validate.

    Zero ERROR findings. `an` errors exactly where it would raise at compile,
    so this is the check that would have caught muvid#44 the day `an`
    tightened the rule — and it costs no render.
    """
    from an.ir.sync import markdown_to_ir
    from an.ir.validate import validate_semantic

    report = validate_semantic(markdown_to_ir(_scene_md(direction)))
    errors = [f for f in report.findings if f.severity == "error"]
    assert not errors, [f"{f.ir_path}: {f.description}" for f in errors]
