# av-multi-angle-video

One avatar look + a script → a finished **multi-camera talking-head** video with professional angle cuts. No editing.

The model is one continuous voice take with cameras switched on top of it (one mic, N cameras, one edit): render the script once on the base angle, drive every other angle render with that **same master audio** (`voice.type:"audio"`), then hard-cut between the frame-aligned cameras per a script-derived cut sheet. Seamless voice, real multi-cam feel.

See `SKILL.md` for the full methodology + the cinematography ruleset.

## Layout
```
av-multi-angle-video/
├── SKILL.md                     # methodology, stages, the angle grammar, gotchas
├── README.md
├── angle_sets.json              # index of generated angle sets, keyed by look_id (created on first run)
├── .engine.json                 # written by the probe: audio-input capability for this env
├── common/
│   ├── angle_grammar.py         # cinematography ruleset (roles + signal→angle + edit constraints + planner prompt)
│   ├── cut_planner.py           # Whisper word-timing + LLM segmentation → timed cut sheet
│   ├── angle_set.py             # generate-or-reuse angle looks (wraps av-image-n-looks angles mode) + index
│   ├── multicam.py              # master + audio-input camera renders + ffmpeg multi-cam assembly
│   └── orchestrate.py           # end-to-end driver (stages A–F)
└── scripts/
    └── probe_audio_input.py     # one-time: confirm Avatar V audio-input works in this env
```

## Quick start
```bash
# 0) once per environment — confirm audio-input renders on Avatar V
HEYGEN_API_KEY=... python scripts/probe_audio_input.py --avatar-id <look> --voice <voice_id>

# end-to-end (first run on a look generates + checkpoints for angle registration; later runs are unattended)
uv run --python 3.11 --with insightface --with onnxruntime --with numpy --with pillow \
  --with google-genai --with httpx --with openai-whisper -- \
  python -m common.orchestrate --look-id <id> --script script.txt --voice <voice_id> \
    --group-id <gid> --look-image-url <url> --reference-video-url <url> \
    --reference-image ref.jpg --out outputs/run1
```

## Dependencies
- **av-image-n-looks** (sibling skill) — angles-mode look generation + ArcFace/Gemini gates.
- `HEYGEN_API_KEY` (renders + audio asset), `GEMINI_API_KEY` (cut planning), `ffmpeg`/`ffprobe`, `openai-whisper`.

Engine is **always Avatar V**. Distribution: GitHub `adamhalper4/av-multi-angle-video` + Notion skills index + `/av-multi-angle-video` HeyGenverse skill.
