"""Named looks — the camera moves and grades a person can pick for a cut.

A cut's ``look`` is one ffmpeg filter chain (:attr:`muvid.footage.edl.EdlEntry.look`),
which is the right thing to RENDER and the wrong thing to OFFER: a screen or an
assistant can choose among named effects, not write filter strings. This is the menu,
each entry a function of a few bounded parameters compiled through
:mod:`muvid.footage.look` (so every fragment is ``looks``' and passes
``validate_edl``'s allowlist):

- camera moves, which read the clock (``look_time_varying``): ``punch_in`` (a steady
  tighter framing), ``slow_push`` / ``slow_pull`` (zoom in / out across the cut),
  ``pan_left`` / ``pan_right`` (drift sideways at a slight zoom);
- grades, which do not: ``vivid``, ``black_and_white``, ``posterize`` and ``cartoon``
  (flatten + posterize + a little colour — in the spirit of Que Calor's V2, whose real
  stylizer was a Python mean-shift and a palette LUT that no filter chain reproduces).

**Tasteful by construction**: every zoom is bounded at :data:`MAX_ZOOM` (1.15) by the
parameter's own schema and again when compiled — a move bigger than that reads as a
mistake on phone footage, where the frame has no resolution to spare.

A move is compiled for the cut's length and the project's canvas at the moment it is
set, and carried as the fragment: lengthening the cut later holds the last framing;
rendering on another canvas re-uses the fragment as compiled.

Import-light: the menu is data, and ``looks`` is imported only to compile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

#: The largest magnification any named move may ask for.
MAX_ZOOM = 1.15
#: The default magnification of a move: visible, not showy.
DFLT_ZOOM = 1.08


class NamedLookError(ValueError):
    """A named look that does not exist, or parameters it does not take."""


@dataclass(frozen=True)
class Param:
    """One parameter of a named look: a bounded number with a default."""

    default: float
    minimum: float
    maximum: float
    title: str
    integer: bool = False

    def schema(self) -> dict:
        return {
            "type": "integer" if self.integer else "number",
            "title": self.title,
            "default": self.default,
            "minimum": self.minimum,
            "maximum": self.maximum,
        }

    def coerce(self, name: str, value) -> float:
        try:
            v = int(value) if self.integer else float(value)
        except (TypeError, ValueError) as e:
            raise NamedLookError(f"{name} must be a number, got {value!r}") from e
        if not self.minimum <= v <= self.maximum:
            raise NamedLookError(
                f"{name} must be between {self.minimum} and {self.maximum}, got {v}"
            )
        return v


@dataclass(frozen=True)
class NamedLook:
    """A menu entry: ``build(canvas=, fps=, duration_s=, **params) -> fragment``."""

    name: str
    title: str
    description: str
    kind: str  # "camera" | "grade"
    build: Callable
    params: Mapping[str, Param] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "kind": self.kind,
            "time_varying": self.kind == "camera",
            "params_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {k: p.schema() for k, p in self.params.items()},
            },
        }


_ZOOM = Param(DFLT_ZOOM, 1.01, MAX_ZOOM, "How far in (1.08 = 8% closer)")
_ANCHOR_X = Param(0.5, 0.0, 1.0, "Where to zoom toward, across (0 left, 1 right)")
_ANCHOR_Y = Param(0.5, 0.0, 1.0, "Where to zoom toward, down (0 top, 1 bottom)")


def _window(zoom: float, x: float, y: float):
    """The canvas-fraction window of magnification ``zoom`` whose fixed point is (x, y)."""
    from muvid.footage.edl import CropWindow

    side = 1.0 / zoom
    return CropWindow(x=x * (1.0 - side), y=y * (1.0 - side), w=side, h=side)


def _full():
    from muvid.footage.edl import CropWindow

    return CropWindow(0.0, 0.0, 1.0, 1.0)


def _move(start, end, *, canvas, fps, duration_s):
    from muvid.footage.look import motion

    return motion([(0.0, start), (duration_s, end)], canvas=canvas, fps=fps)


def _punch_in(*, canvas, fps, duration_s, zoom, anchor_x, anchor_y):
    w = _window(zoom, anchor_x, anchor_y)
    return _move(w, w, canvas=canvas, fps=fps, duration_s=duration_s)


def _slow_push(*, canvas, fps, duration_s, zoom, anchor_x, anchor_y):
    return _move(
        _full(),
        _window(zoom, anchor_x, anchor_y),
        canvas=canvas,
        fps=fps,
        duration_s=duration_s,
    )


def _slow_pull(*, canvas, fps, duration_s, zoom, anchor_x, anchor_y):
    return _move(
        _window(zoom, anchor_x, anchor_y),
        _full(),
        canvas=canvas,
        fps=fps,
        duration_s=duration_s,
    )


def _pan(direction: int):
    def build(*, canvas, fps, duration_s, zoom):
        # Camera pans left = the window slides from the right edge to the left one.
        right, left = _window(zoom, 1.0, 0.5), _window(zoom, 0.0, 0.5)
        start, end = (right, left) if direction < 0 else (left, right)
        return _move(start, end, canvas=canvas, fps=fps, duration_s=duration_s)

    return build


def _grade(*steps):
    def build(*, canvas, fps, duration_s, **params):
        import looks

        from muvid.footage.look import stylize

        effects = tuple(
            looks.Effect(name=name, params=make(params)) for name, make in steps
        )
        return stylize(
            looks.Look(steps=effects), canvas=canvas, fps=fps, duration_s=duration_s
        )

    return build


_MOVE_PARAMS = {"zoom": _ZOOM, "anchor_x": _ANCHOR_X, "anchor_y": _ANCHOR_Y}

NAMED_LOOKS: tuple[NamedLook, ...] = (
    NamedLook(
        "punch_in",
        "Punch in",
        "Hold a slightly closer framing for the whole cut.",
        "camera",
        _punch_in,
        _MOVE_PARAMS,
    ),
    NamedLook(
        "slow_push",
        "Slow push in",
        "Ease in toward a point across the cut.",
        "camera",
        _slow_push,
        _MOVE_PARAMS,
    ),
    NamedLook(
        "slow_pull",
        "Slow pull out",
        "Start close and ease back to the full frame.",
        "camera",
        _slow_pull,
        _MOVE_PARAMS,
    ),
    NamedLook(
        "pan_left",
        "Pan left",
        "Drift left across the frame at a slight zoom.",
        "camera",
        _pan(-1),
        {"zoom": _ZOOM},
    ),
    NamedLook(
        "pan_right",
        "Pan right",
        "Drift right across the frame at a slight zoom.",
        "camera",
        _pan(+1),
        {"zoom": _ZOOM},
    ),
    NamedLook(
        "vivid",
        "Vivid",
        "Richer colour and a touch more contrast.",
        "grade",
        _grade(
            ("saturation", lambda p: {"amount": p["amount"]}),
            ("contrast", lambda p: {"amount": 1.05}),
        ),
        {"amount": Param(1.2, 1.0, 1.6, "How much more colour")},
    ),
    NamedLook(
        "black_and_white",
        "Black and white",
        "Monochrome with a little contrast.",
        "grade",
        _grade(
            ("saturation", lambda p: {"amount": 0.0}),
            ("contrast", lambda p: {"amount": p["contrast"]}),
        ),
        {"contrast": Param(1.1, 1.0, 1.4, "Contrast")},
    ),
    NamedLook(
        "posterize",
        "Posterize",
        "Flat bands of colour.",
        "grade",
        _grade(("posterize", lambda p: {"levels": int(p["levels"])})),
        {"levels": Param(8, 3, 16, "Colour levels", integer=True)},
    ),
    NamedLook(
        "cartoon",
        "Cartoon",
        "Smoothed, banded, a little brighter in colour — a painted look.",
        "grade",
        _grade(
            ("flatten", lambda p: {"scale": 0.5}),
            ("posterize", lambda p: {"levels": int(p["levels"])}),
            ("saturation", lambda p: {"amount": 1.15}),
        ),
        {"levels": Param(8, 4, 16, "Colour levels", integer=True)},
    ),
)

_BY_NAME = {look.name: look for look in NAMED_LOOKS}


def named_look_catalogue() -> list[dict]:
    """The menu as JSON rows (name, title, description, kind, params_schema)."""
    return [look.to_dict() for look in NAMED_LOOKS]


def resolve_named_look(spec: Mapping) -> dict:
    """``{"name": ..., **params}`` checked, with every parameter's value filled in (the
    defaults included) — the spec a cut records so a screen can show the choice."""
    spec = dict(spec)
    name = spec.pop("name", None)
    look = _BY_NAME.get(name)
    if look is None:
        raise NamedLookError(f"unknown look {name!r}; the looks are {sorted(_BY_NAME)}")
    unknown = sorted(set(spec) - set(look.params))
    if unknown:
        raise NamedLookError(
            f"look {name!r} takes {sorted(look.params) or 'no parameters'}, "
            f"not {unknown}"
        )
    params = {k: p.coerce(k, spec.get(k, p.default)) for k, p in look.params.items()}
    return {"name": name, **params}


def compile_named_look(spec: Mapping, *, canvas, fps: float, duration_s: float):
    """``{"name": ..., **params}`` → the cut's filter fragment (a ``LookFragment``,
    which says whether it is time-varying). Unknown names and parameters are refused."""
    resolved = resolve_named_look(spec)
    name = resolved.pop("name")
    return _BY_NAME[name].build(
        canvas=canvas, fps=fps, duration_s=duration_s, **resolved
    )


__all__ = [
    "NAMED_LOOKS",
    "NamedLook",
    "NamedLookError",
    "MAX_ZOOM",
    "named_look_catalogue",
    "resolve_named_look",
    "compile_named_look",
]
