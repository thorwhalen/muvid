# Alignment — references

Background reading on the lyric→audio alignment problem that mtv's
`mtv.align` module currently solves with a greedy token-match. The
files below live next to this one for offline reference.

## Why this matters for mtv

mtv's pipeline turns on the *lyric→audio alignment store*: every
downstream stage (script writing, lipsync render, lyric overlay)
queries it for "what is being sung in `[start, end]`?" v0 uses
ElevenLabs Scribe (the same Scribe `mixing.transcript` already wraps)
and a greedy token-match against the user-edited `lyrics.md`. That
works for prototyping but inherits all the limitations these papers
catalog — and points at the upgrades worth doing later.

## 1. WhisperX forced alignment

[`Forced Alignment System.pdf`](Forced%20Alignment%20System.pdf) /
[`deepwiki_whisperx_3.3_forced_alignment_system.md`](deepwiki_whisperx_3.3_forced_alignment_system.md)

The DeepWiki page on WhisperX's `whisperx/alignment.py`. Key points:

- **Two-stage architecture**: Whisper produces utterance-level
  timestamps (e.g. `[0.0s – 5.2s]` for a sentence); a second
  phoneme-level Wav2Vec2 model + CTC alignment refines those to
  per-word and per-character boundaries.
- **Model registry**: TorchAudio ships fast Wav2Vec2 models for ~5
  languages (`en`, `fr`, `de`, `es`, `it`); HuggingFace covers ~30
  more via Jonatas Grosman's `wav2vec2-large-xlsr-53-*` checkpoints.
- **Pipeline**: segment_data preprocessing → model dictionary mapping
  → CTC log-softmax → trellis → backtrack → punkt sentence
  re-tokenization → NaN interpolation → aligned `TranscriptionResult`.

**Why mtv could care**: WhisperX would replace mtv's "Scribe + greedy
match" with a real forced-alignment path that takes the user's
canonical lyrics text *as input* and lines it up to audio with CTC.
The trade-off is heavy: torch + a Wav2Vec2 download per language,
versus Scribe's "one HTTP call, no local model." A future
`mtv.align` could expose `aligner="scribe-greedy" | "whisperx" |
"user"` and let the user pick.

## 2. STARS — singing-specific alignment

[`STARS- A Unified Framework for Singing Transcription, Alignment, and Refined Style Annotation.pdf`](STARS-%20A%20Unified%20Framework%20for%20Singing%20Transcription%2C%20Alignment%2C%20and%20Refined%20Style%20Annotation.pdf)
(Guo et al., Zhejiang University, arXiv 2507.06670, July 2025)

Singing alignment is *fundamentally* harder than speech alignment —
phoneme durations vary by an order of magnitude (held notes, melisma),
and rhythmic structure means the prior speech-aligners assume goes
out the window. STARS proposes a unified framework that, in one
forward pass, predicts:

- frame-, word-, and phoneme-level boundaries
- MIDI note onset/duration/pitch
- per-phoneme vocal techniques (one of 9: vibrato, falsetto, etc.)
- global stylistic attributes (emotion, pace)

Architecturally: a five-level hierarchical encoder (Frame → Word →
Phone → Note → Sentence) with a Conformer + FreqMOE backbone, sharing
features across levels. Replaces the conventional pipeline that
chains Whisper/Qwen-Audio → MFA → VOCANO/MusicYOLO and accumulates
errors at every handoff.

The framing matters more than the specific architecture: STARS argues
that in singing, **lyric alignment, note transcription, and style
annotation are coupled problems** and forcing them through a
sequential pipeline injects cascading error.

**Why mtv could care**:

- It validates the design choice of having *one alignment store*
  (`lacing` SqliteStore) as the SSOT, with multiple tiers
  (`sections` / `lines` / `words`). Future work can add `notes` and
  `techniques` tiers with the same interval algebra, no schema
  migration needed.
- Demo / audio samples: <https://gwx314.github.io/stars-demo/>.
- If mtv ever needs *true* per-syllable lipsync for stylized
  singing scenes (held notes, vibrato), STARS-style joint inference
  is the right reference. WhisperX-on-singing will under-perform
  because phoneme durations are too variable.

## Practical implication for mtv today

- **Default** (Scribe + greedy match): cheap, network-only, good
  enough for clear pop vocals; the user is expected to fix mishears
  in `lyrics.md`. This is what `mtv align` does now.
- **Local fallback** (WhisperX): would let mtv work offline with
  no API budget, at the cost of torch + model downloads.
  Re-using `an.audio.WhisperLipSync`'s already-installed
  `faster-whisper` is a smaller step than going to WhisperX
  proper, but loses the CTC refinement.
- **Singing-grade** (STARS or similar): only worth pulling in if
  mtv pivots toward generated/synthesized singing where the
  pipeline needs note-level + technique-level annotations to drive
  the renderer. For now, treating the song as fixed audio and
  the user's lyrics as ground truth keeps the problem tractable.

## Sources

- [WhisperX Forced Alignment System (DeepWiki)](https://deepwiki.com/m-bain/whisperX/3.3-forced-alignment-system)
- [STARS: A Unified Framework for Singing Transcription, Alignment, and Refined Style Annotation (arXiv 2507.06670)](https://arxiv.org/html/2507.06670v1)
