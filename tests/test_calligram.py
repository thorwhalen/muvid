"""The ``calligram`` archetype, and the Unicode word boundaries it needs.

Two regressions, both found while building a video of Apollinaire's *Il pleut*:

* ``muvid.align`` and ``muvid.lyricvid.timed_text`` both tokenised with
  ``[a-z0-9']+`` — ASCII ONLY — so every accented letter was a word boundary.
  ``même`` tokenised as ``('m', 'me')``, ``cabrés`` as ``('cabr', 's')``, and a
  one-letter word like ``ô`` or ``à`` disappeared entirely. On a real French
  song this cost 7 of 67 words their measured onset.
* no archetype could lay a line out as anything but a horizontal row, so a
  calligram — the shape made by the run of the text itself — was unreachable.
"""

from __future__ import annotations

import re

import pytest

from muvid.align import _WORD_TOKEN_RE, _tokenize
from muvid.lyricvid import spec as S
from muvid.lyricvid.scene import ARCHETYPE_FNS, Canvas, compile_scene
from muvid.lyricvid.timed_text import _LYRIC_TOKEN_RE, _lyric_tokens, from_words

PORTRAIT = Canvas(width=1440, height=2560, fps=30)

#: Streaks 1 and 3 of the 1918 Mercure de France setting of "Il pleut".
IL_PLEUT = [
    "Il pleut des voix de femmes comme si elles étaient mortes même dans le souvenir",
    "et ces nuages cabrés se prennent à hennir tout un univers de villes auriculaires",
]


# --------------------------------------------------------------------------
# the tokenizer
# --------------------------------------------------------------------------


def test_the_two_token_regexes_are_the_same_pattern():
    """``timed_text`` indexes into ``align``'s tokens by position.

    They are two literals in two modules with only a comment holding them
    together, so a fix applied to one and not the other silently misaligns
    every word after the first divergence.
    """
    assert _WORD_TOKEN_RE.pattern == _LYRIC_TOKEN_RE.pattern


@pytest.mark.parametrize(
    "text, expected",
    [
        ("cabrés", ["cabrés"]),
        ("même", ["même"]),
        ("étaient", ["étaient"]),
        ("ô", ["ô"]),  # a one-letter accented word vanished entirely
        ("à", ["à"]),
        ("prennent à hennir", ["prennent", "à", "hennir"]),
        ("Grüße", ["grüße"]),  # not only French
        ("mañana", ["mañana"]),
        ("naïve café", ["naïve", "café"]),
        ("日本語", ["日本語"]),
        ("don't", ["don't"]),  # the ASCII behaviour that must not regress
        ("Don't stop, Me now!", ["don't", "stop", "me", "now"]),
    ],
)
def test_accents_are_letters_not_word_boundaries(text, expected):
    assert _tokenize(text) == expected


def test_lyric_tokens_keep_the_lyrics_own_case_and_accents():
    assert _lyric_tokens(IL_PLEUT[1]) == [
        "et", "ces", "nuages", "cabrés", "se", "prennent", "à", "hennir",
        "tout", "un", "univers", "de", "villes", "auriculaires",
    ]


def test_the_full_poem_tokenises_to_the_word_count_on_the_printed_page():
    """65 + 67 letters over 14 + 15 words, as set in 1918."""
    assert [len(_tokenize(l)) for l in IL_PLEUT] == [15, 14]


# --------------------------------------------------------------------------
# the archetype
# --------------------------------------------------------------------------


def _tt(lines, per_word=0.4):
    words, t = [], 1.0
    for line in lines:
        for w in line.split():
            words.append((w, t, t + per_word - 0.05))
            t += per_word
        t += 1.0
    return from_words(words, duration=t + 1.0)


def _scene(**params):
    spec = S.TreatmentSpec(
        scenes=(S.Scene(archetype="calligram", persistence="hold", params=params),)
    )
    return compile_scene(spec, _tt(IL_PLEUT), canvas=PORTRAIT)


def test_calligram_is_registered_in_all_three_tables():
    from muvid.genre_lyric_video import _ARCHETYPE_TITLES

    assert "calligram" in ARCHETYPE_FNS
    assert "calligram" in S.ARCHETYPES
    assert "calligram" in _ARCHETYPE_TITLES


def test_one_cue_per_LETTER_not_per_word():
    """This is what separates a calligram from every other archetype."""
    scene = _scene()
    letters = sum(len(w) for line in IL_PLEUT for w in line.split())
    assert len(scene.cues) == letters
    assert all(len(c.text) == 1 for c in scene.cues)


def test_letters_descend_one_slot_at_a_time_and_drift_right():
    """Upright letters on a slanting axis — the construction itself."""
    scene = _scene(slants=[0.186, 0.257], head_offsets=[0.0, 11.7])
    first = [c for c in scene.cues][: len(IL_PLEUT[0].replace(" ", ""))]
    ys = [c.y for c in first]
    xs = [c.x for c in first]
    assert ys == sorted(ys), "a streak must descend monotonically"
    assert xs == sorted(xs), "a streak must drift right as it descends"
    steps = [b - a for a, b in zip(ys, ys[1:])]
    pitch = min(steps)
    # a gap of exactly one empty slot between words => some steps are doubled
    assert all(
        s == pytest.approx(pitch) or s == pytest.approx(2 * pitch) for s in steps
    ), steps


def test_exactly_one_empty_slot_between_words():
    scene = _scene()
    pitch = min(round(b.y - a.y, 9) for a, b in zip(scene.cues, scene.cues[1:]) if b.y > a.y)
    n_words = len(IL_PLEUT[0].split())
    streak0 = [c for c in scene.cues][: len(IL_PLEUT[0].replace(" ", ""))]
    doubles = [1 for a, b in zip(streak0, streak0[1:]) if round(b.y - a.y, 6) == round(2 * pitch, 6)]
    assert len(doubles) == n_words - 1


def test_the_fan_opens_each_streak_is_steeper_than_the_one_before():
    scene = _scene(slant=0.19, slant_step=0.042)
    n0 = len(IL_PLEUT[0].replace(" ", ""))
    s0, s1 = scene.cues[:n0], scene.cues[n0:]
    def slope(cs):
        return (cs[-1].x - cs[0].x) / (cs[-1].y - cs[0].y)
    assert slope(s1) > slope(s0)


def test_every_letter_stays_inside_the_requested_box():
    scene = _scene(top=0.05, bottom=0.95, left=0.10, right=0.90)
    assert all(0.10 <= c.x <= 0.90 for c in scene.cues)
    assert all(0.05 <= c.y <= 0.95 for c in scene.cues)


def test_the_shape_is_identical_in_portrait_and_landscape():
    """Layout is solved in slot units and only fitted at the end, so the
    shape must not depend on the frame it lands in."""
    spec = S.TreatmentSpec(scenes=(S.Scene(archetype="calligram", persistence="hold"),))
    tt = _tt(IL_PLEUT)
    a = compile_scene(spec, tt, canvas=Canvas(width=1440, height=2560))
    b = compile_scene(spec, tt, canvas=Canvas(width=1920, height=1080))
    def norm(cs):  # shape = positions relative to the block's own bounding box
        xs, ys = [c.x for c in cs], [c.y for c in cs]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        return [(round((c.x - min(xs)) / w, 6), round((c.y - min(ys)) / h, 6)) for c in cs]
    assert norm(a.cues) == norm(b.cues)


def test_dim_persistence_emits_the_handover_as_two_cues_not_a_renderer_effect():
    """``Cue.extra['ignite_at']`` is read by render_web ONLY, so an archetype
    that relied on it renders as a flat bright page under the default ``ass``
    backend. Two overlapping cues work on both."""
    spec = S.TreatmentSpec(
        scenes=(S.Scene(archetype="calligram", persistence="dim", motion="fade"),)
    )
    scene = compile_scene(spec, _tt(IL_PLEUT), canvas=PORTRAIT)
    letters = sum(len(w) for line in IL_PLEUT for w in line.split())
    assert len(scene.cues) == 2 * letters
    ghost = [c for c in scene.cues if c.t_in == 0.0]
    ink = [c for c in scene.cues if c.t_in > 0.0]
    assert len(ghost) == len(ink) == letters
    assert all(c.t_gone is not None for c in ghost), "the ghost must clear"
    assert all(c.layer < i.layer for c, i in zip(ghost, ink))
    # every ghost hands over to its own letter at the same place
    assert {(round(c.x, 9), round(c.y, 9)) for c in ghost} == {
        (round(c.x, 9), round(c.y, 9)) for c in ink
    }


def test_a_lyric_with_no_words_does_not_explode():
    spec = S.TreatmentSpec(scenes=(S.Scene(archetype="calligram"),))
    assert compile_scene(spec, from_words([], duration=3.0), canvas=PORTRAIT).cues == ()


def test_the_width_constraint_binds_when_the_fan_is_wider_than_it_is_tall():
    """The fit takes the SMALLER of the height- and width-derived pitch.

    A mutant that sized on height alone survives every test above, because a
    normal poem is far taller than it is wide and the height limit binds. It
    only shows up on a fan that is wide relative to its depth, where sizing on
    height alone runs the letters straight off the sides.
    """
    wide = [" ".join(["ab"] * 3)] * 5  # short lines...
    spec = S.TreatmentSpec(
        scenes=(
            S.Scene(
                archetype="calligram",
                persistence="hold",
                # ...spread far apart, so the block is wide and shallow
                params={"head_offsets": [0, 25, 50, 75, 100],
                        "slants": [0.1] * 5,
                        "left": 0.05, "right": 0.95, "top": 0.05, "bottom": 0.95},
            ),
        )
    )
    scene = compile_scene(spec, _tt(wide), canvas=PORTRAIT)
    assert scene.cues
    xs = [c.x for c in scene.cues]
    assert min(xs) >= 0.05 and max(xs) <= 0.95, (min(xs), max(xs))
