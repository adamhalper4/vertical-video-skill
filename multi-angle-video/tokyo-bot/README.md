# tokyo-bot — production multi-angle implementation

The live `make multiangle video` Slack skill (Avatar-V multi-angle). Mirror of the files in
the tokyo-bot repo (`~/tokyo-bot`). See `../ENGINEERING_HANDOFF.md` (V9) for the full spec.

- **multiangle.py** — orchestration + `handle_command` (the `make multiangle video <group_or_look_id> [16:9|9:16|landscape|portrait] | <script>` dispatcher). Real-human avatar (group has a video look) ⇒ every shot on **Avatar V**: home/close-up (frontal Image-N look + reference footage + motion + eleven_v3 voice tags) and **side** = a side-angle Image-N look (`avatar_id`) + side-angle off-gaze video reference (`SIDE_REFERENCE` → `engine.reference_look_id`), driven by the master audio. Avatar IV is the fallback only for photo-only groups. Contains `render_side_avatar_v`, `cut_sheet`, `assemble`, `_min_cuts`, `_ensure_side`, `SIDE_REFERENCE`.
- **multiangle_orbit.py** — `pick_side_frame(...)`: cohesion/identity/artifact gate on the orbit (Gemini turn/gaze/artifacts + ArcFace identity + Gemini room-overlap), ≤1 re-roll.
- **multiangle_cut/** — director cut grammar (`cut_planner`, `angle_grammar`): LLM intent plan → pause-snap → 2.5–8s shots, home spine, close-up masking bridge.
- **mv_jobs.py** — durable Sheet-backed job queue (`tokyo_jobs`): enqueue/claim/heartbeat/complete/fail/reclaim_stale.
- **worker.py** — local worker entrypoint. In production the **tokyo-worker** Railway service runs the same image with `RENDER_WORKER_MODE=1`; the loop is `slack_bot.py:_run_render_worker()` (not mirrored here — it's in the bot's 5.7k-line `slack_bot.py`, along with the dispatch branch, `ma.register(...)`, and `_mv_enqueue_render`). With `RENDER_QUEUE=1` the bot enqueues; the worker drains the queue so renders survive bot redeploys.

Engine/personality + the api2 cross-ref upload pipeline (for minting per-identity side reference looks from real off-gaze footage) are documented in `../ENGINEERING_HANDOFF.md` §V9.
