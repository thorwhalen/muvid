"""Tests for footage INGEST — share links, folder archives, and honest refusals.

The cases here are the ones real source material produces, and every one of them used to
fail confusingly rather than loudly:

- a Drive/Dropbox *page* link, which is what a person actually copies;
- a *folder* link, which is one URI denoting N files;
- a private link, which answers ``HTTP 200`` with a sign-in page rather than an error.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("fastmcp")
pytest.importorskip("graze")

DRIVE_PAGE = "https://drive.google.com/file/d/1AbCdEf/view?usp=drivesdk"
DROPBOX_FOLDER = "https://www.dropbox.com/scl/fo/x9y8/z7?rlkey=abc&dl=0"
DROPBOX_FILE = "https://www.dropbox.com/scl/fi/a1b2/clip.mp4?rlkey=zz&dl=0"

#: A minimal payload that sniffs as video (ISO-BMFF `ftypmp42`).
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00" + b"\x00" * 64
#: What a private share link actually serves, at HTTP 200.
SIGNIN_PAGE = b'<!doctype html><html><head><base href="https://accounts.google.com/">'


class TestDeadlineScalesWithTheByteCap:
    """A byte cap and a time cap that contradict each other is a misconfiguration."""

    def test_a_big_cap_gets_proportionally_more_time(self):
        from muvid.mcp._fetch import TOTAL_TIMEOUT_S, deadline_for

        small, big = deadline_for(1024), deadline_for(400 * 1024 * 1024)
        assert small >= TOTAL_TIMEOUT_S
        assert big > small
        # 400 MB at the default 1 MB/s floor must not be asked to finish in the old 60 s.
        assert big > 300, "a 400 MB clip needs more than a document's time budget"

    def test_the_floor_still_applies_to_a_tiny_fetch(self):
        from muvid.mcp._fetch import TOTAL_TIMEOUT_S, deadline_for

        assert deadline_for(0) == pytest.approx(TOTAL_TIMEOUT_S)


class TestShareLinkResolution:
    def test_a_drive_page_link_becomes_a_direct_download(self):
        from muvid.mcp._fetch import resolve_share_link

        direct, kind = resolve_share_link(DRIVE_PAGE)
        assert kind == "file"
        assert "uc?export=download" in direct and "1AbCdEf" in direct

    def test_a_dropbox_file_link_becomes_dl_1(self):
        from muvid.mcp._fetch import resolve_share_link

        direct, kind = resolve_share_link(DROPBOX_FILE)
        assert kind == "file" and direct.endswith("dl=1")

    def test_a_dropbox_folder_link_is_typed_as_an_archive(self):
        from muvid.mcp._fetch import resolve_share_link

        _, kind = resolve_share_link(DROPBOX_FOLDER)
        assert kind == "archive", "one URI, N files — the caller must be able to tell"

    def test_a_plain_url_passes_through(self):
        from muvid.mcp._fetch import resolve_share_link

        direct, _ = resolve_share_link("https://example.com/clip.mp4")
        assert direct == "https://example.com/clip.mp4"


class TestFolderLinksAreRefusedByName:
    """The refusal has to name the tool that DOES handle it, or it is just a dead end."""

    def _project(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
        from muvid.footage.workspace import FootageWorkspace

        return FootageWorkspace.for_email("u@x.com").create_project("p")

    def test_add_footage_points_at_add_footage_folder(self, tmp_path, monkeypatch):
        import muvid.mcp.footage_tools as ft
        from fastmcp.exceptions import ToolError
        from muvid.mcp.identity import use_email

        self._project(tmp_path, monkeypatch)
        with use_email("u@x.com"), pytest.raises(ToolError, match="add_footage_folder"):
            ft.add_footage("p", url=DROPBOX_FOLDER)

    def test_set_song_also_refuses_a_folder(self, tmp_path, monkeypatch):
        import muvid.mcp.footage_tools as ft
        from fastmcp.exceptions import ToolError
        from muvid.mcp.identity import use_email

        self._project(tmp_path, monkeypatch)
        with use_email("u@x.com"), pytest.raises(ToolError, match="FOLDER link"):
            ft.set_song("p", url=DROPBOX_FOLDER)

    def test_add_footage_folder_refuses_a_single_file(self, tmp_path, monkeypatch):
        import muvid.mcp.footage_tools as ft
        from fastmcp.exceptions import ToolError
        from muvid.mcp.identity import use_email

        self._project(tmp_path, monkeypatch)
        with use_email("u@x.com"), pytest.raises(ToolError, match="use add_footage"):
            ft.add_footage_folder("p", url=DROPBOX_FILE)


def _zip_bytes(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


class TestArchiveExpansion:
    def test_n_videos_yield_n_clips(self, tmp_path):
        from muvid.mcp._fetch import extract_media_members

        archive = tmp_path / "a.zip"
        archive.write_bytes(
            _zip_bytes({"IMG_1.mov": b"a", "IMG_2.MOV": b"b", "IMG_3.mp4": b"c"})
        )
        got, skipped = extract_media_members(
            archive,
            tmp_path / "out",
            extensions=("mp4", "mov"),
            max_members=8,
            max_member_bytes=1000,
        )
        assert sorted(p.name for p in got) == ["IMG_1.mov", "IMG_2.MOV", "IMG_3.mp4"]
        assert skipped == []

    def test_non_media_members_are_named_not_silently_dropped(self, tmp_path):
        from muvid.mcp._fetch import extract_media_members

        archive = tmp_path / "a.zip"
        archive.write_bytes(_zip_bytes({"clip.mov": b"a", "notes.txt": b"b"}))
        got, skipped = extract_media_members(
            archive,
            tmp_path / "out",
            extensions=("mov",),
            max_members=8,
            max_member_bytes=1000,
        )
        assert [p.name for p in got] == ["clip.mov"]
        assert [s["name"] for s in skipped] == ["notes.txt"]
        assert "not a recognised media file" in skipped[0]["reason"]

    def test_the_clip_cap_is_reported_rather_than_silently_truncating(self, tmp_path):
        """A coverage decision made on quietly-shortened input is worse than a short list."""
        from muvid.mcp._fetch import extract_media_members

        archive = tmp_path / "a.zip"
        archive.write_bytes(_zip_bytes({f"c{i}.mov": b"x" for i in range(5)}))
        got, skipped = extract_media_members(
            archive,
            tmp_path / "out",
            extensions=("mov",),
            max_members=2,
            max_member_bytes=1000,
        )
        assert len(got) == 2
        assert len(skipped) == 3
        assert all("over the 2-clip limit" in s["reason"] for s in skipped)

    def test_an_oversized_member_is_named_with_its_size(self, tmp_path):
        from muvid.mcp._fetch import extract_media_members

        archive = tmp_path / "a.zip"
        archive.write_bytes(_zip_bytes({"big.mov": b"x" * 500, "ok.mov": b"x"}))
        got, skipped = extract_media_members(
            archive,
            tmp_path / "out",
            extensions=("mov",),
            max_members=8,
            max_member_bytes=100,
        )
        assert [p.name for p in got] == ["ok.mov"]
        assert "exceeds" in skipped[0]["reason"]

    def test_zip_slip_cannot_escape_the_destination(self, tmp_path):
        """A member named ../../evil.mov must land inside dest_dir, or nowhere."""
        from muvid.mcp._fetch import extract_media_members

        archive = tmp_path / "a.zip"
        archive.write_bytes(_zip_bytes({"../../evil.mov": b"pwned"}))
        dest = tmp_path / "out"
        got, _ = extract_media_members(
            archive, dest, extensions=("mov",), max_members=8, max_member_bytes=1000
        )
        for p in got:
            assert dest.resolve() in p.resolve().parents
        assert not (tmp_path.parent / "evil.mov").exists()

    def test_a_non_zip_payload_fails_with_a_readable_message(self, tmp_path):
        from muvid.mcp._fetch import FetchError, extract_media_members

        archive = tmp_path / "a.zip"
        archive.write_bytes(SIGNIN_PAGE)
        with pytest.raises(FetchError, match="not a readable ZIP"):
            extract_media_members(
                archive,
                tmp_path / "out",
                extensions=("mov",),
                max_members=8,
                max_member_bytes=1000,
            )


class TestContentKindGuard:
    """An HTML sign-in page must never be written to disk as media."""

    def test_a_signin_page_is_refused_with_the_permission_diagnosis(
        self, tmp_path, monkeypatch
    ):
        import muvid.mcp._fetch as fetch_mod
        from muvid.mcp._fetch import FetchError, fetch_to_file_streaming

        class _Resp:
            def __init__(self):
                self._chunks = [SIGNIN_PAGE, b"more"]

            def read(self, _n):
                return self._chunks.pop(0) if self._chunks else b""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(
            fetch_mod, "_open_following_redirects", lambda u, d: _Resp()
        )
        dest = tmp_path / "song.mp3"
        with pytest.raises(FetchError, match="anyone-with-the-link"):
            fetch_to_file_streaming(
                "https://drive.google.com/x",
                dest,
                max_bytes=10_000,
                expect_kind="audio",
            )
        assert not dest.exists(), "a refused payload must leave no partial file"

    def test_real_media_passes_the_guard(self, tmp_path, monkeypatch):
        import muvid.mcp._fetch as fetch_mod
        from muvid.mcp._fetch import fetch_to_file_streaming

        class _Resp:
            def __init__(self):
                self._chunks = [FAKE_MP4]

            def read(self, _n):
                return self._chunks.pop(0) if self._chunks else b""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(
            fetch_mod, "_open_following_redirects", lambda u, d: _Resp()
        )
        dest = tmp_path / "clip.mp4"
        fetch_to_file_streaming(
            "https://x/y.mp4", dest, max_bytes=10_000, expect_kind="video"
        )
        assert dest.read_bytes() == FAKE_MP4


class TestNoSourceLeavesTheAddressableSet:
    """A source must stay referenceable whatever its measurements say.

    Selection is expressed as references to (source, interval). A clip that is omitted from
    the alignment artifact because it matched badly is still on disk, but nothing downstream
    can point at it — it has effectively vanished from the project, without anyone asking for
    that. Removal is a user's decision, never a measurement's side effect.
    """

    def test_the_alignment_record_round_trips_the_overlaps_flag(self):
        from muvid.footage.edl import FootageAlignment

        a = FootageAlignment(
            clip_id="x",
            offset_s=1.0,
            confidence=0.4,
            duration_s=5.0,
            coverage=(2.0, 2.0),
            overlaps=False,
        )
        assert FootageAlignment.from_dict(a.to_dict()) == a

    def test_a_record_written_before_the_flag_existed_reads_as_overlapping(self):
        """Older artifacts were only ever persisted WHEN they overlapped."""
        from muvid.footage.edl import FootageAlignment

        legacy = {
            "clip_id": "x",
            "offset_s": 1.0,
            "confidence": 0.4,
            "duration_s": 5.0,
            "coverage": [1.0, 6.0],
        }
        assert FootageAlignment.from_dict(legacy).overlaps is True

    def test_a_non_overlapping_clip_is_never_selected_into_an_edit(self):
        """Reported, but not usable — the two are different states, and both must exist."""
        from muvid.footage.edl import FootageAlignment
        from muvid.footage.strategy import select_edl

        good = FootageAlignment("g", 0.0, 0.9, 10.0, (0.0, 10.0), True)
        nope = FootageAlignment("n", 99.0, 0.9, 10.0, (10.0, 10.0), False)
        entries = select_edl("best_confidence", [good, nope], 10.0)
        assert {e.clip_id for e in entries} == {"g"}
