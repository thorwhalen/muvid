"""Which OPTIONS a look may set on a geometry filter — enumerated from the binary.

``LOOK_FILTERS`` bounds which filters a caller-supplied look may name.
``MAX_LOOK_SCALE`` bounds the frame size it may *declare*. Neither bounds the
frame it may *produce*, and the first pass at muvid#75 closed only the second:
it listed the options that set a size and read every other option as nothing,
which is a blocklist wearing an allowlist's clothes. Two options leaked, both
larger than the case it refused, both accepted end to end by the live MCP tool.
Measured on a 1920x1080 canvas with the exact ``-vf`` ``assemble._part_filter``
builds, 3 frames, ``/usr/bin/time -l`` peak RSS:

    ==========================================================  ===========  ========
    fragment                                                    frame        peak RSS
    ==========================================================  ===========  ========
    (a look at canvas size)                                     1920x1080     110 MB
    ``scale=7680:4320`` — the bound's own stated worst case      7680x4320     268 MB
    ``scale=8000:8000`` — REFUSED, the case muvid#75 named       8000x8000     403 MB
    ``pad=w=1920:h=1080:aspect=1/30``                           1920x57600     590 MB
    ``crop=w=1920:h=200,scale=w=7680:h=4320:``
    ``force_original_aspect_ratio=increase``                    41472x4320     941 MB
    ==========================================================  ===========  ========

So the table became an allowlist per ``(filter, option)`` — and this file is what
keeps it honest, in three layers that fail for different reasons:

1. **The census** (``tests/data/ffmpeg_filter_options.json``, generated) records
   every option name the binary declares for every allowlisted filter. A new
   ffmpeg option fails the freshness test, so it becomes a decision someone
   records rather than a surprise refusal in production.
2. **The measured lever list** is a literal here: the ``(filter, option)`` pairs
   that were observed to move the produced frame. Each must be bounded or
   refused. It runs without ffmpeg, so CI holds it even on a runner with no
   binary.
3. **The agreement sweep** drives the census through the real binary and asserts
   the gate never under-reads a growth: an accepted look may not produce a frame
   larger, on either axis, than the size the gate believes it declares. That is
   the property the hand-written corpus could not express — its list contained
   neither leak.

Layers 2 and 3 are complementary rather than redundant, and the boundary is worth
stating because it is not obvious. Mutation-tested with the other layers
deselected: the sweep ALONE catches ``pad=aspect`` and
``scale=force_original_aspect_ratio``, and **cannot** catch
``scale=force_divisible_by`` — that option only moves the frame alongside
``force_original_aspect_ratio``, which the gate refuses, so the sweep's own
combination context is refused before it renders and the row is skipped. An
option whose only effect is in combination with a REFUSED option is structurally
invisible to a sweep that runs through the gate. Layer 2 is the literal record of
what a sweep run WITHOUT the gate found, which is why it is a list of measured
pairs rather than a re-derivation.

The census is deliberately **not** trusted as complete, which is why layer 3
tests the guard rather than the census: ffmpeg 6.1.6's ``-h filter=scale`` does
not print ``s``/``size``, and ``scale=s=320x240`` produces a 320x240 frame there
anyway (measured). A gate that classified only what the help prints would have
had a hole on one of the two binaries this fleet runs.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from muvid.footage.edl import (
    LOOK_FILTERS,
    _allowed_options,
    _LOOK_GEOMETRY_FILTERS,
    _LOOK_REFUSED_OPTIONS,
    _look_output_sizes,
    _validate_look_size,
)
from tests.ffmpeg_support import needs_ffmpeg

CENSUS_PATH = Path(__file__).parent / "data" / "ffmpeg_filter_options.json"
CENSUS = json.loads(CENSUS_PATH.read_text())
RECORDED = CENSUS["options"]

#: ``   name   <type>   ..FV....   help`` for the filter's own options, and
#: ``  -name   <type>`` for a child class's (scale carries SWScaler and
#: framesync, and those ARE settable from a filtergraph string — measured,
#: ``scale=w=64:h=48:dstw=800`` is rc=0). Constants are indented deeper and have
#: no ``<type>``, so the indent test excludes them.
_OPT_LINE = re.compile(
    r"^(?P<ind> +)-?(?P<name>[A-Za-z0-9_]+) +<(?P<type>[^>]+)> +\S+ *(?P<help>.*)$"
)


def declared_options(filter_name: str) -> "dict[str, str]":
    """``{option name: declared type}`` as ``ffmpeg -h filter=<name>`` prints it."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-h", f"filter={filter_name}"],
        capture_output=True,
        text=True,
    ).stdout
    found: "dict[str, str]" = {}
    for line in out.splitlines():
        m = _OPT_LINE.match(line)
        if m and len(m.group("ind")) in (2, 3):
            found.setdefault(m.group("name"), m.group("type").strip())
    return found


# ---------------------------------------------------------------------------
# 1. the census is fresh — a new ffmpeg option cannot slip in silently
# ---------------------------------------------------------------------------


@needs_ffmpeg
@pytest.mark.parametrize("filter_name", sorted(LOOK_FILTERS))
def test_every_option_this_binary_declares_is_in_the_recorded_census(filter_name):
    """Read the installed binary; anything it offers that we never recorded fails.

    The failure is not "the gate is broken" — an unrecorded option is already
    refused on a geometry filter, because the allowlist refuses by default. It is
    "nobody has decided about this option yet", which for one of the four
    geometry filters means deciding whether it belongs in
    ``_LOOK_GEOMETRY_FILTERS``, and for the other thirteen means checking it
    cannot move the frame (the sweep at the bottom of this file does that).
    """
    declared = set(declared_options(filter_name))
    if filter_name == "null":
        assert not declared, "null grew options; classify them"
        return
    assert declared, f"read no options at all for {filter_name!r} — parser broken?"
    unknown = sorted(declared - set(RECORDED[filter_name]))
    assert not unknown, (
        f"this ffmpeg declares {unknown} on {filter_name!r} and the recorded "
        f"census does not. Decide about each"
        + (
            " (it is one of the four filters that can change the output frame, so "
            "an option that moves geometry must be measured and then either "
            "bounded in `sizes` or left out of the allowlist with a reason in "
            "`_LOOK_REFUSED_OPTIONS`)"
            if filter_name in _LOOK_GEOMETRY_FILTERS
            else " (this filter is not option-checked at all, so a new option "
            "that moves the frame would be an unbounded lever)"
        )
        + f", then refresh {CENSUS_PATH.name} — see _refresh_snapshot in this file."
    )


def test_the_allowlist_never_names_an_option_ffmpeg_does_not_have():
    """A typo in the allowlist is dead code, and dead code that LOOKS like a rule.

    Runs without ffmpeg, against the recorded census, so it holds in CI. The
    union of both binaries is the right reference: an option one build hides is
    still real (`scale`'s `s`/`size` on 6.1.6).
    """
    for filt, spec in _LOOK_GEOMETRY_FILTERS.items():
        recorded = set(RECORDED[filt])
        unknown = sorted((set(spec.sizes) | set(spec.free)) - recorded)
        assert not unknown, (
            f"_LOOK_GEOMETRY_FILTERS allows {unknown} on {filt!r}, which no "
            f"recorded ffmpeg declares. Either it is a typo (so the rule it looks "
            f"like does nothing) or the census is stale."
        )
        stale = sorted({o for (f, o) in _LOOK_REFUSED_OPTIONS if f == filt} - recorded)
        assert not stale, (
            f"_LOOK_REFUSED_OPTIONS records a reason for {stale} on {filt!r}, "
            "which no recorded ffmpeg declares — the reason outlived the option."
        )


def test_a_recorded_reason_is_only_ever_for_an_option_that_is_actually_refused():
    """The two tables must not disagree about a single option.

    An option in both ``_LOOK_REFUSED_OPTIONS`` and the allowlist would be
    accepted while carrying a paragraph explaining why it is refused — the
    message would never be reachable and the docstring would be a lie.
    """
    for filt, opt in _LOOK_REFUSED_OPTIONS:
        assert opt not in _allowed_options(filt), (
            f"{filt}.{opt} carries a refusal reason and is ALLOWED — one of the "
            "two is wrong."
        )


# ---------------------------------------------------------------------------
# 2. every measured lever is bounded or refused  (no ffmpeg needed)
# ---------------------------------------------------------------------------

#: The ``(filter, option)`` pairs whose value was MEASURED to move the produced
#: frame, swept over every option of every allowlisted filter in three contexts
#: (alone, beside a size, and beside a size AND
#: ``force_original_aspect_ratio``) on ffmpeg 9.0.1 and 6.1.6 alike.
#:
#: A literal rather than a re-derivation of the tables it checks: parameterising
#: this by ``_LOOK_GEOMETRY_FILTERS`` would make dropping ``aspect`` from the
#: refusal path invisible, which is the shape of the bug this file exists for.
#:
#: It is also literal because the sweep at the bottom of this file cannot rebuild
#: it: that sweep runs fragments THROUGH the gate, so an option that only moves
#: the frame alongside a refused one is never rendered there. ``force_divisible_by``
#: is exactly that case (it does nothing without ``force_original_aspect_ratio``),
#: and this list is the only layer that holds it.
MEASURED_GEOMETRY_LEVERS = [
    ("crop", "out_w"),
    ("crop", "w"),
    ("crop", "out_h"),
    ("crop", "h"),
    ("pad", "width"),
    ("pad", "w"),
    ("pad", "height"),
    ("pad", "h"),
    ("pad", "aspect"),
    ("scale", "w"),
    ("scale", "width"),
    ("scale", "h"),
    ("scale", "height"),
    ("scale", "size"),
    ("scale", "s"),
    ("scale", "force_original_aspect_ratio"),
    ("scale", "force_divisible_by"),
    ("zoompan", "s"),
]

#: The measured levers that are allowed through UNBOUNDED, each with the reason.
#: ``crop`` cannot grow a frame — ``crop=8000:8000`` and
#: ``crop=w='iw*80':h='ih*80'`` are both refused by ffmpeg itself — so its output
#: is bounded by its input, and bounding it would refuse ``looks``' constant-size
#: ``motion`` (``crop=w='iw*0.5':h='ih*0.5'``), the one muvid-compiled fragment
#: whose size options are expressions.
UNBOUNDED_BY_DECISION = {("crop", o) for o in ("out_w", "w", "out_h", "h")}


@pytest.mark.parametrize("filt, opt", MEASURED_GEOMETRY_LEVERS)
def test_every_measured_geometry_lever_is_bounded_or_refused(filt, opt):
    """Each one must be a size the bound reads, a refusal, or a recorded decision.

    This is the test the first pass did not have. Its list is what a systematic
    sweep of the binary found, so an option that moves the frame cannot be
    "not thought of" — only classified.
    """
    spec = _LOOK_GEOMETRY_FILTERS[filt]
    if (filt, opt) in UNBOUNDED_BY_DECISION:
        assert opt in spec.free, f"{filt}.{opt} is a recorded decision; keep it free"
        return
    bounded = opt in spec.sizes
    refused = opt not in _allowed_options(filt)
    assert bounded or refused, (
        f"{filt}.{opt} moves the produced frame (measured) and is neither bounded "
        f"against the canvas nor refused — it is in `free`, which claims it cannot "
        "move the frame."
    )


def test_the_two_leaks_that_reopened_muvid75_are_named_in_the_refusal_reasons():
    """Their measurements are the reason the rule is a refusal, so pin them there.

    Without this the reasons could be trimmed to "not allowed" and the next
    reader would have no way to know that bounding them was considered and why
    it is not possible without reimplementing libavfilter's geometry negotiation.
    """
    assert "57600" in _LOOK_REFUSED_OPTIONS[("pad", "aspect")]
    assert "41472" in _LOOK_REFUSED_OPTIONS[("scale", "force_original_aspect_ratio")]


# ---------------------------------------------------------------------------
# 3. the gate never under-reads a growth  (the census, through the binary)
# ---------------------------------------------------------------------------

SRC_W, SRC_H = 64, 48
#: A LADDER of plausible values per declared type, largest first. Every value
#: ffmpeg accepts is asserted on; the ladder exists because each option has its
#: own declared range (``bilateral``'s ``sigmaR`` is 0..1, its ``planes`` 0..15)
#: and a single value per type leaves most rows vacuous — which the vacuity floor
#: below catches rather than tolerates.
#:
#: The GROWING values are the load-bearing ones. An earlier version of this sweep
#: used ``2`` everywhere, which cannot grow a 64x48 frame, so ``pad``'s own
#: ``w``/``h`` did not register as levers at all: the instrument has to contain
#: the property it is looking for.
VALUES_BY_TYPE = {
    "int": ["200", "16", "2", "1", "0"],
    "int64": ["200", "16", "2", "1", "0"],
    "float": ["2", "0.5"],
    "double": ["2", "0.5"],
    "boolean": ["1"],
    "string": ["200", "iw*4", "2"],
    "rational": ["4/1"],
    "image_size": ["320x240"],
    "video_rate": ["12"],
    "color": ["red"],
    "flags": ["bilinear"],
    "pix_fmt": ["yuv420p"],
}
#: The one frame change the gate deliberately does not read, with its
#: measurement. ``zoompan`` with no ``s`` emits its ``hd720`` DEFAULT whatever
#: the canvas is — a fixed 1280x720, 36.5 MB, which cannot be the memory hazard
#: this bound exists for, and reporting it would refuse a harmless look on a
#: small canvas for a reason that is not memory. Named here as an exemption with
#: a test of its own, rather than absorbed by weakening the assertion.
ZOOMPAN_DEFAULT_SIZE = (1280, 720)
#: Where a type-driven value would simply be rejected by ffmpeg, so the row would
#: be vacuous. ``lut3d``'s ``file`` is excluded: it is the one recorded path
#: option and needs a real ``.cube``.
VALUES_BY_OPTION = {
    ("scale", "force_original_aspect_ratio"): ["increase", "decrease"],
    ("scale", "force_divisible_by"): ["64"],
    ("scale", "flags"): ["bilinear"],
    ("scale", "eval"): ["frame"],
    ("pad", "eval"): ["frame"],
    ("zoompan", "d"): ["1"],
    ("lut3d", "file"): [],
}
#: A size context per geometry filter, so an option that only acts in
#: combination is visible. ``force_divisible_by`` does nothing without
#: ``force_original_aspect_ratio``, which is exactly why a one-at-a-time sweep
#: cannot find it.
SIZE_CONTEXT = {
    "scale": ["w=100:h=100", "w=100:h=100:force_original_aspect_ratio=increase"],
    "pad": ["w=100:h=100"],
    "crop": ["w=32:h=24"],
    "zoompan": ["d=1:s=100x100:fps=25"],
}

_STREAM_SIZE = re.compile(r"Video: wrapped_avframe[^\n]*?, (\d+)x(\d+)")


def _produced(src, look):
    """``(rc, (w, h) or None)`` for ``look`` spliced where the assembler splices it."""
    vf = (
        f"scale={SRC_W}:{SRC_H}:force_original_aspect_ratio=decrease,"
        f"pad={SRC_W}:{SRC_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25,"
        f"tpad=stop=-1:stop_mode=clone,{look}"
    )
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-i", str(src),
            "-vf", vf, "-frames:v", "1", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    tail = proc.stderr.split("Output #0", 1)[-1]
    m = _STREAM_SIZE.search(tail)
    return proc.returncode, (int(m.group(1)), int(m.group(2))) if m else None


@pytest.fixture(scope="module")
def sweep_source(tmp_path_factory):
    src = tmp_path_factory.mktemp("look_options") / "src.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-nostdin",
            "-f", "lavfi", "-i", f"testsrc2=size={SRC_W}x{SRC_H}:rate=25:d=1",
            "-frames:v", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src),
        ],
        check=True,
        capture_output=True,
    )
    return src


def _fragments_for(filter_name, types):
    """Every ``<filter>=<args>`` this sweep will try for one filter."""
    for opt, typ in sorted(types.items()):
        values = VALUES_BY_OPTION.get((filter_name, opt))
        if values is None:
            values = VALUES_BY_TYPE.get(typ, ["200"])
        for value in values:
            yield f"{filter_name}={opt}={value}"
            for context in SIZE_CONTEXT.get(filter_name, []):
                yield f"{filter_name}={context}:{opt}={value}"


@needs_ffmpeg
@pytest.mark.parametrize("filter_name", sorted(LOOK_FILTERS - {"null"}))
def test_an_accepted_look_never_grows_the_frame_past_what_the_gate_read(
    filter_name, sweep_source
):
    """THE instrument. Census-driven, against decoded frame sizes, both directions.

    For every option the binary declares, in every context: either the gate
    REFUSES the fragment, or the frame ffmpeg produces is no larger — on either
    axis — than the size the gate believes the fragment declares (the input size
    when it declares nothing). Growth past that is an unbounded lever, and it is
    exactly what ``pad=aspect`` and ``scale=force_original_aspect_ratio`` were.

    Shrinking is fine and stays unasserted: ``crop`` shrinks by design, and the
    hazard this bound exists for is memory.

    **What this sweep structurally cannot see**, stated because a partial claim
    is worse than none: an option whose only effect is alongside an option the
    gate REFUSES. The fragment is refused, the row is skipped, and the sweep
    never renders it — measured by mutation, ``scale=force_divisible_by``
    survives this test and is caught only by
    ``test_every_measured_geometry_lever_is_bounded_or_refused``, whose list
    comes from a sweep run without the gate in the way.

    Note the assertion is on the PRODUCED frame, not on a string anyone expected.
    The hand-written corpus this replaces had exactly the right shape and simply
    did not contain either leak — which is why the input list is now read out of
    the binary instead of typed.
    """
    types = declared_options(filter_name)
    assert types, f"read no options for {filter_name!r} — parser broken?"
    rendered = 0
    for look in _fragments_for(filter_name, types):
        try:
            _validate_look_size(0, look, (SRC_W, SRC_H))
        except ValueError:
            continue  # refused: the gate never has to be right about its size
        rc, produced = _produced(sweep_source, look)
        if rc != 0 or produced is None:
            continue  # ffmpeg refused the value; the row says nothing
        rendered += 1
        declared = {axis: px for _, _, axis, _, px in _look_output_sizes(look)}
        floor = ZOOMPAN_DEFAULT_SIZE if filter_name == "zoompan" else (SRC_W, SRC_H)
        ceiling = (
            max(declared.get("width") or 0, floor[0]),
            max(declared.get("height") or 0, floor[1]),
        )
        assert produced[0] <= ceiling[0] and produced[1] <= ceiling[1], (
            f"{look!r} was ACCEPTED, the gate reads {declared or 'no size'} out of "
            f"it, and ffmpeg produced {produced[0]}x{produced[1]} — larger than the "
            f"{ceiling[0]}x{ceiling[1]} the gate bounded. That is an unbounded "
            "lever the option table does not know about."
        )
    assert rendered >= 2, (
        f"only {rendered} fragments of {filter_name!r} actually rendered — the "
        "sweep has gone vacuous and would pass with the gate removed."
    )


#: An all-positional fragment per geometry filter that fills the WHOLE prefix
#: muvid classifies, with the frame it must produce. Asymmetric on purpose
#: (200x100, never 100x100): a square target agrees with a w/h swap.
POSITIONAL_PREFIX_CASES = [
    ("scale", "scale=200:100", (200, 100)),
    ("pad", "pad=200:100:0:0", (200, 100)),
    ("crop", "crop=32:24:0:0", (32, 24)),
    ("zoompan", "zoompan=1:0:0:1:200x100:25", (200, 100)),
]


@needs_ffmpeg
@pytest.mark.parametrize("filt, look, expected", POSITIONAL_PREFIX_CASES)
def test_the_positional_prefix_is_the_order_the_binary_really_uses(
    filt, look, expected, sweep_source
):
    """Each case fills every slot muvid classifies, and the frame proves the order.

    A positional order taken from a docstring rather than from the binary is a
    gate that claims a size ffmpeg never sets. muvid stops the prefix short of
    the full option list on purpose (the slots past it differ between the two
    builds this fleet runs, and one of `pad`'s is `aspect`), so what has to be
    pinned is that the SHORT prefix is right — and it is pinned against a decoded
    frame, not against a reading of the help text.
    """
    assert len(POSITIONAL_PREFIX_CASES) == len(_LOOK_GEOMETRY_FILTERS), (
        "a geometry filter has no positional-prefix case — its order is unpinned"
    )
    n_positional = look.count(":") + 1 - (1 if "=" in look.split("=", 1)[1] else 0)
    assert n_positional == len(_LOOK_GEOMETRY_FILTERS[filt].positional), (
        f"{look!r} fills {n_positional} of {filt}'s "
        f"{len(_LOOK_GEOMETRY_FILTERS[filt].positional)} classified slots — the "
        "case must fill them all or the tail of the prefix stays unpinned."
    )
    rc, produced = _produced(sweep_source, look)
    assert rc == 0 and produced == expected, f"{look!r} -> rc={rc} {produced}"
    declared = {axis: px for _, _, axis, _, px in _look_output_sizes(look)}
    if declared:
        assert (declared["width"], declared["height"]) == expected, (
            f"the gate reads {declared} out of {look!r}; ffmpeg produced {expected}"
        )


@needs_ffmpeg
def test_a_bare_argument_after_a_named_one_is_refused_because_builds_DISAGREE(
    sweep_source,
):
    """``_link_options``' ``named_seen`` rule, against the binaries that decide it.

    **This test found that the rule is load-bearing, not cosmetic, and corrected
    what the code said about it.** The first version asserted ffmpeg refuses a
    bare argument after a ``key=value`` one — measured on 9.0.1, where
    ``scale=w=100:8000`` exits 234 with *"No option name near '8000'"*. CI, on
    Ubuntu's ffmpeg 6, returned rc=0. Re-measured on 6.1.6:

        ==========================  ==============  ================
        fragment                    ffmpeg 9.0.1    ffmpeg 6.1.6
        ==========================  ==============  ================
        ``scale=w=100:8000``        rc=234          **100x8000**
        ``scale=w=8000:100``        rc=234          **8000x100**
        ``scale=100:h=8000``        100x8000        100x8000
        ==========================  ==============  ================

    So on ffmpeg 6 a bare argument after a named one **does** fill the next slot,
    and the two builds this fleet runs mean *different things* by the same
    fragment. That is precisely why the gate refuses it rather than reading it:
    a gate that picked either interpretation would be wrong on the other binary,
    and wrong in the dangerous direction on one of them — reading
    ``scale=w=8000:100`` as ``{w: 100}`` accepts a fragment ffmpeg 6 renders
    8000 px wide.

    It also makes the ``named_seen`` rule a HOLE rather than an over-refusal
    where it is dropped: without it the trailing ``100`` refills slot 0 and
    overwrites ``w=8000``, so the gate reads 100 and accepts. On 9.0.1 that is
    harmless because ffmpeg refuses anyway; on 6.1.6 it renders.

    ``scale=100:h=8000`` is the positive control — positionals BEFORE the first
    named argument fill their slots on both builds, so the rule is about
    ordering, not about refusing bare arguments.
    """
    for look in ("scale=w=100:8000", "scale=w=8000:100"):
        with pytest.raises(ValueError, match="does not classify"):
            _validate_look_size(0, look, (SRC_W, SRC_H))
        rc, produced = _produced(sweep_source, look)
        assert rc != 0 or produced is not None, "neither refused nor rendered?"
        if rc == 0:
            # this build fills the slot; the reading the gate declines to make
            # would have under-read the frame by a factor of 80 or more
            assert max(produced) >= 8000, produced
    assert _produced(sweep_source, "scale=100:h=8000")[1] == (100, 8000)


@needs_ffmpeg
def test_zoompan_with_no_size_really_is_the_fixed_hd720_the_gate_ignores(sweep_source):
    """The one exemption in the sweep above, measured rather than assumed.

    ``_look_output_sizes`` deliberately reports nothing for a ``zoompan`` with no
    ``s``, so the sweep would otherwise read its 1280x720 default as an unbounded
    lever. That decision only holds while the default really is a small fixed
    size independent of the input — if it ever became input-relative it would be
    a lever, and the exemption would be hiding it.
    """
    rc, produced = _produced(sweep_source, "zoompan=d=1")
    assert rc == 0 and produced == ZOOMPAN_DEFAULT_SIZE, (
        f"zoompan with no `s` produced {produced} from a {SRC_W}x{SRC_H} input, "
        f"not the fixed {ZOOMPAN_DEFAULT_SIZE} the gate's exemption assumes."
    )
    assert not _look_output_sizes("zoompan=d=1"), "the premise: the gate reads nothing"



def test_the_edl_modules_own_doctests_actually_run():
    """`testpaths = ["tests"]` and no `--doctest-modules`, so nothing collects them.

    Every `>>>` in `muvid/footage/edl.py` documents the lexer and the option
    reader -- `_link_options`' positional rules, `_look_unclassified_options`'
    three offences, `_significant`'s two escape mechanisms. Uncollected they are
    prose that happens to look executable, which is the "green tick over zero
    tests" shape (`an`'s CLAUDE.md names it; muvid's own CI canary exists for the
    same reason). Running them from a real test makes them a gate without
    changing what the whole suite collects.

    The attempt floor is the half that matters: `testmod` reports 0 failures for
    a module with no examples at all, so asserting only `failed == 0` would pass
    if every doctest were deleted.
    """
    import doctest

    import muvid.footage.edl as E

    result = doctest.testmod(E, verbose=False)
    assert result.failed == 0, f"{result.failed} doctest failures in muvid.footage.edl"
    assert result.attempted >= 20, (
        f"only {result.attempted} doctest examples ran in muvid.footage.edl -- the "
        "module's examples have been deleted or this call stopped finding them."
    )

def _refresh_snapshot() -> None:  # pragma: no cover - a maintenance helper
    """Regenerate ``tests/data/ffmpeg_filter_options.json`` from THIS binary.

    Run it once per binary you want represented and the union accumulates — the
    recorded census is a union across builds on purpose, because an option one
    build hides is still real (``scale``'s ``s``/``size`` on 6.1.6)::

        python -c "import tests.test_edl_look_options as t; t._refresh_snapshot()"

    Never hand-edit the file: a hand-typed census is the bug this guards.
    """
    version = subprocess.run(
        ["ffmpeg", "-version"], capture_output=True, text=True
    ).stdout.splitlines()[0]
    version = version.split(" Copyright")[0].replace("ffmpeg version ", "").strip()
    doc = dict(CENSUS)
    merged = {k: set(v) for k, v in RECORDED.items()}
    for name in LOOK_FILTERS:
        merged.setdefault(name, set()).update(declared_options(name))
    doc["options"] = {k: sorted(v) for k, v in sorted(merged.items())}
    seen = [v for v in doc.get("recorded_from", []) if v != version]
    doc["recorded_from"] = sorted(seen + [version])
    CENSUS_PATH.write_text(json.dumps(doc, indent=2) + "\n")
