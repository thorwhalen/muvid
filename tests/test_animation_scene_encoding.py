"""``scene.md`` carries the shot's lyrics, so it is UTF-8 by contract.

:func:`muvid.renderers.animation.render_animation` synthesizes a ``scene.md``
for ``an`` and interpolates ``ctx.lyric_lines`` into its ``dialogue`` block
verbatim. That makes it a third lyric-carrying markdown alongside ``lyrics.md``
and ``script.md``, and it had the same defect: an unqualified ``write_text``
takes its codec from the process locale, so under ``LC_ALL=C`` a Spanish shot
raised ``UnicodeEncodeError`` instead of rendering.

``an`` is a soft dependency and CI installs no extra that provides it, so — as
in :mod:`tests.test_animation_failure` — the engine is a fake injected into
``sys.modules``, which behaves the same whether or not the real package is
installed. The locale cannot be changed once the interpreter is running, so
this runs in a child under :mod:`tests.ascii_locale_support`.
"""

from __future__ import annotations

import json

from tests.ascii_locale_support import needs_ascii_locale, run_under_ascii_locale


#: A sung line with the accents the animation renderer must be able to write.
NON_ASCII_LYRIC = "Qué calor, cómo está el ambiente — «dembow»"

_RENDER_WITH_FAKE_AN = """
import json, sys, types
from pathlib import Path

root = Path(sys.argv[1])
lyric = json.loads(sys.argv[2])


class _Report:
    success = True

    def __init__(self, output_path):
        self.output_path = str(output_path)


output = root / "an-output.mp4"
output.write_bytes(b"")
fake = types.ModuleType("an")
orch = types.ModuleType("an.orchestrate")
orch.orchestrate = lambda scene_dir, **kw: _Report(output)
fake.orchestrate = orch
sys.modules["an"] = fake
sys.modules["an.orchestrate"] = orch

from muvid.renderers import RenderContext
from muvid.renderers.animation import render_animation
from muvid.schema import ShotSpec

shot_dir = root / "shots" / "s1"
shot_dir.mkdir(parents=True)
render_animation(
    RenderContext(
        project=None,
        shot=ShotSpec(
            id="s1",
            start_s=0.0,
            end_s=4.0,
            render_strategy="animation",
            characters=("alicia",),
            environment="parque",
        ),
        shot_dir=shot_dir,
        audio_slice_path=root / "audio.wav",
        character_image_paths={},
        environment_image_path=None,
        lyric_lines=[{"text": lyric, "start_s": 0.0, "end_s": 4.0}],
    )
)
# ensure_ascii keeps stdout readable under the ASCII codec too.
print(json.dumps((shot_dir / "an_scene" / "scene.md").read_bytes().decode("utf-8")))
"""


@needs_ascii_locale
def test_scene_md_is_written_as_utf8_under_ascii_locale(tmp_path):
    """A shot whose lyrics carry accents still renders under ``LC_ALL=C``."""
    proc = run_under_ascii_locale(
        _RENDER_WITH_FAKE_AN, str(tmp_path), json.dumps(NON_ASCII_LYRIC)
    )
    assert proc.returncode == 0, proc.stderr
    assert NON_ASCII_LYRIC in json.loads(proc.stdout)
