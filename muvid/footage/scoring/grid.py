"""The song-time score grid: ``ScoreTrack``, resample-to-grid, robust normalization,
the fused ``ScoreTensor``, and crash-consistent persistence.

The keystone of the footage-scoring layer (thorwhalen/muvid#13): every clip's every metric
is resolved onto ONE fixed-rate song-time grid (``t0=0``, ``hop_s≈0.1`` → ~10 Hz), so tracks
stack into a tensor ``S[clip, frame, metric]`` that BOTH the auto composer (the Viterbi
selector) and the Phase-2 multichannel editor read. Frame ``k`` ↔ song time ``t0 + k*hop_s``,
identical across clips.

Design decisions (see ``misc/docs/footage_scoring_design.md`` — LOCKED post-critique):

- **Raw is the SSOT.** Extractors emit ``raw_values`` + a coverage ``mask`` (NaN where
  masked). Normalization is **per-metric-global across all clips** (robust median/IQR,
  percentile-clipped), so it can only be computed once every clip's raw track for a metric
  is in hand — it is a tensor-assembly step, not a per-extractor one. The normalized
  ``values`` are derived from ``raw`` + the stored ``norm`` params, so re-normalizing is free
  and the editor can show raw *and* normalized.
- **NaN never reaches a serializer.** The manifest stores ``null`` for an all-masked metric's
  norm params; the MCP wire maps masked entries to ``null`` and relies on the ``mask`` array.
- **The manifest is the SSOT for grid geometry** (``t0/hop_s/n``); a per-track geometry
  mismatch is a load-time assertion, never a silent 2× misalignment.
- **Persistence is crash-consistent**: each clip's ``.npz`` is written to a temp then
  ``os.replace``\\ d; ``manifest.json`` is written LAST via tmp+rename, so a reader sees
  either the whole prior state or the whole new one.

Pure numpy — no cv2/torch/ffmpeg here. Imported only under the ``muvid[scoring]`` extra
(never on the import-light ``muvid.genre_music_video`` path).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

#: Default grid step (seconds) → 10 Hz. Ample for a UI and beat-level selection.
DEFAULT_HOP_S = 0.1
#: Percentile clip edges for robust normalization.
_P_LO, _P_HI = 5.0, 95.0


def grid_len(song_duration: float, hop_s: float = DEFAULT_HOP_S) -> int:
    """Number of grid frames spanning ``[0, song_duration]`` at ``hop_s``."""
    return int(np.ceil(max(0.0, song_duration) / hop_s)) + 1


def align_fingerprint(alignments) -> str:
    """A stable, ORDER-INDEPENDENT fingerprint of a set of alignments (offsets + durations).

    The SSOT (shared by the scoring job's idempotency key AND the read-time staleness guard):
    a re-align that moves any offset changes this fingerprint, so persisted scores computed
    against the old offsets are detected as stale. Sorting the full triples (not just by
    clip_id) makes it independent of alignment order.
    """
    import hashlib
    import json

    basis = json.dumps(
        sorted(
            [str(a.clip_id), round(float(a.offset_s), 4), round(float(a.duration_s), 4)]
            for a in alignments
        )
    )
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class ScoreTrack:
    """One ``(clip, metric)`` curve on the shared song-time grid.

    Extractors produce ``raw_values`` (NaN where masked) + ``mask``; ``direction`` says
    whether higher raw = better (``"higher_better"``) or lower = better
    (``"lower_better"``, e.g. a distance like LSE-D — inverted at normalization). The
    normalized ``values`` are NOT stored here; they are derived at tensor assembly from
    ``raw_values`` + the per-metric global ``norm`` params (see :func:`apply_norm`).
    """

    clip_id: str
    metric: str
    t0: float
    hop_s: float
    raw_values: np.ndarray  # float32[n], NaN where masked
    mask: np.ndarray  # bool[n], True = valid/covered
    direction: str = "higher_better"

    def __post_init__(self):
        if self.raw_values.shape != self.mask.shape:
            raise ValueError(
                f"raw_values{self.raw_values.shape} and mask{self.mask.shape} "
                "must have the same shape"
            )
        if self.direction not in ("higher_better", "lower_better"):
            raise ValueError(f"unknown direction {self.direction!r}")

    @property
    def n(self) -> int:
        return int(self.raw_values.shape[0])

    def coverage_fraction(self) -> float:
        """Fraction of frames that are valid — surfaced so an all-NA metric is visible."""
        return float(np.count_nonzero(self.mask)) / max(1, self.n)

    def to_meta(self) -> dict:
        """Everything except the arrays (arrays live in the ``.npz``)."""
        return {
            "clip_id": self.clip_id,
            "metric": self.metric,
            "t0": self.t0,
            "hop_s": self.hop_s,
            "direction": self.direction,
            "coverage": round(self.coverage_fraction(), 4),
        }


def resample_to_grid(
    sample_times: Sequence[float],
    sample_values: Sequence[float],
    *,
    t0: float,
    hop_s: float,
    n: int,
    max_gap_s: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Resample irregular ``(song_time, value)`` samples onto the fixed grid.

    Vectorized (O(n_samples + n)): samples are mean-binned onto the grid, then linearly
    interpolated across bins that have no sample — but ONLY within the sampled span and
    ONLY across gaps ≤ ``max_gap_s`` (so a real coverage gap stays masked, never invented).
    Outside ``[first_sample, last_sample]`` the grid is masked (no extrapolation).

    Args:
        sample_times: song-time (s) of each sample (any order; NaN values dropped).
        sample_values: the sample values (parallel to ``sample_times``).
        t0/hop_s/n: the grid geometry (frame k ↔ ``t0 + k*hop_s``, k in ``[0, n)``).
        max_gap_s: bridge gaps up to this many seconds (default ``4*hop_s``).

    Returns:
        ``(values, mask)`` — ``values`` float32[n] (NaN where masked), ``mask`` bool[n].
    """
    if max_gap_s is None:
        max_gap_s = 4.0 * hop_s
    values = np.full(n, np.nan, dtype=np.float32)
    mask = np.zeros(n, dtype=bool)

    st = np.asarray(sample_times, dtype=np.float64)
    sv = np.asarray(sample_values, dtype=np.float64)
    if st.size == 0:
        return values, mask
    good = np.isfinite(st) & np.isfinite(sv)
    st, sv = st[good], sv[good]
    if st.size == 0:
        return values, mask

    # Bin each sample to the nearest grid frame; mean-aggregate collisions.
    idx = np.rint((st - t0) / hop_s).astype(np.int64)
    inside = (idx >= 0) & (idx < n)
    idx, sv_in = idx[inside], sv[inside]
    if idx.size == 0:
        return values, mask
    counts = np.bincount(idx, minlength=n).astype(np.float64)
    sums = np.bincount(idx, weights=sv_in, minlength=n)
    filled = counts > 0
    binned = np.full(n, np.nan)
    binned[filled] = sums[filled] / counts[filled]

    valid_frames = np.flatnonzero(filled)
    first, last = valid_frames[0], valid_frames[-1]
    grid_frames = np.arange(n)
    # Linear interpolation across the sampled span using the filled bins as knots.
    interp = np.interp(grid_frames, valid_frames, binned[valid_frames])

    # Mask by the WHOLE enclosing knot-gap, not distance-to-nearest — so an oversized gap is
    # masked end to end, never partially invented on its edges (a distance test would keep
    # the ≤max_gap fringe of a huge gap). `filled` keeps real knots adjacent to a large gap.
    pos = np.searchsorted(valid_frames, grid_frames)
    left = np.clip(pos - 1, 0, valid_frames.size - 1)
    right = np.clip(pos, 0, valid_frames.size - 1)
    gap_span = valid_frames[right] - valid_frames[left]
    max_gap_frames = max_gap_s / hop_s
    keep = (
        (grid_frames >= first)
        & (grid_frames <= last)
        & (filled | (gap_span <= max_gap_frames))
    )
    values[keep] = interp[keep].astype(np.float32)
    mask[keep] = True
    return values, mask


def compute_norm(
    raw_tracks: Sequence[np.ndarray],
    masks: Sequence[np.ndarray],
    *,
    direction: str,
) -> dict | None:
    """Per-metric-GLOBAL robust norm params from every clip's raw track for one metric.

    Pools all valid (unmasked, finite) raw values across clips → ``{median, iqr, p5, p95}``.
    Returns ``None`` if there are no valid values (an all-masked metric) — the caller stores
    ``null`` and :func:`apply_norm` then yields all-NaN (never a divide-by-zero or a NaN in
    the manifest).
    """
    pooled = []
    for raw, m in zip(raw_tracks, masks):
        vals = np.asarray(raw, dtype=np.float64)[np.asarray(m, dtype=bool)]
        vals = vals[np.isfinite(vals)]
        if vals.size:
            pooled.append(vals)
    if not pooled:
        return None
    allv = np.concatenate(pooled)
    median = float(np.median(allv))
    q1, q3 = np.percentile(allv, [25.0, 75.0])
    p5, p95 = np.percentile(allv, [_P_LO, _P_HI])
    return {
        "median": median,
        "iqr": float(q3 - q1),
        "p5": float(p5),
        "p95": float(p95),
        "direction": direction,
    }


def apply_norm(raw: np.ndarray, norm: dict | None, *, direction: str) -> np.ndarray:
    """Map raw values to a robust [0,1] score (higher = better), preserving NaN.

    ``z = (raw − median)/IQR`` (IQR==0 → neutral 0.5), clipped to ``[p5,p95]`` mapped to
    [0,1]; ``lower_better`` metrics are inverted so the output is always higher_better.
    """
    out = np.full(np.shape(raw), np.nan, dtype=np.float32)
    if norm is None:
        return out
    raw = np.asarray(raw, dtype=np.float64)
    finite = np.isfinite(raw)
    if not finite.any():
        return out
    median, iqr = norm["median"], norm["iqr"]
    p5, p95 = norm["p5"], norm["p95"]
    x = raw.copy()
    if iqr <= 0 or p95 <= p5:
        out[finite] = 0.5  # constant metric → neutral, comparable but non-decisive
        return out
    # Percentile-clip in raw space, then min-max to [0,1].
    x = np.clip(x, p5, p95)
    scaled = (x - p5) / (p95 - p5)
    if direction == "lower_better":
        scaled = 1.0 - scaled
    out[finite] = scaled[finite].astype(np.float32)
    return out


@dataclass(frozen=True)
class ScoreTensor:
    """The fused ``S[clip, frame, metric]`` (normalized) + ``M[clip, frame]`` mask.

    ``S`` carries NaN where masked (never used for reward — the selector reads ``M``).
    ``raw`` keeps the un-normalized values for the editor/tooltips. Geometry
    (``t0/hop_s/n``) is authoritative here (validated against the manifest at load).
    """

    clip_ids: list[str]
    metrics: list[str]
    t0: float
    hop_s: float
    n: int
    S: np.ndarray  # float32[n_clips, n, n_metrics] normalized (NaN where masked)
    M: np.ndarray  # bool[n_clips, n, n_metrics] valid mask
    raw: np.ndarray  # float32[n_clips, n, n_metrics] raw (NaN where masked)
    norms: dict  # metric -> norm params (or None)

    def metric_index(self, metric: str) -> int:
        return self.metrics.index(metric)

    def clip_index(self, clip_id: str) -> int:
        return self.clip_ids.index(clip_id)


def build_tensor(
    tracks: Mapping[str, Sequence[ScoreTrack]],
    *,
    t0: float,
    hop_s: float,
    n: int,
    metrics: Sequence[str] | None = None,
    clip_ids: Sequence[str] | None = None,
    norms: Mapping[str, dict | None] | None = None,
) -> ScoreTensor:
    """Assemble per-clip :class:`ScoreTrack` lists into a normalized :class:`ScoreTensor`.

    ``tracks`` maps ``clip_id -> [ScoreTrack, ...]``. The metric axis is the union of metrics
    present (or the explicit ``metrics`` order); a clip missing a listed metric contributes
    an all-masked column (so the tensor is rectangular and a missing extractor never shifts
    columns). Normalization is per-metric-global (computed here if ``norms`` is not supplied).
    Every track's geometry must match ``(t0, hop_s, n)`` — a mismatch raises (the SSOT rule).
    """
    clip_ids = list(clip_ids) if clip_ids is not None else list(tracks.keys())
    by_clip: dict[str, dict[str, ScoreTrack]] = {}
    all_metrics: list[str] = []
    for cid in clip_ids:
        by_clip[cid] = {}
        for tr in tracks.get(cid, []):
            if not (tr.t0 == t0 and tr.hop_s == hop_s and tr.n == n):
                raise ValueError(
                    f"track {cid}/{tr.metric} geometry (t0={tr.t0},hop={tr.hop_s},n={tr.n}) "
                    f"!= grid (t0={t0},hop={hop_s},n={n})"
                )
            by_clip[cid][tr.metric] = tr
            if tr.metric not in all_metrics:
                all_metrics.append(tr.metric)
    metrics = list(metrics) if metrics is not None else sorted(all_metrics)

    nc, nm = len(clip_ids), len(metrics)
    raw = np.full((nc, n, nm), np.nan, dtype=np.float32)
    M = np.zeros((nc, n, nm), dtype=bool)
    for ci, cid in enumerate(clip_ids):
        for mi, metric in enumerate(metrics):
            tr = by_clip[cid].get(metric)
            if tr is not None:
                raw[ci, :, mi] = tr.raw_values
                M[ci, :, mi] = tr.mask

    # Per-metric-global norm (median/IQR over all clips' valid raw for that metric).
    computed_norms: dict[str, dict | None] = {}
    S = np.full((nc, n, nm), np.nan, dtype=np.float32)
    for mi, metric in enumerate(metrics):
        direction = _metric_direction(by_clip, metric)
        if norms is not None and metric in norms:
            nrm = norms[metric]
        else:
            nrm = compute_norm(
                [raw[ci, :, mi] for ci in range(nc)],
                [M[ci, :, mi] for ci in range(nc)],
                direction=direction,
            )
        computed_norms[metric] = nrm
        for ci in range(nc):
            S[ci, :, mi] = apply_norm(raw[ci, :, mi], nrm, direction=direction)
    return ScoreTensor(
        clip_ids=clip_ids,
        metrics=metrics,
        t0=t0,
        hop_s=hop_s,
        n=n,
        S=S,
        M=M,
        raw=raw,
        norms=computed_norms,
    )


def _metric_direction(by_clip: Mapping[str, Mapping[str, ScoreTrack]], metric: str) -> str:
    for tracks in by_clip.values():
        if metric in tracks:
            return tracks[metric].direction
    return "higher_better"


# ---------------------------------------------------------------------------
# Persistence — crash-consistent, NaN-safe, under {project_root}/scores/
# ---------------------------------------------------------------------------

SCORES_DIRNAME = "scores"
MANIFEST_NAME = "manifest.json"


def scores_dir(project_root: Path) -> Path:
    return Path(project_root) / SCORES_DIRNAME


def scores_present(project_root: Path) -> bool:
    return (scores_dir(project_root) / MANIFEST_NAME).exists()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save_scores(
    project_root: Path,
    tracks: Mapping[str, Sequence[ScoreTrack]],
    *,
    t0: float,
    hop_s: float,
    n: int,
    metrics: Sequence[str],
    song_hash: str,
    align_fingerprint: str = "",
    beat_times: Sequence[float] = (),
    downbeat_times: Sequence[float] = (),
    extra: Mapping | None = None,
) -> None:
    """Persist per-clip raw arrays + a manifest, crash-consistently.

    Each clip → ``scores/{clip_id}.npz`` (raw + mask per metric), written tmp+``os.replace``.
    The per-metric global ``norm`` params + grid geometry + beats + ``song_hash`` go in
    ``scores/manifest.json``, written LAST via tmp+rename — so a concurrent reader sees a
    whole consistent state or the prior one, never a torn mix. NaN never enters the manifest
    (all-masked metric → ``null`` norm).
    """
    d = scores_dir(project_root)
    d.mkdir(parents=True, exist_ok=True)

    # Compute per-metric-global norms once (shared by save + the manifest).
    tensor = build_tensor(
        tracks, t0=t0, hop_s=hop_s, n=n, metrics=list(metrics)
    )
    clip_ids = tensor.clip_ids

    written = []
    for ci, cid in enumerate(clip_ids):
        arrays = {}
        for mi, metric in enumerate(metrics):
            arrays[f"{metric}__raw"] = tensor.raw[ci, :, mi]
            arrays[f"{metric}__mask"] = tensor.M[ci, :, mi]
        import io

        buf = io.BytesIO()
        np.savez_compressed(buf, **arrays)
        _atomic_write_bytes(d / f"{cid}.npz", buf.getvalue())
        written.append(cid)

    manifest = {
        "song_hash": song_hash,
        "align_fingerprint": align_fingerprint,
        "t0": t0,
        "hop_s": hop_s,
        "n": n,
        "clips": clip_ids,
        "metrics": list(metrics),
        "directions": {
            m: _metric_direction(
                {c: {tr.metric: tr for tr in tracks.get(c, [])} for c in clip_ids}, m
            )
            for m in metrics
        },
        "norms": {m: tensor.norms.get(m) for m in metrics},  # None → null (JSON-safe)
        "coverage": {
            m: round(
                float(np.count_nonzero(tensor.M[:, :, mi]))
                / max(1, tensor.M.shape[0] * n),
                4,
            )
            for mi, m in enumerate(metrics)
        },
        "beats": {
            "beat_times": [round(float(t), 4) for t in beat_times],
            "downbeat_times": [round(float(t), 4) for t in downbeat_times],
        },
        **(dict(extra) if extra else {}),
    }
    # manifest LAST — its presence signals a complete scores/ dir. allow_nan=False makes any
    # stray NaN/Inf FAIL LOUD here rather than silently emit invalid JSON (the "NaN never
    # reaches a serializer" invariant — norm params for an all-masked metric are already None).
    _atomic_write_bytes(
        d / MANIFEST_NAME,
        json.dumps(manifest, indent=2, allow_nan=False).encode("utf-8"),
    )


def manifest_is_current(
    manifest: dict | None, *, song_hash: str, align_fingerprint: str
) -> bool:
    """Whether persisted scores match the CURRENT song + alignments (else they are stale).

    Guards the read path: a re-align (or a new song) that raced the score job's rmtree, or a
    manifest predating the fingerprint, is detected here so no stale/mislabeled scores are
    served to the editor or the weighted selector.
    """
    if not manifest:
        return False
    return (
        manifest.get("song_hash") == song_hash
        and manifest.get("align_fingerprint") == align_fingerprint
    )


def load_manifest(project_root: Path) -> dict | None:
    p = scores_dir(project_root) / MANIFEST_NAME
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def load_tensor(project_root: Path) -> ScoreTensor | None:
    """Reconstruct the :class:`ScoreTensor` from persisted arrays + manifest, or ``None``.

    Geometry + norms come from the manifest (SSOT); each clip's ``.npz`` supplies raw+mask.
    A clip/metric absent from a ``.npz`` is filled as an all-masked column.
    """
    manifest = load_manifest(project_root)
    if manifest is None:
        return None
    d = scores_dir(project_root)
    t0, hop_s, n = manifest["t0"], manifest["hop_s"], manifest["n"]
    clip_ids = list(manifest["clips"])
    metrics = list(manifest["metrics"])
    directions = manifest.get("directions", {})
    norms = manifest.get("norms", {})

    tracks: dict[str, list[ScoreTrack]] = {}
    for cid in clip_ids:
        npz_path = d / f"{cid}.npz"
        tracks[cid] = []
        if not npz_path.exists():
            continue
        # Wrap BOTH np.load and the lazy member reads: a corrupted-but-openable npz raises
        # on member access, not at open. A bad clip → all-masked columns, not a crash.
        try:
            data = np.load(npz_path)
            clip_tracks = []
            for metric in metrics:
                rk, mk = f"{metric}__raw", f"{metric}__mask"
                if rk in data and mk in data:
                    clip_tracks.append(
                        ScoreTrack(
                            clip_id=cid,
                            metric=metric,
                            t0=t0,
                            hop_s=hop_s,
                            raw_values=np.asarray(data[rk], dtype=np.float32),
                            mask=np.asarray(data[mk], dtype=bool),
                            direction=directions.get(metric, "higher_better"),
                        )
                    )
            tracks[cid] = clip_tracks
        except Exception:
            tracks[cid] = []  # corrupted npz → build_tensor fills all-masked columns
    return build_tensor(
        tracks,
        t0=t0,
        hop_s=hop_s,
        n=n,
        metrics=metrics,
        clip_ids=clip_ids,
        norms=norms,
    )
