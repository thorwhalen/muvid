"""The scene compiler: geometry stays on the canvas, and no lyric vanishes.

Regressions for the correctness review of #96:

* ``karaoke_wipe`` and ``concrete_page`` computed horizontal positions in
  HEIGHT units and used them as WIDTH fractions, so on 16:9 every word sat
  1.78x too far from centre and a normal 47-character line put 4 of 10 words
  off the canvas.
* ``karaoke_wipe`` drew consecutive lines on top of each other whenever the
  gap between them was shorter than the 1 s preroll — the common case.
* the section fallback the spec's docstring promised was never implemented, so
  a treatment that named only ``chorus`` rendered the verses as nothing.
* a word shorter than its attack never reached full opacity.
"""

from __future__ import annotations

import pytest

from muvid.lyricvid import spec as S
from muvid.lyricvid.scene import ARCHETYPE_FNS, Canvas, compile_scene
from muvid.lyricvid.timed_text import Line, Section, TimedText, Word, from_words

WIDE = Canvas(width=1920, height=1080, fps=30)
LONG_LINE = "the night we met i knew i needed you so tonight"  # 47 chars


def _tt_one_line(text: str, t0: float = 1.0, per_word: float = 0.4) -> TimedText:
    words = text.split()
    return from_words(
        [(w, t0 + i * per_word, t0 + (i + 1) * per_word - 0.05) for i, w in enumerate(words)],
        duration=t0 + len(words) * per_word + 1.0,
    )


@pytest.mark.parametrize("archetype", ["karaoke_wipe", "concrete_page", "scatter",
                                       "stacked_lines", "one_word_centred", "text_on_path"])
def test_every_cue_stays_on_a_16x9_canvas(archetype):
    tt = _tt_one_line(LONG_LINE)
    spec = S.TreatmentSpec(scenes=(S.Scene(archetype=archetype),))
    scene = compile_scene(spec, tt, canvas=WIDE)
    assert scene.cues, archetype
    off = [(c.text, round(c.x, 3)) for c in scene.cues if not (0.0 <= c.x <= 1.0)]
    assert not off, f"{archetype}: cues off-canvas: {off}"
    off_y = [(c.text, round(c.y, 3)) for c in scene.cues if not (0.0 <= c.y <= 1.0)]
    assert not off_y, f"{archetype}: cues off-canvas vertically: {off_y}"


def test_karaoke_wipe_words_are_centred_as_a_group_on_16x9():
    tt = _tt_one_line(LONG_LINE)
    scene = compile_scene(
        S.TreatmentSpec(scenes=(S.Scene(archetype="karaoke_wipe"),)), tt, canvas=WIDE
    )
    wipes = [c for c in scene.cues if c.layer == 1]
    assert wipes
    centre = (min(c.x for c in wipes) + max(c.x for c in wipes)) / 2
    assert abs(centre - 0.5) < 0.05, centre


def test_karaoke_wipe_never_shows_two_live_lines_at_once():
    # two lines 0.35 s apart — closer than the 1 s preroll
    l0 = Line(words=tuple(Word(text=w, start=9.0 + i * 0.4, end=9.35 + i * 0.4)
                          for i, w in enumerate("first line of song".split())), index=0)
    l1 = Line(words=tuple(Word(text=w, start=11.05 + i * 0.4, end=11.4 + i * 0.4)
                          for i, w in enumerate("second line here".split())), index=1)
    tt = TimedText(sections=(Section(label="*", lines=(l0, l1)),), duration=14.0)
    scene = compile_scene(
        S.TreatmentSpec(scenes=(S.Scene(archetype="karaoke_wipe"),)), tt, canvas=WIDE
    )
    live = [c for c in scene.cues if c.layer == 0 and abs(c.y - 0.80) < 1e-6]
    assert len(live) == 2
    a, b = sorted(live, key=lambda c: c.t_in)
    assert b.t_in >= a.t_out - 1e-9, (a.t_in, a.t_out, b.t_in)
    # and the preview of line 2 is gone by the time line 2 goes live
    previews = [c for c in scene.cues if c.layer == 0 and c.y > 0.80 + 1e-6]
    assert previews and all(p.t_out <= b.t_in + 1e-9 for p in previews)


def test_sections_no_scene_names_fall_back_instead_of_vanishing():
    verse = Line(words=(Word(text="verse", start=1.0, end=1.5),), index=0)
    chorus = Line(words=(Word(text="chorus", start=5.0, end=5.5),), index=1)
    tt = TimedText(
        sections=(Section(label="verse", lines=(verse,)),
                  Section(label="chorus", lines=(chorus,))),
        duration=7.0,
    )
    only_chorus = S.TreatmentSpec(
        scenes=(S.Scene(applies_to=("chorus",), archetype="scatter"),)
    )
    scene = compile_scene(only_chorus, tt, canvas=WIDE)
    texts = sorted(c.text for c in scene.cues)
    assert texts == ["chorus", "verse"], texts
    assert scene.meta["uncovered_sections"] == ["verse"]

    # a '*' scene claims the rest, and a named scene still wins its section
    both = S.TreatmentSpec(scenes=(
        S.Scene(applies_to=("*",), archetype="one_word_centred"),
        S.Scene(applies_to=("chorus",), archetype="scatter"),
    ))
    scene = compile_scene(both, tt, canvas=WIDE)
    assert sorted(c.text for c in scene.cues) == ["chorus", "verse"]
    assert "uncovered_sections" not in scene.meta


def test_a_word_shorter_than_the_attack_still_arrives_in_time():
    tt = from_words([("yo", 1.0, 1.18)], duration=3.0)
    spec = S.TreatmentSpec(scenes=(S.Scene(archetype="one_word_centred",
                                           timing=S.Timing(attack_s=0.5)),))
    cue = compile_scene(spec, tt, canvas=WIDE).cues[0]
    assert cue.t_full <= cue.t_out


def test_the_three_archetype_tables_agree():
    """spec.ARCHETYPES (what the model may name), scene.ARCHETYPE_FNS (what
    renders) and the nw Template titles must be the same set, or a name the
    model chose silently falls back to one_word_centred."""
    from muvid.genre_lyric_video import _ARCHETYPE_TITLES

    assert set(S.ARCHETYPES) == set(ARCHETYPE_FNS) == set(_ARCHETYPE_TITLES)


def test_from_dict_is_total_over_hostile_input():
    hostile = {
        "scenes": [None, "x", {"applies_to": "chorus", "timing": "fast", "shape": "circle",
                              "archetype": 7}],
        "direction": {"palette": {"foo": 1}, "motion_vocabulary": "fade",
                      "typography": {"weight": "700", "tracking": None}},
    }
    spec = S.TreatmentSpec.from_dict(hostile)
    assert S.validate(spec) == [] or all(isinstance(e, str) for e in S.validate(spec))
    fixed, notes = S.repair(spec)
    assert S.validate(fixed) == []
    assert fixed.scenes[-1].applies_to == ("chorus",)
    assert fixed.direction.motion_vocabulary == ("fade",)
    # repair is idempotent
    assert S.repair(fixed)[0] == fixed


def test_json_schema_accepts_what_to_dict_emits():
    jsonschema = pytest.importorskip("jsonschema")
    schema = S.json_schema()
    for spec in (S.TreatmentSpec(),
                 S.TreatmentSpec(scenes=(S.Scene(archetype="shape_fill",
                                                 shape=S.ShapeRef()),))):
        jsonschema.validate(spec.to_dict(), schema)
