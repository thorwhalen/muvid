"""Check a rendered music video against what YouTube actually wants.

Rendering can succeed and still produce something wrong: a video a few seconds
longer than the song, a pixel format half the world cannot decode, a thumbnail
over the upload limit, a track 6 LU quieter than the rest of the album. Those
failures are silent — the file plays fine locally — so they are worth asserting
rather than eyeballing.

    >>> from muvid.visualize import verify_video, report
    >>> checks = verify_video("song.mp4", audio="song.wav")   # doctest: +SKIP
    >>> print(report(checks))                                 # doctest: +SKIP
    ✓ container      h264 / aac
    ✓ pixel format   yuv420p
    ...

This is the machine-checkable half of the quality checklist; the ``music2video``
skill carries the half that needs judgement.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from muvid.visualize.canvas import THUMBNAIL_MAX_BYTES, THUMBNAIL_SIZE
from muvid.visualize.ffmpeg import (
    DEFAULT_LOUDNESS_I,
    Loudness,
    PathLike,
    measure_loudness,
    media_duration,
    probe,
)

#: How far the video may run past (or short of) the audio, in seconds. A still
#: video is cut on a GOP boundary, so an exact match is not achievable.
DURATION_TOLERANCE = 0.5

#: How far the integrated loudness may sit from the target, in LU. Encoding to
#: AAC moves it slightly, so demanding an exact hit would fail every time.
LOUDNESS_TOLERANCE = 1.5

#: How much of the file's head to scan for the ``moov`` atom's boxes. With
#: ``+faststart`` the moov comes first, so this is generous.
_MOOV_SCAN_BYTES = 1 << 20


@dataclass(frozen=True)
class Check:
    """One verification result."""

    name: str
    ok: bool
    detail: str

    def __bool__(self) -> bool:
        return self.ok


def verify_video(
    video: PathLike,
    *,
    audio: PathLike | None = None,
    thumbnail: PathLike | None = None,
    loudness: Loudness | None = None,
    check_loudness: bool = False,
    duration_tolerance: float = DURATION_TOLERANCE,
) -> list[Check]:
    """Check ``video`` against YouTube's expectations; return one result per check.

    Args:
        video: The rendered mp4.
        audio: The source song — enables the duration-match check, which is the
            one that catches a mis-built filtergraph.
        thumbnail: The thumbnail to check against YouTube's limits.
        loudness: The target the video was normalized to.
        check_loudness: Actually measure the output's loudness. This decodes the
            whole track, so it is off by default.
        duration_tolerance: Allowed audio/video duration difference, in seconds.

    Returns:
        A list of :class:`Check`. Falsy checks are the problems; :func:`report`
        renders them, and :func:`failures` filters them.
    """
    video = Path(video)
    checks: list[Check] = []
    info = probe(video)
    streams = info.get("streams", [])
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    astream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if not vstream or not astream:
        return [
            Check(
                "streams",
                False,
                f"expected a video and an audio stream, found "
                f"{[s.get('codec_type') for s in streams]}",
            )
        ]

    vcodec, acodec = vstream.get("codec_name"), astream.get("codec_name")
    checks.append(
        Check(
            "container",
            vcodec == "h264" and acodec == "aac",
            f"{vcodec} / {acodec}"
            + ("" if vcodec == "h264" and acodec == "aac" else " (want h264 / aac)"),
        )
    )

    pix_fmt = vstream.get("pix_fmt")
    checks.append(
        Check(
            "pixel format",
            pix_fmt == "yuv420p",
            f"{pix_fmt}"
            + (
                ""
                if pix_fmt == "yuv420p"
                else " — yuv420p is the only format every player decodes"
            ),
        )
    )

    width, height = int(vstream.get("width", 0)), int(vstream.get("height", 0))
    ratio = width / height if height else 0
    checks.append(
        Check(
            "aspect ratio",
            abs(ratio - 16 / 9) < 0.02,
            f"{width}x{height}"
            + (
                "" if abs(ratio - 16 / 9) < 0.02 else " — not 16:9, YouTube will bar it"
            ),
        )
    )
    checks.append(
        Check(
            "resolution",
            width >= 1280 and height >= 720,
            f"{width}x{height}" + ("" if width >= 1280 else " — below 720p"),
        )
    )

    channels, rate = astream.get("channels"), astream.get("sample_rate")
    checks.append(
        Check(
            "audio", channels == 2 and str(rate) == "48000", f"{channels}ch @ {rate} Hz"
        )
    )
    checks.append(_verify_no_edit_lists(video))

    if audio is not None:
        song, rendered = media_duration(audio), media_duration(video)
        delta = rendered - song
        checks.append(
            Check(
                "duration",
                abs(delta) <= duration_tolerance,
                f"video {rendered:.2f}s vs audio {song:.2f}s ({delta:+.2f}s)",
            )
        )

    if check_loudness:
        target = loudness or Loudness()
        measured = measure_loudness(video, target).measured or {}
        actual = float(measured.get("input_i", "nan"))
        want = target.integrated if loudness else DEFAULT_LOUDNESS_I
        checks.append(
            Check(
                "loudness",
                abs(actual - want) <= LOUDNESS_TOLERANCE,
                f"{actual:.2f} LUFS (target {want} LUFS)",
            )
        )

    if thumbnail is not None:
        checks.append(_verify_thumbnail(Path(thumbnail)))

    return checks


def _verify_no_edit_lists(video: Path) -> Check:
    """YouTube: "No Edit Lists (or the video might not get processed correctly)".

    ffmpeg writes an ``elst`` box by default, to declare the AAC encoder's priming
    delay — and ``-movflags +faststart`` does not remove it. The boxes live in the
    ``moov`` atom, which faststart has already moved to the front of the file, so
    scanning the head is enough to find them.
    """
    head = video.open("rb").read(_MOOV_SCAN_BYTES)
    count = head.count(b"elst")
    return Check(
        "edit lists",
        count == 0,
        "none"
        if count == 0
        else f"{count} elst box(es) — YouTube may fail to process this; "
        "render with -use_editlist 0",
    )


def _verify_thumbnail(thumbnail: Path) -> Check:
    """Check a thumbnail against YouTube's size, ratio, and byte limits."""
    if not thumbnail.exists():
        return Check("thumbnail", False, f"{thumbnail} does not exist")
    stream = next(
        (s for s in probe(thumbnail).get("streams", []) if s.get("width")), None
    )
    if not stream:
        return Check("thumbnail", False, f"{thumbnail.name} is not a readable image")

    width, height = int(stream["width"]), int(stream["height"])
    size_bytes = thumbnail.stat().st_size
    problems = []
    if (width, height) < THUMBNAIL_SIZE:
        # 640 wide is YouTube's hard floor; 1280x720 is the recommended size and
        # the bar worth holding your own output to.
        problems.append(
            f"below the recommended {THUMBNAIL_SIZE[0]}x{THUMBNAIL_SIZE[1]}"
        )
    if abs(width / height - 16 / 9) > 0.02:
        problems.append("not 16:9")
    if size_bytes > THUMBNAIL_MAX_BYTES:
        # thumbnails.set caps at 2 MB — 25x stricter than the web UI's 50 MB.
        problems.append(f"over the {THUMBNAIL_MAX_BYTES // 1024 // 1024} MiB API limit")
    detail = f"{width}x{height}, {size_bytes / 1024:.0f} KB"
    return Check(
        "thumbnail",
        not problems,
        detail + (f" — {', '.join(problems)}" if problems else ""),
    )


def failures(checks: list[Check]) -> list[Check]:
    """Just the checks that failed."""
    return [c for c in checks if not c.ok]


def report(checks: list[Check]) -> str:
    """Render ``checks`` as an aligned, readable block."""
    width = max((len(c.name) for c in checks), default=0)
    return "\n".join(
        f"{'✓' if c.ok else '✗'} {c.name.ljust(width)}  {c.detail}" for c in checks
    )
