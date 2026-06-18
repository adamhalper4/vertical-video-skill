# Install — av-multi-angle-video

A Claude Code skill: one avatar look + a script → a finished multi-camera talking-head with
intent-driven angle cuts. See `SKILL.md` for the full methodology.

## Install
Unzip into your Claude Code skills directory:
```
~/.claude/skills/av-multi-angle-video/
```
(Then it's available as `/av-multi-angle-video`, or auto-invoked on "multi-angle video" / "multi-cam".)

## Layout
```
av-multi-angle-video/
├── SKILL.md                  # methodology, stages, the angle grammar, gotchas
├── README.md                 # quick start
├── INSTALL.md                # this file
├── common/
│   ├── angle_grammar.py      # intent-driven cinematography ruleset + planner prompt
│   ├── cut_planner.py        # Whisper word-timing + LLM segmentation → timed cut sheet
│   ├── orbit_angles.py       # background-consistent ¾ generation (camera orbit + 45° frame-pick)
│   ├── angle_set.py          # generate-or-reuse angle looks (index keyed by look_id)
│   ├── multicam.py           # master + audio-input camera renders + ffmpeg multi-cam assembly
│   └── orchestrate.py        # end-to-end driver (stages A–F)
└── scripts/
    └── probe_audio_input.py  # one-time: confirm Avatar V audio-input in this env
```

## Dependencies

**Sibling skill:** [`av-image-n-looks`](../av-image-n-looks) — provides the Seedance primitives
(`common/image_n.py`) that `orbit_angles.py` and `angle_set.py` import. Install it alongside this skill.

**Python (run under `uv`, Python 3.11 — onnxruntime has no 3.14 wheels):**
```
uv run --python 3.11 \
  --with insightface --with onnxruntime --with numpy --with pillow \
  --with google-genai --with httpx --with openai-whisper --with pycryptodome -- \
  python -m common.orchestrate ...
```

**System:** `ffmpeg` + `ffprobe` on PATH.

**Credentials:**
| Need | Used for | Source |
|---|---|---|
| HeyGen renders | master + camera renders, photo-avatar creation | HeyGen MCP (`create_video_from_avatar`, `create_photo_avatar`, asset upload) **or** `HEYGEN_API_KEY` for `/v2/video/generate` |
| `GEMINI_API_KEY` | cut planning + the orbit 45°-frame picker + visual gates | your Gemini key |
| `VOLC_ACCESSKEY` / `VOLC_SECRETKEY` / `ARK_API_KEY` / `SEEDANCE_AES_KEY` | Seedance angle + camera-orbit generation | infisical `/heygen-server` (dev), or your ARK/VolcEngine creds |

## Run
```
# 0) once per environment — confirm audio-input renders
python scripts/probe_audio_input.py --avatar-id <look_id> --voice <voice_id>

# end-to-end
uv run --python 3.11 --with insightface --with onnxruntime --with numpy --with pillow \
  --with google-genai --with httpx --with openai-whisper --with pycryptodome -- \
  python -m common.orchestrate --look-id <id> --script script.txt --voice <voice_id> \
    --group-id <gid> --look-image-url <url> --reference-video-url <url> \
    --reference-image ref.jpg --out outputs/run1
```

## Engine notes (learned in production)
- All cameras render on **Avatar V** for digital-twin looks (a look in a group with a real uploaded
  video). Fresh **virtual** photo avatars (no twin video) are **Avatar IV–only** via the public API —
  IV at low expressiveness holds the off-camera ¾ gaze well and is a fine fallback.
- **close_up** is a digital **zoom on the home shot** (not a separate render) — guaranteed identity match.
- **Side angles use the camera-orbit method** (`orbit_angles.py`): orbit off the home frame so the
  background is the SAME room from a rotated camera (correct parallax), then frame-pick the ~45°
  three-quarter. This replaced independent-still ¾ generation, which invented mismatched backgrounds.
- Avatar V's "Gaze → Looking ahead" preset (`"looking straight ahead, without turning the eyes toward
  the camera"`) helps hold gaze off-lens; fully consistent gaze additionally wants "More expressive" OFF
  (a Shots-UI lever, not exposed on the public API).
