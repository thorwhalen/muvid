"""A low-confidence alignment is a refusal, not a number (muvid#59).

The defect: on a rigidly programmed, repetitive track the whole-clip cross-correlation
surface has near-tied peaks spaced at musical periods, so the estimator returns the
argmax of a coin flip. It reported low confidence (0.086-0.121) and handed the offsets
back anyway with ``overlaps=True``, and three clips of a real shoot came back 83 s, 174 s
and 83 s wrong. Nothing downstream re-measures an offset, so the failure presented as a
silently desynced video rather than as an error.

What is pinned here is that "we are not sure" now STOPS the render:

- the verdict lives in one place (``align.vouches_for``) and rides on the record
  (``FootageAlignment.reliable`` / ``.support``), so a persisted alignment carries it;
- ``validate_edl`` — the ONE gate — refuses to cut to an unvouched clip, and the escape
  (``allow_unreliable=True``) has to be said out loud, the way an unpriceable shot has to
  be passed with ``--allow-unpriced`` rather than being read as free;
- a refusal is not a removal: the clip keeps its record and stays addressable.

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
        assert vouches_for(confidence=0.01, support=MIN_SUPPORT)

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

    def test_a_record_written_before_the_fields_existed_reads_back_vouched(self):
        # Same compatibility posture as `overlaps`: defaulting to False would refuse
        # every project already on disk over a measurement nobody ever made.
        legacy = {
            "clip_id": "A",
            "offset_s": 1.0,
            "confidence": 0.9,
            "duration_s": 10.0,
            "coverage": [1.0, 11.0],
        }
        a = FootageAlignment.from_dict(legacy)
        assert a.reliable is True and a.support is None


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
    def test_align_footage_reports_the_unreliable_list(self, tmp_path, monkeypatch):
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
            "JUNK", str(_clip(tmp_path, rng.normal(0, 1.0, int(8 * SR)), "j")), ext="mp4"
        )
        with use_email("u@x.com"):
            out = ft.align_footage("p")
        assert [u["clip_id"] for u in out["unreliable"]] == ["JUNK"]
        assert out["unreliable"][0]["support"] is None  # not measured, not zero
        assert out["support_threshold"] == MIN_SUPPORT


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
