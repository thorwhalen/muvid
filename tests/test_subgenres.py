"""The subgenre plugin system: registration, discovery, isolation, conformance.

The properties defended here are the ones that make the plugin system worth
having rather than a dict with extra steps:

* listing every installed subgenre imports **no** rendering code;
* one broken plugin is a recorded error, not a dead catalogue;
* the manifest a third party ships is checkable before they publish it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest

from muvid.subgenres import (
    API_VERSION,
    RenderRequest,
    RenderResult,
    Subgenre,
    get_subgenre,
    list_subgenres,
    register_subgenre,
    render_subgenre,
    resolve_renderer,
    subgenre_catalog,
    unregister_subgenre,
)
from muvid.subgenres.testing import check_subgenre_conformance, echo_renderer


@pytest.fixture
def demo() -> Subgenre:
    sg = Subgenre(
        slug="test-demo",
        title="Demo",
        description="A demo subgenre.",
        render="muvid.subgenres.testing:echo_renderer",
        inputs={"type": "object", "properties": {"audio": {"type": "string"}}},
    )
    register_subgenre(sg)
    yield sg
    unregister_subgenre(sg.slug)


def test_register_list_and_get(demo):
    assert "test-demo" in list_subgenres()
    assert get_subgenre("test-demo") is demo


def test_a_slug_cannot_be_silently_reused(demo):
    """Slugs are persisted path segments and public contract values.

    ``nw.genres`` refuses a collision for the same reason; a registry that
    silently replaced one would let two plugins fight over a directory.
    """
    with pytest.raises(ValueError, match="already registered"):
        register_subgenre(demo)


def test_render_string_is_required_to_be_a_reference():
    """The whole design rests on the renderer being named, not imported."""
    with pytest.raises(ValueError, match="module:function"):
        Subgenre(slug="x", title="X", description="d", render="not_a_reference")


def test_api_version_mismatch_fails_loudly_at_declaration():
    with pytest.raises(ValueError, match="api_versions"):
        Subgenre(
            slug="x", title="X", description="d",
            render="m:f", api_versions=("999",),
        )


def test_catalog_is_json_able_and_names_the_api_version(demo):
    cat = subgenre_catalog()
    json.dumps(cat)  # must not raise
    assert cat["api_version"] == API_VERSION
    slugs = [s["slug"] for s in cat["subgenres"]]
    assert "test-demo" in slugs


def test_render_goes_through_the_contract(demo, tmp_path):
    out = tmp_path / "out.json"
    result = render_subgenre(
        "test-demo",
        inputs={"audio": "song.wav"},
        params={"k": 1},
        workdir=tmp_path,
        output=out,
    )
    assert isinstance(result, RenderResult)
    assert out.exists()
    assert json.loads(out.read_text())["inputs"] == {"audio": "song.wav"}


def test_unknown_subgenre_says_what_is_available(demo):
    with pytest.raises(KeyError, match="test-demo"):
        get_subgenre("no-such-subgenre")


def test_conformance_kit_passes_a_good_manifest(demo, tmp_path):
    report = check_subgenre_conformance(demo, workdir=tmp_path)
    assert report.ok, report.summary()


def test_conformance_kit_catches_a_renderer_that_lies(tmp_path):
    """A renderer that reports success without writing the file must fail."""

    def _liar(request: RenderRequest) -> RenderResult:
        return RenderResult(output=request.output)

    import muvid.subgenres.testing as t

    t._liar = _liar  # a module-level target the manifest can name
    sg = Subgenre(
        slug="liar", title="Liar", description="Writes nothing.",
        render="muvid.subgenres.testing:_liar",
    )
    report = check_subgenre_conformance(sg, workdir=tmp_path)
    assert not report.ok
    assert any("produced no file" in f for f in report.failures)


def test_conformance_kit_catches_a_wrong_return_type(tmp_path):
    import muvid.subgenres.testing as t

    t._wrong = lambda request: {"output": str(request.output)}
    sg = Subgenre(
        slug="wrong", title="Wrong", description="Returns a dict.",
        render="muvid.subgenres.testing:_wrong",
    )
    report = check_subgenre_conformance(sg, workdir=tmp_path)
    assert not report.ok
    assert any("expected RenderResult" in f for f in report.failures)


def test_echo_renderer_is_a_real_implementation(tmp_path):
    """The fixture renderer is not a stub — the conformance kit runs against it."""
    out = tmp_path / "o.json"
    r = echo_renderer(
        RenderRequest(subgenre="s", inputs={"a": 1}, params={}, workdir=tmp_path, output=out)
    )
    assert r.output == out and out.exists()


def test_listing_imports_no_renderer(tmp_path):
    """The load-bearing property, asserted in a CHILD interpreter.

    Asserting in-process would pass whatever the code did, because this test
    module has already imported the renderer machinery.
    """
    code = textwrap.dedent(
        """
        import sys, json
        from muvid.subgenres import subgenre_catalog
        import muvid.lyricvid.manifest as m
        from muvid.subgenres import register_subgenre
        register_subgenre(m.LYRIC_VIDEO)
        cat = subgenre_catalog()
        assert any(s['slug'] == 'lyric-video' for s in cat['subgenres']), cat
        heavy = sorted(
            n for n in sys.modules
            if n.split('.')[0] in {
                'numpy', 'pysubs2', 'playwright', 'moviepy', 'fastmcp',
                'cv2', 'librosa', 'torch', 'PIL', 'anthropic',
            }
        )
        print(json.dumps(heavy))
        """
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == []


def test_a_broken_plugin_does_not_take_the_catalogue_down(monkeypatch):
    """A plugin whose entry point raises is recorded and skipped, never raised."""
    from importlib.metadata import EntryPoint

    import muvid.subgenres._registry as reg

    class _Boom(EntryPoint):
        def load(self):  # type: ignore[override]
            raise ImportError("no such optional dependency")

    broken = _Boom(name="broken", value="nope:nope", group=reg.ENTRY_POINT_GROUP)
    monkeypatch.setattr(reg, "entry_points", lambda **_: [broken], raising=False)
    monkeypatch.setattr(
        "importlib.metadata.entry_points", lambda **kw: [broken] if kw else []
    )
    with pytest.warns(RuntimeWarning, match="could not load subgenre plugin"):
        cat = subgenre_catalog(refresh=True)
    assert "broken" in cat["load_errors"]
    # and the catalogue still serves
    assert isinstance(cat["subgenres"], list)
    subgenre_catalog(refresh=True)  # restore


def test_resolve_renderer_explains_a_missing_dependency(demo):
    sg = Subgenre(
        slug="needs-dep", title="Needs dep", description="d",
        render="a_module_that_does_not_exist:render",
    )
    register_subgenre(sg)
    try:
        with pytest.raises(ImportError, match="could not be imported"):
            resolve_renderer("needs-dep")
    finally:
        unregister_subgenre("needs-dep")
