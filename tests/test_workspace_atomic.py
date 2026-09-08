"""Crash-consistency of the footage project's on-disk records (muvid#17 item 4).

Two facts the workspace used to get wrong, each simulated here rather than reasoned
about:

- ``manifest.json`` / ``alignments.json`` were bare ``write_text`` calls — a
  truncate-then-write — while ``manifest()`` swallows a bad read into an EMPTY project.
  A torn write therefore presented as a project with no song and no clips. The tests
  tear the write at both seams (the data fsync and the final ``os.replace``) and assert
  the previous complete record is what a reader sees, with no temp litter left behind.
- ``set_song`` wrote the manifest naming the new song and THEN unlinked the old
  alignment. A crash between the two left offsets measured against one song read as if
  they belonged to another. The test fails the manifest write and asserts the stale
  alignment is already gone; two more fail the song ingest itself and assert nothing
  the old song owned was touched.

No ffmpeg: ``_probe_duration`` is patched, and a stand-in byte string is the song.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from muvid.footage.edl import FootageAlignment
from muvid.footage import workspace as W

_ALIGN = FootageAlignment("A", 1.0, 0.9, 5.0, (1.0, 6.0))


def _project(tmp_path, monkeypatch):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(W, "_probe_duration", lambda p: 30.0)
    return W.FootageWorkspace.for_email("u@x.com").create_project("p")


def _with_song(proj, tmp_path, *, name="old"):
    """A project holding a song, one clip, an alignment and a scores dir."""
    src = tmp_path / f"{name}.wav"
    src.write_bytes(f"RIFF-{name}".encode())
    proj.set_song(str(src), ext="wav")
    clip = tmp_path / "A.mp4"
    clip.write_bytes(b"x")
    proj.add_clip("A", str(clip), ext="mp4")
    proj.save_alignments([_ALIGN])
    (proj.root / "scores").mkdir()
    (proj.root / "scores" / "manifest.json").write_text("{}")
    return proj


def _litter(root: Path) -> list[str]:
    """Temp files the atomic dance may have left: any dotfile in the project root."""
    return sorted(p.name for p in root.iterdir() if p.name.startswith("."))


def _tear_replace_of(monkeypatch, name: str):
    """Make ``os.replace`` fail for a target called ``name`` AFTER the temp is written."""
    real = os.replace

    def torn(src, dst, *a, **k):
        if Path(dst).name == name:
            raise OSError(errno.EIO, f"simulated torn write of {name}")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", torn)


# -- the helper itself --------------------------------------------------------


def test_atomic_write_text_replaces_in_place_and_leaves_no_temp(tmp_path):
    target = tmp_path / "manifest.json"
    W.atomic_write_text(target, '{"v": 1}')
    W.atomic_write_text(target, '{"v": 2}')
    assert json.loads(target.read_text()) == {"v": 2}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["manifest.json"]


def test_atomic_write_text_emits_the_same_bytes_write_text_did(tmp_path):
    # The FORMAT must not change (that is a migration): json.dumps is ASCII by default,
    # so the UTF-8 encoding on the way out is byte-identical to the old write_text —
    # with one deliberate, platform-level exception. The helper writes BYTES, so its
    # newlines are "\n" everywhere; the old write_text inherited the platform's
    # text-mode translation and emitted "\r\n" on Windows. Pinning the newline is
    # what cross-platform code is supposed to do (a record must not change bytes
    # depending on which machine wrote it), and every reader of these files is a
    # JSON parser, for which CRLF and LF are the same document. So the comparison
    # is against write_text with the newline pinned, which is the intended contract.
    payload = json.dumps({"title": "café", "clips": []}, indent=2)
    W.atomic_write_text(tmp_path / "a.json", payload)
    (tmp_path / "b.json").write_text(payload, encoding="utf-8", newline="\n")
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()
    assert b"\r\n" not in (tmp_path / "a.json").read_bytes()


# -- torn writes read as the previous good state --------------------------------


def test_torn_manifest_replace_keeps_the_previous_state(tmp_path, monkeypatch):
    proj = _with_song(_project(tmp_path, monkeypatch), tmp_path)
    before = proj.manifest()
    assert before.get("song") and before.get("clips")  # a real, non-empty state

    _tear_replace_of(monkeypatch, "manifest.json")
    clip = tmp_path / "B.mp4"
    clip.write_bytes(b"y")
    with pytest.raises(OSError):
        proj.add_clip("B", str(clip), ext="mp4")

    # Not an empty project (the old failure shape) — the last complete record.
    assert proj.manifest() == before
    assert _litter(proj.root) == []


def test_torn_manifest_data_write_keeps_the_previous_state(tmp_path, monkeypatch):
    # The other seam: the data never reaches the disk (fsync fails), so the temp holds
    # a record nobody may read. The previous manifest must still be the one read back.
    proj = _with_song(_project(tmp_path, monkeypatch), tmp_path)
    before = proj.manifest()

    def no_space(fd):
        raise OSError(errno.ENOSPC, "simulated full disk")

    monkeypatch.setattr(os, "fsync", no_space)
    with pytest.raises(OSError):
        proj._write_manifest({**before, "title": "renamed"})

    assert proj.manifest() == before
    assert _litter(proj.root) == []


def test_torn_alignments_write_keeps_the_previous_alignments(tmp_path, monkeypatch):
    proj = _with_song(_project(tmp_path, monkeypatch), tmp_path)
    assert [a.clip_id for a in proj.load_alignments()] == ["A"]

    _tear_replace_of(monkeypatch, "alignments.json")
    with pytest.raises(OSError):
        proj.save_alignments([FootageAlignment("B", 2.0, 0.9, 5.0, (2.0, 7.0))])

    assert [a.clip_id for a in proj.load_alignments()] == ["A"]
    assert _litter(proj.root) == []


# -- set_song ordering ------------------------------------------------------------


def test_set_song_drops_the_stale_alignment_before_the_manifest_can_name_the_new_song(
    tmp_path, monkeypatch
):
    proj = _with_song(_project(tmp_path, monkeypatch), tmp_path, name="old")
    before = proj.manifest()

    _tear_replace_of(monkeypatch, "manifest.json")
    new_src = tmp_path / "new.wav"
    new_src.write_bytes(b"RIFF-new")
    with pytest.raises(OSError):
        proj.set_song(str(new_src), ext="wav")

    # The invariant: whatever state the crash left, no alignment measured against the
    # OLD song survives beside a manifest — this one, or the new one it would have named.
    assert not (proj.root / "alignments.json").exists()
    assert proj.load_alignments() == []
    assert not (proj.root / "scores").exists()
    # The manifest is the previous complete record, not a torn or empty one...
    assert proj.manifest() == before
    # ...and neither the manifest's temp nor the staged song was left behind.
    assert _litter(proj.root) == []


@pytest.mark.parametrize(
    "inject",
    [
        pytest.param(lambda mp, src: src.unlink(), id="missing-source"),
        pytest.param(
            lambda mp, src: mp.setattr(
                W, "_probe_duration", lambda p: (_ for _ in ()).throw(RuntimeError("x"))
            ),
            id="unprobeable-source",
        ),
    ],
)
def test_set_song_that_fails_before_the_point_of_no_return_touches_nothing(
    tmp_path, monkeypatch, inject
):
    # Everything that can fail runs first, against a staged copy: a bad source or an
    # un-probeable file must leave the old song, its alignment and its scores standing.
    proj = _with_song(_project(tmp_path, monkeypatch), tmp_path, name="old")
    before = proj.manifest()
    old_song = proj.song_path()
    assert old_song.read_bytes() == b"RIFF-old"

    new_src = tmp_path / "new.wav"
    new_src.write_bytes(b"RIFF-new")
    inject(monkeypatch, new_src)
    with pytest.raises((OSError, RuntimeError)):
        proj.set_song(str(new_src), ext="wav")

    assert proj.manifest() == before
    assert old_song.read_bytes() == b"RIFF-old"
    assert [a.clip_id for a in proj.load_alignments()] == ["A"]
    assert (proj.root / "scores" / "manifest.json").exists()
    assert _litter(proj.root) == []


def test_set_song_happy_path_replaces_the_song_and_invalidates(tmp_path, monkeypatch):
    # The reorder must not cost the behaviour test_set_song_clears_stale_alignments
    # already pins; this one also checks the manifest and the file agree afterwards.
    proj = _with_song(_project(tmp_path, monkeypatch), tmp_path, name="old")
    old_hash = proj.manifest()["song_hash"]
    new_src = tmp_path / "new.mp3"
    new_src.write_bytes(b"ID3-new")
    proj.set_song(str(new_src), ext="mp3")

    m = proj.manifest()
    assert m["song"] == "song.mp3" and m["song_hash"] != old_hash
    assert proj.song_path().read_bytes() == b"ID3-new"
    assert sorted(p.name for p in (proj.root / "song").iterdir()) == ["song.mp3"]
    assert proj.load_alignments() == [] and not (proj.root / "scores").exists()
    assert m["clips"][0]["clip_id"] == "A"  # a song change never removes a source
    assert _litter(proj.root) == []


# -- render meta shares the dance ---------------------------------------------------


def test_torn_render_meta_write_keeps_the_previous_meta(tmp_path, monkeypatch):
    proj = _project(tmp_path, monkeypatch)
    proj.new_render_dir("r1")
    proj.write_render_meta("r1", {"render_id": "r1", "ref_n": 1})

    _tear_replace_of(monkeypatch, "meta.json")
    with pytest.raises(OSError):
        proj.write_render_meta("r1", {"render_id": "r1", "ref_n": 999})

    assert proj.list_renders()[0]["ref_n"] == 1
    assert _litter(proj.root / "renders" / "r1") == []
