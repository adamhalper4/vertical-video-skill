# av-multi-angle-video — Engineering Handoff (V9, 2026-06-17)

One avatar look + a script → a finished talking-head that cuts between camera angles like a
multi-camera studio shoot. This doc is the **current, authoritative spec** (supersedes the V1–V3
parts of `SKILL.md`). Read THIS first; §0–§8 (the V8 core model: one-take/N-cameras/one-edit,
cohesion-gated orbit, cut grammar, assembler) are still accurate and detailed below.

Iteration history + example videos: https://www.heygenverse.com/a/8c06e498-1077-4aa8-baec-d3dae7b215d0

================================================================================
# V9 UPDATE (2026-06-17) — all-Avatar-V, full personality, correct side-angle method, tokyo-bot

## V9.1 Engine matrix — what each shot renders on
| Shot | Engine | How |
|---|---|---|
| HOME (`straight_on`) | **Avatar V** (`/v3/videos`, flat body `engine.type=avatar_v`) | frontal look + full personality |
| close_up | Avatar V (center-crop of HOME, no extra render) | eye-level-aware 1.45× crop |
| side (`left_45`/`right_45`) | **Avatar V** | side-angle look + side-angle reference (V9.3) |

**Full Avatar V "personality" = 3 levers (all on the same render call):**
- `engine.reference_look_id` — reference footage (delivery/motion + gaze). MUST be an **`instant_avatar`** look in the **SAME group** as `avatar_id` (server 400s a `studio_avatar`, a photo look, or a cross-group id). `mv.reference_look_for(tone, group, key)` resolves one.
- `motion_prompt` (top-level) — custom motion (gesture/posture/gaze; NOT camera/scene).
- eleven_v3 voice tags — `voice_settings.engine_settings`+`stability:0.5`; inline `[emotion]` fires as prosody.
- Public MCP `create_video_from_avatar` does motion + v3 tags in one call.

## V9.2 ⭐ The side-angle method (the corrected, working recipe)
**Avatar V renders the `avatar_id` look's framing** (it's a talk-to-camera engine; the reference only
drives delivery/gaze, NOT camera position). Therefore a left/right shot needs **BOTH inputs side-angle**:
- `avatar_id` = a **side-angle PHOTO look** (the appearance/framing IS the side camera), AND
- `engine.reference_look_id` (public) / `avatar_settings.cross_ref_avatar_id` (Shots) = a **side-angle VIDEO reference** where the subject's **eyes face forward, off the lens** (this is what kills eye contact).

Failure modes proven out:
- Frontal photo look + side reference → stays **frontal** (only gaze nudges). ❌
- Side **studio** look on Avatar V → 3/4 but **camera-facing** (studio avatars always address the lens); also can't be a `reference_look_id` (studio rejected). ❌
- Side photo look + side reference, both side-angle → **genuine side camera, eyes forward, Avatar V quality.** ✅
  Validated: https://app.heygen.com/videos/8bb9162d020b4c3182cde0e80eb78cb3

**Headless vs session:** the public `/v3/videos` accepts `avatar_id`=photo look + `engine.reference_look_id`=instant_avatar (same group) with **X-Api-Key** (verified) — so the side render runs on the worker, no session needed. The api2 Shots 2-call path (`text_to_speech.generate` → `/v2/avatar/shortcut/submit` with `avatar_settings.cross_ref_avatar_id`, `model:"tokyo_v2_2"`) is the session-auth alternative.

## V9.3 Producing the two side assets
- **Side PHOTO look (`avatar_id`) — automatable.** Orbit the HOME render → cohesion/identity/artifact gate (§3) with re-roll → a turned 3/4 still in the home's setting → upload as a `photo_avatar` look (`/v2/photo_avatar/avatar_group/add`, X-Api-Key). Orbit is **stochastic** — keep the re-roll budget (a roll can come back frontal). The gate needs insightface+numpy (present on the worker, not a plain local box).
  - **PERSISTED fast path (preferred when available) — `SIDE_LOOK[home_look_id] → side_look_id`.** Because the orbit is stochastic (a run can reject every candidate → no side reveal that render), pre-vet ONE side-angle photo look per **home look** and register it in `SIDE_LOOK` (keyed by `look_id`, NOT group — the side must match that look's exact setting/outfit/lighting). When present and the home is Avatar V, the pipeline renders the side **directly** off the saved look and **skips the orbit + gate entirely** (deterministic, faster, no Seedance cost). Falls back to a fresh orbit if the saved render fails. Build one by running the pipeline once, picking a gated side frame → `create_look_avatar`, eye-verifying, then mapping it here. Eng extends per persona.
- **Side VIDEO reference (`reference_look_id`/`cross_ref`) — one-time per-identity, manual.** Train a real **3/4 off-gaze clip** (eyes forward to someone beside the lens, NOT at it; **must have audio + frontal-enough faces**) as a sibling `instant_avatar` look via `POST api2 /v1/instant_avatar/video.submit` (**session cookies**, not X-Api-Key; `reuse_consent:true`, `gender:male|female|unknown`). ~5-min train, **paid PHOTO_AVATAR_ADD_MOTION**.
  - Gotchas: silent clip → `Missing voices` (mux speech); pure profile → `Missing faces` (use 3/4, not full profile); generated/Seedance profiles often fail the face check — **use real footage**.
  - This step is NOT headless-automatable (session + real footage) → it's a per-identity setup, stored as `identity → side_reference_look_id`.

## V9.4 Frame-alignment (multi-cam cut)
HOME and SIDE must share ONE master audio so cuts are seamless. Generate the TTS **once**, then drive
BOTH renders off that same `audio_data` (Shots `audio_type:"tts"` with the same `audio_url`+`words`), or
use the audio-input path. Never stitch audio fragments.

## V9.5 tokyo-bot integration (shipped)
- Command: `@tokyo-bot make multiangle video <group_or_look_id> [16:9|9:16|landscape|portrait] | <script>` (or `auto`). A look id resolves to its group via `/v2/photo_avatar/{id}`.
- Files (bot repo `~/tokyo-bot`): `video_skills/multiangle.py` (orchestration), `multiangle_orbit.py` (V8 gate), `multiangle_cut/` (cut grammar), `mv_jobs.py` (queue).
- **Voice = the avatar's OWN voice** (look `default_voice_id` → group default → Adam_2025 only for a twin). Never a stranger's face + Adam's voice.
- **Server-key fallback:** if the requested avatar isn't on the user's connected key, render on the server account (enables shared/public avatars).
- **Queue + worker (deploy-race fix):** `RENDER_QUEUE=1` → bot enqueues to the `tokyo_jobs` Google-Sheet queue; separate **`tokyo-worker`** Railway service (same image, `RENDER_WORKER_MODE=1`, no Slack socket) drains it → renders survive bot redeploys. Concurrency-capped; re-claims orphaned jobs. `mv_jobs.py` + `slack_bot.py:_run_render_worker`.

## V9.6 Status / TODO
- ✅ **SHIPPED (tokyo-bot):** real-human avatar (home rendered on Avatar V) ⇒ the side is **Avatar V** too — the gated side-profile still becomes the side-angle PHOTO look (`avatar_id`), an optional same-group off-gaze VIDEO look (`SIDE_REFERENCE[group_id]` → `engine.reference_look_id`) drives forward gaze, driven by the master `audio_url` (frame-aligned). `render_side_avatar_v()` in `multiangle.py` (public `/v3/videos`, X-Api-Key). Avatar IV (Seedance-still) is the fallback ONLY for photo-only groups with no video look.
- ✅ **FIXED:** min-cuts floor (`_min_cuts`, ≥3) so calm/long pacing no longer collapses to 1 shot.
- ✅ **SHIPPED — persisted side look (`SIDE_LOOK`):** removes the stochastic-orbit dependency on the hot path. A pre-vetted side look per home look renders the side directly (skip orbit/gate), falling back to the orbit if absent/failed. Seeded with `b566f1d0…` (AdamH Exec Office, landscape) → `df7bd41b…`. Extend the map per persona to make every persona's side deterministic.
- **Per-identity setup:** the side VIDEO reference (`SIDE_REFERENCE` entry) is created once from real 3/4 off-gaze footage (V9.3) — eng extends the map per identity. Without it, the side still renders on Avatar V (gated side-profile photo look) but gaze control is weaker.
- Clean up failed `instant_avatar` training looks (`Missing voices/faces`) left in the group.

================================================================================
--------------------------------------------------------------------------------
## 0. The core model — one take, N cameras, one edit
A real multi-cam shoot records ONE vocal performance with several cameras rolling, then the editor
cuts between angles. We reproduce that exactly so the audio is seamless across every cut:

1. **HOME camera** renders the full script (text→TTS) → `home.mp4`; extract its audio → `master`.
2. **Every other camera** (close-up, side profile) renders the *full script too*, but **driven by
   the master audio** (audio-input, not TTS), so each is the SAME take frame-for-frame.
3. **The edit** (ffmpeg) picks which camera is live per the cut sheet and lays the ONE master audio
   over everything. Never slice/stitch audio fragments — a shared continuous take is what makes
   cuts seamless.

The three camera sources we actually use:
- **HOME** (`straight_on`): the rendered home video. Spine of the edit.
- **close_up** (ZOOM): a **center-crop of the HOME video** (no extra render) — see §4 assembler.
- **side profile** (`right_45`): a genuine 70–90° profile, separately rendered (see §2–§3).

--------------------------------------------------------------------------------
## 1. Pipeline at a glance
```
look + script
 ├─[A] HOME render        create_video_from_avatar(text, voice)  → home.mp4 ; extract master audio + seed frame
 ├─[B] SIDE profile look  Seedance orbit of the seed frame → cohesion/identity/artifact-gated frame-pick
 │                         → register as photo_avatar (create_photo_avatar)
 ├─[C] SIDE render        create_video_from_avatar(audio=master, motion=documentary)  → side.mp4
 ├─[D] cut sheet          cut_planner.build_cut_sheet(script, master_audio, [straight_on,close_up,right_45])
 └─[E] assemble           profile_assemble[_ls].py  → final.mp4  (home + close-up crop + side, one master audio)
```
Orientation rule (V6): pick the look's native orientation and use it for the WHOLE chain (home AR,
orbit `aspect_ratio`, side render AR, assembler). Landscape-native look ⇒ everything 16:9. Never mix.

--------------------------------------------------------------------------------
## 2. Held-profile RENDER recipe (V4 — the breakthrough)
A side angle must be a genuine 70–90° profile that HOLDS while talking. Recipe:
- **Engine = Avatar IV, never Avatar V** for the profile. Avatar V re-centers the head to the lens
  and collapses any profile to frontal.
- **Non-twin photo-avatar group** — a digital-twin group carries a frontal prior that relaxes the
  angle; a standalone photo-avatar group holds a stronger profile.
- **Motion prompt (verbatim):** "Fixed locked-off side camera, observational documentary. The
  subject is completely unaware of this camera and never turns toward it or looks at this lens. Keep
  the head and body in the same near-profile orientation facing forward for the entire clip — do not
  rotate toward the camera, no re-centering. Only natural small movements while speaking."
  (This documentary "subject-unaware" register beat both an "address the interviewer" framing and a
  mechanical "stay at 80°" instruction.)
- **expressiveness = low**; **audio-input** = the master audio (frame-aligned with HOME).
- HOME camera: Avatar V for a digital twin (best frontal), Avatar IV for a virtual/public avatar.
- Profile renders draw on the Avatar IV monthly quota (also gates Avatar V).

--------------------------------------------------------------------------------
## 3. Cohesion-aware orbit + frame-pick  (`common/orbit_cohesion.py`)
How we get the profile LOOK (a still to register as the side photo-avatar): orbit the HOME seed
frame with Seedance, then pick the best frame. Gates, in order:
1. **Continuity prompt** (key for cohesion): "...this is the SAME room seen from the side — the same
   wall, the same furniture and objects behind them stay VISIBLE and CONTINUOUS in this side view.
   Do NOT invent or replace the background..." + "...no posters / framed photos / portraits / any
   duplicate of the person on the wall..." Orbit target ~75° (66–82° band; lower = more overlap).
2. **Gemini gate per frame:** reject if `looking_at_this_camera`, not `recognizable`, turn∉[66,82],
   or any of `face_on_wall_or_poster` / `extra_person` / `warped_background` (the David eye-on-wall
   artifact class).
3. **insightface (buffalo_l)** second-face check — reject a frame with a sizable 2nd face.
4. **Score** the survivors: `0.6·background_overlap + 0.4·ArcFace_vs_home`, where overlap is a Gemini
   0–1 "is this the same room continued?" score and ArcFace is identity vs the home frame.
5. Budget **1–2 re-rolls**; stop when overlap≥0.45 and ArcFace≥0.5. ArcFace under-rates profiles
   (caps ~0.55–0.75) — judge by eye/scene-consistency, not the number. **Setting choice dominates:**
   bookshelf / shelves / string-lights / windows continue into the side view and cohere; plain or
   empty walls (e.g. a bare conference room) give nothing to overlap and read as a different room.

Seed image + audio must be a **public URL** (Seedance/ARK + create_photo_avatar fetch server-side).

--------------------------------------------------------------------------------
## 4. Cut grammar — director logic  (`common/cut_planner.py` + `common/angle_grammar.py`)
`build_cut_sheet(script, master_audio, available_angles, pace="auto")` runs:
1. `transcribe_words` — Whisper word timestamps off the master audio.
2. `plan_cuts` — Gemini segments the SCRIPT by **intent** (clarity→home, emphasis→close_up,
   contrast/aside→side, reset→home), governed by `planner_system_prompt`. NOT mechanical fractions.
3. `resolve_timings` — align each cut's text span to the words; **snap boundaries to pauses** (never
   mid-word).
4. `enforce_constraints` — repair to `EDIT_CONSTRAINTS`: min/max shot length, HOME is the spine,
   return-home-after-excursion.
5. `bridge_side_closeups` — **MASKING CUT**: never enter a side angle straight from a wide shot;
   insert a ~1.8s close-up bridge so it's `HOME → close_up → side`. The close-up is a crop of the
   SAME home footage (seamless punch-in); cutting to the side while tight removes the viewer's wide
   background reference so the side's regenerated background can't be compared → the 2nd-camera
   illusion holds. `close_up→side` is the ONE allowed excursion→excursion adjacency.

**Duration-scaled pacing (V8): the longer the video, the fewer the cuts.** `pace="auto"` picks a
tier from the master-audio duration; min/avg/max shot all scale up (the **min-shot bump** is the real
lever — it forces short shots to merge). From `EDIT_CONSTRAINTS`:

| tier | duration | min / avg / max shot (s) |
|------|----------|--------------------------|
| social    | <16s   | 2.0 / 3.0 / 6  |
| balanced  | 16–30s | 2.5 / 4.5 / 8  |
| explainer | 30–50s | 4.0 / 6.5 / 11 |
| calm      | >50s   | 5.5 / 9.0 / 15 |

Cut-sheet schema (the contract the assembler consumes), contiguous + gap-free:
`[{idx, text, angle ∈ {straight_on,close_up,right_45}, t_start, t_end, rationale}]`.

--------------------------------------------------------------------------------
## 5. Assembly  (`profile_assemble.py` portrait 720×1280 · `profile_assemble_ls.py` landscape 1280×720)
`profile_assemble.py <cut.json> <home.mp4> <left.mp4> <right.mp4> <master_audio> <out.mp4>`
- `straight_on` → home clip; `close_up` → center punch-in on the home clip (≈1.45×); `right_45` →
  the side clip; (`left_45` slot unused in the current 1-side pipeline — pass home as a placeholder).
- Cuts segments per the sheet, concats, muxes the ONE master audio (`-c:a aac -shortest`).
- Pick the assembler that matches the look's orientation (§1 orientation rule).
- **Eye-level close-up (don't blind-center the punch-in).** The `close_up` crop is positioned from
  the subject's detected nose-x / eye-y (insightface kps on a HOME frame) so the eyes stay at the
  SAME screen height through the `HOME→ZOOM` cut — a blind center crop drops the eyeline and the eyes
  visibly jump on the cut. Fallback when no face: (0.5, 0.42) = centered, eyes on the upper third.

--------------------------------------------------------------------------------
## 6. Dependencies / external calls
- **HeyGen API** — `create_video_from_avatar` (text→TTS for HOME; **audio-input** for SIDE; params:
  `aspectRatio`, `fit:"cover"`, `expressiveness`, `motionPrompt`, `engine`), `create_photo_avatar`
  (register the side look from a public image URL). Public **photo** avatars render on Avatar IV;
  public **studio** presets do NOT support IV/V API — use photo avatars.
- **Seedance / ARK** (`generate_scene_video` in [[av-image-n-looks]] `common/image_n.py`) — the orbit.
  Needs VOLC_ACCESSKEY / VOLC_SECRETKEY / ARK_API_KEY / SEEDANCE_AES_KEY (Infisical /heygen-server).
- **Gemini 2.5 Pro** — cut planning + orbit frame gating/cohesion scoring (GEMINI_API_KEY).
- **openai-whisper** (`base`) — word timings for cut boundaries.
- **insightface** (`buffalo_l`) — ArcFace identity + 2nd-face check.
- **ffmpeg** — extraction, crop, concat, mux.
- Public hosting for seed frame / master audio / profile frame (any public URL works).

--------------------------------------------------------------------------------
## 7. File map (this skill dir)
- `common/cut_planner.py`  — build_cut_sheet, plan_cuts, resolve_timings, enforce_constraints,
  bridge_side_closeups, pace_for_duration  *(current, V8)*
- `common/angle_grammar.py` — ANGLE_ROLES, SIGNAL_GRAMMAR, EDIT_CONSTRAINTS (pace tiers + masking
  rule), planner_system_prompt  *(current, V8)*
- `common/orbit_cohesion.py` — cohesion/identity/artifact-gated orbit frame-pick  *(V8, copied from
  the batch driver — parameterize the hardcoded paths/manifest for production)*
- `profile_assemble.py` / `profile_assemble_ls.py` — ffmpeg assembler (portrait / landscape)
- `common/orbit_angles.py`, `common/multicam.py`, `common/angle_set.py`, `common/orchestrate.py` —
  earlier pipeline modules (orbit + assembly + orchestration scaffolding)
- `SKILL.md` — original methodology (one-take model, angle sets). `INSTALL.md`, `README.md`.

## 8b. Recommended steps (these are what actually made it good — don't skip)
1. **Gate every generated angle frame BEFORE proceeding, with ≤1 retry.** Generation is stochastic;
   one bad frame poisons everything downstream (the side look, every render, the final cut). After
   the orbit, validate the picked frame with the vision+identity gates (§3): Gemini for
   turn/gaze/artifacts, insightface ArcFace for identity, Gemini overlap for cohesion. If it fails,
   **re-roll once**; if it still fails, stop and surface it rather than shipping a bad look. This one
   gate is what killed the David eye-on-wall artifact and the "doesn't look like me" side angles.
   (Same principle applies to the HOME frame before you orbit it.)
2. **Ship 3 director-cut variations per request, for free.** Every angle already renders the FULL
   clip and the master audio is shared, so producing alternates is just re-running the cut sheet +
   ffmpeg — **no re-render of any audio or video** (cents of LLM + ffmpeg vs. dollars + minutes of
   avatar render). Generate three and let the user pick:
   - **Energetic** — `pace="social"` (more, shorter cuts; more close-up emphasis).
   - **Standard** — `pace="auto"`/`"balanced"` (the default director rhythm).
   - **Minimal** — `pace="calm"` (few cuts, long holds; ~1 side excursion).
   All three reuse `home.mp4` / `side.mp4` / `master audio`; only `build_cut_sheet(pace=...)` +
   `profile_assemble*` change. Make this the default UX, not an extra.

## 8. What to harden for production (the scratch orchestration was per-batch)
- The orbit/frame-pick script uses hardcoded local paths + a per-batch `manifest.json`. Turn it into
  a function: `(seed_image_url, home_frame, orientation) → profile_image_url`.
- Frame-pick is **stochastic** — keep the 1–2 re-roll budget; log when it can't clear the gates.
- The LLM planner sometimes returns **no side angle** on mid/long clips (chooses only close-ups). If
  the side is required for the use case, force one (see `ensure_side` helper) or re-prompt.
- Identity ceiling: generated profiles cap ~0.55–0.75 ArcFace; reference/look quality is the lever,
  not more orbit attempts.
