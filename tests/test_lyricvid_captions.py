"""Caption tracks per sung line: translation and Hepburn romaji (muvid#145)."""

import json

import pytest

pysubs2 = pytest.importorskip("pysubs2")

from muvid.lyricvid import captions as C
from muvid.lyricvid import glyph_align as ga
from muvid.lyricvid.timed_text import Glyph, from_word_records

EN = {"バス バス": "Bus, bus", "スタート": "Start", "フン": "Hmph"}


def _tt():
    return from_word_records(
        [
            {"text": "バス", "start": 1.0, "end": 1.4},
            {"text": "バス", "start": 1.5, "end": 1.9, "line_end": True},
            {"text": "スタート", "start": 2.5, "end": 5.0, "line_end": True},
            {"text": "フン", "start": 5.2, "end": 5.5, "line_end": True},
        ],
        duration=8.0,
    )


def _events(track):
    return [(e.start, e.end, e.text) for e in track]


def test_each_line_is_captioned_from_its_onset_until_the_next_line():
    # lead 0.15 s; held at least 1.2 s and at least to the line's end, but
    # never past the next caption; the last one no later than 1.5 s after
    tracks = C.caption_tracks(_tt(), {"en": EN, "ja-Latn": "hepburn"})
    en = _events(tracks["en"])
    assert en[0] == (850, 2050, "Bus, bus")  # max(1.9, 0.85 + 1.2), before 2.35
    assert en[1] == (2350, 5000, "Start")  # line end 5.0, next caption at 5.05
    assert en[2] == (5050, 6250, "Hmph")  # max(5.5, 6.25), before 5.5 + 1.5
    assert [t for *_, t in _events(tracks["ja-Latn"])] == ["basu basu", "sutāto", "fun"]


def test_a_caption_starts_at_the_first_SUNG_GLYPH_when_glyph_times_exist():
    from dataclasses import replace

    tt = _tt()
    first = next(tt.words())
    early = replace(
        first,
        glyphs=(
            Glyph(text="バ", start=0.9, end=1.0),
            Glyph(text="ス", start=1.1, end=1.2),
        ),
    )
    tt = replace(
        tt,
        sections=tuple(
            replace(
                s,
                lines=tuple(
                    replace(l, words=tuple(early if w is first else w for w in l.words))
                    for l in s.lines
                ),
            )
            for s in tt.sections
        ),
    )
    assert _events(C.caption_tracks(tt, {"en": EN})["en"])[0][0] == 750


def test_offset_shifts_everything_and_never_goes_below_zero():
    tracks = C.caption_tracks(_tt(), {"en": EN}, offset_s=0.8, lead_s=2.0)
    en = _events(tracks["en"])
    assert en[0][0] == 0  # 1.0 - 2.0 + 0.8 < 0: clamped
    assert en[1][0] == round((2.5 - 2.0 + 0.8) * 1000)


def test_a_translation_must_cover_every_sung_line():
    with pytest.raises(ValueError, match="do not cover 1 sung line") as e:
        C.caption_tracks(_tt(), {"en": {k: v for k, v in EN.items() if k != "フン"}})
    assert "フン" in str(e.value)


def test_an_empty_caption_omits_that_line_on_purpose():
    tracks = C.caption_tracks(_tt(), {"en": {**EN, "フン": ""}})
    assert [t for *_, t in _events(tracks["en"])] == ["Bus, bus", "Start"]


def test_a_callable_transform_is_accepted():
    tracks = C.caption_tracks(_tt(), {"ja": str.upper})
    assert [t for *_, t in _events(tracks["ja"])] == ["バス バス", "スタート", "フン"]


@pytest.mark.parametrize("lang", ["../x", "en us", "", "en/../../x"])
def test_a_language_that_is_not_a_bcp47_tag_is_refused(lang):
    with pytest.raises(ValueError, match="BCP-47"):
        C.caption_tracks(_tt(), {lang: "original"})


def test_an_unknown_named_transform_is_refused_with_the_names():
    with pytest.raises(ValueError, match="hepburn"):
        C.caption_tracks(_tt(), {"en": "google-translate"})


def test_tracks_are_written_one_file_per_language(tmp_path):
    tracks = C.caption_tracks(_tt(), {"en": EN, "ja-Latn": "hepburn"})
    paths = C.write_caption_tracks(tracks, tmp_path, stem="song")
    assert {k: p.name for k, p in paths.items()} == {
        "en": "song.en.srt",
        "ja-Latn": "song.ja-Latn.srt",
    }
    assert _events(pysubs2.load(str(paths["en"]))) == _events(tracks["en"])


def test_hepburn_writes_long_vowels_and_the_alignment_romaniser_does_not():
    assert ga.romanize_hepburn("スーパー") == "sūpā"
    assert "".join(r for _, r in ga.romanize_kana("スーパー")) == "supa"
    assert ga.romanize_hepburn("コンヤ") == "kon'ya"
    assert ga.romanize_hepburn("ぱん") == "pan"  # hiragana too


def test_the_manifest_names_exactly_the_registered_transforms():
    from muvid.lyricvid.manifest import _PARAMS

    names = _PARAMS["properties"]["captions"]["additionalProperties"]["anyOf"][0][
        "enum"
    ]
    assert sorted(names) == sorted(C.CAPTION_TRANSFORMS)


def test_export_captions_and_the_cli_read_a_saved_timing(tmp_path):
    from muvid.lyricvid import tools
    from muvid.lyricvid.__main__ import _caption_tracks_arg

    timing = tmp_path / "t.json"
    timing.write_text(json.dumps(_tt().to_dict(), ensure_ascii=False), encoding="utf-8")
    en = tmp_path / "en.json"
    en.write_text(json.dumps(EN, ensure_ascii=False), encoding="utf-8")
    tracks = _caption_tracks_arg(json.dumps({"en": str(en), "ja-Latn": "hepburn"}))
    assert tracks["en"] == EN and tracks["ja-Latn"] == "hepburn"
    out = tools.export_captions(
        str(timing), str(tmp_path / "caps"), tracks, offset_s=0.8
    )
    assert {k: v["n_captions"] for k, v in out["tracks"].items()} == {
        "en": 3,
        "ja-Latn": 3,
    }


def _fake_backend(calls):
    from muvid.subgenres import RenderResult

    def backend(scene, *, audio, output, workdir):
        calls.append(output)
        output.write_bytes(b"")
        return RenderResult(output=output)

    return backend


def _render(tmp_path, monkeypatch, captions, calls):
    from muvid.lyricvid import pipeline
    from muvid.subgenres import RenderRequest

    monkeypatch.setattr(pipeline, "select_renderer", lambda name: "fake")
    monkeypatch.setattr(pipeline, "resolve_renderer", lambda name: _fake_backend(calls))
    audio = tmp_path / "song.wav"
    audio.write_bytes(b"")
    timing = tmp_path / "t.json"
    timing.write_text(json.dumps(_tt().to_dict(), ensure_ascii=False), encoding="utf-8")
    req = RenderRequest(
        subgenre="lyric-video",
        inputs={"audio": str(audio), "timed_text": str(timing)},
        params={"captions": captions},
        workdir=tmp_path / "wd",
        output=tmp_path / "out.mp4",
    )
    return pipeline.render(req)


def test_render_writes_caption_tracks_as_artifacts(tmp_path, monkeypatch):
    calls = []
    result = _render(tmp_path, monkeypatch, {"en": EN, "ja-Latn": "hepburn"}, calls)
    assert calls  # the fake backend really ran
    assert result.artifacts["captions.en"].name == "captions.en.srt"
    assert result.artifacts["captions.ja-Latn"].exists()


def test_render_refuses_uncovered_captions_BEFORE_rendering(tmp_path, monkeypatch):
    calls = []
    with pytest.raises(ValueError, match="do not cover"):
        _render(tmp_path, monkeypatch, {"en": {"バス バス": "Bus, bus"}}, calls)
    assert calls == []


# --- regressions from the adversarial review of muvid#145 ------------------


def test_hepburn_keeps_what_it_cannot_romanise_instead_of_dropping_the_line():
    tt = from_word_records(
        [
            {"text": "東京", "start": 1.0, "end": 2.0, "line_end": True},
            {"text": "ﾊﾞｽ", "start": 3.0, "end": 4.0, "line_end": True},
        ],
        duration=6.0,
    )
    got = [
        t for *_, t in _events(C.caption_tracks(tt, {"ja-Latn": "hepburn"})["ja-Latn"])
    ]
    assert got == ["東京", "basu"]


def test_hepburn_punctuation_glides_and_decomposed_input():
    import unicodedata

    assert ga.romanize_hepburn("バス、スタート") == "basu, sutāto"
    assert ga.romanize_hepburn("ン、アイ") == "n, ai"  # no apostrophe across a comma
    assert ga.romanize_hepburn("ウェ クァ イェ") == "we kwa ye"
    assert ga.romanize_hepburn(unicodedata.normalize("NFD", "ガンダム")) == "gandamu"


@pytest.mark.parametrize(
    "bad", [{"en": 5}, {"en": {"バス バス": 5}}, {"en\n": "original"}]
)
def test_malformed_requests_are_value_errors_before_any_render(
    tmp_path, monkeypatch, bad
):
    calls = []
    with pytest.raises(ValueError):
        _render(tmp_path, monkeypatch, bad, calls)
    assert calls == []


def test_lines_are_captioned_in_sung_order_and_simultaneous_lines_overlap():
    tt = from_word_records(
        [
            {"text": "a", "start": 5.0, "end": 6.0, "line_end": True},
            {"text": "b", "start": 2.0, "end": 3.0, "line_end": True},
            {"text": "c", "start": 2.0, "end": 4.0, "line_end": True},
        ],
        duration=10.0,
    )
    ev = _events(C.caption_tracks(tt, {"x": "original"})["x"])
    assert [t for *_, t in ev] == ["b", "c", "a"]
    assert ev[0] == (1850, 3050, "b")  # not cut to nothing by c starting with it


def test_an_untimed_first_glyph_does_not_pull_the_caption_to_zero():
    from dataclasses import replace

    tt = _tt()
    first = next(tt.words())
    nan = float("nan")
    early = replace(
        first,
        glyphs=(
            Glyph(text="バ", start=nan, end=nan, measured=False),
            Glyph(text="ス", start=1.1, end=1.2),
        ),
    )
    tt = replace(
        tt,
        sections=tuple(
            replace(
                s,
                lines=tuple(
                    replace(l, words=tuple(early if w is first else w for w in l.words))
                    for l in s.lines
                ),
            )
            for s in tt.sections
        ),
    )
    assert _events(C.caption_tracks(tt, {"en": EN})["en"])[0][0] == 950


def test_braces_are_refused_and_blank_lines_cannot_split_a_cue(tmp_path):
    with pytest.raises(ValueError, match="braces"):
        C.caption_tracks(_tt(), {"en": {**EN, "フン": "{laughs}"}})
    with pytest.raises(ValueError, match="-->"):
        C.caption_tracks(
            _tt(), {"en": {**EN, "フン": "a\n00:00:00,000 --> 00:09:00,000\nb"}}
        )
    tracks = C.caption_tracks(_tt(), {"en": {**EN, "フン": "a\n\n\nb"}})
    path = C.write_caption_tracks(tracks, tmp_path)["en"]
    assert [e.plaintext for e in pysubs2.load(str(path))][-1] == "a\nb"


def test_a_callables_own_keyerror_is_not_reported_as_a_missing_line():
    def broken(text):
        return {}[text]

    with pytest.raises(KeyError):
        C.caption_tracks(_tt(), {"en": broken})


def test_a_defaultdict_does_not_cover_lines_by_accident():
    from collections import defaultdict

    with pytest.raises(ValueError, match="do not cover"):
        C.caption_tracks(_tt(), {"en": defaultdict(str, {"フン": "Hmph"})})


@pytest.mark.parametrize(
    "kw", [{"stem": "../../escaped"}, {"stem": "a/b"}, {"fmt": "../x"}]
)
def test_stem_and_format_cannot_leave_the_output_directory(tmp_path, kw):
    with pytest.raises(ValueError):
        C.write_caption_tracks(C.caption_tracks(_tt(), {"en": EN}), tmp_path, **kw)


def test_no_caption_outlasts_the_song():
    from dataclasses import replace

    tt = replace(_tt(), duration=5.8)
    assert _events(C.caption_tracks(tt, {"en": EN})["en"])[-1][1] == 5800
