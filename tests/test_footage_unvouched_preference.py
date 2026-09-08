"""One badly-aligned clip costs its own spans, not the whole edit (muvid#88).

muvid#59 made an offset nobody vouches for a refusal rather than a number, and muvid#87
put that refusal in ``validate_edl``. On the EXPLICIT-``edl`` path that is exactly right —
the caller named the clip. On the AUTO path it was blunter than intended: every built-in
strategy picked from whatever covered the span without looking at ``reliable``, so a
five-clip shoot with one bad clip produced an EDL that cut to it somewhere, and the gate
refused an edit the other four clips could have carried. The only recoveries were
``allow_unreliable=True`` (render the bad clip — the thing the refusal exists to prevent)
and hand-writing an EDL (the thing the auto path exists to prevent).

What is pinned here is the shape of the fix, which is deliberately NOT "filter unreliable
clips out in each strategy" — that would put a second trust check beside the single gate
and drop a source from the edit on the strength of a measurement:

- a strategy sees ``reliable`` as a **preference**: a vouched clip wins any span it
  covers, and an unvouched clip is still chosen where nothing else covers the span, so
  no strategy ever removes a source;
- ``exclude_unvouched`` — a transform, not a gate — sets those forced spans aside as
  typed :class:`~muvid.footage.edl.ExcludedSpan` records that ride back in the tool's own
  result, and gap-fills what is left;
- ``validate_edl`` stays the ONE gate and still raises the SAME
  :class:`~muvid.footage.edl.UnreliableAlignmentError` when nothing trustworthy covers
  the song, because a black video reported as success is muvid#59's failure one level up;
- ``weighted`` carries the same preference as a reward penalty larger than the whole
  composite range, so no METRIC can buy an unvouched clip a span another clip covers —
  but two of the DP's terms are not metrics, and both were measured producing a cutaway
  to an unvouched clip over a span a vouched one covers: a caller-raised
  ``l_max_overrun_penalty``, and the ``max_seg_s`` transition window, which forces a
  vouched take longer than the cap to cut away and back. Gapping those would punch an
  avoidable hole through a continuous take and blame an alignment for it, so the recovery
  ABSORBS such a span into the vouched cut beside it — losslessly, since that clip
  already covers it — and only gaps what nothing vouched-for covers;
- an excluded span says WHOSE loss it was: ``no_vouched_coverage`` (re-align or re-shoot)
  or ``unvouched_selection`` (the footage exists; the selection did not use it).

Everything here is synthetic: alignment records and score tensors, no audio, no ffmpeg.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from muvid.footage.edl import (
    NO_VOUCHED_COVERAGE,
    UNVOUCHED_SELECTION,
    EdlEntry,
    ExcludedSpan,
    FootageAlignment,
    UnreliableAlignmentError,
    exclude_unvouched,
    fill_gaps,
    validate_edl,
)
from muvid.footage import strategy as S

SONG_S = 30.0
BUILT_INS = ("best_confidence", "longest_take", "fewest_cuts")


def _align(clip_id, start, end, *, reliable=True, conf=0.9, support=0.9, margin=0.5):
    """A clip covering ``[start, end)`` of the song, aligned at ``start``."""
    return FootageAlignment(
        clip_id=clip_id,
        offset_s=start,
        confidence=conf,
        duration_s=end - start,
        coverage=(start, end),
        overlaps=True,
        support=support,
        reliable=reliable,
        margin=margin,
    )


def _clip_at(entries, t: float) -> str | None:
    """Which clip is on air at song time ``t`` — asked pointwise, deliberately.

    Filtering entries by span END cannot tell "no entry reaches here" from "one long
    entry covers here", so a preference test written that way passes VACUOUSLY when the
    preference is absent and one clip wins the whole song. Measured: with
    ``_prefer_vouched`` stubbed out, the span-filtered form of these assertions stayed
    green while the unvouched clip held every second of the edit.
    """
    for e in entries:
        if e.song_start - 1e-6 <= t < e.song_end + 1e-6:
            return e.clip_id
    return None


def _shoot():
    """Five-clip shoot, one bad clip — the issue's own example.

    ``BAD`` overlaps everything, so under the old behaviour it won spans on confidence
    alone and failed the whole edit. It is also the ONLY cover for ``[20, 30)``, which is
    what makes the two halves of the fix visible in one fixture.
    """
    return [
        _align("A", 0.0, 10.0),
        _align("B", 10.0, 20.0),
        _align("BAD", 0.0, 30.0, reliable=False, conf=0.99, support=0.2, margin=-0.3),
    ]


class TestThePreference:
    """``reliable`` ranks clips; it never removes one (muvid#88)."""

    @pytest.mark.parametrize("name", BUILT_INS)
    def test_a_vouched_clip_wins_any_span_it_covers(self, name):
        # BAD carries the HIGHEST confidence in the fixture, which is the point: the
        # preference has to beat the metric each strategy optimizes, not tie-break it.
        entries = S.select_edl(name, _shoot(), SONG_S)
        assert _clip_at(entries, 5.0) == "A"
        assert _clip_at(entries, 15.0) == "B"

    @pytest.mark.parametrize("name", BUILT_INS)
    def test_an_unvouched_clip_is_still_chosen_where_nothing_else_covers(self, name):
        """A preference, not a filter — the strategy proposes, the gate decides."""
        entries = S.select_edl(name, _shoot(), SONG_S)
        assert _clip_at(entries, 25.0) == "BAD"

    def test_the_preference_is_per_span_not_per_clip_set(self):
        """A clip preferred away from one span keeps the span only it covers."""
        entries = S.select_edl("best_confidence", _shoot(), SONG_S)
        by_clip = {e.clip_id for e in entries}
        assert by_clip == {"A", "B", "BAD"}

    def test_nothing_changes_when_every_clip_is_vouched_for(self):
        aligns = [_align("A", 0.0, 20.0), _align("B", 10.0, 30.0, conf=0.5)]
        entries = S.select_edl("best_confidence", aligns, SONG_S)
        assert [(e.song_start, e.song_end, e.clip_id) for e in entries] == [
            (0.0, 20.0, "A"),
            (20.0, 30.0, "B"),
        ]


class TestTheRecovery:
    """``exclude_unvouched``: a smaller edit, reported — and never an empty one."""

    def test_the_forced_spans_are_set_aside_and_named(self):
        aligns = _shoot()
        kept, excluded = exclude_unvouched(
            S.select_edl("best_confidence", aligns, SONG_S), aligns
        )
        assert [e.clip_id for e in kept] == ["A", "B"]
        assert len(excluded) == 1
        x = excluded[0]
        assert isinstance(x, ExcludedSpan)
        assert (x.clip_id, x.song_start, x.song_end) == ("BAD", 20.0, 30.0)
        assert x.reason == NO_VOUCHED_COVERAGE
        # The three numbers the verdict rests on ride along, so the report and the gate
        # can never disagree about WHY — and `support`/`margin` stay measurements.
        assert (x.confidence, x.support, x.margin) == (0.99, 0.2, -0.3)

    def test_the_smaller_edit_passes_the_gate_untouched(self):
        aligns = _shoot()
        kept, excluded = exclude_unvouched(
            S.select_edl("best_confidence", aligns, SONG_S), aligns
        )
        entries = validate_edl(fill_gaps(kept, SONG_S), aligns, SONG_S)
        assert [(e.song_start, e.song_end, e.clip_id) for e in entries] == [
            (0.0, 10.0, "A"),
            (10.0, 20.0, "B"),
            (20.0, 30.0, ""),
        ]
        assert excluded and entries[-1].is_gap

    def test_a_span_that_was_never_covered_is_not_reported_as_excluded(self):
        """``uncovered`` and ``excluded`` are different facts: nothing vs. nothing usable."""
        aligns = [_align("A", 0.0, 10.0)]
        kept, excluded = exclude_unvouched(
            S.select_edl("best_confidence", aligns, SONG_S), aligns
        )
        assert excluded == []
        assert [e.clip_id for e in kept] == ["A"]

    def test_a_fully_vouched_edit_is_returned_unchanged(self):
        aligns = [_align("A", 0.0, 30.0)]
        edl = S.select_edl("best_confidence", aligns, SONG_S)
        kept, excluded = exclude_unvouched(edl, aligns)
        assert excluded == [] and kept == edl

    def test_it_refuses_to_empty_the_edit_so_the_gate_still_speaks(self):
        """Nothing trustworthy covers the song → the SAME typed refusal as before.

        The recovery declines to act rather than returning an all-gap edit: a black video
        reported as success is exactly the plausible-artifact failure muvid#59 exists to
        prevent, one level up from a desynced cut.
        """
        aligns = [
            _align("X", 0.0, 15.0, reliable=False, support=0.1, margin=-0.2),
            _align("Y", 15.0, 30.0, reliable=False, support=0.1, margin=-0.2),
        ]
        edl = S.select_edl("best_confidence", aligns, SONG_S)
        kept, excluded = exclude_unvouched(edl, aligns)
        assert kept == edl and excluded == []
        with pytest.raises(UnreliableAlignmentError) as ei:
            validate_edl(fill_gaps(kept, SONG_S), aligns, SONG_S)
        assert sorted(ei.value.clip_ids) == ["X", "Y"]

    def test_an_explicit_gap_survives_the_transform(self):
        aligns = _shoot()
        edl = [
            EdlEntry(0.0, 10.0, "A"),
            EdlEntry(10.0, 20.0, ""),
            EdlEntry(20.0, 30.0, "BAD"),
        ]
        kept, excluded = exclude_unvouched(edl, aligns)
        assert [e.clip_id for e in kept] == ["A", ""]
        assert [x.clip_id for x in excluded] == ["BAD"]


class TestTheWeightedStrategy:
    """The score-driven selector carries the same preference, as a reward penalty."""

    def _fixture(self, *, hop=0.1):
        from muvid.footage.select_score import SelectionContext, WeightedSelectionConfig

        aligns = _shoot()
        n = int(np.ceil(SONG_S / hop)) + 1
        t = np.arange(n) * hop
        # BAD scores PERFECTLY everywhere and the vouched clips score ZERO where they
        # cover: the tensor is rigged as hard as it can be against the preference, so a
        # penalty that merely competes with the metrics would lose here.
        comp = {
            "A": np.where(t < 10.0, 0.0, np.nan),
            "B": np.where((t >= 10.0) & (t < 20.0), 0.0, np.nan),
            "BAD": np.full(n, 1.0),
        }
        from tests.test_scoring_select import make_tensor

        tensor = make_tensor(aligns, comp, hop=hop, n=n)
        ctx = SelectionContext(
            tensor=tensor,
            beat_times=np.arange(0.0, SONG_S + hop, 1.0),
            config=WeightedSelectionConfig(weights={"m": 1.0}),
        )
        return aligns, ctx

    def test_a_perfect_score_does_not_buy_an_unvouched_clip_a_covered_span(self):
        aligns, ctx = self._fixture()
        entries = S.select_edl("weighted", aligns, SONG_S, context=ctx)
        assert _clip_at(entries, 5.0) == "A"
        assert _clip_at(entries, 15.0) == "B"

    def test_it_still_reaches_the_clip_where_nothing_else_covers(self):
        aligns, ctx = self._fixture()
        entries = S.select_edl("weighted", aligns, SONG_S, context=ctx)
        assert _clip_at(entries, 25.0) == "BAD"

    def test_the_penalty_outruns_the_whole_composite_range(self):
        """ĝ ∈ [0,1], so anything > 1 makes the preference lexicographic."""
        from muvid.footage.select_score import UNVOUCHED_REWARD_PENALTY

        assert UNVOUCHED_REWARD_PENALTY > 1.0

    def test_the_selection_margin_diagnostic_is_left_alone(self):
        """Trust is not a score: folding it into ĝ would change what that number means."""
        from muvid.footage.select_score import selection_margin

        aligns, ctx = self._fixture()
        m = selection_margin(aligns, ctx.tensor, weights={"m": 1.0})
        finite = m[np.isfinite(m)]
        assert finite.size and float(np.max(finite)) == pytest.approx(1.0)


class _StubProject:
    """Just enough project for the two tools, so the result SHAPE can be pinned offline.

    The tools are the surface muvid#88 changed, and asserting the recovery only on the
    library functions would leave the thing a remote caller actually reads — the
    ``coverage.excluded`` list — untested. A real project would need ffmpeg to probe a
    song duration and encode a render; neither is what is under test here.
    """

    def __init__(self, aligns, song_dur, root):
        self._aligns, self._dur, self._root = aligns, song_dur, root
        self.meta = None

    def has_song(self):
        return True

    def load_alignments(self):
        return self._aligns

    def song_duration(self):
        return self._dur

    def canvas(self):
        return (1280, 720)

    def song_path(self):
        return self._root / "song.wav"

    def clip_paths(self):
        return {a.clip_id: str(self._root / f"{a.clip_id}.mp4") for a in self._aligns}

    def next_render_ref(self):
        return 1

    def new_render_dir(self, render_id):
        d = self._root / "renders" / render_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_render_meta(self, render_id, meta):
        self.meta = meta


@pytest.fixture
def tools(tmp_path, monkeypatch):
    """``muvid.mcp.footage_tools`` wired to a stub project holding ``_shoot()``."""
    pytest.importorskip("fastmcp")
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    import muvid.mcp.footage_tools as ft

    proj = _StubProject(_shoot(), SONG_S, tmp_path)
    monkeypatch.setattr(ft, "_open", lambda project_id: proj)
    return ft, proj


class TestTheToolSurface:
    """What a remote caller sees: a smaller edit, and a typed reason for the difference."""

    def test_propose_edit_reports_the_excluded_span(self, tools):
        ft, _ = tools
        out = ft.propose_edit("p")
        cov = out["coverage"]
        assert cov["excluded"] == [
            {
                "clip_id": "BAD",
                "song_start": 20.0,
                "song_end": 30.0,
                "reason": NO_VOUCHED_COVERAGE,
                "confidence": 0.99,
                "support": 0.2,
                "margin": -0.3,
            }
        ]
        # The same span shows as uncovered — the user has no usable footage there — and
        # NOT as a weak segment, which is reserved for footage that made the cut.
        assert cov["uncovered"] == [{"song_start": 20.0, "song_end": 30.0}]
        assert cov["weak_segments"] == []
        assert [e["clip_id"] for e in out["edl"]] == ["A", "B", None]

    def test_the_proposal_feeds_back_through_the_explicit_path(self, tools):
        """propose_edit promises the edit assemble would build; a gap must survive it."""
        from muvid.footage.edl import validate_edl as _validate

        ft, proj = tools
        out = ft.propose_edit("p")
        entries = _validate(
            out["edl"], proj.load_alignments(), SONG_S, canvas=(1280, 720)
        )
        assert [e.clip_id for e in entries] == ["A", "B", ""]

    def test_assemble_renders_the_smaller_edit_and_records_why(
        self, tools, monkeypatch
    ):
        import muvid.footage.assemble as A
        import muvid.visualize as V

        ft, proj = tools
        monkeypatch.setattr(
            A,
            "assemble_music_video",
            lambda cuts, song, out, canvas, on_note=None: (
                Path(out).write_bytes(b"v"),
                Path(out),
            )[1],
        )
        monkeypatch.setattr(V, "verify_video", lambda *a, **k: [])
        monkeypatch.setattr(V, "failures", lambda c: [])
        monkeypatch.setattr(V, "report", lambda c: "ok")
        out = ft.assemble_music_video("p")
        assert out["ok"] is True
        assert [x["clip_id"] for x in out["coverage"]["excluded"]] == ["BAD"]
        # It reaches meta.json too: the record beside the file has to say why the video
        # goes black for ten seconds, or the answer dies with this reply.
        assert proj.meta["coverage"]["excluded"] == out["coverage"]["excluded"]

    def test_allow_unreliable_renders_the_footage_rather_than_gapping_it(
        self, tools, monkeypatch
    ):
        import muvid.footage.assemble as A
        import muvid.visualize as V

        ft, _ = tools
        monkeypatch.setattr(
            A,
            "assemble_music_video",
            lambda cuts, song, out, canvas, on_note=None: (
                Path(out).write_bytes(b"v"),
                Path(out),
            )[1],
        )
        monkeypatch.setattr(V, "verify_video", lambda *a, **k: [])
        monkeypatch.setattr(V, "failures", lambda c: [])
        monkeypatch.setattr(V, "report", lambda c: "ok")
        out = ft.assemble_music_video("p", allow_unreliable=True)
        assert out["coverage"]["excluded"] == []
        assert "BAD" in {e["clip_id"] for e in out["edl"]}
        # ...and it is still reported as weak, which is the whole of the caller's warning.
        assert [w["clip_id"] for w in out["coverage"]["weak_segments"]] == ["BAD"]

    def test_a_shoot_with_nothing_trustworthy_still_refuses(self, tools, monkeypatch):
        from fastmcp.exceptions import ToolError

        ft, proj = tools
        proj._aligns = [
            _align("X", 0.0, 15.0, reliable=False, support=0.1, margin=-0.2),
            _align("Y", 15.0, 30.0, reliable=False, support=0.1, margin=-0.2),
        ]
        with pytest.raises(ToolError, match="not trustworthy"):
            ft.assemble_music_video("p")
        # propose_edit renders nothing, so it still DIAGNOSES rather than refusing: the
        # spans it cannot recover come back as weak_segments instead of excluded.
        cov = ft.propose_edit("p")["coverage"]
        assert cov["excluded"] == []
        assert sorted(w["clip_id"] for w in cov["weak_segments"]) == ["X", "Y"]


class TestTheSelectorArtifact:
    """A gap is the last resort: a span a vouched neighbour covers is absorbed, not lost.

    Both fixtures are the reviewer's, reproduced before the fix and pinned after it. They
    are ``weighted``'s own doing, not the aligner's — the DP's non-composite terms force a
    cut away from a vouched clip and back, over a span that clip covers. Left as gaps they
    would be avoidable black holes in a continuous take, labelled as an alignment problem.
    """

    SONG = 60.0
    HOP = 0.1

    def _run(self, aligns, comp, cfg):
        from muvid.footage.select_score import SelectionContext

        from tests.test_scoring_select import make_tensor

        n = int(np.ceil(self.SONG / self.HOP)) + 1
        ctx = SelectionContext(
            tensor=make_tensor(aligns, comp, hop=self.HOP, n=n),
            beat_times=np.arange(0.0, self.SONG + 1e-9, 1.0),
            config=cfg,
        )
        return S.select_edl("weighted", aligns, self.SONG, context=ctx)

    def _times(self):
        n = int(np.ceil(self.SONG / self.HOP)) + 1
        return n, np.arange(n) * self.HOP

    def test_the_transition_window_cannot_punch_a_hole_in_a_long_take(self):
        """DEFAULT config: a 2 s opener + one 58 s vouched take (max_seg_s = 32 s)."""
        from muvid.footage.select_score import WeightedSelectionConfig

        n, t = self._times()
        aligns = [
            _align("O", 0.0, 2.0),
            _align("V", 2.0, 60.0),
            _align("BAD", 0.0, 60.0, reliable=False, support=0.2, margin=-0.3),
        ]
        comp = {
            "O": np.where(t < 2.0, 0.9, np.nan),
            "V": np.where(t >= 2.0, 0.9, np.nan),
            "BAD": np.full(n, 0.9),
        }
        raw = self._run(aligns, comp, WeightedSelectionConfig(weights={"m": 1.0}))
        # The DP really does cut away and back — if this stops being true the fixture has
        # stopped being adversarial and the assertion below proves nothing.
        assert "BAD" in {e.clip_id for e in raw}
        kept, excluded = exclude_unvouched(raw, aligns)
        assert [(e.song_start, e.song_end, e.clip_id) for e in kept] == [
            (0.0, 2.0, "O"),
            (2.0, 60.0, "V"),
        ]
        assert excluded == []

    def test_a_caller_raised_overrun_penalty_cannot_either(self):
        """``l_max_overrun_penalty=0.9`` cuts away from a lone 40 s take every 10 s."""
        from muvid.footage.select_score import WeightedSelectionConfig

        n, _ = self._times()
        aligns = [
            _align("V", 0.0, 40.0),
            _align("BAD", 0.0, 40.0, reliable=False, support=0.2, margin=-0.3),
        ]
        comp = {"V": np.full(n, 0.9), "BAD": np.full(n, 0.9)}
        raw = self._run(
            aligns,
            comp,
            WeightedSelectionConfig(weights={"m": 1.0}, l_max_overrun_penalty=0.9),
        )
        assert "BAD" in {e.clip_id for e in raw}
        kept, excluded = exclude_unvouched(raw, aligns)
        assert [(e.song_start, e.song_end, e.clip_id) for e in kept] == [(0.0, 40.0, "V")]
        assert excluded == []

    def test_absorption_never_reaches_past_what_the_neighbour_holds(self):
        """The neighbour must actually contain the span — else it is a gap, as before."""
        aligns = [
            _align("A", 0.0, 10.0),
            _align("BAD", 10.0, 20.0, reliable=False, support=0.2, margin=-0.3),
            _align("B", 20.0, 30.0),
        ]
        edl = [
            EdlEntry(0.0, 10.0, "A"),
            EdlEntry(10.0, 20.0, "BAD"),
            EdlEntry(20.0, 30.0, "B"),
        ]
        kept, excluded = exclude_unvouched(edl, aligns)
        assert [(e.song_start, e.song_end, e.clip_id) for e in kept] == [
            (0.0, 10.0, "A"),
            (20.0, 30.0, "B"),
        ]
        assert [x.reason for x in excluded] == [NO_VOUCHED_COVERAGE]

    def test_a_cut_whose_meaning_depends_on_its_length_is_not_stretched(self):
        """A pan or a moving look would be silently re-timed, so absorption declines."""
        from muvid.footage.edl import CropWindow

        aligns = [
            _align("V", 0.0, 30.0),
            _align("BAD", 0.0, 30.0, reliable=False, support=0.2, margin=-0.3),
        ]
        pan = EdlEntry(
            0.0,
            10.0,
            "V",
            crop=CropWindow(0.0, 0.0, 1.0, 0.5),
            crop_end=CropWindow(0.0, 0.5, 1.0, 0.5),
        )
        edl = [pan, EdlEntry(10.0, 12.0, "BAD"), EdlEntry(12.0, 30.0, "V")]
        kept, excluded = exclude_unvouched(edl, aligns)
        # The FOLLOWING plain cut takes it instead — lossless, and nothing was re-timed.
        assert [(e.song_start, e.song_end, e.clip_id) for e in kept] == [
            (0.0, 10.0, "V"),
            (10.0, 30.0, "V"),
        ]
        assert kept[0].crop_end is not None and excluded == []

    def test_a_span_a_non_adjacent_vouched_clip_covers_is_named_as_a_selection_loss(self):
        """Whose loss it was decides the remedy, so the record has to distinguish them."""
        aligns = [
            _align("A", 0.0, 10.0),
            _align("FAR", 10.0, 20.0),
            _align("B", 20.0, 30.0),
            _align("BAD", 0.0, 30.0, reliable=False, support=0.2, margin=-0.3),
        ]
        # A hand-written edit; no built-in would choose BAD here (that is the preference).
        edl = [
            EdlEntry(0.0, 10.0, "A"),
            EdlEntry(10.0, 20.0, "BAD"),
            EdlEntry(20.0, 30.0, "B"),
        ]
        kept, excluded = exclude_unvouched(edl, aligns)
        assert [e.clip_id for e in kept] == ["A", "B"]
        assert [x.reason for x in excluded] == [UNVOUCHED_SELECTION]
