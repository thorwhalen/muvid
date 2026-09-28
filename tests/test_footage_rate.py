"""A cut's speed (``EdlEntry.rate``, thorwhalen/muvid#61): each cut's footage time is
an affine map of song time, ``clip_in_of(e) + rate * (t - song_start)``.

What is pinned, beside the plain round trip:

- **containment reads ``span * rate``** — a sped-up cut consumes more of its clip, and a
  rate-blind check validates clean and renders past the end of the clip;
- **a part consumes ``span * rate`` of source** — measured on real frames: a part's
  LAST frame shows the source moment the map says, which a missing retime or an
  unscaled source read gets wrong while producing exactly the right number of frames;
- **moving a start keeps the footage map** (split, a moved boundary, a join), since at
  a speed other than 1 an unchanged slip would jump;
- a sped-up cut is never stretched or coalesced into a neighbour silently.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

nw = pytest.importorskip("nw")

from muvid.footage import service  # noqa: E402
from muvid.footage.edl import (  # noqa: E402
    RATE_MAX_DEV,
    AssemblyCut,
    EdlEntry,
    _absorbable,
    _coalesce_absorbed,
    clip_time_at,
    derive_cuts,
    with_start,
)
from muvid.footage.errors import FootageError  # noqa: E402
from muvid.footage.workspace import FootageWorkspace  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg + ffprobe")


@pytest.fixture
def fp(tmp_path, monkeypatch):
    """A 30 s song; clips A (0-20 s of song) and B (10-30 s), declared offsets."""
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path / "data"))
    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    song = tmp_path / "song.wav"
    song.write_bytes(b"RIFF-song")
    service.set_song(proj, path=str(song), duration_s=30.0)
    for cid in ("A", "B"):
        clip = tmp_path / f"{cid}.mp4"
        clip.write_bytes(f"clip-{cid}".encode())
        service.add_clip(proj, path=str(clip), clip_id=cid, duration_s=20.0)
    service.set_offset(proj, clip_id="A", offset_s=0.0)
    service.set_offset(proj, clip_id="B", offset_s=10.0)
    service.save_edit(
        proj,
        edl=[
            {"song_start": 0.0, "song_end": 12.0, "clip_id": "A"},
            {"song_start": 12.0, "song_end": 30.0, "clip_id": "B"},
        ],
        edit_id="e",
    )
    return proj


def _align(fp, cid):
    return {a.clip_id: a for a in fp.load_alignments()}[cid]


def test_a_speed_is_kept_absent_at_one_and_reaches_the_renderer(fp):
    out = service.set_cut(fp, edit_id="e", index=0, rate=1.05)
    assert out["edl"][0]["rate"] == 1.05
    assert "rate" not in out["edl"][1]  # absent at 1: older edits stay byte-identical
    entries = service._edit_entries(fp, "e")[1]
    cuts = derive_cuts(entries, fp.load_alignments(), fp.clip_paths())
    assert cuts[0].rate == 1.05 and cuts[0].source_duration == pytest.approx(12.6)
    out = service.set_cut(fp, edit_id="e", index=0, rate=1)
    assert "rate" not in out["edl"][0]


def test_containment_reads_span_times_rate(fp):
    """B's cut reads clip time [2, 20] at speed 1 — the clip's last frame exactly. At
    1.01 it would read [2, 20.18]: the rate-blind rule (2 + 18 <= 20) passes it."""
    before = service.get_edit(fp, edit_id="e")["edl"]
    with pytest.raises(FootageError, match="does not contain"):
        service.set_cut(fp, edit_id="e", index=1, rate=1.01)
    assert service.get_edit(fp, edit_id="e")["edl"] == before
    service.set_cut(fp, edit_id="e", index=1, rate=0.99)  # slower reads less: fits


def test_a_speed_outside_its_bound_is_refused(fp):
    with pytest.raises(FootageError, match="not a slow-motion"):
        service.set_cut(fp, edit_id="e", index=0, rate=1 + RATE_MAX_DEV + 0.01)
    with pytest.raises(FootageError, match="must be a number"):
        service.set_cut(fp, edit_id="e", index=0, rate=True)


def test_splitting_a_sped_up_cut_keeps_its_footage_continuous(fp):
    service.set_cut(fp, edit_id="e", index=0, rate=1.05)
    out = service.split_cut(fp, edit_id="e", at_s=4.0)
    first, second = out["edl"][0], out["edl"][1]
    assert first["rate"] == second["rate"] == 1.05
    assert second["slip_s"] == pytest.approx(0.2)  # (1.05 - 1) * 4
    a = _align(fp, "A")
    e1, e2 = service._edit_entries(fp, "e")[1][:2]
    assert clip_time_at(e1, a, 4.0) == pytest.approx(clip_time_at(e2, a, 4.0))
    assert clip_time_at(e2, a, 4.0) == pytest.approx(4.2)


def test_a_split_that_would_need_too_big_a_slip_is_refused(fp):
    service.set_cut(fp, edit_id="e", index=0, rate=0.92)
    with pytest.raises(FootageError, match="speed back to x1"):
        service.split_cut(fp, edit_id="e", at_s=10.0)  # -0.08 * 10 = -0.8 s of slip


def test_moving_a_start_keeps_the_map_and_a_new_video_starts_at_speed_one(fp):
    service.set_cut(fp, edit_id="e", index=1, rate=0.98)
    out = service.set_cut(fp, edit_id="e", index=1, song_start=13.0)
    assert out["edl"][1]["slip_s"] == pytest.approx(-0.02)
    out = service.set_cut(fp, edit_id="e", index=1, clip_id="A", song_end=18.0)
    assert out["edl"][1]["clip_id"] == "A"
    assert "rate" not in out["edl"][1] and "slip_s" not in out["edl"][1]


def test_a_sped_up_cut_is_never_stretched_or_merged_silently():
    by_id = {"A": type("A", (), {"reliable": True})()}
    assert not _absorbable(EdlEntry(0.0, 4.0, "A", rate=1.02), by_id)
    # Same speed, but the second cut does not continue the first's footage: two cuts.
    kept = _coalesce_absorbed(
        [EdlEntry(0.0, 5.0, "A", rate=1.04), EdlEntry(5.0, 8.0, "A", rate=1.04)]
    )
    assert len(kept) == 2
    # Continuing it exactly: one take.
    first = EdlEntry(0.0, 5.0, "A", rate=1.04)
    merged = _coalesce_absorbed([first, replace_end(with_start(first, 5.0), 8.0)])
    assert len(merged) == 1 and merged[0].song_end == 8.0
    assert len(_coalesce_absorbed([EdlEntry(0.0, 5.0, "A"), EdlEntry(5.0, 8.0, "A", rate=1.02)])) == 2


def replace_end(e, end):
    from dataclasses import replace

    return replace(e, song_end=end)


def test_speed_survives_the_editor_round_trip_and_a_bad_one_reads_as_one():
    from muvid.footage.lacing_bridge import _edl_body, edl_annotations, edl_from_annotations

    assert _edl_body(EdlEntry(0.0, 4.0, "A", rate=0.95))["rate"] == 0.95
    assert "rate" not in _edl_body(EdlEntry(0.0, 4.0, "A"))
    anns = edl_annotations(
        [EdlEntry(0.0, 4.0, "A", rate=1.03), EdlEntry(4.0, 8.0, "A", rate=1.03)],
        song_asset_id="song",
        attributed_to="test",
    )
    anns[1].body["rate"] = 3.0
    out = edl_from_annotations(anns)
    assert out[0]["rate"] == 1.03 and "rate" not in out[1]


def test_the_part_filter_retimes_before_fps_and_reads_span_times_rate(monkeypatch, tmp_path):
    import muvid.visualize.ffmpeg as F
    from muvid.footage.assemble import _part_filter, _render_part

    cut = AssemblyCut(0.0, 2.0, "A", 1.0, "/tmp/a.mp4", rate=1.08)
    vf = _part_filter(cut, w=320, h=240, fps=30)
    assert vf.startswith("setpts=(PTS-STARTPTS)/1.080000,")
    assert vf.index("setpts") < vf.index("fps=")
    assert "setpts" not in _part_filter(AssemblyCut(0.0, 2.0, "A", 1.0, "/tmp/a.mp4"), w=320, h=240, fps=30)
    calls = []
    monkeypatch.setattr(F, "run_ffmpeg", lambda args, **k: calls.append(list(args)))
    _render_part(cut, tmp_path / "p.mp4", w=320, h=240, fps=30, n_frames=60, crf=18, preset="ultrafast")
    t = float(calls[0][calls[0].index("-t") + 1])
    assert t == pytest.approx((2.0 + 1 / 30) * 1.08)


# -- measured on real frames --------------------------------------------------------


def _frame_counter_video(path: Path, *, seconds: float = 8.0, fps: int = 30) -> Path:
    """A clip whose every frame's brightness IS its frame number (0, 1, 2, ...)."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
            "-i", f"color=gray:size=64x48:rate={fps}:duration={seconds}",
            "-vf", "geq=lum='N':cb=128:cr=128",
            "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )
    return path


def _frame_levels(path: Path) -> np.ndarray:
    """Each frame's mean LUMA as coded (the Y plane of yuv420p, no range conversion —
    a ``gray`` output would rescale limited-range luma)."""
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
        check=True,
        capture_output=True,
    ).stdout
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 48 * 64 * 3 // 2)
    return frames[:, : 48 * 64].mean(axis=1)


@needs_ffmpeg
def test_a_sped_up_part_consumes_span_times_rate_of_source(tmp_path):
    """The guard #61 asks for: the CONSUMED source span, read off the frames. A 2 s cut
    from 1.0 s into the clip at x1.08 must end on source time 1 + 1.08 * 59/30 ≈ 3.12
    (frame ~94). Unretimed, or retimed but reading only ``duration`` s of source, it
    ends on frame 89 (the second by ``tpad`` freezing the tail) — with the same 60
    frames either way. Both mutations were run against this test."""
    from muvid.footage.assemble import _render_part

    src = _frame_counter_video(tmp_path / "src.mp4")
    fps = 30
    out = tmp_path / "part.mp4"
    cut = AssemblyCut(0.0, 2.0, "A", 1.0, str(src), rate=1.08)
    _render_part(cut, out, w=64, h=48, fps=fps, n_frames=60, crf=0, preset="ultrafast")
    levels = _frame_levels(out)
    assert len(levels) == 60
    expected_first = 1.0 * fps
    expected_last = (1.0 + 1.08 * 59 / fps) * fps
    assert levels[0] == pytest.approx(expected_first, abs=1.5)
    assert levels[-1] == pytest.approx(expected_last, abs=1.5)
    # The source advanced 1.08 frames per output frame, on average.
    assert (levels[-1] - levels[0]) / 59 == pytest.approx(1.08, abs=0.03)
