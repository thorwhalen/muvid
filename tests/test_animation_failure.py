"""``an``'s refusal must be loud, and the journal must say what really rendered.

muvid#46. ``an`` reports a validate failure as *data* — ``OrchestratorReport``
with ``success=False`` — not as an exception, so the old handling was a single
``if`` that dropped ``report.error``, ``report.validation`` and
``report.verifications`` on the floor and returned a still image. Three things
made that worse than a missing output:

1. the output was *wrong*, not absent — a freeze frame reads as a creative choice;
2. the provenance line recorded the REQUESTED strategy, so the affected shots
   could not be found afterwards;
3. ``still`` can reach ``falaw.generate_image``, so the silent degradation could
   bill — and ``cost.py`` prices ``animation`` at nothing, so the budget gate had
   already been told the shot was free.

The split this file pins: **an engine that never ran is not an engine that ran
and refused.** ``an`` is deliberately declared in no muvid extra and carries no
version floor, so a machine without it is a supported machine and degrading to
``still`` there is intended — but the *dispatcher* decides that and journals it.
An ``an`` that IS installed and refuses the scene is a bug in what muvid
synthesized, and raises.

**Why none of the central guards import ``an``.** CI installs the ``ai,mcp``
extras and ``an`` appears nowhere in ``pyproject.toml``, so a test that reaches
for the real package skips on every runner — i.e. the exact failure class this
file exists to close would be unguarded in the only environment that gates a
merge. That lesson is already written down in ``tests/test_animation_camera.py``
and it applies here verbatim. So the failure shapes are driven through a fake
``an`` injected into ``sys.modules``, which behaves identically whether or not
the real package is present, and the ``an``-importing tests at the bottom are
the FRESHNESS check: they run the same formatter over ``an``'s real dataclasses
and go red on a developer machine when its report shape moves.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from muvid.project import MusicVideoProject
from muvid.renderers import RenderContext, render_shot
from muvid.renderers._errors import AnimationRenderError, RendererUnavailable
from muvid.renderers.animation import _format_an_failure, render_animation
from muvid.schema import ShotSpec

try:  # `an` is a soft dependency — see the module docstring.
    import an.orchestrate  # noqa: F401

    HAS_AN = True
except ImportError:  # pragma: no cover - depends on the environment
    HAS_AN = False

needs_an = pytest.mark.skipif(not HAS_AN, reason="`an` is not installed")


# --------------------------------------------------------------------------
# Doubles: the five shapes `an.orchestrate` can return with success=False
# --------------------------------------------------------------------------


class _Finding:
    """Stands in for BOTH of ``an``'s finding types.

    ``an.ir.validate.ValidationFinding`` and ``an.verify._base.Finding`` are
    different dataclasses in different modules and only the second carries
    ``suggested_fix`` — which is why the formatter reads it with ``getattr``.
    One double with an optional field exercises both.
    """

    def __init__(self, severity, ir_path, description, suggested_fix=None):
        self.severity = severity
        self.ir_path = ir_path
        self.description = description
        if suggested_fix is not None:
            self.suggested_fix = suggested_fix


class _Report:
    """Stands in for ``an.orchestrate.OrchestratorReport``."""

    def __init__(self, *, success=False, error=None, validation=None, verifications=()):
        self.success = success
        self.error = error
        self.validation = validation
        self.verifications = list(verifications)
        self.output_path = None


class _SubReport:
    """Stands in for ``ValidationReport``/``VerificationReport``."""

    def __init__(self, findings=(), passed=False):
        self.findings = list(findings)
        self.passed = passed


def _fake_an(orchestrate):
    """An ``an`` package whose only member is ``orchestrate``.

    Deliberately lacks ``an.audio``, so ``_make_lipsync_provider``'s
    ``from an.audio import ...`` raises ``ImportError`` and it returns ``None``
    — the same path a real older ``an`` takes, and one that never reaches
    ``ctx.project``.
    """
    pkg = types.ModuleType("an")
    mod = types.ModuleType("an.orchestrate")
    mod.orchestrate = orchestrate
    pkg.orchestrate = mod
    return pkg, mod


@pytest.fixture
def ctx(tmp_path) -> RenderContext:
    shot = ShotSpec(
        id="s1",
        start_s=0.0,
        end_s=4.0,
        render_strategy="animation",
        characters=("alice",),
        environment="park",
        camera="slow push-in",
        description="she sings",
    )
    shot_dir = tmp_path / "shots" / "s1"
    shot_dir.mkdir(parents=True)
    return RenderContext(
        project=None,
        shot=shot,
        shot_dir=shot_dir,
        audio_slice_path=tmp_path / "audio.wav",
        character_image_paths={},
        environment_image_path=None,
        lyric_lines=[],
        global_style="",
    )


@pytest.fixture
def never_spends(monkeypatch):
    """Trip if anything reaches the ``still`` strategy.

    ``still`` is the whole spend path: ``still.py`` calls
    ``falaw.generate_image`` when there is no environment anchor and no cached
    ``storyboard.png``. Patching the module attribute catches a reintroduced
    swallow even though ``animation.py`` no longer imports it — the import
    would happen inside the function, at call time, after this patch.
    """
    calls = []

    def _boom(*a, **k):  # pragma: no cover - the assertion is that it is unused
        calls.append((a, k))
        raise AssertionError(
            "the animation renderer fell back to `still` on an `an` FAILURE. "
            "That is muvid#46: a wrong output that can bill, under a budget "
            "gate that priced the shot at zero."
        )

    monkeypatch.setattr("muvid.renderers.still.render_still", _boom)
    return calls


# --------------------------------------------------------------------------
# The formatter: no single field is populated in every failure shape
# --------------------------------------------------------------------------


def _msg(report, tmp_path) -> str:
    return _format_an_failure(report, scene_dir=tmp_path, shot_id="s1")


def test_validation_crashed_shape_reports_the_error(tmp_path):
    """``validation`` is left None on this path — reading it alone says nothing."""
    msg = _msg(_Report(error="validation crashed: ValueError('bad rate')"), tmp_path)
    assert "validation crashed" in msg
    assert "bad rate" in msg


def test_validation_failed_shape_reports_every_error_finding(tmp_path):
    report = _Report(
        error="schema/semantic validation failed",
        validation=_SubReport(
            [
                _Finding("error", "shots/0/camera", "unknown move 'static'"),
                _Finding("warning", "shots/0", "line may be clipped"),
            ]
        ),
    )
    msg = _msg(report, tmp_path)
    assert "shots/0/camera: unknown move 'static'" in msg
    # Warnings are context, not cause: the failure is the error findings.
    assert "may be clipped" not in msg


def test_verifier_only_shape_is_reported_even_though_error_is_none(tmp_path):
    """The shape a formatter reading ``report.error`` alone renders as empty.

    ``an``'s pre- and post-render verifier paths set ``success=False`` through
    ``merge_verification`` and never touch ``error`` — and
    ``LayoutLintVerifier`` is in ``an``'s DEFAULT verifier list, so this is the
    shape muvid can actually provoke by synthesizing a bad ``scene.md``.
    """
    report = _Report(
        error=None,
        validation=_SubReport(passed=True),
        verifications=[
            _SubReport([_Finding("error", "shots/0", "duplicate shot id 's1'")])
        ],
    )
    msg = _msg(report, tmp_path)
    assert "duplicate shot id" in msg
    assert "shots/0" in msg


def test_a_suggested_fix_is_carried_through_when_present(tmp_path):
    report = _Report(
        verifications=[
            _SubReport(
                [_Finding("error", "shots/0", "resolution is 0", "set meta.resolution")]
            )
        ]
    )
    assert "set meta.resolution" in _msg(report, tmp_path)


def test_an_unexplained_failure_is_still_a_loud_failure(tmp_path):
    """success=False with nothing in any field must not produce an empty message.

    Silence is the bug this whole change exists to end, so the last resort says
    so in words and dumps the report rather than shrugging.
    """
    msg = _msg(_Report(), tmp_path)
    assert "no error" in msg
    assert repr(_Report()).split(" object")[0] in msg or "_Report" in msg


def test_the_message_always_names_the_scene_it_synthesized(tmp_path):
    """The surviving ``scene.md`` is the only forensic breadcrumb on disk."""
    msg = _msg(_Report(error="boom"), tmp_path)
    assert str(tmp_path / "scene.md") in msg


# --------------------------------------------------------------------------
# render_animation: refusal raises, absence degrades
# --------------------------------------------------------------------------


def test_an_failure_raises_and_never_renders_a_still(ctx, monkeypatch, never_spends):
    """The headline guard. Break the raise and this goes red on both counts."""
    report = _Report(
        error="schema/semantic validation failed",
        validation=_SubReport([_Finding("error", "shots/0/camera", "unknown move")]),
    )
    pkg, mod = _fake_an(lambda *a, **k: report)
    monkeypatch.setitem(sys.modules, "an", pkg)
    monkeypatch.setitem(sys.modules, "an.orchestrate", mod)

    with pytest.raises(AnimationRenderError) as e:
        render_animation(ctx)

    assert "unknown move" in str(e.value)
    assert "s1" in str(e.value)
    assert never_spends == []


def test_a_missing_an_is_reported_as_unavailable_not_as_a_failure(ctx, monkeypatch):
    """Absence is a capability report, and the renderer does not decide.

    ``sys.modules['an'] = None`` is the documented way to make ``import an``
    raise ``ImportError`` regardless of what is installed, so this test means
    the same thing on a runner and on a developer machine.

    The submodules have to go too, and that is not belt-and-braces:
    ``importlib._bootstrap._gcd_import`` returns a cached ``sys.modules`` entry
    for ``an.orchestrate`` **without ever consulting its parent**, so poisoning
    ``an`` alone leaves ``from an.orchestrate import orchestrate`` resolving to
    the real function on a machine that has it — and this test would then be
    silently exercising the opposite path from the one it names.
    """
    for name in [n for n in sys.modules if n == "an" or n.startswith("an.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "an", None)

    with pytest.raises(RendererUnavailable) as e:
        render_animation(ctx)

    assert e.value.strategy == "animation"
    assert e.value.fallback == "still"
    assert "pip install an" in str(e.value)


def test_a_broken_an_propagates_instead_of_masquerading_as_an_absent_one(
    ctx, monkeypatch
):
    """The second swallow: the catch was bare ``except Exception``.

    An ``an`` that is installed but broken — a syntax error in a submodule, a
    native dep that blows up on import — was indistinguishable from one that was
    never installed, and degraded just as quietly. Only ``ImportError`` means
    "not there".
    """

    class _Exploding(types.ModuleType):
        def __getattr__(self, name):
            raise RuntimeError("an's native deps are wired wrong")

    for name in [n for n in sys.modules if n.startswith("an.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "an", _Exploding("an"))

    with pytest.raises(RuntimeError) as e:
        render_animation(ctx)
    assert not isinstance(e.value, RendererUnavailable)
    assert "wired wrong" in str(e.value)


def test_a_successful_render_is_untouched(ctx, monkeypatch, tmp_path):
    """The happy path must not have moved."""
    produced = tmp_path / "an_out.mp4"
    produced.write_bytes(b"mp4")
    report = _Report(success=True)
    report.output_path = produced
    pkg, mod = _fake_an(lambda *a, **k: report)
    monkeypatch.setitem(sys.modules, "an", pkg)
    monkeypatch.setitem(sys.modules, "an.orchestrate", mod)

    out = render_animation(ctx)
    assert out == ctx.shot_dir / "output.mp4"
    assert out.read_bytes() == b"mp4"


# --------------------------------------------------------------------------
# The dispatcher: the journal records what RENDERED, not what was asked
# --------------------------------------------------------------------------


def _project_with_animation_shot(tmp_path) -> MusicVideoProject:
    proj = MusicVideoProject.init(tmp_path / "p")
    proj.upsert_shot(
        ShotSpec(id="s1", start_s=0.0, end_s=2.0, render_strategy="animation")
    )
    return proj


def _decisions(proj: MusicVideoProject) -> list[dict]:
    log = proj.root / ".muvid" / "decisions.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()]


@pytest.fixture
def stub_context(monkeypatch):
    """Skip audio slicing — the dispatcher's fallback logic is what is under test."""

    def _build(project, shot, global_style):
        shot_dir = project.shot_dir(shot.id)
        shot_dir.mkdir(parents=True, exist_ok=True)
        return RenderContext(
            project=project,
            shot=shot,
            shot_dir=shot_dir,
            audio_slice_path=shot_dir / "audio.wav",
            character_image_paths={},
            environment_image_path=None,
            lyric_lines=[],
            global_style=global_style,
        )

    monkeypatch.setattr("muvid.renderers._build_context", _build)


def _stub_still(ctx, **k):
    out = ctx.shot_dir / "output.mp4"
    out.write_bytes(b"still")
    return out


def test_a_fallback_is_journalled_as_what_it_actually_rendered(
    tmp_path, monkeypatch, stub_context
):
    """muvid#46's second consequence: the record has to be usable to FIND these."""
    proj = _project_with_animation_shot(tmp_path)

    def _unavailable(ctx, **k):
        raise RendererUnavailable("no `an` here", strategy="animation")

    monkeypatch.setattr("muvid.renderers.animation.render_animation", _unavailable)
    monkeypatch.setattr("muvid.renderers.still.render_still", _stub_still)

    with pytest.warns(RuntimeWarning, match="asked for the 'animation' strategy"):
        render_shot(proj, "s1")

    (entry,) = [d for d in _decisions(proj) if d["kind"] == "render_shot"]
    assert entry["strategy"] == "still"
    assert entry["requested_strategy"] == "animation"
    assert "no `an` here" in entry["fallback_reason"]


def test_a_degraded_render_is_not_cached_as_satisfying_the_shot(
    tmp_path, monkeypatch, stub_context
):
    """A still must never satisfy a request for an animation.

    ``_shot_hash`` is computed from the shot alone, so recording it for a
    degraded render would make the freeze frame permanent: the moment the user
    installs ``an``, ``render_shot`` would keep returning the cached still
    without ever retrying, and the warning would never be seen again.
    """
    proj = _project_with_animation_shot(tmp_path)

    def _unavailable(ctx, **k):
        raise RendererUnavailable("no `an` here", strategy="animation")

    monkeypatch.setattr("muvid.renderers.animation.render_animation", _unavailable)
    monkeypatch.setattr("muvid.renderers.still.render_still", _stub_still)

    with pytest.warns(RuntimeWarning):
        render_shot(proj, "s1")
    assert not (proj.shot_dir("s1") / "output.hash").exists()

    # ... and it is retried, rather than served from cache.
    with pytest.warns(RuntimeWarning):
        render_shot(proj, "s1")
    assert len([d for d in _decisions(proj) if d["kind"] == "render_shot"]) == 2


def test_a_render_that_did_what_was_asked_is_journalled_plainly_and_cached(
    tmp_path, monkeypatch, stub_context
):
    """The control. ``requested_strategy`` present IFF a fallback happened."""
    proj = _project_with_animation_shot(tmp_path)
    monkeypatch.setattr(
        "muvid.renderers.animation.render_animation",
        lambda ctx, **k: _stub_still(ctx),
    )

    render_shot(proj, "s1")

    (entry,) = [d for d in _decisions(proj) if d["kind"] == "render_shot"]
    assert entry["strategy"] == "animation"
    assert "requested_strategy" not in entry
    assert "fallback_reason" not in entry
    assert (proj.shot_dir("s1") / "output.hash").exists()


def test_an_animation_failure_reaches_the_caller_of_render_shot(
    tmp_path, monkeypatch, stub_context, never_spends
):
    """The dispatcher degrades on UNAVAILABLE only — a refusal propagates."""
    proj = _project_with_animation_shot(tmp_path)

    def _refuses(ctx, **k):
        raise AnimationRenderError("`an` refused to render shot 's1'")

    monkeypatch.setattr("muvid.renderers.animation.render_animation", _refuses)

    with pytest.raises(AnimationRenderError):
        render_shot(proj, "s1")
    assert never_spends == []
    assert not (proj.shot_dir("s1") / "output.hash").exists()


def test_the_fallback_is_exactly_one_level_deep(tmp_path, monkeypatch, stub_context):
    """A fallback that is itself unavailable propagates; it does not re-degrade.

    Structural, not checked — the degrade runs inside the ``except`` handler,
    so nothing catches it a second time. Pinned by COUNTING calls rather than
    by asserting the exception type, because "a ``RendererUnavailable`` came
    out" is also what an unbounded retry loop looks like from the outside. The
    first draft of this test asserted only the type and survived deleting the
    guard it was written to protect.
    """
    proj = _project_with_animation_shot(tmp_path)
    calls: list[str] = []

    def _unavailable(name, fallback):
        def _f(ctx, **k):
            calls.append(name)
            raise RendererUnavailable(f"no {name}", strategy=name, fallback=fallback)

        return _f

    monkeypatch.setattr(
        "muvid.renderers.animation.render_animation",
        _unavailable("animation", "still"),
    )
    monkeypatch.setattr(
        "muvid.renderers.still.render_still", _unavailable("still", "text_to_video")
    )
    monkeypatch.setattr(
        "muvid.renderers.text_to_video.render_text_to_video",
        _unavailable("text_to_video", "still"),
    )

    with pytest.warns(RuntimeWarning):
        with pytest.raises(RendererUnavailable) as e:
            render_shot(proj, "s1")

    assert calls == ["animation", "still"], (
        "the dispatcher degraded more than once; the third strategy must never "
        "be reached"
    )
    assert e.value.strategy == "still"


def test_every_strategy_in_the_table_resolves(tmp_path):
    """The if/elif chain became a table; a typo in it is now a data bug."""
    from muvid.renderers import _STRATEGIES, _load_strategy

    for name in _STRATEGIES:
        assert callable(_load_strategy(name))
    with pytest.raises(ValueError, match="Unknown render_strategy"):
        _load_strategy("nope")


# --------------------------------------------------------------------------
# Freshness: the formatter must read `an`'s REAL shapes, not just our doubles
# --------------------------------------------------------------------------


@needs_an
def test_the_formatter_reads_ans_real_report_dataclasses(tmp_path):
    """Runs the same formatter over objects ``an`` itself constructs.

    This is the freshness half, in the shape ``tests/test_animation_camera.py``
    established: the doubles above are what CI can run, and this is what goes
    red on a developer machine when ``an``'s report shape moves under them.
    """
    from an.ir.validate import ValidationReport
    from an.orchestrate import OrchestratorReport
    from an.verify._base import VerificationReport

    validation = ValidationReport()
    validation.add("error", "shots/0/camera", "unknown move 'static'")
    verification = VerificationReport()
    verification.add("error", "shots/0", "resolution is 0", "set meta.resolution")

    report = OrchestratorReport()
    report.success = False
    report.error = "schema/semantic validation failed"
    report.validation = validation
    report.verifications = [verification]

    msg = _format_an_failure(report, scene_dir=tmp_path, shot_id="s1")
    assert "schema/semantic validation failed" in msg
    assert "shots/0/camera: unknown move 'static'" in msg
    assert "resolution is 0" in msg
    assert "set meta.resolution" in msg


@needs_an
def test_ans_verifier_only_failure_still_leaves_error_unset(tmp_path):
    """Pins the premise the formatter is built on.

    If ``an`` ever starts setting ``error`` on the verifier paths, reading three
    fields becomes belt-and-braces rather than necessary — worth knowing, and
    this is where it surfaces.
    """
    from an.orchestrate import OrchestratorReport
    from an.verify._base import VerificationReport

    report = OrchestratorReport()
    failing = VerificationReport()
    failing.add("error", "shots/0", "duplicate shot id")
    report.merge_verification(failing)

    assert report.success is False
    assert report.error is None, (
        "`an` now sets `error` on the verifier path; _format_an_failure's "
        "three-field read can be simplified, and its docstring table updated."
    )
    assert "duplicate shot id" in _format_an_failure(
        report, scene_dir=tmp_path, shot_id="s1"
    )


@needs_an
def test_ans_default_verifiers_still_include_one_that_can_emit_errors():
    """Why the verifier-only shape is reachable at all, pinned.

    If ``an``'s default verifier list ever stops containing a verifier that
    emits error-severity findings, the shape becomes unreachable — and the
    reader of this code deserves to be told rather than left guessing.
    """
    import inspect

    from an.orchestrate import orchestrate
    from an.verify.layout import LayoutLintVerifier

    src = inspect.getsource(orchestrate)
    assert "LayoutLintVerifier()" in src
    assert "error" in inspect.getsource(LayoutLintVerifier)
