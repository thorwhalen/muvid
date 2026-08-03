"""Shared ffmpeg-capability guards for muvid's test suite.

A rendering test needs more than the ``ffmpeg`` binary: the specific *filter* it
exercises has to be compiled into that build. ``drawtext`` (libfreetype) and
``showcqt`` (libfftw) are the usual absentees — a stock Homebrew or minimal
Debian ffmpeg runs happily without them, and a test guarded only on ``which
ffmpeg`` then **fails** where it should have **skipped**.

So the guards here compose the binary check with
:func:`muvid.visualize.ffmpeg.has_filter` — muvid's own filter probe, so the
tests ask the build exactly the question the library asks it, rather than
growing a second answer that can drift.
"""

from __future__ import annotations

import shutil

import pytest

from muvid.visualize.ffmpeg import has_filter

#: Both binaries muvid.visualize shells out to are on PATH.
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

#: Skip a test that needs ffmpeg at all (but no particular filter).
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed")


def has_ffmpeg_filter(name: str) -> bool:
    """Whether ffmpeg is installed *and* this build has the ``name`` filter.

    The two checks are inseparable: :func:`has_filter` shells out to ``ffmpeg
    -filters``, so asking it about a machine with no ffmpeg raises rather than
    answering ``False``.
    """
    return HAS_FFMPEG and has_filter(name)


def needs_ffmpeg_filter(*names: str):
    """A skip marker for a test that needs these filters specifically.

    Name every filter the test's chain relies on; the test skips (rather than
    fails) if this build is missing any of them.
    """
    missing = [name for name in names if not has_ffmpeg_filter(name)]
    return pytest.mark.skipif(
        bool(missing),
        reason=f"this ffmpeg build has no {', '.join(map(repr, missing))} filter",
    )
