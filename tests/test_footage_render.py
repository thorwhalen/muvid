"""Render correctness for the footage path (muvid#21) + bounded assembly (muvid#24).

The contract under test: a render is EXACTLY the song's duration (head/tail/interior
holes become explicit black gap entries), the returned EDL reproduces the same video,
mixed-fps sources land on one constant output rate, a delivery-contract master is
stream-copied bit-identically, rotation side-data pads rather than stretches, and the
assembler's memory does not grow with cut count (one bounded ffmpeg per cut).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

nw = pytest.importorskip("nw")

from muvid.footage.edl import (  # noqa: E402
    EdlEntry,
    FootageAlignment,
    derive_cuts,
    fill_gaps,
    validate_edl,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg + ffprobe")

SONG_DUR = 20.0


# -- fill_gaps (pure) --------------------------------------------------------

_A = FootageAlignment("A", 0.0, 0.9, 20.0, (0.0, 20.0))
_LATE = FootageAlignment("L", 5.0, 0.9, 10.0, (5.0, 15.0))


def test_fill_gaps_pads_head_interior_and_tail():
    got = fill_gaps([EdlEntry(5, 8, "A"), EdlEntry(12, 15, "A")], SONG_DUR)
    assert [(e.song_start, e.song_end, e.clip_id) for e in got] == [
        (0.0, 5, ""),
        (5, 8, "A"),
        (8, 12, ""),
        (12, 15, "A"),
        (15, SONG_DUR, ""),
    ]
    # and the result is exactly what validate_edl accepts
    entries = validate_edl(got, [_A], SONG_DUR)
    assert [e.is_gap for e in entries] == [True, False, True, False, True]


def test_fill_gaps_leaves_full_coverage_untouched():
    full = [EdlEntry(0, 12, "A"), EdlEntry(12, 20, "A")]
    assert fill_gaps(full, SONG_DUR) == full


def test_fill_gaps_on_an_empty_selection_stays_empty():
    assert fill_gaps([], SONG_DUR) == []


def test_validate_still_rejects_an_implicit_hole():
    with pytest.raises(ValueError, match="gap"):
        validate_edl([EdlEntry(0, 5, "A"), EdlEntry(10, 20, "A")], [_A], SONG_DUR)


def test_fill_gaps_names_the_callers_out_of_range_entry():
    # An out-of-range CALLER entry must be reported as itself — not as the phantom gap
    # inserted to reach it (adversarial-review finding 5).
    with pytest.raises(ValueError, match=r"\[32\.000, 35\.000\] is outside"):
        fill_gaps([EdlEntry(0, 10, "A"), EdlEntry(32, 35, "A")], 30.0)


def test_edl_cap_counts_footage_cuts_not_inserted_gaps(monkeypatch):
    # fill_gaps can double the entry count; the cap must bind on what the CALLER wrote
    # (adversarial-review finding 4).
    import muvid.footage.edl as E

    monkeypatch.setattr(E, "MAX_EDL_ENTRIES", 3)
    aligns = [_A]
    three_with_holes = [EdlEntry(0, 2, "A"), EdlEntry(5, 7, "A"), EdlEntry(10, 12, "A")]
    filled = E.fill_gaps(three_with_holes, SONG_DUR)
    assert len(filled) == 6  # 3 footage + 3 gaps (two interior + tail)
    E.validate_edl(filled, aligns, SONG_DUR)  # must NOT trip the cap
    four = [EdlEntry(i * 2, i * 2 + 1, "A") for i in range(4)]
    with pytest.raises(ValueError, match="footage cuts"):
        E.validate_edl(E.fill_gaps(four, SONG_DUR), aligns, SONG_DUR)


def test_gap_entries_round_trip_json_null():
    from muvid.footage.edl import _as_entry

    e = _as_entry({"song_start": 0, "song_end": 5, "clip_id": None})
    assert e.is_gap and e.clip_id == ""


def test_derive_cuts_maps_a_gap_to_no_source():
    entries = validate_edl(fill_gaps([EdlEntry(5, 15, "L")], SONG_DUR), [_LATE], SONG_DUR)
    cuts = derive_cuts(entries, [_LATE], {"L": "/tmp/l.mp4"})
    assert [c.clip_path for c in cuts] == ["", "/tmp/l.mp4", ""]
    assert cuts[0].duration == pytest.approx(5.0)
    assert cuts[2].duration == pytest.approx(5.0)


# -- bounded assembly: one ffmpeg per cut, no per-cut memory growth ----------


def test_assemble_runs_one_bounded_ffmpeg_per_cut(monkeypatch, tmp_path):
    """The OOM fix (muvid#24): N single-input invocations + one stream-copy concat —
    never one filtergraph holding N decoder contexts (that needed >2.3 GB at 30 cuts)."""
    import muvid.visualize.ffmpeg as F
    from muvid.footage.assemble import assemble_music_video

    calls = []
    monkeypatch.setattr(F, "require_ffmpeg", lambda *a, **k: None)
    monkeypatch.setattr(F, "probe", lambda *a, **k: {})
    monkeypatch.setattr(F, "run_ffmpeg", lambda args, **k: calls.append(args))

    entries = validate_edl(fill_gaps([EdlEntry(5, 15, "L")], SONG_DUR), [_LATE], SONG_DUR)
    cuts = derive_cuts(entries, [_LATE], {"L": "/tmp/l.mp4"})
    assemble_music_video(cuts, "/tmp/song.wav", str(tmp_path / "final.mp4"))

    assert len(calls) == len(cuts) + 1  # one per cut + the concat/mux pass
    per_cut, final = calls[:-1], calls[-1]
    for args in per_cut:
        assert args.count("-i") == 1, "a cut stage must hold ONE decoder, ever"
    assert "-ss" in per_cut[1], "footage cuts seek input-side (no whole-head decode)"
    assert any("color=black" in a for a in per_cut[0]), "gap cuts render black"
    i = final.index("-c:v")
    assert final[i + 1] == "copy", "the concat pass re-encodes nothing"


def test_frame_counts_telescope_to_the_total():
    """Frame counts quantize BOUNDARIES, not durations — drift cannot accumulate."""
    from muvid.footage.assemble import _frame_counts

    rng = np.random.default_rng(7)
    bounds = np.sort(rng.uniform(0, 204.501, 60))
    spans = [0.0, *bounds.tolist(), 204.501]
    cuts = derive_cuts(
        validate_edl(
            fill_gaps(
                [EdlEntry(a, b, "S") for a, b in zip(spans, spans[1:]) if b - a > 2e-3],
                204.501,
            ),
            [FootageAlignment("S", 0.0, 0.9, 204.501, (0.0, 204.501))],
            204.501,
        ),
        [FootageAlignment("S", 0.0, 0.9, 204.501, (0.0, 204.501))],
        {"S": "/tmp/s.mp4"},
    )
    counts = _frame_counts(cuts, 30)
    assert sum(counts) == round(204.501 * 30)


def test_assemble_never_deletes_a_preexisting_parts_dir(monkeypatch, tmp_path):
    # The parts dir is unique per call — a fixed "_parts" name would rmtree whatever
    # already lived there (adversarial-review finding 3).
    import muvid.visualize.ffmpeg as F
    from muvid.footage.assemble import assemble_music_video

    precious = tmp_path / "_parts" / "precious.txt"
    precious.parent.mkdir()
    precious.write_text("user data")
    monkeypatch.setattr(F, "require_ffmpeg", lambda *a, **k: None)
    monkeypatch.setattr(F, "probe", lambda *a, **k: {})
    monkeypatch.setattr(F, "run_ffmpeg", lambda args, **k: None)
    cuts = derive_cuts(
        validate_edl(fill_gaps([EdlEntry(0, 20, "A")], SONG_DUR), [_A], SONG_DUR),
        [_A],
        {"A": "/tmp/a.mp4"},
    )
    assemble_music_video(cuts, "/tmp/song.wav", str(tmp_path / "final.mp4"))
    assert precious.read_text() == "user data"
    assert not list(tmp_path.glob(".parts-*")), "the call's own dir is cleaned up"


# -- real renders (ffmpeg) ---------------------------------------------------


def _wav_song(tmp_path, seconds=SONG_DUR, sr=44100) -> Path:
    """A 44.1 kHz MONO wav — the delivery contract must be the renderer's doing."""
    from scipy.io import wavfile

    t = np.arange(int(seconds * sr)) / sr
    x = 0.5 * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 1.3 * t))
    p = tmp_path / "song.wav"
    wavfile.write(str(p), sr, (x * 32767).astype(np.int16))
    return p


def _testsrc_clip(tmp_path, name, seconds, *, size="1280x720", rate="30", extra=()):
    out = tmp_path / f"{name}.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate={rate}:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            *extra,
            str(out),
        ],
        check=True,
    )
    return out


def _probe_json(path) -> dict:
    import json

    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(r.stdout)


def _frame_pixels(video, t) -> np.ndarray:
    """One decoded frame at ``t`` as an (h, w, 3) array, via a piped PPM."""
    r = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(t),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-c:v",
            "ppm",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    header, rest = r.stdout.split(b"\n", 1)
    assert header == b"P6"
    dims, rest = rest.split(b"\n", 1)
    w, h = map(int, dims.split())
    _maxval, raw = rest.split(b"\n", 1)
    return np.frombuffer(raw[: w * h * 3], dtype=np.uint8).reshape(h, w, 3)


def _stream_md5(path, stream) -> str:
    r = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            f"0:{stream}",
            "-c",
            "copy",
            "-f",
            "md5",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


@needs_ffmpeg
def test_partial_coverage_renders_the_full_song_with_black_gaps(tmp_path):
    """muvid#21 items 1+2+4: full duration, black gaps, one constant output rate —
    with deliberately MIXED source rates (30 and 359/12, the real iPhone folder)."""
    from muvid.footage.assemble import assemble_music_video
    from muvid.visualize.ffmpeg import media_duration

    song = _wav_song(tmp_path)
    ca = _testsrc_clip(tmp_path, "A", 6.0, rate="30")
    cb = _testsrc_clip(tmp_path, "B", 8.0, rate="359/12")
    aligns = [
        FootageAlignment("A", 3.0, 0.9, 6.0, (3.0, 9.0)),
        FootageAlignment("B", 9.0, 0.9, 8.0, (9.0, 17.0)),
    ]
    entries = validate_edl(
        fill_gaps([EdlEntry(3, 9, "A"), EdlEntry(9, 16, "B")], SONG_DUR),
        aligns,
        SONG_DUR,
    )
    cuts = derive_cuts(entries, aligns, {"A": str(ca), "B": str(cb)})
    out = assemble_music_video(
        cuts, str(song), str(tmp_path / "final.mp4"), canvas=(1280, 720)
    )

    assert media_duration(out) == pytest.approx(SONG_DUR, abs=0.15)
    v = [s for s in _probe_json(out)["streams"] if s["codec_type"] == "video"][0]
    assert v["avg_frame_rate"] == "30/1"
    a = [s for s in _probe_json(out)["streams"] if s["codec_type"] == "audio"][0]
    assert (a["sample_rate"], a["channels"]) == ("48000", 2)
    assert _frame_pixels(out, 1.0).mean() < 8, "the head gap must be black"
    assert _frame_pixels(out, 5.0).mean() > 40, "footage spans must show footage"
    assert _frame_pixels(out, 18.5).mean() < 8, "the tail gap must be black"


@needs_ffmpeg
def test_full_coverage_stream_copies_a_contract_master_bit_identically(tmp_path):
    """muvid#21 item 5 + acceptance 2: an aac/48k/stereo master's packets are COPIED
    (bit-identical), and a re-render from the same EDL reproduces the same streams."""
    from muvid.footage.assemble import assemble_music_video
    from muvid.visualize.ffmpeg import media_duration

    song = tmp_path / "master.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=10",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "aac",
            str(song),
        ],
        check=True,
    )
    dur = media_duration(song)
    clip = _testsrc_clip(tmp_path, "C", dur + 0.5)
    aligns = [FootageAlignment("C", 0.0, 0.9, dur + 0.5, (0.0, dur))]
    entries = validate_edl(fill_gaps([EdlEntry(0.0, dur, "C")], dur), aligns, dur)
    cuts = derive_cuts(entries, aligns, {"C": str(clip)})

    out1 = assemble_music_video(cuts, str(song), str(tmp_path / "r1.mp4"))
    out2 = assemble_music_video(cuts, str(song), str(tmp_path / "r2.mp4"))

    assert _stream_md5(out1, "a") == _stream_md5(song, "a"), (
        "a delivery-contract master must be stream-copied, not re-encoded"
    )
    assert _stream_md5(out1, "v") == _stream_md5(out2, "v")
    assert _stream_md5(out1, "a") == _stream_md5(out2, "a")


@needs_ffmpeg
def test_a_portrait_render_passes_its_own_verify(tmp_path):
    """Adversarial-review finding 1: verify_video hard-coded 16:9/720p, so every use of
    the new canvas= override self-reported failure. With expected_canvas, a deliberate
    portrait render must verify clean — through the REAL verifier, not a stub."""
    from muvid.footage.assemble import assemble_music_video
    from muvid.visualize import failures, verify_video

    song = _wav_song(tmp_path, seconds=2.0)
    clip = _testsrc_clip(tmp_path, "P", 2.5)
    aligns = [FootageAlignment("P", 0.0, 0.9, 2.5, (0.0, 2.0))]
    cuts = derive_cuts(
        validate_edl(fill_gaps([EdlEntry(0.0, 2.0, "P")], 2.0), aligns, 2.0),
        aligns,
        {"P": str(clip)},
    )
    out = assemble_music_video(
        cuts, str(song), str(tmp_path / "final.mp4"), canvas=(1080, 1920)
    )
    checks = verify_video(out, audio=str(song), expected_canvas=(1080, 1920))
    assert not failures(checks), "\n".join(
        f"{c.name}: {c.detail}" for c in failures(checks)
    )
    # …and the landscape expectation still fails it, so the check is not vacuous.
    assert failures(verify_video(out))


@needs_ffmpeg
def test_a_source_exhausted_cut_cannot_shorten_or_desync_the_video(tmp_path):
    """Adversarial-review finding 2: alignment durations come from the AUDIO track, so a
    clip whose audio outlives its video validates spans past the last video frame.
    -frames:v alone is a cap: the straddling cut came up short, the wholly-beyond cut
    produced a STREAMLESS mp4 the concat demuxer swallowed, and everything after played
    early. tpad (clone last frame) + the black fallback must keep the frame count exact.
    """
    from muvid.footage.assemble import assemble_music_video
    from muvid.visualize import failures, verify_video

    # A clip whose video is 4.0 s but whose audio runs 4.5 s.
    clip = tmp_path / "short_video.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=4.0",
            "-f", "lavfi", "-i", "sine=frequency=330:duration=4.5",
            "-pix_fmt", "yuv420p",
            str(clip),
        ],
        check=True,
    )  # fmt: skip
    song = _wav_song(tmp_path, seconds=4.5)
    aligns = [FootageAlignment("S", 0.0, 0.9, 4.5, (0.0, 4.5))]  # audio-derived length
    edl = [EdlEntry(0.0, 3.8, "S"), EdlEntry(3.8, 4.3, "S"), EdlEntry(4.3, 4.5, "S")]
    cuts = derive_cuts(
        validate_edl(fill_gaps(edl, 4.5), aligns, 4.5), aligns, {"S": str(clip)}
    )
    out = assemble_music_video(
        cuts, str(song), str(tmp_path / "final.mp4"), canvas=(1280, 720)
    )

    v = [s for s in _probe_json(out)["streams"] if s["codec_type"] == "video"][0]
    assert int(v["nb_frames"]) == round(4.5 * 30), (
        "the promised frame count, not what the source happened to contain"
    )
    # The armed verify must agree — it reads the VIDEO STREAM duration now, so a short
    # video track can no longer hide behind the container (= audio) duration.
    assert not failures(
        verify_video(out, audio=str(song), expected_canvas=(1280, 720))
    )
    # The straddling cut clones its last frame (bright testsrc), the wholly-beyond cut
    # falls back to black — visible properties, not implementation details.
    assert _frame_pixels(out, 4.05).mean() > 40, "straddle: cloned last frame"
    assert _frame_pixels(out, 4.42).mean() < 8, "beyond-video: black fallback"


@needs_ffmpeg
def test_rotation_side_data_is_padded_not_stretched(tmp_path):
    """muvid#21 item 10: a display-matrix portrait clip (the actual iPhone case — encoded
    landscape, rotated by side data) must land upright and pillarboxed on a landscape
    canvas. Tested BY side data, not by encoded size."""
    from muvid.footage.assemble import assemble_music_video

    flat = _testsrc_clip(tmp_path, "flat", 4.0, size="1280x720")
    rotated = tmp_path / "rot.mp4"
    r = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-display_rotation",
            "90",
            "-i",
            str(flat),
            "-c",
            "copy",
            str(rotated),
        ],
        capture_output=True,
    )
    if r.returncode != 0:
        pytest.skip("this ffmpeg cannot write display-rotation side data")
    streams = _probe_json(rotated)["streams"]
    if not any(s.get("side_data_list") for s in streams):
        pytest.skip("remux produced no rotation side data")

    song = _wav_song(tmp_path, seconds=4.0)
    aligns = [FootageAlignment("R", 0.0, 0.9, 4.0, (0.0, 4.0))]
    entries = validate_edl(fill_gaps([EdlEntry(0.0, 4.0, "R")], 4.0), aligns, 4.0)
    cuts = derive_cuts(entries, aligns, {"R": str(rotated)})
    out = assemble_music_video(
        cuts, str(song), str(tmp_path / "final.mp4"), canvas=(1280, 720)
    )

    frame = _frame_pixels(out, 2.0)
    h, w, _ = frame.shape
    assert (w, h) == (1280, 720)
    left, center = frame[:, : w // 8], frame[:, 3 * w // 8 : 5 * w // 8]
    assert left.mean() < 8, "expected a pillarbox: rotated content, padded"
    assert center.mean() > 40, "the rotated content must be IN the center"


# -- tool surface ------------------------------------------------------------


def _fake_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.footage.workspace import FootageWorkspace

    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    (proj.root / "song").mkdir()
    (proj.root / "song" / "song.wav").write_bytes(b"x")
    (proj.root / "clips").mkdir()
    (proj.root / "clips" / "A.mp4").write_bytes(b"x")
    m = proj.manifest()
    m.update(
        song="song.wav",
        song_duration=30.0,
        clips=[{"clip_id": "A", "file": "A.mp4", "name": "A"}],
    )
    proj._write_manifest(m)
    proj.save_alignments([FootageAlignment("A", 5.0, 0.9, 10.0, (5.0, 15.0))])
    return proj


def _stub_render(monkeypatch):
    import muvid.footage.assemble as A
    import muvid.visualize as V

    seen = {}

    def fake_assemble(cuts, song, out, canvas):
        seen["cuts"], seen["canvas"] = cuts, canvas
        Path(out).write_bytes(b"v")
        return Path(out)

    def fake_verify(*a, **k):
        seen["verify_kwargs"] = k
        return []

    monkeypatch.setattr(A, "assemble_music_video", fake_assemble)
    monkeypatch.setattr(V, "verify_video", fake_verify)
    monkeypatch.setattr(V, "failures", lambda c: [])
    monkeypatch.setattr(V, "report", lambda c: "ok")
    return seen


def test_assemble_tool_gap_fills_a_partial_edl_and_reports_coverage(
    tmp_path, monkeypatch
):
    pytest.importorskip("fastmcp")
    import muvid.mcp.footage_tools as ft
    from muvid.mcp.identity import use_email

    _fake_state(tmp_path, monkeypatch)
    seen = _stub_render(monkeypatch)
    with use_email("u@x.com"):
        meta = ft.assemble_music_video(
            "p", edl=[{"song_start": 5, "song_end": 15, "clip_id": "A"}]
        )
    # The render spans the whole song; the holes are explicit null-clip entries…
    assert meta["rendered_span"] == [0.0, 30.0]
    assert [e["clip_id"] for e in meta["edl"]] == [None, "A", None]
    # …and the coverage report still says where the user has NO footage.
    assert meta["coverage"]["uncovered"] == [
        {"song_start": 0.0, "song_end": 5.0},
        {"song_start": 15.0, "song_end": 30.0},
    ]
    assert [c.clip_path for c in seen["cuts"]][0] == ""  # head gap reached the renderer
    # B3 second half: the duration-match check is armed with the song.
    assert str(seen["verify_kwargs"]["audio"]).endswith("song.wav")


def test_assemble_tool_canvas_override(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    from fastmcp.exceptions import ToolError

    import muvid.mcp.footage_tools as ft
    from muvid.mcp.identity import use_email

    _fake_state(tmp_path, monkeypatch)
    seen = _stub_render(monkeypatch)
    with use_email("u@x.com"):
        meta = ft.assemble_music_video(
            "p",
            edl=[{"song_start": 5, "song_end": 15, "clip_id": "A"}],
            canvas="portrait",
        )
        assert meta["canvas"] == [1080, 1920]
        assert seen["canvas"] == (1080, 1920)
        with pytest.raises(ToolError, match="unknown canvas"):
            ft.assemble_music_video(
                "p",
                edl=[{"song_start": 5, "song_end": 15, "clip_id": "A"}],
                canvas="imax",
            )


def test_propose_edit_returns_a_full_song_edl_with_explicit_gaps(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    import muvid.mcp.footage_tools as ft
    from muvid.mcp.identity import use_email

    _fake_state(tmp_path, monkeypatch)
    with use_email("u@x.com"):
        r = ft.propose_edit("p", strategy="best_confidence")
    edl = r["edl"]
    assert edl[0]["song_start"] == 0.0 and edl[-1]["song_end"] == 30.0
    assert edl[0]["clip_id"] is None and edl[-1]["clip_id"] is None
    assert r["coverage"]["uncovered"], "the report still names the footage-less spans"
    # …and the proposal feeds back into a render verbatim (nulls included).
    seen = _stub_render(monkeypatch)
    with use_email("u@x.com"):
        meta = ft.assemble_music_video("p", edl=edl)
    assert meta["edl"] == edl
