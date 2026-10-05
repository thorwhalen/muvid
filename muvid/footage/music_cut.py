"""Cut footage to the music — the montage half of a music video.

A music video in muvid has always assumed its footage is a RECORDING OF THE SONG — a
concert, a dance, a lip-sync — so every clip is *synced*: placed on the song by
listening to its own soundtrack (:mod:`muvid.footage.align`) and shown at exactly the
moment it was filmed. That is the right answer for that footage and the wrong one for
the other common case, which is the one phone galleries sell as "memories": a day out
filmed in short clips and photos, plus a song that was never playing. There is nothing
to sync to, so the aligner rightly cannot place those clips, and an edit built only from
synced clips is mostly black.

This module is the other answer. Footage that does not contain the song is cut TO the
music instead of synced WITH it:

* **where the cuts fall** is the montage planner's decision
  (:func:`muvid.montage.plan.plan_montage`): every cut on a beat, bar or section
  boundary, denser in loud sections, with the reuse policy that lets a few clips carry a
  whole song and keeps a revisited clip from showing the same stretch twice;
* **which stretch of a video each cut shows** is decided here, by the picture: the
  stretch whose visual hits (:func:`muvid.footage.beats.activity_signal` — the onsets of
  picture change, camera moves included) land on the beats inside the cut, and whose
  liveliness suits the section's loudness. That is the alignment phone "memories" do
  not do;
* **how a still moves** is a named camera look (push, pull, pan) toward the photo's
  salient region (``burns.salient_box`` when ``burns`` is installed).

The output is ordinary :class:`~muvid.footage.edl.EdlEntry` cuts with
:attr:`~muvid.footage.edl.EdlEntry.source_in` set — *free* cuts — so a montage is an
edit like any other: saved, changed cut by cut, rendered by the same assembler over the
clean song. Synced and free cuts mix in one edit: :func:`fill_spans` fills only the
spans it is given, which the service uses to put free cuts in the gaps a synced edit
leaves.

Pure planning: no file is written; the measurements (song analysis, picture-change
envelopes, salient boxes) come in through keyword seams with working defaults.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

__all__ = [
    "ARCHETYPE_AUTO",
    "Envelope",
    "FreeSource",
    "MusicCut",
    "choose_source_in",
    "fill_spans",
    "pick_archetype",
]

#: Pick the cutting style from the song (see :func:`pick_archetype`).
ARCHETYPE_AUTO = "auto"
#: Below this tempo the auto choice is the slow dissolve, at or above it hard cuts.
SLOW_TEMPO_BPM = 92.0
#: The montage archetypes a free cut can express: one picture per slot. ``grid`` tiles
#: several pictures in one frame, which an edit (one clip per span) cannot hold.
SINGLE_PICTURE_ARCHETYPES = ("beat_cut", "ballad_dissolve", "stop_motion")
#: A piece of a cut shorter than this (seconds) is folded into its neighbour.
MIN_PIECE_S = 0.4
#: Keep this far (seconds) from a video's first and last frame: phone clips start and
#: stop with the shake of a thumb on the button.
EDGE_S = 0.25
#: Weights of the source-in score: beats hit, liveliness matched to loudness, reuse,
#: and focus (log of the stretch's sharpness over the video's own, so a whip-pan blur —
#: which changes a lot and shows nothing — loses to a lively stretch in focus).
W_HITS, W_ENERGY, W_REUSE, W_SHARP = 1.0, 0.35, 2.0, 0.6
#: How far a still's camera move goes (zoom factor; the named looks cap it at 1.15).
STILL_ZOOM = 1.12
#: The camera moves a still cycles through, by how often the still has been used, when
#: the planner left it unmoved — so a returning photo moves differently.
STILL_MOVES = ("slow_push", "pan_right", "slow_pull", "pan_left")
#: The montage planner's motion vocabulary, as muvid's named looks.
MOTION_TO_LOOK = {
    "zoom_in": "slow_push",
    "zoom_out": "slow_pull",
    "pan_left": "pan_left",
    "pan_right": "pan_right",
    "punch": "punch_in",
}
#: Looks that take an anchor (where to zoom toward).
_ANCHORED_LOOKS = ("slow_push", "slow_pull", "punch_in")
#: How a picture meets a canvas of another shape: ``cover`` fills the frame (cropping
#: the overflow, around the photo's focal point — what phone "memories" do), ``contain``
#: shows all of it with bars (what a synced recording of the song gets).
FITS = ("cover", "contain")
#: Aspect ratios closer than this are treated as equal (no crop for 2 %).
_ASPECT_TOLERANCE = 0.02
STILL = "still"
VIDEO = "video"


@dataclass(frozen=True)
class FreeSource:
    """One picture source a montage may cut to: a video (``duration_s`` its length) or a
    still (``kind="still"``, ``duration_s=None`` — it can be held for any span)."""

    clip_id: str
    path: str
    kind: str = VIDEO
    duration_s: Optional[float] = None

    @property
    def is_still(self) -> bool:
        return self.kind == STILL


@dataclass(frozen=True)
class Envelope:
    """A video's picture-change signal on a regular grid of its own time."""

    hop_s: float
    activity: np.ndarray
    hits: np.ndarray
    sharpness: Optional[np.ndarray] = None

    @classmethod
    def from_signals(cls, record: Mapping) -> "Optional[Envelope]":
        """From :func:`muvid.footage.beats.activity_signal`'s ``signals`` mapping."""
        from muvid.footage.beats import ACTIVITY, ACTIVITY_HITS, SHARPNESS

        a, h, f = record.get(ACTIVITY), record.get(ACTIVITY_HITS), record.get(SHARPNESS)
        if not a or not h or not a.get("n"):
            return None

        def arr(sig):
            return np.array(
                [np.nan if v is None else v for v in sig["values"]], dtype=float
            )

        return cls(
            hop_s=float(a["hop_s"]),
            activity=arr(a),
            hits=arr(h),
            sharpness=arr(f) if f and f.get("n") else None,
        )


@dataclass
class MusicCut:
    """The result: free cuts for the requested spans, and an account of how they were
    chosen (tempo, style, how well each video's picture landed on the beat)."""

    entries: list = field(default_factory=list)
    report: dict = field(default_factory=dict)


# -----------------------------------------------------------------------------
# choosing the stretch of a video: the visual alignment
# -----------------------------------------------------------------------------


def choose_source_in(
    env: "Optional[Envelope]",
    *,
    clip_duration: float,
    length: float,
    beat_offsets: Sequence[float] = (),
    used: Sequence[tuple[float, float]] = (),
    energy: float = 0.0,
    lead_s: float = 0.0,
    trail_s: float = 0.0,
    ordinal: int = 0,
) -> tuple[float, float]:
    """Where in a video a cut of ``length`` seconds should start: ``(source_in, fit)``.

    Scores every in-point on the envelope's grid (``lead_s``/``trail_s`` of extra
    footage kept free on either side, for a blend), by three terms:

    * **hits on the beat** — the mean picture-change hit at each ``beat_offsets`` (beats
      inside the cut, in seconds from its start), relative to the video's own mean hit:
      above 1 means the picture changes ON the beat more than it does anywhere;
    * **liveliness matched to loudness** — ``energy`` in [-1, 1] (quiet to loud
      section) times how lively the stretch is relative to the whole video;
    * **reuse** — the fraction of the stretch already shown by another cut, penalised.

    ``fit`` is the hits term at the chosen point (0 when there are no inner beats or no
    envelope). Without an envelope it falls back to a golden-ratio stride over the
    room, the montage planner's own blind rule, with reuse still avoided.

    >>> hits = np.zeros(100); hits[[20, 30]] = 1.0   # changes at 2.0 s and 3.0 s
    >>> env = Envelope(hop_s=0.1, activity=np.full(100, 0.1), hits=hits)
    >>> s, fit = choose_source_in(env, clip_duration=10.0, length=2.0, beat_offsets=[0.5, 1.5])
    >>> round(s, 2), fit > 1
    (1.5, True)
    """
    room_lo = EDGE_S + lead_s
    room_hi = clip_duration - EDGE_S - trail_s - length
    if room_hi < room_lo:  # too short for the margins: centre what there is
        room_lo = max(0.0, lead_s)
        room_hi = max(room_lo, clip_duration - trail_s - length)
    if env is None or env.hits.size < 2:
        span = max(0.0, room_hi - room_lo)
        candidates = np.linspace(room_lo, room_hi, max(2, int(span / 0.25) + 1))
        golden = room_lo + ((ordinal * 0.6180339887498949) % 1.0) * span
        overlap = np.array([_overlap(s, s + length, used) for s in candidates])
        best = candidates[np.argmin(overlap * 1e3 + np.abs(candidates - golden))]
        return math.floor(max(0.0, float(best)) * 1e4) / 1e4, 0.0

    hop = env.hop_s
    hits = np.nan_to_num(env.hits, nan=0.0)
    act = np.nan_to_num(env.activity, nan=0.0)
    mean_hit = float(np.mean(hits)) + 1e-9
    mean_act = float(np.mean(act)) + 1e-9
    i_lo = int(math.ceil(room_lo / hop))
    i_hi = int(math.floor(room_hi / hop))
    if i_hi < i_lo:
        i_hi = i_lo
    starts = np.arange(i_lo, i_hi + 1)
    n = hits.size
    offsets = [o for o in beat_offsets if 0.0 < o < length]

    def at(sig, idx):
        return sig[np.clip(idx, 0, n - 1)]

    if offsets:
        # a beat is "hit" by a change on its grid step, half-credited one step off
        acc = np.zeros(starts.size)
        for o in offsets:
            k = starts + int(round(o / hop))
            near = np.maximum(at(hits, k - 1), at(hits, k + 1))
            acc += np.maximum(at(hits, k), 0.5 * near)
        hit_term = acc / len(offsets) / mean_hit
    else:
        hit_term = np.zeros(starts.size)
    w = max(1, int(round(length / hop)))
    csum = np.concatenate([[0.0], np.cumsum(act)])
    lively = (csum[np.clip(starts + w, 0, n)] - csum[np.clip(starts, 0, n)]) / w
    lively_term = energy * (lively / mean_act - 1.0)
    reuse = np.array(
        [_overlap(s * hop, s * hop + length, used) / length for s in starts]
    )
    score = W_HITS * hit_term + W_ENERGY * lively_term - W_REUSE * reuse
    if env.sharpness is not None and env.sharpness.size:
        sharp = np.nan_to_num(env.sharpness, nan=0.0)
        ssum = np.concatenate([[0.0], np.cumsum(sharp)])
        m = sharp.size
        window = (ssum[np.clip(starts + w, 0, m)] - ssum[np.clip(starts, 0, m)]) / w
        mean_sharp = float(np.mean(sharp)) + 1e-9
        score = score + W_SHARP * np.clip(
            np.log((window + 1e-9) / mean_sharp), -2.0, 1.0
        )
    j = int(np.argmax(score))
    # never past the room (grid rounding), never negative, rounded DOWN for the wire
    s_in = max(0.0, min(float(starts[j] * hop), max(room_lo, room_hi)))
    return math.floor(s_in * 1e4) / 1e4, float(hit_term[j])


def _overlap(a: float, b: float, used: Sequence[tuple[float, float]]) -> float:
    return sum(max(0.0, min(b, u1) - max(a, u0)) for u0, u1 in used)


# -----------------------------------------------------------------------------
# the plan
# -----------------------------------------------------------------------------


def pick_archetype(analysis) -> str:
    """The cutting style for a song: a slow dissolve below :data:`SLOW_TEMPO_BPM`, hard
    cuts on the beat otherwise.

    >>> class A: tempo_bpm = 80.0
    >>> pick_archetype(A())
    'ballad_dissolve'
    """
    return "ballad_dissolve" if analysis.tempo_bpm < SLOW_TEMPO_BPM else "beat_cut"


#: Fill a hole in the beat grid longer than this many beats (a quiet passage the beat
#: tracker went silent on) — and run the grid on to the song's ends.
GRID_HOLE_BEATS = 1.6


def extend_grid(analysis, duration: Optional[float] = None):
    """``analysis`` with its beat grid carried through the quiet passages a beat
    tracker goes silent on, and on to both ends of the song, at its own pace.

    Without this, a song whose last 25 s are a quiet outro has no beats there: the
    planner cannot cut on them, and the last picture holds to the end (measured: a
    21 s hold on a 4:24 song whose beat was last found at 3:58). Returns
    ``(analysis, note)``; ``note`` is ``None`` when nothing was added.

    >>> from muvid.montage.analysis import Analysis
    >>> a = Analysis(duration=4.6, tempo_bpm=120.0, beats=(1.0, 1.5, 2.0, 2.5, 3.0),
    ...              downbeats=(1.0, 3.0))
    >>> b, note = extend_grid(a)
    >>> b.beats[:3], b.beats[-2:], b.downbeats, note is not None
    ((0.0, 0.5, 1.0), (3.5, 4.0), (1.0, 3.0), True)
    """
    from dataclasses import replace

    beats = list(analysis.beats)
    dur = float(duration if duration is not None else analysis.duration)
    if len(beats) < 4:
        return analysis, None
    period = float(np.median(np.diff(beats)))
    if not period > 0:
        return analysis, None
    out = []
    filled = []
    for a, b in zip(beats, beats[1:]):
        out.append(a)
        if b - a > GRID_HOLE_BEATS * period:
            n = int(round((b - a) / period))
            step = (b - a) / n
            out += [a + k * step for k in range(1, n)]
            filled.append((a, b))
    out.append(beats[-1])
    head = []
    t = beats[0] - period
    while t > -1e-6:
        head.insert(0, max(0.0, t))
        t -= period
    tail = []
    t = beats[-1] + period
    while t < dur - 0.5 * period:
        tail.append(t)
        t += period
    if not head and not tail and not filled:
        return analysis, None
    grid = tuple(round(x, 4) for x in head + out + tail)
    bpb = int(getattr(analysis, "beats_per_bar", 4) or 4)
    downs = list(analysis.downbeats) or [grid[0]]
    # downbeats: keep the measured ones, and continue the bar count over what was added
    first = min(range(len(grid)), key=lambda i: abs(grid[i] - downs[0]))
    phase = first % bpb
    new_downs = tuple(grid[i] for i in range(len(grid)) if i % bpb == phase)
    note = (
        f"the song's beat was last heard at {_clock(beats[-1])}; it was carried on, at the "
        "song's own pace, to the end"
        if tail
        else "the song's beat was carried on through its quiet passages, at its own pace"
    )
    return replace(analysis, beats=grid, downbeats=new_downs), note


def _clock(t: float) -> str:
    """``72.41`` -> ``"1:12.4"``."""
    m, sec = divmod(max(0.0, float(t)), 60.0)
    return f"{int(m)}:{sec:04.1f}"


def _default_analysis(song_path: str):
    from muvid.montage.analysis import analyze

    return analyze(song_path)


def _file_key(path: str) -> tuple:
    """``(path, mtime_ns, size)`` — what a per-file memo is keyed on, so a replaced file
    is measured again and an unchanged one never is."""
    import os

    try:
        st = os.stat(path)
    except OSError:
        return (str(path), None, None)
    return (str(path), st.st_mtime_ns, st.st_size)


def _memo(fn):
    """Memoise a per-file measurement for the life of the process (a server answers
    many "Cut it for me"s over the same files)."""
    from functools import lru_cache, wraps

    @lru_cache(maxsize=1024)
    def cached(key, *rest):
        return fn(key[0], *rest)

    @wraps(fn)
    def wrapper(path, *rest):
        return cached(_file_key(path), *rest)

    wrapper.cache_clear = cached.cache_clear
    return wrapper


@_memo
def _salient_centre(path: str) -> tuple[float, float]:
    from burns import salient_box

    x, y, w, h = salient_box(path)
    return float(x + w / 2.0), float(y + h / 2.0)


def _default_anchor(source: FreeSource) -> tuple[float, float]:
    """Where a still's camera move should go: the centre of ``burns.salient_box`` —
    the photo's busy, detailed region, away from sky and wall — or the frame's centre
    without ``burns``."""
    try:
        return _salient_centre(source.path)
    except Exception:  # no burns, or an unreadable image: the centre, never a failure
        return 0.5, 0.5


@_memo
def display_size(path: str) -> Optional[tuple[int, int]]:
    """A picture's size as SHOWN — a phone video stored 640x360 with a -90 rotation is
    360x640 on screen, and ffmpeg rotates before any filter, so a crop must be computed
    on this size, not the stored one. ``None`` when it cannot be read."""
    from muvid.visualize.ffmpeg import probe

    try:
        streams = probe(path)["streams"]
    except Exception:
        return None
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video or not video.get("width"):
        return None
    w, h = int(video["width"]), int(video["height"])
    rotation = 0.0
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            rotation = float(side["rotation"])
    rotation = rotation or float((video.get("tags") or {}).get("rotate", 0) or 0)
    if int(round(abs(rotation))) % 180 == 90:
        w, h = h, w
    return w, h


def cover_crop(
    size: Optional[tuple[int, int]],
    canvas: tuple[int, int],
    *,
    anchor: tuple[float, float] = (0.5, 0.5),
):
    """The crop window that fills ``canvas`` from a picture of ``size``, centred as near
    ``anchor`` as the picture allows; ``None`` when the shapes already match.

    >>> c = cover_crop((1920, 1080), (1080, 1920))     # landscape onto portrait
    >>> round(c.w, 3), c.h, round(c.x, 3)
    (0.316, 1.0, 0.342)
    >>> cover_crop((1080, 1920), (1080, 1920)) is None
    True
    """
    from muvid.footage.edl import CropWindow

    if not size or not size[0] or not size[1]:
        return None
    a, target = size[0] / size[1], canvas[0] / canvas[1]
    if abs(a / target - 1.0) <= _ASPECT_TOLERANCE:
        return None
    if a > target:  # wider than the canvas: keep full height, take a column
        w = target / a
        x = min(max(anchor[0] - w / 2.0, 0.0), 1.0 - w)
        return CropWindow(x=round(x, 4), y=0.0, w=round(w, 4), h=1.0)
    h = a / target
    y = min(max(anchor[1] - h / 2.0, 0.0), 1.0 - h)
    return CropWindow(x=0.0, y=round(y, 4), w=1.0, h=round(h, 4))


@_memo
def _probe_one(path: str, kind: str):
    from muvid.montage.analysis import probe_media

    (m,) = list(probe_media([path], kind=kind, start_index=0))
    return m


def _media_for(sources: Sequence[FreeSource]):
    """The montage planner's pool, one :class:`~muvid.montage.analysis.Media` per source,
    ranked by the planner's own strength measure."""
    from dataclasses import replace

    from muvid.montage.analysis import Media

    media = []
    for i, s in enumerate(sources):
        kind = "photo" if s.is_still else "clip"
        try:
            m = replace(_probe_one(s.path, kind), index=i)
        except Exception:  # unmeasurable: plan with it anyway, weakest
            m = Media(index=i, path=s.path, kind=kind, width=1, height=1, strength=0.0)
        if not s.is_still and s.duration_s is not None:
            m = Media(
                index=i,
                path=m.path,
                kind=kind,
                width=m.width,
                height=m.height,
                duration=float(s.duration_s),
                strength=m.strength,
            )
        media.append(m)
    return media


def _treatment(archetype: str, cut_feel: str):
    from muvid.montage import spec as spec_mod
    from muvid.montage.pipeline import build_treatment

    treatment, _notes, _src = build_treatment({"archetype": archetype})
    if cut_feel and cut_feel != treatment.direction.cut_feel:
        from dataclasses import replace

        if cut_feel not in spec_mod.CUT_FEELS:
            raise ValueError(
                f"unknown pace {cut_feel!r}; the paces are {sorted(spec_mod.CUT_FEELS)}"
            )
        treatment = replace(
            treatment, direction=replace(treatment.direction, cut_feel=cut_feel)
        )
    return treatment


def _section_energy(analysis) -> Callable[[float], float]:
    """Song time -> the loudness of its section in [-1, 1] (0 when unmeasured)."""
    secs = [s for s in analysis.sections if s.energy_db is not None]
    if len(secs) < 2:
        return lambda t: 0.0
    dbs = np.array([s.energy_db for s in secs])
    mid, half = float(np.mean(dbs)), float(np.ptp(dbs)) / 2.0 or 1.0

    def energy(t: float) -> float:
        for s in secs:
            if s.start <= t < s.end:
                return max(-1.0, min(1.0, (s.energy_db - mid) / half))
        return 0.0

    return energy


def fill_spans(
    song_path: str,
    sources: Sequence[FreeSource],
    *,
    spans: Optional[Sequence[tuple[float, float]]] = None,
    song_duration: Optional[float] = None,
    analysis: Any = None,
    archetype: str = ARCHETYPE_AUTO,
    cut_feel: str = "",
    envelope_of: Optional[Callable[[FreeSource], "Optional[Envelope]"]] = None,
    anchor_of: Callable[[FreeSource], tuple[float, float]] = _default_anchor,
    canvas: tuple[int, int] = (1920, 1080),
    fps: float = 30.0,
    fit: str = "cover",
    size_of: Callable[[str], Optional[tuple[int, int]]] = display_size,
) -> MusicCut:
    """Free cuts of ``sources`` covering ``spans`` of the song (default: all of it).

    Seams, each with a default that works: ``analysis`` (the song's beats, bars and
    sections — :func:`muvid.montage.analysis.analyze`), ``envelope_of`` (a video's
    picture-change :class:`Envelope`; ``None`` falls back to the planner's blind stride),
    ``anchor_of`` (a still's focal point). ``archetype`` is a montage archetype with one
    picture per slot, or ``"auto"``; ``cut_feel`` a montage pace (``slow``, ``steady``,
    ``driving``, ``frantic``). ``fit`` is one of :data:`FITS`; ``size_of`` reads a
    picture's displayed size (for ``cover``).

    Returns :class:`MusicCut` whose ``entries`` are validated-shape
    :class:`~muvid.footage.edl.EdlEntry` free cuts, contiguous over each span, in song
    order (spans with nothing in them are simply not covered — the caller's
    ``fill_gaps`` makes that explicit).
    """
    from muvid.montage.plan import plan_montage

    if not sources:
        raise ValueError("no footage to cut to the music")
    if analysis is None:
        analysis = _default_analysis(song_path)
    duration = float(song_duration or analysis.duration)
    analysis, grid_note = extend_grid(analysis, duration)
    spans = (
        [(0.0, duration)] if spans is None else [(float(a), float(b)) for a, b in spans]
    )
    spans = [(a, b) for a, b in spans if b - a > MIN_PIECE_S]
    chosen = (
        pick_archetype(analysis) if archetype in ("", ARCHETYPE_AUTO) else archetype
    )
    if chosen not in SINGLE_PICTURE_ARCHETYPES:
        raise ValueError(
            f"archetype {chosen!r} cannot be an edit (one picture per cut); use one of "
            f"{list(SINGLE_PICTURE_ARCHETYPES)} or 'auto'"
        )
    treatment = _treatment(chosen, cut_feel)
    media = _media_for(sources)
    plan = plan_montage(analysis, media, treatment)
    energy_at = _section_energy(analysis)
    beats = np.asarray(analysis.beats, dtype=float)
    if fit not in FITS:
        raise ValueError(f"fit must be one of {list(FITS)}, got {fit!r}")
    crops: dict[str, Any] = {}

    def crop_for(src: FreeSource):
        if fit != "cover":
            return None
        if src.clip_id not in crops:
            anchor = (
                anchors.setdefault(src.clip_id, anchor_of(src))
                if src.is_still
                else (0.5, 0.5)
            )
            crops[src.clip_id] = cover_crop(size_of(src.path), canvas, anchor=anchor)
        return crops[src.clip_id]

    envelopes: dict[str, Optional[Envelope]] = {}

    def envelope(src: FreeSource) -> "Optional[Envelope]":
        if src.clip_id not in envelopes:
            envelopes[src.clip_id] = (
                None if envelope_of is None or src.is_still else envelope_of(src)
            )
        return envelopes[src.clip_id]

    used: dict[str, list[tuple[float, float]]] = {}
    fits: dict[str, list[float]] = {}
    anchors: dict[str, tuple[float, float]] = {}
    uses: dict[str, int] = {}
    pieces: list[dict] = []
    for slot in plan.slots:
        for a, b in spans:
            lo, hi = max(slot.start, a), min(slot.end, b)
            if hi - lo <= 1e-6:
                continue
            tile = slot.tiles[0]
            fade = (
                slot.transition_s
                if (lo == slot.start and slot.transition != "cut")
                else 0.0
            )
            pieces.append(
                {
                    # a sliver a span boundary cut off a slot — not a slot the
                    # planner made short on purpose (a flip-book hold)
                    "clipped": lo > slot.start + 1e-6 or hi < slot.end - 1e-6,
                    "start": lo,
                    "end": hi,
                    "media": tile.media,
                    "motion": tile.motion,
                    "fade": fade,
                    "first_in_span": abs(lo - a) < 1e-6,
                }
            )
    pieces = _fold_tiny(pieces)
    _fades_start_on_the_beat(pieces)
    for this, nxt in zip(pieces, pieces[1:]):
        this["next"], nxt["prev"] = {"media": nxt["media"]}, {"media": this["media"]}
        # The next cut's blend reads past this cut's end in this cut's source.
        if abs(this["end"] - nxt["start"]) < 1e-6 and not nxt["first_in_span"]:
            this["next_fade"] = nxt["fade"]

    entries = []
    for k, p in enumerate(pieces):
        entries += _cuts_for_piece(
            p,
            k,
            sources=sources,
            beats=beats,
            energy_at=energy_at,
            envelope=envelope,
            anchor_of=anchor_of,
            anchors=anchors,
            used=used,
            fits=fits,
            uses=uses,
            canvas=canvas,
            fps=fps,
            crop_for=crop_for,
        )
    entries = _feasible_blends(entries, {s.clip_id: s for s in sources})
    asked = sum(b - a for a, b in spans)
    covered = sum(e.song_end - e.song_start for e in entries)
    report = {
        # seconds of the asked spans no picture could fill (left as gaps) — 0 normally
        "uncovered_s": round(max(0.0, asked - covered), 2),
        "grid_note": grid_note,
        "tempo_bpm": round(float(analysis.tempo_bpm), 2),
        "beat_source": getattr(analysis, "beat_source", ""),
        "style": chosen,
        "pace": treatment.direction.cut_feel,
        "sections": [
            {"label": s.label, "start": round(s.start, 2), "end": round(s.end, 2)}
            for s in analysis.sections
        ],
        "n_cuts": len(entries),
        "uses": {cid: n for cid, n in sorted(uses.items())},
        # Mean "hits on the beat" per video: above 1, its picture changes on the beat
        # more than it does anywhere else in it.
        "on_beat": {
            cid: round(float(np.mean(v)), 3) for cid, v in sorted(fits.items()) if v
        },
        "measured_pictures": sorted(c for c, e in envelopes.items() if e is not None),
    }
    return MusicCut(entries=entries, report=report)


def _fades_start_on_the_beat(pieces: list[dict]) -> None:
    """Move each faded boundary later by the fade's lead, so the fade STARTS on the
    beat the planner chose, instead of straddling it (measured on a real edit: 44 of
    62 cuts were fades centred on their beat, so the picture began changing a third
    of a second early — heard as "slightly off the beat"). In place; a piece the move
    would leave shorter than :data:`MIN_PIECE_S` keeps a hard cut instead."""
    from muvid.footage.edl import TRANSITION_SPLIT

    for prev, p in zip(pieces, pieces[1:]):
        fade = p.get("fade") or 0.0
        if fade <= 0 or p["first_in_span"] or abs(prev["end"] - p["start"]) > 1e-6:
            continue
        lead = fade * TRANSITION_SPLIT
        if p["end"] - (p["start"] + lead) < MIN_PIECE_S:
            p["fade"] = 0.0
            continue
        prev["end"] = p["start"] = p["start"] + lead


def _fold_tiny(pieces: list[dict]) -> list[dict]:
    """Fold the slivers a span boundary clipped off a slot — shorter than
    :data:`MIN_PIECE_S` — into the previous piece of the same span (or the next
    one), so a clipped slot leaves no flash frames. A slot the planner made short
    ON PURPOSE (a flip-book hold of a third of a second) is left alone: folding
    those cascades them into one long hold."""
    out: list[dict] = []
    for p in pieces:
        short = p.get("clipped", True) and p["end"] - p["start"] < MIN_PIECE_S
        if (
            short
            and out
            and not p["first_in_span"]
            and abs(out[-1]["end"] - p["start"]) < 1e-6
        ):
            out[-1] = dict(out[-1], end=p["end"])
        else:
            out.append(dict(p))
    merged: list[dict] = []
    for p in out:
        if (
            merged
            and merged[-1].get("clipped", True)
            and merged[-1]["end"] - merged[-1]["start"] < MIN_PIECE_S
            and abs(merged[-1]["end"] - p["start"]) < 1e-6
            and not p["first_in_span"]
        ):
            p = dict(p, start=merged[-1]["start"], fade=0.0)
            merged[-1] = p
        else:
            merged.append(p)
    return merged


def _cuts_for_piece(
    p,
    k,
    *,
    sources,
    beats,
    energy_at,
    envelope,
    anchor_of,
    anchors,
    used,
    fits,
    uses,
    canvas,
    fps,
    crop_for,
) -> list:
    """One planned piece -> one or more free cuts (more when its video is shorter than
    the piece: the rest goes to the longest other source)."""
    from muvid.footage.edl import EdlEntry, Transition

    out = []
    start, end = p["start"], p["end"]
    src = sources[p["media"]]
    # the pictures either side of this piece, so a take-over does not repeat them
    avoid = {sources[q["media"]].clip_id for q in (p.get("prev"), p.get("next")) if q}
    fade = p["fade"] if not p["first_in_span"] else 0.0
    steps, max_steps = 0, int((end - start) / MIN_PIECE_S) + 4
    while end - start > 1e-6 and steps < max_steps:
        steps += 1
        length = end - start
        need = length + 2 * EDGE_S + fade + p.get("next_fade", 0.0)
        if not src.is_still and (src.duration_s or 0.0) < need:
            # The video cannot hold the whole piece: show what it has, ending on a beat.
            cap = max(0.0, (src.duration_s or 0.0) - 2 * EDGE_S - fade)
            cut_at = _last_beat_before(beats, start + cap, after=start + MIN_PIECE_S)
            if cut_at is None or cap < MIN_PIECE_S:
                nxt = _take_over(sources, src, need=length, avoid=avoid, uses=uses)
                if nxt.clip_id == src.clip_id:
                    break  # nothing else to show: the rest stays uncovered (reported)
                avoid.add(src.clip_id)
                src = nxt
                continue
            piece_end = min(cut_at, end)
        else:
            piece_end = end
        trail = p.get("next_fade", 0.0) if piece_end >= end - 1e-6 else 0.0
        entry = _one_cut(
            src,
            start,
            piece_end,
            fade=fade,
            trail=trail,
            motion=p["motion"],
            k=k,
            beats=beats,
            energy_at=energy_at,
            envelope=envelope,
            anchor_of=anchor_of,
            anchors=anchors,
            used=used,
            fits=fits,
            uses=uses,
            canvas=canvas,
            fps=fps,
            transition_cls=Transition,
            entry_cls=EdlEntry,
        )
        crop = crop_for(src)
        if crop is not None:
            from dataclasses import replace as _replace

            entry = _replace(entry, crop=crop)
        out.append(entry)
        start, fade = piece_end, 0.0
        if end - start > 1e-6:
            avoid.add(src.clip_id)
            src = _take_over(sources, src, need=end - start, avoid=avoid, uses=uses)
    return out


def _last_beat_before(beats, t: float, *, after: float) -> Optional[float]:
    cands = beats[(beats <= t + 1e-6) & (beats >= after)]
    if cands.size:
        return float(cands[-1])
    return t if t >= after else None


def _take_over(sources, current, *, need: float, avoid: set, uses: Mapping[str, int]):
    """A source to take over the rest of a piece: one that can hold ``need`` seconds (a
    still always can) — preferably not a picture either side, the least used first —
    and only when nothing can hold it, the longest of the rest. ``current`` back means
    there is nothing else at all."""
    others = [s for s in sources if s.clip_id != current.clip_id]
    if not others:
        return current

    def holds(s):
        return s.is_still or (s.duration_s or 0.0) >= need + 2 * EDGE_S

    able = [s for s in others if holds(s)]
    pool = [s for s in able if s.clip_id not in avoid] or able
    if pool:
        return min(
            pool,
            key=lambda s: (uses.get(s.clip_id, 0), -(s.duration_s or 1e9), s.clip_id),
        )
    fresh = [s for s in others if s.clip_id not in avoid] or others
    return max(fresh, key=lambda s: (s.is_still, s.duration_s or 0.0))


def _one_cut(
    src,
    start,
    end,
    *,
    fade,
    trail,
    motion,
    k,
    beats,
    energy_at,
    envelope,
    anchor_of,
    anchors,
    used,
    fits,
    uses,
    canvas,
    fps,
    transition_cls,
    entry_cls,
):
    from muvid.footage.named_looks import compile_named_look, resolve_named_look

    length = end - start
    transition = transition_cls(duration_s=fade, curve="fade") if fade > 0 else None
    use = uses.get(src.clip_id, 0)
    uses[src.clip_id] = use + 1
    if src.is_still:
        name = MOTION_TO_LOOK.get(motion) or STILL_MOVES[use % len(STILL_MOVES)]
        spec: dict = {"name": name, "zoom": STILL_ZOOM}
        if name in _ANCHORED_LOOKS:
            if src.clip_id not in anchors:
                anchors[src.clip_id] = anchor_of(src)
            ax, ay = anchors[src.clip_id]
            spec |= {"anchor_x": round(ax, 3), "anchor_y": round(ay, 3)}
        resolved = resolve_named_look(spec)
        look = compile_named_look(resolved, canvas=canvas, fps=fps, duration_s=length)
        return entry_cls(
            song_start=start,
            song_end=end,
            clip_id=src.clip_id,
            transition=transition,
            look=look,
            look_time_varying=bool(getattr(look, "time_varying", True)),
            look_spec=resolved,
            # a blend reads its lead BEFORE the cut's in-point; a still has all the
            # time it wants, so it starts that far in
            source_in=round(fade, 4),
        )
    inner = [float(b - start) for b in beats if start < b < end]
    s_in, fit = choose_source_in(
        envelope(src),
        clip_duration=float(src.duration_s or length),
        length=length,
        beat_offsets=inner,
        used=used.get(src.clip_id, ()),
        energy=energy_at((start + end) / 2.0),
        lead_s=fade,
        trail_s=trail,
        ordinal=k,
    )
    used.setdefault(src.clip_id, []).append((s_in, s_in + length))
    if inner:
        fits.setdefault(src.clip_id, []).append(fit)
    return entry_cls(
        song_start=start,
        song_end=end,
        clip_id=src.clip_id,
        transition=transition,
        source_in=round(s_in, 4),
    )


def _feasible_blends(entries: list, by_id: Mapping[str, FreeSource]) -> list:
    """Drop a blend the edit gate would refuse — a short video that could not keep the
    margin, a cut too short to give its neighbour the blend — asking the gate's own
    check (:func:`muvid.footage.edl._validate_transition`), so the two cannot drift. A
    hard cut there is the honest fallback."""
    from dataclasses import replace

    from muvid.footage.edl import STILL_DURATION_S, _validate_transition, unplaced

    places = {
        cid: unplaced(
            cid, STILL_DURATION_S if s.is_still else float(s.duration_s or 0.0)
        )
        for cid, s in by_id.items()
    }
    out = list(entries)
    for i in range(len(out)):
        e = out[i]
        if e.transition is None:
            continue
        prev = out[i - 1] if i > 0 else None
        try:
            if prev is None or abs(prev.song_end - e.song_start) > 1e-6:
                raise ValueError("no contiguous predecessor")
            _validate_transition(i, e, prev, places)
        except (ValueError, KeyError):
            out[i] = replace(e, transition=None)
    return out
