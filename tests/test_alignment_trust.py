"""A low-confidence alignment is a refusal, not a number (muvid#59).

The defect: on a rigidly programmed, repetitive track the whole-clip cross-correlation
surface has near-tied peaks spaced at musical periods, so the estimator returns the
argmax of a coin flip. It reported low confidence (0.086-0.121) and handed the offsets
back anyway with ``overlaps=True``, and three clips of a real shoot came back 83 s, 174 s
and 83 s wrong. Nothing downstream re-measures an offset, so the failure presented as a
silently desynced video rather than as an error.

What is pinned here is that "we are not sure" now STOPS the render:

- the verdict lives in one place (``edl.vouches_for``) and rides on the record
  (``FootageAlignment.reliable`` / ``.support``), so a persisted alignment carries it;
- ``validate_edl`` — the ONE gate — refuses to cut to an unvouched clip, and the escape
  (``allow_unreliable=True``) has to be said out loud, the way an unpriceable shot has to
  be passed with ``--allow-unpriced`` rather than being read as free;
- a refusal is not a removal: the clip keeps its record and stays addressable;
- a record from before the fields existed has its verdict DERIVED, not assumed —
  otherwise the upgrade would quietly un-flag the very alignments the issue is about.

The estimator puts each clip's windows to a vote, fits the window to the clip, and
GRADES each window's evidence, so the verdict rests on ``support`` rather than on a
coefficient. The end-to-end cases here build a loop track that defeats the whole-clip
argmax — asserted, in its own test, so the fixture cannot quietly stop being adversarial
— and then show consensus recovering the offset with support above the threshold.

``MIN_SUPPORT`` is 0.5 EXCLUSIVE because 0.5 is the ceiling of ballot-only evidence:
above it, at least one window found the offset unaided. Measured on the real master, 24
correct alignments against six pure-noise clips, that cut passes 18/24 correct and 0/6
noise, where 0.25 passes 24/24 and 1/6.

What is still NOT pinned is that the verdict is always right. A clip too short to hold
two windows has ``support is None`` and falls back to the coefficient, which does not
rank correctness there (worst correct 0.129 against worst pure noise 0.139; on the real
shoot the WRONG offset scored highest of three). That case has its own test, asserting
only that muvid can TELL. What muvid should do about it is muvid#91.

Everything here is offline. The end-to-end cases synthesise their own audio; the real
shoot's footage is verification material, not a fixture.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from muvid.footage.align import MIN_CONFIDENCE, MIN_SUPPORT, vouches_for
from muvid.footage.edl import (
    EdlEntry,
    FootageAlignment,
    UnreliableAlignmentError,
    validate_edl,
)

SR = 44100
SONG_S = 20.0

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
try:
    import mixing.audio as _ma

    HAS_ALIGNER = hasattr(_ma, "align_clips_to_reference")
except Exception:  # pragma: no cover - absence is what is being detected
    HAS_ALIGNER = False
needs_pipeline = pytest.mark.skipif(
    not (HAS_FFMPEG and HAS_ALIGNER), reason="needs ffmpeg + mixing"
)


def _align(clip_id: str, *, confidence=0.9, support=None, reliable=True):
    return FootageAlignment(
        clip_id, 0.0, confidence, SONG_S, (0.0, SONG_S), True, support, reliable
    )


class TestTheVerdict:
    """``vouches_for`` is the ONE place the trust question is answered."""

    def test_support_decides_when_it_was_measured(self):
        # A high coefficient does not rescue an offset only a tenth of the clip agrees
        # with — that combination IS muvid#59's failure mode.
        assert not vouches_for(confidence=0.99, support=MIN_SUPPORT / 2)
        assert vouches_for(confidence=0.01, support=1.0)

    def test_confidence_decides_only_when_support_is_absent(self):
        assert vouches_for(confidence=MIN_CONFIDENCE, support=None)
        assert not vouches_for(confidence=MIN_CONFIDENCE / 2, support=None)

    def test_the_issues_own_numbers_are_not_vouched_for(self):
        # Two of the three wrong offsets scored under the whole-clip threshold; the
        # third (0.121) did not, which is exactly why support is the measure of record
        # and this threshold is documented as the weaker fallback.
        assert not vouches_for(confidence=0.086, support=None)
        # ...and once support is measured, all three are refused.
        for support in (0.0, 0.04, 0.1):
            assert not vouches_for(confidence=0.121, support=support)


class TestTheThresholdIsTheBallotCeiling:
    """``MIN_SUPPORT`` is 0.5 exclusive because 0.5 is what ballot-only evidence tops out at.

    ``mixing`` grades each window: 1.0 where that window's own argmax reached the
    offset, at most ``BALLOT_VOTE_WEIGHT`` (0.5) where the offset was merely on its
    ballot. So a support of exactly 0.5 is the case where every window had it on the
    ballot and NOT ONE found it unaided — the ambiguous case by construction — and
    anything above it means at least one did.

    Measured on the muvid#59 master, 24 correct alignments against six pure-noise
    clips: ``> 0.5`` passes 18/24 correct and **0/6** noise, while ``>= 0.25`` passes
    24/24 correct and **1/6** noise. This is the only cut measured that refuses every
    known-wrong case.
    """

    def test_exactly_the_ballot_ceiling_does_not_vouch(self):
        # The strictness IS the semantics: at 0.5 nothing found the offset unaided.
        assert not vouches_for(confidence=0.0, support=0.5)
        assert vouches_for(confidence=0.0, support=0.5 + 1e-9)

    def test_the_window_does_not_enter_the_verdict(self):
        # Deliberately reversed. An earlier revision refused to read a support from a
        # fitted window; measured on six noise clips at a 6.67 s window, that guard
        # routes them to the confidence fallback and VOUCHES for four of them, while
        # reading the graded support refuses all six. The window is a diagnostic now.
        for window_s in (None, 3.0, 6.67, 20.0, 60.0):
            assert vouches_for(confidence=0.0, support=0.8, window_s=window_s)
            assert not vouches_for(confidence=0.0, support=0.3, window_s=window_s)

    def test_the_noise_floor_this_threshold_was_chosen_against(self):
        # The six pure-noise clips, graded, at a fitted window — every one refused.
        for support in (0.16, 0.16, 0.17, 0.12, 0.33, 0.02):
            assert not vouches_for(confidence=0.9, support=support)
        # ...and the six correct short clips it costs, recorded so the trade is visible.
        for support in (0.30, 0.32, 0.35, 0.36, 0.37, 0.38):
            assert not vouches_for(confidence=0.9, support=support)

    def test_on_short_repetitive_clips_vouched_and_correct_are_the_same_set(
        self, tmp_path
    ):
        """The threshold's whole claim, on adversarial material: vouched IFF correct.

        The direction the floor bump bought cannot be committed — it was measured on the
        muvid#59 master, where 21 clips in the 12-29 s band went from 7-of-15 wrong under
        the pre-fit estimator to 21 of 21 correct once the window is fitted per clip. So
        this reproduces the *property* on the loop fixture instead, sweeping clip lengths
        rather than picking one.

        Measured across this sweep: seven lengths land on the right offset with support
        0.57-0.80, and two (10 s and 14 s) land on a REPEAT — and those two score 0.487
        and 0.451, both under the ballot-only ceiling, so the gate refuses exactly them.

        The assertion is the biconditional, not either list. It stays true if a future
        estimator rescues the two wrong ones (they become correct AND vouched); it fails
        if the gate ever vouches for a repeat or refuses a good alignment. Asserting
        "10 s is wrong" would have pinned the bug instead of the guarantee, and picking
        the one length that passed would have been fitting the test to the outcome.
        """
        if not (HAS_FFMPEG and HAS_ALIGNER and _has_support()):
            pytest.skip("needs ffmpeg + mixing>=0.0.49")
        from muvid.footage.align import align_footage

        song_p, song = _repetitive_song(tmp_path)
        song_s = len(song) / SR
        clips = [
            (
                f"L{seconds}",
                str(
                    _device_recording(
                        tmp_path,
                        song,
                        f"L{seconds}",
                        at=28.0,
                        seconds=seconds,
                        seed=101,
                    )
                ),
            )
            for seconds in (8.0, 10.0, 12.0, 14.0, 16.0, 20.0)
        ]
        aligned = align_footage(str(song_p), clips, song_duration=song_s)

        for a in aligned:
            correct = abs(a.offset_s - 28.0) < 0.1
            assert a.reliable is correct, (
                f"{a.clip_id}: offset {a.offset_s:.3f} is "
                f"{'correct' if correct else 'a repeat'} but the gate says "
                f"reliable={a.reliable} (support {a.support}) — the threshold has "
                "stopped separating correct from repeated on this fixture"
            )
        # And the fixture is still adversarial: if everything lands correct, this test
        # only proves the gate does not refuse good clips, which is half its job.
        assert not all(abs(a.offset_s - 28.0) < 0.1 for a in aligned), (
            "no clip landed on a repeat, so the fixture no longer exercises the refusal "
            "half — widen the sweep before trusting this as a separation test"
        )

    def test_the_noise_clip_that_predates_this_release_is_still_refused(self):
        """0.33 is the fixture to check before ever loosening this threshold.

        One of the six noise clips scored 0.33 BOTH before and after grading — it was
        already clearing the old 0.25 threshold, for reasons that have nothing to do
        with the release that prompted this number. So it is the case that says whether
        a future, lower threshold has quietly re-admitted pure noise: grading did not
        put it there and grading will not take it away.
        """
        assert not vouches_for(confidence=0.9, support=0.33)

    def test_the_window_survives_the_round_trip(self):
        a = FootageAlignment(
            "A", 0.0, 0.2, 30.0, (0.0, 30.0), True, 0.5, True, 6.67, 3.33
        )
        back = FootageAlignment.from_dict(a.to_dict())
        assert back == a
        assert (back.window_s, back.hop_s) == (6.67, 3.33)

    def test_a_stored_verdict_is_never_re_judged_against_the_new_threshold(self):
        # The compat read that matters most here. Alignments written by muvid 0.0.51/52
        # carry an UNGRADED support — the shoot's own correct offsets were 0.42 and 0.46
        # on that scale — which this threshold would refuse. They are never re-judged,
        # because `reliable` was persisted and only an ABSENT verdict is derived.
        stored = {
            "clip_id": "A",
            "offset_s": 0.0,
            "confidence": 0.05,
            "duration_s": 120.0,
            "coverage": [0.0, 120.0],
            "support": 0.42,  # correct, on the pre-grading scale
            "reliable": True,
        }
        assert FootageAlignment.from_dict(stored).reliable is True


class TestTheRecordCarriesIt:
    """A verdict that does not survive the round trip is a verdict the render never sees."""

    def test_round_trip(self):
        a = _align("A", confidence=0.2, support=0.42, reliable=True)
        assert FootageAlignment.from_dict(a.to_dict()) == a

    def test_support_none_stays_none(self):
        # "not measured" is a different fact from "measured zero", and 0.0 would read
        # as a refusal the aligner never actually made.
        a = _align("A", support=None)
        assert FootageAlignment.from_dict(a.to_dict()).support is None

    def test_a_record_written_before_the_fields_existed_gets_a_DERIVED_verdict(self):
        # Not a default — a derivation. Blanket-defaulting True would have been a
        # regression: an alignments.json written before this PR holds exactly the
        # offsets muvid#59 is about, so a blanket True would render them AND drop them
        # out of the caller-facing weak list, which is quieter than the behaviour the
        # issue was filed against.
        legacy = {
            "clip_id": "A",
            "offset_s": 1.0,
            "confidence": 0.9,
            "duration_s": 10.0,
            "coverage": [1.0, 11.0],
        }
        good = FootageAlignment.from_dict(legacy)
        assert good.reliable is True and good.support is None

        bad = FootageAlignment.from_dict({**legacy, "confidence": 0.086})
        assert bad.reliable is False and bad.support is None

    def test_a_legacy_low_confidence_record_is_refused_by_the_gate(self):
        legacy = {
            "clip_id": "A",
            "offset_s": 0.0,
            "confidence": 0.086,  # muvid#59's own c01/c03
            "duration_s": SONG_S,
            "coverage": [0.0, SONG_S],
        }
        aligns = [FootageAlignment.from_dict(legacy)]
        with pytest.raises(UnreliableAlignmentError):
            validate_edl([EdlEntry(0.0, SONG_S, "A")], aligns, SONG_S)

    def test_a_legacy_high_confidence_record_still_renders(self):
        # The other half of the derivation: a project that was fine before this PR is
        # still fine after it. Nobody's working edit breaks on upgrade.
        legacy = {
            "clip_id": "A",
            "offset_s": 0.0,
            "confidence": 0.6,
            "duration_s": SONG_S,
            "coverage": [0.0, SONG_S],
        }
        aligns = [FootageAlignment.from_dict(legacy)]
        entries = validate_edl([EdlEntry(0.0, SONG_S, "A")], aligns, SONG_S)
        assert [e.clip_id for e in entries] == ["A"]

    def test_an_explicit_verdict_is_never_overridden_by_the_derivation(self):
        # A record that DOES carry the field is authoritative — the aligner may have
        # refused it for a reason no coefficient can reconstruct.
        d = _align("A", confidence=0.9, support=None, reliable=False).to_dict()
        assert FootageAlignment.from_dict(d).reliable is False


class TestTheGateRefuses:
    """``validate_edl`` is the ONE gate, so this is where the refusal lands."""

    def test_cutting_to_an_unvouched_clip_is_refused(self):
        aligns = [_align("A", confidence=0.05, reliable=False)]
        with pytest.raises(UnreliableAlignmentError) as e:
            validate_edl([EdlEntry(0.0, SONG_S, "A")], aligns, SONG_S)
        assert e.value.clip_ids == ["A"]
        # A caller catching ValueError (every current call site does) still catches it.
        assert isinstance(e.value, ValueError)

    def test_the_message_says_what_to_do_about_it(self):
        aligns = [_align("A", confidence=0.05, support=0.03, reliable=False)]
        with pytest.raises(UnreliableAlignmentError) as e:
            validate_edl([EdlEntry(0.0, SONG_S, "A")], aligns, SONG_S)
        msg = str(e.value)
        assert "out of sync" in msg  # the consequence, not just the number
        assert "allow_unreliable" in msg  # the escape
        assert "support 0.03" in msg and "confidence 0.050" in msg  # the evidence

    def test_every_offending_clip_is_named_at_once(self):
        # One round trip per bad clip would make a three-camera shoot three round trips.
        aligns = [
            _align("A", reliable=False),
            _align("B", reliable=True),
            _align("C", reliable=False),
        ]
        edl = [
            EdlEntry(0.0, 5.0, "A"),
            EdlEntry(5.0, 10.0, "B"),
            EdlEntry(10.0, 15.0, "C"),
            EdlEntry(15.0, SONG_S, "A"),  # a repeat must not double-report
        ]
        with pytest.raises(UnreliableAlignmentError) as e:
            validate_edl(edl, aligns, SONG_S)
        assert e.value.clip_ids == ["A", "C"]

    def test_opting_in_renders_it(self):
        aligns = [_align("A", reliable=False)]
        entries = validate_edl(
            [EdlEntry(0.0, SONG_S, "A")], aligns, SONG_S, allow_unreliable=True
        )
        assert [e.clip_id for e in entries] == ["A"]

    def test_an_unvouched_clip_the_edit_never_cuts_to_is_not_an_error(self):
        # The refusal is about what is RENDERED, not about what is in the project — a
        # source must never leave the addressable set as a side effect of being measured.
        aligns = [_align("A", reliable=True), _align("B", reliable=False)]
        entries = validate_edl([EdlEntry(0.0, SONG_S, "A")], aligns, SONG_S)
        assert [e.clip_id for e in entries] == ["A"]

    def test_a_gap_over_an_unvouched_span_is_fine(self):
        aligns = [_align("A", reliable=False)]
        entries = validate_edl([EdlEntry(0.0, SONG_S, "")], aligns, SONG_S)
        assert entries[0].is_gap

    def test_a_malformed_edl_is_reported_as_malformed(self):
        # Trust is checked LAST: an EDL with a hole in it is a bad EDL, and blaming the
        # footage would send the caller off to re-align over a problem they authored.
        aligns = [_align("A", reliable=False)]
        with pytest.raises(ValueError, match="gap before entry"):
            validate_edl(
                [EdlEntry(0.0, 5.0, "A"), EdlEntry(10.0, SONG_S, "A")], aligns, SONG_S
            )


# -- end to end: the aligner reaches the same verdict on real audio ----------


def _song(tmp_path: Path) -> tuple[Path, np.ndarray]:
    """A 20 s song with enough spectral variety to be locatable within itself."""
    from scipy.io import wavfile

    t = np.arange(int(SONG_S * SR)) / SR
    x = np.zeros_like(t)
    for f0, f1 in [(180, 520), (440, 130), (700, 900), (110, 250)]:
        x += np.sin(2 * np.pi * (f0 + (f1 - f0) * (t / t[-1])) * t)
    x *= 0.6 + 0.4 * np.sin(2 * np.pi * 1.7 * t)
    x /= np.max(np.abs(x))
    p = tmp_path / "song.wav"
    wavfile.write(str(p), SR, (x * 32767).astype(np.int16))
    return p, x


def _clip(tmp_path: Path, samples: np.ndarray, name: str) -> Path:
    """``samples`` as the audio of a small video — a stand-in for a phone recording."""
    from scipy.io import wavfile

    seg = samples / np.max(np.abs(samples))
    awav = tmp_path / f"{name}_a.wav"
    wavfile.write(str(awav), SR, (seg * 32767).astype(np.int16))
    out = tmp_path / f"{name}.mp4"
    dur = len(seg) / SR
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=320x240:rate=10:duration={dur}",
            "-i",
            str(awav),
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
    )
    return out


@needs_pipeline
def test_a_matching_clip_is_vouched_for_and_an_unrelated_one_is_not(tmp_path):
    from muvid.footage.align import align_footage

    song_p, song = _song(tmp_path)
    rng = np.random.default_rng(59)
    real = song[int(3.0 * SR) : int(13.0 * SR)] + rng.normal(0, 0.1, int(10.0 * SR))
    junk = rng.normal(0, 1.0, int(10.0 * SR))  # a clip of something else entirely
    clips = [
        ("REAL", str(_clip(tmp_path, real, "real"))),
        ("JUNK", str(_clip(tmp_path, junk, "junk"))),
    ]
    by = {a.clip_id: a for a in align_footage(str(song_p), clips, song_duration=SONG_S)}

    assert by["REAL"].offset_s == pytest.approx(3.0, abs=0.1)
    assert by["REAL"].reliable is True
    # The unrelated clip keeps its record — it is still in the project, still named,
    # still addressable. What it has lost is the right to be cut to.
    assert set(by) == {"REAL", "JUNK"}
    assert by["JUNK"].reliable is False

    # Cut to it over a span it genuinely covers — the offset an unrelated clip lands on
    # is arbitrary, so reading the span off the record is the only stable way to make
    # the EDL structurally valid and leave the refusal as the only thing under test.
    lo, hi = by["JUNK"].coverage
    span = EdlEntry(lo, min(lo + 1.0, hi), "JUNK")
    with pytest.raises(UnreliableAlignmentError):
        validate_edl([span], list(by.values()), SONG_S)


class TestTheToolSurface:
    """What a remote caller sees: a refusal where it renders, a report where it does not."""

    def _project(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
        from muvid.footage.workspace import FootageWorkspace

        song_p, song = _song(tmp_path)
        clip_p = _clip(tmp_path, song[: int(10 * SR)], "c")
        proj = FootageWorkspace.for_email("u@x.com").create_project("p")
        proj.set_song(str(song_p), ext="wav")
        proj.add_clip("A", str(clip_p), ext="mp4")
        dur = proj.song_duration()
        proj.save_alignments(
            [FootageAlignment("A", 0.0, 0.04, 10.0, (0.0, 10.0), True, 0.05, False)]
        )
        return proj, dur

    @needs_pipeline
    def test_assemble_refuses_and_says_which_clips(self, tmp_path, monkeypatch):
        pytest.importorskip("fastmcp")
        import muvid.mcp.footage_tools as ft
        from fastmcp.exceptions import ToolError
        from muvid.mcp.identity import use_email

        self._project(tmp_path, monkeypatch)
        with use_email("u@x.com"), pytest.raises(ToolError, match="clips: A"):
            ft.assemble_music_video("p")

    @needs_pipeline
    def test_propose_edit_still_answers_and_names_the_weak_span(
        self, tmp_path, monkeypatch
    ):
        # The tool that renders nothing must keep answering: `weak_segments` is the
        # diagnosis a caller needs in order to decide what to do about the refusal.
        pytest.importorskip("fastmcp")
        import muvid.mcp.footage_tools as ft
        from muvid.mcp.identity import use_email

        self._project(tmp_path, monkeypatch)
        with use_email("u@x.com"):
            out = ft.propose_edit("p")
        weak = out["coverage"]["weak_segments"]
        assert [w["clip_id"] for w in weak] == ["A"]
        assert weak[0]["support"] == 0.05

    @needs_pipeline
    def test_align_footage_reports_agree_with_the_records_they_summarise(
        self, tmp_path, monkeypatch
    ):
        """The two report lists say exactly what the records say — no second opinion.

        Asserted as a PROPERTY rather than against a hardcoded verdict on purpose. The
        first version of this test pinned "8 s of noise is unreliable", which was true
        under the old estimator and false under the new one: the same clip scores 0.113
        with consensus against a ``MIN_CONFIDENCE`` of 0.1, so the test failed for a
        reason that had nothing to do with the reporting it claims to cover. A summary
        can only be wrong by disagreeing with what it summarises, and that is what this
        checks — it cannot flip when an estimator is swapped underneath it.
        """
        pytest.importorskip("fastmcp")
        import muvid.mcp.footage_tools as ft
        from muvid.mcp.identity import use_email

        monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
        from muvid.footage.workspace import FootageWorkspace

        song_p, song = _song(tmp_path)
        rng = np.random.default_rng(59)
        proj = FootageWorkspace.for_email("u@x.com").create_project("p")
        proj.set_song(str(song_p), ext="wav")
        proj.add_clip(
            "REAL", str(_clip(tmp_path, song[: int(10 * SR)], "r")), ext="mp4"
        )
        proj.add_clip(
            "JUNK",
            str(_clip(tmp_path, rng.normal(0, 1.0, int(8 * SR)), "j")),
            ext="mp4",
        )
        with use_email("u@x.com"):
            out = ft.align_footage("p")

        by_id = {a["clip_id"]: a for a in out["alignments"]}
        assert set(by_id) == {"REAL", "JUNK"}  # nothing is dropped by a measurement
        assert {u["clip_id"] for u in out["unreliable"]} == {
            cid for cid, a in by_id.items() if not a["reliable"]
        }
        assert set(out["no_consensus"]) == {
            cid for cid, a in by_id.items() if a["support"] is None
        }
        assert out["support_threshold"] == MIN_SUPPORT
        for u in out["unreliable"]:
            # The report rounds for legibility; the record keeps full precision. What
            # must agree is the VALUE, not its formatting — comparing them raw made
            # this fail on 0.124 vs 0.12375..., which is the test being wrong about
            # what it was checking rather than a disagreement worth catching.
            recorded = by_id[u["clip_id"]]["support"]
            assert u["support"] == pytest.approx(recorded, abs=5e-4)


def _has_support() -> bool:
    """Does the installed ``mixing`` report a consensus support fraction at all?"""
    try:
        import dataclasses

        from mixing.audio import ClipAlignment
    except Exception:  # pragma: no cover - absence is what is being detected
        return False
    return "support" in {f.name for f in dataclasses.fields(ClipAlignment)}


#: A capability guard, not a version comparison — a floor says what pip resolved, this
#: says what got imported. `tests/test_ci_extras_canary.py` asserts the same capability
#: WITHOUT a skip, so a CI whose resolver picked an older mixing fails loudly instead of
#: skipping these and reporting green over an estimator muvid#59 was filed about.
needs_support = pytest.mark.skipif(
    not (HAS_FFMPEG and HAS_ALIGNER and _has_support()),
    reason="needs mixing>=0.0.46 (ClipAlignment.support)",
)

#: The loop: a 2 s bar, 45 of them, hits on the dembow 3+3+2 of eight slots.
_BAR_S = 2.0
_BARS = 45
_SLOTS = 8
_DEMBOW = (0, 3, 6)
#: Roots the bar cycles through, and the seed for its per-bar fill.
_ROOTS = (110, 98, 131, 116)
_FILL_SEED = 59


def _struck(seconds: float, f0: float) -> np.ndarray:
    """One percussive, harmonically rich hit — a plucked chord, decaying."""
    t = np.arange(int(seconds * SR)) / SR
    y = np.zeros_like(t)
    for k, mul in enumerate((1, 2, 3, 5)):
        y += np.exp(-14 * t) * (0.85**k) * np.sin(2 * np.pi * f0 * mul * t)
    return y


def _repetitive_song(tmp_path: Path) -> tuple[Path, np.ndarray]:
    """90 s of loop music: one bar, 45 times, with per-bar variation. Repetitive, not tiled.

    Getting this fixture right took three tries and each failure was informative, so the
    reasoning is here rather than in a commit nobody will find:

    - A **through-composed** reference (the 20 s sweep in ``_song`` above) has one
      unambiguous correlation peak. Nothing about muvid#59 can happen on it.
    - A **perfectly tiled** reference is the opposite mistake and a worse one: every
      offset differing by a whole bar is *equally correct*, so there is no answer to get
      right. A first version of this fixture tiled one motif and the test duly "failed"
      at −9.26 s against a "truth" of 28 s that had never been the only right answer.
      Consensus cannot invent an answer the signal does not contain, and demanding that
      it does is a test asserting a fantasy.
    - **Real loop music is neither.** A dembow bar repeats verbatim — same hits, same
      grid, so the correlation surface really is near-tied at musical periods — while
      fills and harmonic motion still make any twenty seconds of it individually
      identifiable. That second property is what a window needs to have an opinion
      worth voting with, and it is exactly what pure tiling removes.

    So: a fixed 3+3+2 bar (the ties), plus one extra hit per bar at a position drawn from
    a seeded sequence and a root cycling through four chords (the identity). Measured on
    the result, the whole-clip estimator lands **48 s wrong at confidence 0.29** while the
    windowed consensus lands within 10 ms — which is muvid#59, reproduced from scratch
    with no copyrighted audio and nothing on disk.
    """
    from scipy.io import wavfile

    rng = np.random.default_rng(_FILL_SEED)
    n_bar = int(_BAR_S * SR)
    slot = n_bar // _SLOTS
    x = np.zeros(n_bar * _BARS)

    def place(sample: np.ndarray, at: int) -> None:
        x[at : at + len(sample)] += sample[: len(x) - at]

    for bar in range(_BARS):
        root = _ROOTS[bar % len(_ROOTS)]
        for s in _DEMBOW:
            place(_struck(0.5, root), bar * n_bar + s * slot)
        # The fill: one hit an octave up, wherever this bar's draw puts it.
        place(
            0.55 * _struck(0.3, root * 4),
            bar * n_bar + int(rng.integers(1, _SLOTS)) * slot,
        )

    x /= np.max(np.abs(x))
    p = tmp_path / "loop.wav"
    wavfile.write(str(p), SR, (x * 32767).astype(np.int16))
    return p, x


def _device_recording(
    tmp_path: Path,
    song: np.ndarray,
    name: str,
    *,
    at: float,
    seconds: float,
    seed: int,
    noise: float = 0.08,
) -> Path:
    """``seconds`` of ``song`` from ``at``, as a second device would have caught it.

    Band-limited and run through a DENSE random room tail, because that is what
    destroys raw-waveform similarity between two microphones in a room while leaving
    onsets where they are — the whole reason the aligner works on an onset envelope.
    A bright single echo would leave the waveform correlated and test nothing.

    ``seed`` is explicit rather than derived from ``name``: ``hash()`` of a string is
    salted per process, so seeding a room impulse response from it makes the room —
    and therefore every number measured through it — different on every run. That is
    how this test first "failed", reporting a support of 0.0 for a run whose material
    no earlier run had ever aligned.

    Written as audio rather than muxed into a video: the aligner reads the audio track
    either way, and encoding a minute of video per case buys the test nothing.
    """
    from scipy.io import wavfile
    from scipy.signal import butter, lfilter

    rng = np.random.default_rng(seed)
    seg = song[int(at * SR) : int((at + seconds) * SR)].copy()
    b, a = butter(4, [200 / (SR / 2), 4500 / (SR / 2)], btype="band")
    seg = lfilter(b, a, seg)
    n_ir = int(0.25 * SR)
    ir = rng.normal(0, 1, n_ir) * np.exp(-np.arange(n_ir) / (0.06 * SR))
    ir[0] += 3.0
    seg = np.convolve(seg, ir)[: len(seg)]
    seg = seg / (np.max(np.abs(seg)) or 1.0) * 0.5 + rng.normal(0, 0.08, len(seg))
    p = tmp_path / f"{name}.wav"
    wavfile.write(str(p), SR, (np.clip(seg, -1, 1) * 32767).astype(np.int16))
    return p


@needs_support
def test_the_old_estimator_is_confidently_wrong_on_this_fixture(tmp_path):
    """The fixture is adversarial — asserted, not assumed (muvid#59).

    The control for the test below. If the whole-clip argmax ever aligns this material
    correctly, the fixture has stopped reproducing the defect and the consensus test
    underneath it is measuring an easy alignment while crediting the fix.
    ``consensus=False`` is a documented contract ("restores the single whole-clip
    correlation exactly"), so this pins a property of the FIXTURE, not the survival of
    a bug. It also exercises the estimator-kwargs seam on a value ``mixing`` honours.
    """
    from muvid.footage.align import align_footage

    song_p, song = _repetitive_song(tmp_path)
    clip = [
        (
            "A",
            str(
                _device_recording(tmp_path, song, "a", at=28.0, seconds=50.0, seed=101)
            ),
        )
    ]
    a = align_footage(str(song_p), clip, song_duration=len(song) / SR, consensus=False)[
        0
    ]

    assert abs(a.offset_s - 28.0) > 1.0, (
        "the whole-clip estimator aligned the fixture correctly, so the fixture no "
        "longer reproduces muvid#59 and the consensus test below is vacuous"
    )
    # And it does not know that it is wrong: the wrong answer clears the confidence
    # threshold, which is the entire reason `support` had to exist.
    assert a.confidence > MIN_CONFIDENCE
    assert a.support is None  # nothing was put to a vote


@needs_support
def test_consensus_recovers_the_offset_and_support_vouches_for_it(tmp_path):
    """The end of the wiring: a vote is held, muvid reads it, and it decides.

    Same fixture the test above shows the old estimator failing on, so what is measured
    here is the fix rather than an easy alignment.
    """
    from muvid.footage.align import align_footage

    song_p, song = _repetitive_song(tmp_path)
    song_s = len(song) / SR
    # Both clips are long enough for the estimator to hold a vote. Only a clip too
    # short for two windows at the 3 s floor comes back support=None — correctly, and
    # that case has its own test rather than being smuggled in here.
    clips = [
        (
            "A",
            str(
                _device_recording(tmp_path, song, "a", at=28.0, seconds=50.0, seed=101)
            ),
        ),
        (
            "B",
            str(
                _device_recording(tmp_path, song, "b", at=40.0, seconds=45.0, seed=202)
            ),
        ),
    ]
    by = {a.clip_id: a for a in align_footage(str(song_p), clips, song_duration=song_s)}

    for cid, truth in (("A", 28.0), ("B", 40.0)):
        a = by[cid]
        assert a.offset_s == pytest.approx(truth, abs=0.1), f"{cid} landed on a repeat"
        # The vote happened and muvid read it — what the floor bump buys.
        assert a.support is not None, f"{cid} has no support; is mixing too old?"
        assert a.support > MIN_SUPPORT
        assert a.reliable is True
        # muvid DERIVES the verdict from what mixing reported rather than forming a
        # second opinion — the property that keeps this one gate rather than two.
        assert a.reliable is vouches_for(confidence=a.confidence, support=a.support)

    # And the gate lets the edit through, which is what a caller actually feels.
    assert validate_edl([EdlEntry(28.0, 40.0, "A")], list(by.values()), song_s)


@needs_support
def test_the_window_is_fitted_to_the_clip_and_reported_with_the_support(tmp_path):
    """The scale of ``support`` is now a per-clip quantity, and it travels with it.

    ``mixing>=0.0.48`` fits the window to the clip, which is what makes short-clip
    offsets work. The consequence a consumer has to handle is that two clips' supports
    are no longer the same measurement — so this pins that the grid is reported, that it
    tracks the clip. The window no longer enters the VERDICT (see
    :class:`TestTheThresholdIsTheBallotCeiling`) but it is still what tells a caller
    which grid a support was measured on, and a support arriving without it would be a
    fraction whose denominator nobody can see.
    """
    from muvid.footage.align import align_footage

    song_p, song = _repetitive_song(tmp_path)
    song_s = len(song) / SR

    def aligned(seconds: float):
        p = _device_recording(
            tmp_path, song, f"len{seconds:g}", at=28.0, seconds=seconds, seed=101
        )
        return align_footage(str(song_p), [("A", str(p))], song_duration=song_s)[0]

    short, mid, full = aligned(12.0), aligned(30.0), aligned(60.0)

    # The grid comes back, and the hop is half the window — one knob, not two.
    for a in (short, mid, full):
        assert a.window_s is not None and a.hop_s == pytest.approx(a.window_s / 2)
    # It tracks the clip, and tops out where mixing caps it.
    assert short.window_s < mid.window_s < full.window_s
    assert full.window_s == pytest.approx(mid.window_s * 2)
    # And all three are judged on their support, whatever grid it came from.
    for a in (short, mid, full):
        assert a.reliable is (a.support > MIN_SUPPORT)


@needs_support
def test_a_wider_window_is_refused_because_it_would_turn_the_gate_off(tmp_path):
    """Measured: ``window_s=45`` on a 50 s clip takes support from 0.75 to None.

    That is the support gate switching off — the verdict silently falls back to the
    confidence coefficient — from a keyword that reads like a tuning knob. An argument
    that quietly disables a safety check is refused rather than documented.
    """
    from muvid.footage.align import align_footage

    song_p, song = _repetitive_song(tmp_path)
    clip = [
        (
            "A",
            str(
                _device_recording(tmp_path, song, "a", at=28.0, seconds=50.0, seed=101)
            ),
        )
    ]
    for kwargs in ({"window_s": 45.0}, {"hop_s": 22.5}):
        with pytest.raises(TypeError, match="MIN_SUPPORT"):
            align_footage(str(song_p), clip, song_duration=len(song) / SR, **kwargs)


@needs_support
def test_the_noise_floor_now_clears_min_confidence(tmp_path):
    """Characterisation: in the unvoted band, the coefficient no longer excludes noise.

    ``MIN_CONFIDENCE = 0.1`` was calibrated (muvid#15) against the whole-clip estimator.
    Consensus changed what the number IS — a median over the winning window group — and
    pure noise now reaches past it. This records that, because it is the premise behind
    two decisions someone will otherwise re-litigate: why the unvoted band is described
    as unguarded, and why ``MIN_CONFIDENCE`` was nevertheless left alone.

    **It was left alone because raising it does not work**, and that measurement needs
    the real material rather than this fixture: on clips of the muvid#59 master under the
    30 s vote boundary, the worst CORRECT alignment scored 0.129 while the worst noise
    clip scored 0.139 — overlapping, so every threshold either admits noise or refuses
    real footage. This fixture is *easier* than that (its correct alignments sit at
    ~0.32), so it can honestly pin the noise floor and not the overlap; asserting the
    overlap here would mean degrading the fixture until it appeared, which is fitting a
    fixture to a conclusion. Whether muvid should stop vouching for unvoted clips
    altogether is muvid#91.

    Expect this to fail the day the estimator or the threshold changes. That is the
    point — it is the note that says which measurement to redo.
    """
    from scipy.io import wavfile

    from muvid.footage.align import align_footage

    song_p, song = _repetitive_song(tmp_path)
    song_s = len(song) / SR
    rng = np.random.default_rng(59)

    floor = []
    for seed in range(6):
        p = tmp_path / f"noise{seed}.wav"
        wavfile.write(
            str(p), SR, (rng.normal(0, 0.3, int(15 * SR)) * 32767).astype(np.int16)
        )
        a = align_footage(str(song_p), [("N", str(p))], song_duration=song_s)[0]
        # 15 s fits a 5 s window, so a support IS measured and IS what decides.
        assert a.window_s is not None
        assert a.reliable is (a.support > MIN_SUPPORT)
        floor.append(a.confidence)

    assert max(floor) > MIN_CONFIDENCE, (
        f"noise topped out at {max(floor):.3f}, under MIN_CONFIDENCE={MIN_CONFIDENCE} "
        "— the coefficient excludes noise again, so the unvoted band may no longer be "
        "unguarded and muvid#91 should be re-read before this test is deleted"
    )


@needs_support
def test_a_clip_too_short_to_vote_reports_no_support(tmp_path):
    """``support is None`` still exists, and still means one specific thing.

    Since ``mixing>=0.0.48`` fits the window, almost everything gets a vote — the window
    floors at 3 s, so only a clip too short to hold two of those comes back unvoted
    (measured: 4 s yes, 6 s no). The case is rarer than it was and it has not gone away,
    so the flag is still worth pinning: ``None`` rather than an invented 1.0, because a
    unanimous vote of one would be the strongest claim the record can make resting on no
    evidence at all.

    This does not assert the result is RIGHT. It asserts muvid can still tell that it
    does not know — ``support is None`` is the flag, and ``align_footage``'s
    ``no_consensus`` list is where a caller reads it.
    """
    from muvid.footage.align import align_footage

    song_p, song = _repetitive_song(tmp_path)
    song_s = len(song) / SR
    clip = [
        (
            "S",
            str(_device_recording(tmp_path, song, "s", at=12.0, seconds=4.0, seed=303)),
        )
    ]
    a = align_footage(str(song_p), clip, song_duration=song_s)[0]

    # None, not 0.0 — "no vote was held" and "the windows disagreed" are different
    # facts, and collapsing them would make an unvoted clip look like a refused one.
    assert a.support is None
    # The verdict therefore rests on the coefficient, which is what the exposure IS.
    assert a.reliable is vouches_for(confidence=a.confidence, support=None)


@needs_pipeline
def test_an_estimator_parameter_the_installed_mixing_cannot_honour_raises(tmp_path):
    # The seam forwards estimator parameters rather than enumerating them, so the one
    # thing it must never do is accept a window setting and drop it: a caller would
    # believe they had tuned an estimator that never saw the value.
    from muvid.footage.align import align_footage

    song_p, song = _song(tmp_path)
    clip = [("A", str(_clip(tmp_path, song[: int(5 * SR)], "a")))]
    with pytest.raises(TypeError, match="no_such_estimator_knob"):
        align_footage(
            str(song_p), clip, song_duration=SONG_S, no_such_estimator_knob=1.0
        )
