---
name: av-multi-angle-video
description: "[Avatar Studio] Turn ONE avatar look + a script into a finished multi-camera talking-head video with professional angle cuts — no editing. Generates (or reuses) the look's Image-N angle set (straight-on / close-up / 3-4 / wide), plans camera cuts from the script with an explicit cinematography ruleset (wide=establish, medium=exposition, close=emphasis/emotion/CTA, 3-4=asides), renders the SAME continuous take from each chosen angle driven by one shared master audio track, and hard-cuts them together with ffmpeg so the voice is seamless across cuts. Composes /av-image-n-looks (angles mode) for looks and the /v2/video/generate audio-input path for frame-aligned cameras. Use when the user says 'multi-angle video', 'multi-cam', 'add camera cuts to this script', 'make this look like a studio shoot', or hands a look + script and wants a cut talking-head."
---

# av-multi-angle-video — script → multi-camera talking-head

**Type:** end-to-end video pipeline + reusable module. Built on top of [[av-image-n-looks]] (angles mode) and the Avatar audio-input render path.

**What it is:** the user gives ONE avatar look + a script. The skill outputs a finished video that cuts between several camera angles of that look — the way a multi-cam studio shoot is edited — driven entirely by parsing the script. The core idea: a **single continuous voice take, with cameras switched on top of it** (one mic, three cameras, one edit), so the audio is perfectly seamless across every cut.

## The model: one take, N cameras, one edit

A real multi-cam studio records ONE performance with several cameras rolling, then the editor cuts between angles. We reproduce that exactly:

1. **Master camera** renders the full script (text→TTS) → `master.mp4` + its audio.
2. **Extract the master audio once** → it becomes the single source of truth for the whole video.
3. **Every other camera** renders the *full script too*, but **driven by the master audio** (`voice.type:"audio"`), so each camera is the SAME take, frame-for-frame, from a different angle.
4. **The edit** (ffmpeg) picks which camera is "live" in each time window per the cut sheet, and lays the **one master audio track** over the whole thing.

Because every camera lip-syncs to the *same* audio and the final track IS that audio, cuts are seamless — no pitch/pace jump, no lip drift. This is why we DON'T slice audio and stitch fragments: fragment boundaries are fragile; a shared continuous take is not. (Decision locked with Adam 2026-06-08.)

```
look + script
   │
   ├─[A] angle set  ── generate-or-reuse the look's Image-N angles (/av-image-n-looks angles mode)
   │
   ├─[C] master render (base angle, text→TTS) ──► master.mp4 + master audio asset
   │                                                       │
   ├─[B] cut plan  ◄── Whisper word-timing on master audio + LLM/ruleset segmentation
   │                                                       │
   ├─[D] camera renders (other angles, voice.type:audio = master audio) ──► angleB.mp4, angleC.mp4
   │
   └─[E] ffmpeg multi-cam cut ── per cut sheet, video from the live camera + ONE master audio
                                                       │
                                                       └─► final.mp4 (caption-free)
```

## Stages

### Stage 0 — Engine: Avatar V always (`scripts/probe_audio_input.py`)
**Every camera renders on Avatar V (tokyo).** There is no per-camera engine choice and no V-vs-IV fallback — so there's never an engine mismatch across a cut. Two facts settle the engine question (Adam, 2026-06-08):
- **Our looks are digital twins.** Image-N angle frames live in the creator's twin group (which has a real uploaded video), so they render on **Avatar V directly**.
- **Virtual avatars (photo-only, no video upload) still output Avatar V** — the pipeline just **JIT-creates the base motion with Avatar IV first**, then Avatar V renders on top ([[reference_avatar_v_cross_ref_upload_pipeline]] / [[reference_avatar_v_api_jit_gate]]). This is a one-time base-motion bootstrap for that look, not a per-camera downgrade. `angle_set.py` ensures every angle look has a video base before rendering (attach the twin clip via the cross_ref upload pipeline) so Avatar V is available.

The thing the probe DOES need to confirm is the narrow capability the multi-cam model depends on: **does the render path accept a supplied audio asset (`voice.type:"audio"`) on Avatar V**, so every camera lip-syncs the *same* master audio? The probe renders a 1-line clip with `voice.type:"audio"` and inspects the result's actual rendered model ([[feedback_lookside_vs_outputside_classification]] — truth is the rendered model, not what we asked for). If a given environment can only feed audio-input through a specific surface (e.g. the Shots path vs public `/v2/video/generate`), the probe records which one works in `.engine.json`; the engine is still Avatar V either way.

### Stage A — Angle set (generate-or-reuse) · `common/angle_set.py`
Keyed by `look_id`. Look up the angle-set index first; **reuse if it exists** (we persist angle sets so a look is only ever angled once).
- If missing: call `av-image-n-looks` **angles mode** (`generate_look_pack(mode="angles")`) → one Seedance pass yields ~6 angle frames (`straight_on, close_up, left_45, right_45, full_body, side_view`), ArcFace-gated (≥0.55) + Gemini visual-gated (bald / garbled-text / facial-hair) with the re-roll loop.
- Persist every passing frame as a **`photo_avatar` look in the look's avatar group** ([[feedback_all_image_n_looks_persist_to_avatar]]), mirror to the Image-N look gallery + Notion artifact index, and write the angle-set index entry `{look_id → {slug → avatar_id, frame_path, arcface}}`.
- We generate the **full ~6-angle batch** (it's the same Seedance cost as generating fewer) but a given video **cuts only 3** of them (Adam, 2026-06-08). The extra angles are banked for future videos on this look.

**Why these are renderable:** an Image-N angle frame → HeyGen Photo Avatar → a `photo_avatar` look in the twin group. Because the group has ≥1 video look it's a digital twin, so it renders on Avatar V ([[feedback_identity_preserving_looks_are_digital_twins]] / [[reference_avatar_taxonomy]]). Voice must be the look's cloned voice ([[feedback_voice_matches_avatar]]); for Adam that's `Adam_2025` = `a8452146053043668c9ab1ba5a27650b`.

### Stage C — Master render · `common/multicam.py`
Render the full script on the **base angle** (default `straight_on`) text→TTS with the look's voice → `master.mp4`. Extract the audio (`ffmpeg -vn`) → upload as a HeyGen audio asset → keep the `audio_asset_id`. This audio is the spine of the whole video. (Stage C runs before B because B needs the real spoken timing.)

### Stage B — Cut plan · `common/cut_planner.py` + `common/angle_grammar.py`
1. **Whisper** transcribes the master audio → **word-level timestamps** (`hyperframes-media transcribe`, model `base`/`small`).
2. **Claude** segments the script into cuts using the explicit cinematography ruleset in `angle_grammar.py` (see below) — each cut = `{text_span, angle, rationale}`, capped at **3 distinct angles**.
3. Resolve each cut's `text_span` → `[t_start, t_end]` seconds via the Whisper word timings, **snapping boundaries to pauses / clause ends** (never cut mid-word/mid-clause).
4. Enforce edit constraints (min/max shot length, no same-angle adjacent cuts, meaningful framing change between cuts). Emit `cut_sheet.json`.

### Stage D — Camera renders · `common/multicam.py`
For each non-base angle in the cut sheet, render the **full script** driven by the **master `audio_asset_id`** (`voice.type:"audio"`) on **Avatar V** → `angle_<slug>.mp4`. These are frame-aligned to the master because they lip-sync to identical audio.

### Stage E — Assembly (ffmpeg) · `common/multicam.py`
Per the cut sheet, build the video by hard-cutting between the camera mp4s at the cut windows (`trim`/`concat` on the matching camera's stream for each window), then **lay the single master audio over the entire timeline** (drop every camera's own audio). Output `final.mp4`, **caption-free** ([[feedback_va_demos_caption_free]]). ffmpeg is used ONLY for stitching, never layout ([[feedback_va_hyperframes_for_layout_ffmpeg_for_stitching]]).

### Stage F — Ship
Save `final.mp4` + `cut_sheet.json` + the angle set. Persist a newly-generated angle set to the gallery/Notion. Optionally publish an HV app showing inputs → cut sheet → output.

## The cinematography ruleset (`common/angle_grammar.py`) — the smart mapping

The IP of this skill is *when to cut to what*. Encoded as a script-signal → angle table the LLM planner follows.

**Angle roles**

| Slug | Role | Use for |
|---|---|---|
| `full_body` (wide) | Establish | opener, scene-set, "here's where we are", reset between major topics |
| `straight_on` (medium) | **A-cam / home** | neutral exposition, the default the video lives in (~50–60% of runtime) |
| `close_up` | Emphasis / intimacy | key claim, a number that matters, emotional beat, sincerity, direct appeal, the CTA |
| `left_45` / `right_45` (¾) | Variation / turn | an aside, a shift of thought ("but…", "now…", "here's the thing"), alternating list items |
| `side_view` (profile) | Rare accent | reflective/voiceover-feel only; never for direct address |

**Signal → cut grammar**

| Script signal | Cut to |
|---|---|
| Opening line / establishing context | `full_body` (or a wide `straight_on`) |
| Steady explanation, neutral info | `straight_on` (return here between accents) |
| Point of emphasis, key stat, strong claim | push to `close_up` |
| Emotional / sincere / personal beat | `close_up` |
| Aside, caveat, change of direction | `left_45` or `right_45` (a fresh angle marks the turn) |
| Enumerated list / rapid points | alternate `left_45`↔`right_45`, or `straight_on`↔`close_up` per item |
| Call to action / closing line | `close_up`, optionally pulling back to `straight_on` to breathe |

**Edit constraints (anti-jarring — hard rules the planner must satisfy)**
- **Min shot ≈ 2.0s, max ≈ 7s.** No frantic cutting; no static drone on one angle.
- **Never cut between two shots of the same angle** (that's a jump cut).
- **Adjacent cuts must change framing size meaningfully** — don't cut `straight_on`→`left_45` if both read as "medium"; pair a size change with any angle change for emphasis cuts.
- **Cut on clause/sentence boundaries**, snapped to a Whisper-detected pause — never mid-word, never mid-clause.
- **Exactly 3 distinct angles** per video (one of them the base/master). Pick the 3 that best serve the script's beats; bank the rest.
- **Open deliberately, land deliberately** — the first and last shots are chosen for intent (establish / land the CTA), not by the round-robin.

The module exposes `ANGLE_ROLES`, `SIGNAL_GRAMMAR`, `EDIT_CONSTRAINTS`, and `planner_system_prompt(available_angles)` which renders the ruleset into the system prompt the cut planner sends to Claude. Changing the grammar = editing this one file.

## Inputs

| Input | Source | Required |
|---|---|---|
| `look` | a `look_id` / avatar-group look, OR a look image (+ the group's twin video for identity) | ✅ |
| `script` | the full spoken script | ✅ |
| `voice_id` | the look's cloned voice (defaults to the group's primary; Adam → `Adam_2025`) | recommended |
| `aspect_ratio` | avatar-native by default ([[feedback_aroll_orientation_native.md]]) | optional |

## Run

```
uv run --python 3.11 --with insightface --with onnxruntime --with numpy --with pillow \
  --with google-genai --with httpx --with openai-whisper -- \
  python -m common.orchestrate --look-id <id> --script script.txt --voice <voice_id> --out outputs/run1
```
First do `python scripts/probe_audio_input.py` once to record the engine for this environment (writes `.engine.json`).

## Gotchas / decisions
- **Always reuse the angle set** — never re-angle a look that already has an index entry; angling is the slow/expensive part.
- **All cameras are Avatar V** — never a per-camera engine choice. For a photo-only/virtual look, give the look a video base (IV JIT base motion / cross_ref twin clip) ONCE in Stage A so Avatar V renders; don't fall back to IV as the output engine.
- **Master audio is the only audio** — discard every camera render's own audio in assembly; otherwise tiny per-render TTS differences leak in.
- **Caption-free output** by default; layered text/branding is out of scope (that's a Video Agent composite, not a clean multi-cam).
- **The base angle must be a frontal, high-ArcFace angle** (`straight_on`), since it carries the most runtime and seeds the master audio.

## Distribution
Mirror to GitHub (`adamhalper4/av-multi-angle-video`), the Notion skills index (under the Avatar Studio hub), and publish as the `/av-multi-angle-video` HeyGenverse skill in the `/av-studio` hub. See [[reference_adams_skills_master_index]].

## changelog
- v1 (2026-06-08) — initial build. One-take/N-camera/one-edit model; angle set reuse keyed by look_id; cinematography ruleset; Whisper-timed cut planning; audio-input camera renders with V/IV engine probe; ffmpeg multi-cam assembly.
