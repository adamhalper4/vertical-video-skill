"""
make multiangle video — Slack command for the tokyo-bot.

User flow:  `@tokyo-bot make multiangle video`
  → pick an avatar IDENTITY (avatar group)
  → optionally pick a LOOK (else auto: first look in the group)
  → optionally type a SCRIPT (else a generic one is generated)
  → bot renders a multi-camera talking-head and posts 3 director-cut variations in-thread.

Pipeline (see av-multi-angle-video ENGINEERING_HANDOFF.md for the full rationale):
  1. HOME render — render the look on the script (text→TTS), caption-free → home.mp4; extract the
     master audio + a seed frame.
  2. SIDE profile look — generate_look() orbits the HOME render to a held ~75° side profile in the
     SAME identity group (ArcFace-gated frame pick), registered as a photo-avatar look.
  3. SIDE render — render the side look DRIVEN BY THE MASTER AUDIO (audio-input) + a documentary
     "subject-unaware" motion prompt, so it's frame-aligned with HOME and holds the profile.
  4. Cut sheets — director grammar (intent plan → proportional timing → shot-length bounds →
     masking close-up before every side). Three pacings → 3 variations, NO re-render.
  5. Assemble — ffmpeg cuts HOME / close-up(crop of HOME) / SIDE per the sheet over the one master
     audio; upload each variation to the Slack thread.

Self-contained: only needs the helpers injected via register(). The cut grammar is vendored in
video_skills/multiangle_cut/ (Railway has no ~/.claude/skills).
"""
from __future__ import annotations
import os, re, json, time, tempfile, subprocess, threading, logging, urllib.request, urllib.error
from pathlib import Path
import httpx

from . import make_video as mv
from .lookgen import generate as lookgen_gen
from .lookgen import personality as perslib
from . import multiangle_orbit as mo
from .multiangle_cut import cut_planner as cp
from .multiangle_cut import angle_grammar as ag

log = logging.getLogger("tokyo-bot.multiangle")

# helpers injected by register(app, helpers): connected_key(user)->key|None, get_avatar(user)->look_id|None
_H: dict = {}

# Adam's chosen voice clone (Adam_2025). The avatar looks' default voices are unreliable (some are a
# fast British voice that reads as "2x speed") — drive the home TTS with this so the take is correct.
ADAM_VOICE = "a8452146053043668c9ab1ba5a27650b"

ANGLES = ["straight_on", "close_up", "right_45"]
# (label, pace) — three director cuts from the SAME render. "auto" scales pace to duration.
VARIATIONS = [("Energetic", "social"), ("Standard", "auto"), ("Minimal", "calm")]

DOC_MOTION = ("Fixed locked-off side camera, observational documentary. The subject is completely "
              "unaware of this camera and never turns toward it or looks at this lens. Keep the head "
              "and body in the same near-profile orientation facing forward for the entire clip — do "
              "not rotate toward the camera, no re-centering. Only natural small movements while speaking.")

# Avatar V re-centers a profile toward the lens unless the motion prompt is explicit about gaze.
# This strong eyes-forward prompt + the side reference_look_id holds the look-away on /v3/videos
# (headless, verified) — no session cross_ref needed. Used for the Avatar V side render.
SIDE_MOTION = ("The person stays in a three-quarter side profile, talking to someone seated to their "
               "side; they look FORWARD toward that person and NEVER turn toward or look at this camera; "
               "the head stays turned away and the eyes stay off this lens for the entire clip. "
               "Only natural small movements while speaking — do not rotate to face the camera.")

# Avatar V "full personality" for the HOME/close-up shot: a delivery tone (→ reference-footage
# look via mv.reference_look_for) + a natural-presenter motion prompt. eleven_v3 voice tags are
# on by default in render_avatar_v.
HOME_TONE = "confident"
HOME_MOTION = ("Engaged, natural presenter delivery to camera — relaxed upright posture, easy "
               "shoulders, occasional subtle hand gestures that match the emphasis; warm and clear.")

# Per-identity side-angle VIDEO reference (instant_avatar look, SAME group) used as the Avatar V
# side-shot reference_look_id to drive a forward, off-lens gaze. Created once from real 3/4
# off-gaze footage (see ENGINEERING_HANDOFF V9.3). Optional — the gated side-profile photo look
# already carries the side framing; the reference just firms up the gaze. Eng extends this map.
SIDE_REFERENCE = {
    "f1c500db5d1546389b284f098d4e51ff": "8d92ce67719b4f24a0a922238f8171c5",  # Adam (ahalps) twin
}

# Pre-vetted, PERSISTED side-angle photo look per HOME look — when present, the pipeline skips
# the stochastic Seedance orbit + frame-gate entirely (the side no longer depends on a fresh
# orbit clearing the gate every render). Key = the HOME look_id, because the side must match THAT
# look's exact setting/outfit/lighting (a different home look needs its own side look). The value
# is a side-angle (~75°, eyes-forward) photo_avatar in the SAME group, renderable on Avatar V.
# How to add one: render the home look, orbit once, pick a gated side frame → register it as a
# photo_avatar (lookgen_gen.create_look_avatar), verify by eye, then map home_look_id -> side_look_id
# here. Eng extends this map per persona. Falls back to a fresh orbit if the saved render fails.
SIDE_LOOK = {
    "b566f1d0c57942bab012514843ff48ac": "df7bd41b1fd146db99bf3a7053e6c589",  # AdamH ImageN — landscape Exec Office
}

SIDE_CONTEXT = ("8 seconds. Fly-on-the-wall observational documentary. The SAME person seated in the "
                "SAME room, filmed from a second camera about 75 degrees to their side (near-profile): "
                "one eye and the nose stay visible, never the back of the head. They speak to someone in "
                "front of them and are UNAWARE of this side camera; they never turn toward it. CRITICAL: "
                "the same wall, furniture and objects behind them stay VISIBLE and CONTINUOUS — the same "
                "room seen from the side, do NOT invent or replace the background. No posters, framed "
                "photos, portraits or any duplicate of the person on the wall. Same outfit, medium shot.")

HEY = "https://api.heygen.com"


# ─────────────────────────────────────────────────────────────────────────────
# HeyGen REST helpers (urllib, per-user X-Api-Key — mirrors make_video.py)
# ─────────────────────────────────────────────────────────────────────────────
def _get(url, key, timeout=30):
    req = urllib.request.Request(url, headers={"X-Api-Key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def list_groups(key):
    """[{id, name, image}] of the user's avatar groups (identities) — REAL names + a thumbnail."""
    d = _get(f"{HEY}/v2/avatar_group.list", key)
    groups = (d.get("data") or {}).get("avatar_group_list") or []
    out = []
    for g in groups:
        gid = g.get("id")
        if not gid:
            continue
        out.append({"id": gid, "name": g.get("name") or "Avatar",
                    "image": g.get("preview_image_url") or g.get("preview_image")})
    return out[:50]


def list_looks(gid, key):
    """[{id, name, image, type}] within a group — REAL look names + thumbnails.
    Never raises (some instant-avatar twin groups 404 this sub-endpoint)."""
    try:
        d = _get(f"{HEY}/v2/avatar_group/{gid}/avatars", key)
    except Exception as e:
        log.info("list_looks(%s) failed: %s", gid, e)
        return []
    looks = (d.get("data") or {}).get("avatar_list") or []
    out = []
    for a in looks:
        lid = a.get("id")
        if not lid:
            continue
        out.append({"id": lid, "name": a.get("name") or "Look",
                    "image": a.get("image_url") or a.get("preview_image_url"),
                    "type": (a.get("avatar_type") or a.get("type") or "").lower(),
                    "voice": a.get("default_voice_id")})
    return out[:50]


def _resolve_look_group(look_id, key):
    """If `look_id` is a photo-avatar LOOK, return its group_id (so a look id passed to the
    command resolves to the group it lives in). None if it isn't a look on this key."""
    try:
        d = _get(f"{HEY}/v2/photo_avatar/{look_id}", key)
        return (d.get("data") or {}).get("group_id")
    except Exception:
        return None


def _group_voice(gid, key):
    """The group's default voice id — fallback when a look has no default voice."""
    try:
        d = _get(f"{HEY}/v2/avatar_group.list", key)
        for g in (d.get("data") or {}).get("avatar_group_list") or []:
            if g.get("id") == gid:
                return g.get("default_voice_id")
    except Exception:
        pass
    return None


def _upload_talking_photo(image_path, key):
    """Upload a still as a STANDALONE talking_photo the key owns → talking_photo_id.
    Used for the gated side-profile frame. Unlike /v2/photo_avatar/avatar_group/add (which
    404s on a group the account doesn't own — e.g. public/shared avatars like the report
    personas), this works for public AND owned avatars since the new talking_photo is the
    caller's own resource."""
    with open(image_path, "rb") as f:
        data = f.read()
    req = urllib.request.Request("https://upload.heygen.com/v1/talking_photo", data=data,
                                 headers={"X-Api-Key": key, "Content-Type": "image/jpeg"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    return (d.get("data") or {}).get("talking_photo_id") or (d.get("data") or {}).get("id")


def render_audio_input(look_id, audio_url, key, orientation="portrait", motion_prompt=None):
    """Render a photo-avatar look DRIVEN BY an existing audio track (audio-input), so it's
    frame-aligned with the home take. Returns video_id or None.
    NOTE (verify on first live render): photo-avatar `motion_prompt` on /v2/video/generate is the
    field the MCP exposed for the held-profile recipe; if HeyGen rejects it for a given account the
    call retries without it (the profile may relax slightly)."""
    dim = {"width": 720, "height": 1280} if orientation == "portrait" else {"width": 1280, "height": 720}
    char = {"type": "talking_photo", "talking_photo_id": look_id}
    if motion_prompt:
        char["motion_prompt"] = motion_prompt
    body = {"video_inputs": [{"character": char,
                              "voice": {"type": "audio", "audio_url": audio_url}}],
            "dimension": dim}

    def _post(payload):
        req = urllib.request.Request(f"{HEY}/v2/video/generate", data=json.dumps(payload).encode(),
                                     headers={"X-Api-Key": key, "Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
        return (d.get("data") or {}).get("video_id") or d.get("video_id")

    try:
        return _post(body)
    except urllib.error.HTTPError as he:
        msg = he.read().decode("utf-8", "replace")[:300]
        log.warning("audio-input render %d: %s — retrying without motion_prompt", he.code, msg)
        char.pop("motion_prompt", None)
        try:
            return _post(body)
        except Exception as e:
            log.error("audio-input render failed: %s", e)
            return None
    except Exception as e:
        log.error("audio-input render failed: %s", e)
        return None


def render_side_avatar_v(look_id, audio_url, key, orientation="landscape",
                         reference_look_id=None, motion_prompt=None):
    """Render the side-angle PHOTO look on **Avatar V** driven by the master `audio_url`
    (so it's frame-aligned with the home take), with an optional same-group side-angle VIDEO
    `reference_look_id` to drive a forward, off-lens gaze. Public /v3/videos accepts a top-level
    audio_url + engine.reference_look_id with X-Api-Key (no session needed). Returns video_id."""
    engine = {"type": "avatar_v"}
    if reference_look_id:
        engine["reference_look_id"] = reference_look_id
    body = {"type": "avatar", "avatar_id": look_id, "engine": engine,
            "audio_url": audio_url, "aspect_ratio": "16:9" if orientation == "landscape" else "9:16"}
    if motion_prompt:
        body["motion_prompt"] = motion_prompt[:500]

    def _post(b):
        req = urllib.request.Request(f"{HEY}/v3/videos", data=json.dumps(b).encode(),
                                     headers={"X-Api-Key": key, "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
        return (d.get("data") or {}).get("video_id") or d.get("video_id")
    try:
        return _post(body)
    except urllib.error.HTTPError as he:
        msg = he.read().decode("utf-8", "replace")[:300]
        log.warning("side avatar_v render %d: %s — retrying without reference_look_id", he.code, msg)
        body["engine"] = {"type": "avatar_v"}
        try:
            return _post(body)
        except Exception as e:
            log.error("side avatar_v render failed: %s", e); return None
    except Exception as e:
        log.error("side avatar_v render failed: %s", e); return None


# ─────────────────────────────────────────────────────────────────────────────
# Block Kit — identity → look → script
# ─────────────────────────────────────────────────────────────────────────────
def _upload_thumb(client, channel, thread_ts, image_url, title):
    """Download a HeyGen thumbnail and re-upload it to Slack (Slack can't fetch HeyGen's signed
    preview URLs directly — they fail Block-Kit image blocks). Returns True if shown."""
    if not image_url:
        return False
    try:
        with httpx.Client(timeout=40.0, follow_redirects=True) as c:
            r = c.get(image_url)
        if r.status_code != 200 or not r.content:
            return False
        ext = ".png" if "png" in r.headers.get("content-type", "").lower() else ".jpg"
        fp = Path(tempfile.mkdtemp()) / ("thumb" + ext)
        fp.write_bytes(r.content)
        client.files_upload_v2(channel=channel, thread_ts=thread_ts, file=str(fp),
                               filename="thumb" + ext, title=title[:100])
        return True
    except Exception as e:
        log.info("thumb upload failed: %s", e)
        return False


def post_chooser(client, channel, thread_ts, header, items, action_id, auto_value=None, cap=8):
    """Visual chooser that actually works in Slack: upload each item's thumbnail (Slack-hosted) as a
    numbered gallery, then a dropdown of the REAL names to pick. `items` = [{name, image, value}]."""
    opts = []
    if auto_value:
        opts.append({"text": {"type": "plain_text", "text": "Auto — pick the best look for me"}, "value": auto_value})
    for i, it in enumerate(items[:cap], 1):
        label = f"{i}. {it['name']}"[:74]
        _upload_thumb(client, channel, thread_ts, it.get("image"), label)
        opts.append({"text": {"type": "plain_text", "text": label}, "value": it["value"]})
    extra = f" (showing {cap} of {len(items)})" if len(items) > cap else ""
    client.chat_postMessage(
        channel=channel, thread_ts=thread_ts, text=header,
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": header + extra}},
                {"type": "actions", "elements": [
                    {"type": "static_select", "action_id": action_id,
                     "placeholder": {"type": "plain_text", "text": "Choose from the previews above"},
                     "options": opts}]}])


def _first_usable_group(key):
    """First avatar group with a renderable look — prefer one with a photo look, else any."""
    groups = list_groups(key)
    log.info("multiangle _first_usable_group: %d groups for this key", len(groups))
    fallback = None
    for g in groups:
        looks = list_looks(g["id"], key)
        log.info("multiangle group %s (%s): %d looks [%s]", g["id"], g["name"], len(looks),
                 ",".join(sorted({l.get("type") or "?" for l in looks})))
        if not looks:
            continue
        if any(l.get("type") in ("photo_avatar", "talking_photo") for l in looks):
            return g["id"]
        fallback = fallback or g["id"]
    return fallback


def post_identity_chooser(client, channel, thread_ts, key):
    # Only show identities that actually have a renderable look (avoids the "no looks" dead-ends and
    # drops empty instant-twin groups). Falls back to all groups if the filter empties everything.
    groups = [g for g in list_groups(key) if list_looks(g["id"], key)]
    if not groups:
        groups = list_groups(key)
    if not groups:
        client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                text="No avatar identities found on your HeyGen account. Create one, then try again.")
        return
    items = [{"name": g["name"], "image": g.get("image"), "value": g["id"]} for g in groups]
    post_chooser(client, channel, thread_ts,
                 "*Make a multi-angle video* — pick an avatar identity (previews above):",
                 items, "mvma_identity")


def post_look_chooser(client, channel, thread_ts, gid, key):
    looks = list_looks(gid, key)
    items = [{"name": l["name"], "image": l.get("image"), "value": f"{gid}::{l['id']}"} for l in looks]
    post_chooser(client, channel, thread_ts, "Pick a *look* (previews above) — or Auto:",
                 items, "mvma_look", auto_value=f"{gid}::auto")


def build_script_modal(gid, look_id, channel, thread_ts):
    return {
        "type": "modal", "callback_id": "mvma_submit",
        "private_metadata": json.dumps({"gid": gid, "look": look_id, "channel": channel, "thread_ts": thread_ts}),
        "title": {"type": "plain_text", "text": "Multi-angle video"},
        "submit": {"type": "plain_text", "text": "Make it"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {"type": "input", "optional": True, "block_id": "script",
             "label": {"type": "plain_text", "text": "Script (optional — leave blank to auto-generate)"},
             "element": {"type": "plain_text_input", "action_id": "v", "multiline": True,
                         "placeholder": {"type": "plain_text", "text": "What should they say?"}}},
            {"type": "context", "elements": [{"type": "mrkdwn",
             "text": "I'll render a home camera + a held side profile on one shared take, then post 3 director-cut variations (Energetic / Standard / Minimal)."}]},
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Cut grammar (vendored, Whisper-free: proportional timing)
# ─────────────────────────────────────────────────────────────────────────────
def _proportional_timings(cuts, script, duration):
    toks = script.split() or [""]
    total = len(toks)
    cursor, out = 0, []
    for c in cuts:
        n = max(1, len((c.get("text") or "").split()))
        t0 = duration * cursor / total
        cursor = min(cursor + n, total)
        t1 = duration * cursor / total
        out.append({**c, "t_start": round(t0, 2), "t_end": round(t1, 2)})
    if out:
        out[0]["t_start"] = 0.0
        out[-1]["t_end"] = round(duration, 2)
    return out


def _ensure_side(sheet):
    """Guarantee at least one masked side reveal (mirror of batch ensure_side): if no side, turn the
    longest close-up that follows a home into ZOOM(1.5s)+SIDE."""
    if any(c["angle"] == "right_45" for c in sheet):
        return sheet
    cand = [i for i in range(1, len(sheet))
            if sheet[i]["angle"] == "close_up" and sheet[i - 1]["angle"] == "straight_on"
            and (sheet[i]["t_end"] - sheet[i]["t_start"]) >= 3.1]
    if not cand:
        return sheet
    i = max(cand, key=lambda i: sheet[i]["t_end"] - sheet[i]["t_start"])
    cu = sheet[i]; split = round(cu["t_start"] + 1.5, 2)
    sheet = sheet[:i] + [
        {**cu, "angle": "close_up", "t_end": split},
        {**cu, "angle": "right_45", "t_start": split},
    ] + sheet[i + 1:]
    for j, c in enumerate(sheet):
        c["idx"] = j
    return sheet


def cut_sheet(script, words, duration, pace, angles=None):
    """Same director grammar as the report. With Whisper `words` the cuts are pause-snapped (exact
    report parity); without them it falls back to proportional timing. `angles` lets the caller
    drop the side angle (home + close-up only) when no clean side profile cleared the gate."""
    angles = angles or ANGLES
    has_side = "right_45" in angles
    if pace == "auto":
        pace = cp.pace_for_duration(duration)
    max_shot = ag.EDIT_CONSTRAINTS["pace_max_shot_s"].get(pace, ag.EDIT_CONSTRAINTS["max_shot_s"])
    min_shot = ag.EDIT_CONSTRAINTS["pace_min_shot_s"].get(pace, ag.EDIT_CONSTRAINTS["min_shot_s"])
    cuts = cp.plan_cuts(script, angles, pace=pace, max_shot_s=max_shot, min_shot_s=min_shot)
    timed = cp.resolve_timings(cuts, words, script) if words else _proportional_timings(cuts, script, duration)
    final = cp.enforce_constraints(timed, angles, max_shot_s=max_shot, min_shot_s=min_shot)
    if has_side:
        final = cp.bridge_side_closeups(final, angles)
        final = _ensure_side(final)
    final = _open_on_home(final)
    final = _min_cuts(final, angles, dur=duration, n=3, min_shot=min_shot)
    return final


def _min_cuts(sheet, angles, dur, n=3, min_shot=2.0):
    """Floor on shot count so calm/long pacing never collapses to ~1 static shot. Split the
    longest shot in half (alternating angle) until len>=n, while no shot drops below min_shot.
    Keeps HOME on the opening shot."""
    if not sheet or dur < min_shot * 2:
        return sheet
    others = [a for a in angles if a != "straight_on"] or ["close_up"]
    guard = 0
    while len(sheet) < n and guard < 12:
        guard += 1
        i = max(range(len(sheet)), key=lambda k: sheet[k]["t_end"] - sheet[k]["t_start"])
        s = sheet[i]; d = s["t_end"] - s["t_start"]
        if d < min_shot * 2:
            break  # can't split further without going under min_shot
        mid = round(s["t_start"] + d / 2, 2)
        # second half flips to a complementary angle (close_up by default; never make shot 0 non-home)
        alt = "close_up" if s["angle"] != "close_up" else others[0]
        a, b = {**s, "t_end": mid}, {**s, "t_start": mid, "angle": (s["angle"] if i == 0 else alt)}
        if i == 0:
            b["angle"] = "close_up" if "close_up" in angles else b["angle"]  # open stays home, then cut
        sheet = sheet[:i] + [a, b] + sheet[i + 1:]
    for j, c in enumerate(sheet):
        c["idx"] = j
    return sheet


def _open_on_home(sheet):
    """V8 rule: the edit ALWAYS opens on the front-facing home (straight_on) shot — establish
    before any excursion. If the planner opened on a close-up/side, force the first shot to
    straight_on and merge any resulting same-angle adjacency."""
    if not sheet:
        return sheet
    if sheet[0]["angle"] != "straight_on":
        sheet[0] = {**sheet[0], "angle": "straight_on"}
    # collapse straight_on|straight_on adjacency the flip may have created
    while len(sheet) > 1 and sheet[0]["angle"] == "straight_on" and sheet[1]["angle"] == "straight_on":
        sheet[1] = {**sheet[1], "t_start": sheet[0]["t_start"],
                    "text": (sheet[0]["text"] + " " + sheet[1]["text"]).strip()}
        sheet = sheet[1:]
    for j, c in enumerate(sheet):
        c["idx"] = j
    return sheet


# ─────────────────────────────────────────────────────────────────────────────
# ffmpeg assembly (home + close-up crop of home + side, one master audio)
# ─────────────────────────────────────────────────────────────────────────────
def _ff(args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + args, check=True)


_ZOOM = 1.45  # close-up punch-in factor


def _face_xy_fraction(home_mp4):
    """(fx, fy) = the subject's nose-x / eye-y as a fraction of frame size in the HOME shot, so the
    close-up crop can keep them at the SAME screen position through the zoom (no eyeline jump).
    Falls back to (0.5, 0.42) — centered horizontally, eyes on the upper third — if no face."""
    try:
        import tempfile as _t, cv2, numpy as np
        from insightface.app import FaceAnalysis
        fp = Path(_t.mkdtemp()) / "f.jpg"
        _ff(["-ss", "0.6", "-i", home_mp4, "-frames:v", "1", str(fp)])
        im = cv2.imread(str(fp)); H, W = im.shape[:2]
        app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"]); app.prepare(ctx_id=-1, det_size=(640, 640))
        fs = app.get(im)
        if not fs:
            return 0.5, 0.42
        f = max(fs, key=lambda x: (x.bbox[2]-x.bbox[0])*(x.bbox[3]-x.bbox[1]))
        kps = f.kps  # [left_eye, right_eye, nose, l_mouth, r_mouth]
        eye_y = float((kps[0][1] + kps[1][1]) / 2) / H
        nose_x = float(kps[2][0]) / W
        return min(max(nose_x, 0.0), 1.0), min(max(eye_y, 0.0), 1.0)
    except Exception as e:
        log.info("eye-level detect fell back to default: %s", e)
        return 0.5, 0.42


def assemble(sheet, home_mp4, side_mp4, audio_path, out_mp4, orientation):
    W, H = (720, 1280) if orientation == "portrait" else (1280, 720)
    # Eye-level-aware close-up: position the punch-in crop so the subject's eyes/nose stay at the
    # same screen position as the wide shot (keeps the eyeline steady across the HOME→ZOOM cut).
    if any(c["angle"] == "close_up" for c in sheet):
        fx, fy = _face_xy_fraction(home_mp4)
    else:
        fx, fy = 0.5, 0.42
    Wz, Hz = round(W * _ZOOM), round(H * _ZOOM)
    cx = int(min(max(round(fx * (Wz - W)), 0), Wz - W))
    cy = int(min(max(round(fy * (Hz - H)), 0), Hz - H))
    closeup_vf = f"scale={Wz}:{Hz},crop={W}:{H}:{cx}:{cy}"
    base_vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    SRC = {"straight_on": home_mp4, "close_up": home_mp4, "right_45": side_mp4, "left_45": side_mp4}
    tmp = Path(tempfile.mkdtemp(prefix="mvma_"))
    segs = []
    for c in sheet:
        seg = tmp / f"s_{c['idx']:02d}.mp4"
        t0 = float(c["t_start"]); dur = max(0.1, float(c["t_end"]) - t0)
        vf = closeup_vf if c["angle"] == "close_up" else base_vf
        _ff(["-ss", f"{t0:.3f}", "-i", SRC[c["angle"]], "-t", f"{dur:.3f}",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
             "-r", "30", "-vf", vf, "-an", str(seg)])
        segs.append(seg)
    (tmp / "l.txt").write_text("".join(f"file '{s}'\n" for s in segs))
    vid = tmp / "v.mp4"
    _ff(["-f", "concat", "-safe", "0", "-i", str(tmp / "l.txt"), "-c", "copy", str(vid)])
    _ff(["-i", str(vid), "-i", audio_path, "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", out_mp4])
    return out_mp4


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator (runs in a background thread)
# ─────────────────────────────────────────────────────────────────────────────
def _download(url, dest):
    with httpx.Client(timeout=180.0, follow_redirects=True) as c:
        dest.write_bytes(c.get(url).content)
    return dest


def _gen_script(label):
    """Generic ~30s script when the user gives none."""
    try:
        from google import genai
        cl = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        r = cl.models.generate_content(model="gemini-2.5-pro", contents=(
            "Write a natural, first-person ~30-second spoken monologue (about 75 words) a creator "
            "would say to camera — one clear idea with a couple of commas per sentence. Plain text only."))
        return (r.text or "").strip() or "Here's one thing I wish I'd learned sooner."
    except Exception:
        return ("Here's one thing I wish I'd learned sooner. Most progress isn't loud, it's quiet and "
                "steady. So pick the one habit that compounds, protect it, and let time do the rest.")


def _render(client, meta, user):
    key = _H["connected_key"](user)
    ch, thread_ts = meta["channel"], meta["thread_ts"]

    def post(t):
        try:
            client.chat_postMessage(channel=ch, thread_ts=thread_ts, text=t)
        except Exception as e:
            log.warning("post failed: %s", e)

    try:
        gid, look_id = meta["gid"], meta["look"]
        looks = list_looks(gid, key)
        if not looks:
            # The requested identity may live on the server/shared account rather than the
            # user's personally-connected key — retry resolution there before giving up.
            sk = (_H.get("server_key") or (lambda: None))()
            if sk and sk != key:
                sl = list_looks(gid, sk)
                if sl:
                    log.info("multiangle: gid %s not on connected key — using server key", gid)
                    key, looks = sk, sl
        if not looks:
            # The id may be a LOOK id (not a group). Resolve it to its group + render THAT look.
            for k in (key, (_H.get("server_key") or (lambda: None))()):
                if not k:
                    continue
                grp = _resolve_look_group(gid, k)
                if grp:
                    log.info("multiangle: %s is a look in group %s — rendering that look", gid, grp)
                    look_id, gid, key, looks = gid, grp, k, list_looks(grp, k)
                    break
        ltype = {l["id"]: l.get("type") for l in looks}
        if look_id == "auto":
            if not looks:
                post(":x: That identity has no looks I can render — pick another identity."); return
            photo = [l for l in looks if l.get("type") in ("photo_avatar", "talking_photo")]
            look_id = (photo[0] if photo else looks[0])["id"]
        is_twin = ltype.get(look_id) in ("digital_twin", "avatar", "video")
        script = (meta.get("script") or "").strip() or _gen_script("creator")
        img_url = mv.look_image_url(look_id, key)
        # orientation: explicit override (`16:9`/`landscape`/…) wins; else infer from the look image
        orientation = meta.get("orient_override")
        if not orientation:
            orientation = "portrait"
            try:
                from PIL import Image
                import io
                with httpx.Client(timeout=60, follow_redirects=True) as c:
                    im = Image.open(io.BytesIO(c.get(img_url).content))
                orientation = "landscape" if im.width >= im.height else "portrait"
            except Exception:
                pass
        ar = "16:9" if orientation == "landscape" else "9:16"
        # Voice must MATCH the avatar's identity — never mix (e.g. a stranger's face + Adam's
        # voice). Use the avatar's own assigned voice: the LOOK's default voice first (that's
        # where photo-avatars carry it), then the group default, then Adam_2025 only for a
        # twin/own-identity look with nothing set.
        look_voice = next((l.get("voice") for l in looks if l["id"] == look_id), None)
        voice_id = look_voice or _group_voice(gid, key) or (ADAM_VOICE if is_twin else None)
        if not voice_id:
            post(":x: That avatar has no assigned voice — set a default voice on the group first."); return

        post(":clapper: Rendering the home camera…")
        # FULL Avatar V personality: reference footage (a same-group video look — the presenter/
        # delivery leg) + custom motion_prompt + eleven_v3 voice tags. reference_look_for returns
        # None if the group has no usable video look (then it's Avatar V + motion/voice only).
        try:
            ref_look = mv.reference_look_for(HOME_TONE, gid, key, look_id)
        except Exception as e:
            log.info("reference_look_for(%s): %s", gid, e); ref_look = None
        # Prefer Avatar V (best frontal); fall back to talking-photo (Avatar IV) only if the look
        # can't render on V (e.g. a photo-avatar group with no cross-reference look).
        order = ["v", "tp"]
        home_url, home_eng = None, None
        for eng in order:
            if eng == "tp":
                hv = mv.render_talking_photo(look_id, script, voice_id, key, orientation=orientation)
            else:
                hv = mv.render_avatar_v(look_id, script, voice_id, key, orientation=orientation,
                                        motion_prompt=HOME_MOTION, reference_look_id=ref_look)
            home_url = mv.poll_video(hv, key) if hv else None
            if home_url:
                home_eng = eng; break
        if not home_url:
            post(":x: Home render failed (look isn't renderable as talking-photo or Avatar V)."); return

        work = Path(tempfile.mkdtemp(prefix="mvma_run_"))
        home_mp4 = _download(home_url, work / "home.mp4")
        # master audio (drives the side so it's frame-aligned) + duration
        audio_mp3 = work / "master.mp3"
        _ff(["-i", str(home_mp4), "-vn", "-c:a", "libmp3lame", "-b:a", "192k", str(audio_mp3)])
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "csv=p=0", str(home_mp4)], capture_output=True, text=True).stdout or 0)
        audio_url = perslib.upload_audio_asset(audio_mp3.read_bytes(), key)

        side_mp4 = None
        gmeta = {}
        # FAST PATH — a pre-vetted, persisted side-angle look for THIS home look skips the
        # stochastic orbit + gate entirely (no fresh Seedance orbit each render). Still Avatar V +
        # eyes-forward SIDE_MOTION + the side video reference, driven by the master audio so cuts
        # stay frame-aligned. Only for Avatar-V (real-human) homes. [project_multi_angle_side_angle_methods]
        pre_side = SIDE_LOOK.get(look_id) if home_eng == "v" else None
        if pre_side:
            post(":busts_in_silhouette: Using the saved side-angle look (skipping the orbit)…")
            side_ref = SIDE_REFERENCE.get(gid)
            sv = render_side_avatar_v(pre_side, audio_url, key, orientation=orientation,
                                      reference_look_id=side_ref, motion_prompt=SIDE_MOTION)
            side_url = mv.poll_video(sv, key) if sv else None
            if side_url:
                side_mp4 = str(_download(side_url, work / "side.mp4"))
            else:
                post(":warning: Saved side look didn't render — falling back to a fresh orbit.")

        if side_mp4 is None:
            post(":busts_in_silhouette: Generating the held side-profile camera…")
            # ARK's reference-video asset must be 1.8–15.2s; the full home render is usually longer.
            # Trim a representative ~12s window (skip the first beat) and re-host it at an
            # ARK-fetchable HeyGen URL to seed the orbit. (Identity only — audio dropped.)
            home_ref = work / "home_ref.mp4"
            ref_start = min(2.0, max(0.0, dur - 12.0))
            _ff(["-ss", f"{ref_start:.2f}", "-i", str(home_mp4), "-t", "12", "-an",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                 str(home_ref)])
            ref_url = perslib._upload_video_asset(str(home_ref), key) or home_url
            # orbit the HOME render → held side profile, then GATE every candidate frame on
            # Gemini (turn/gaze/artifacts) + ArcFace identity + Gemini cohesion, with ≤1 re-roll
            # (V8 "Recommended step 1" — this is what kills wrong-looking / off-identity side angles).
            # Best-effort side angle: gate it (≤1 re-roll). If it never clears — or the side render
            # fails — DON'T abort: drop the side and make the video with home + close-ups only.
            frame, gmeta = mo.pick_side_frame(home_mp4, ref_url, SIDE_CONTEXT, ar, str(work / "orbit"))
            if frame:
                post(f":white_check_mark: Side frame cleared the gate — profile {gmeta['turn_deg']}°, "
                     f"identity {gmeta['arcface']}, room-overlap {gmeta['overlap']} (attempt {gmeta['attempt'] + 1}).")
                sv = None
                if home_eng == "v":
                    # REAL HUMAN AVATAR (rendered on Avatar V) → the side is Avatar V too: the gated
                    # side-profile still is the side-angle PHOTO look (avatar_id); an optional same-group
                    # off-gaze video look is the side-angle REFERENCE (drives forward gaze). Driven by the
                    # master audio for frame-aligned cuts. [feedback_multiangle_always_avatar_v]
                    side_look = lookgen_gen.create_look_avatar(frame, gid, "multiangle side-angle", key)
                    side_ref = SIDE_REFERENCE.get(gid)
                    sv = render_side_avatar_v(side_look, audio_url, key, orientation=orientation,
                                              reference_look_id=side_ref, motion_prompt=SIDE_MOTION) if side_look else None
                else:
                    # Photo-only group (no video look) → Avatar V unavailable; fall back to Avatar IV.
                    side_look = _upload_talking_photo(frame, key)
                    sv = render_audio_input(side_look, audio_url, key, orientation=orientation,
                                            motion_prompt=DOC_MOTION) if side_look else None
                side_url = mv.poll_video(sv, key) if sv else None
                if side_url:
                    side_mp4 = str(_download(side_url, work / "side.mp4"))
        if not side_mp4:
            post(f":warning: No clean side angle this time ({gmeta.get('rejects', 0)} frames rejected over "
                 f"≤2 attempts) — making the video with home + close-ups (no side reveal).")

        post(":scissors: Cutting 3 variations (no re-render)…")
        # Whisper word timings once → pause-snapped cuts (exact report parity); fall back gracefully.
        try:
            words = cp.transcribe_words(str(audio_mp3))
        except Exception as e:
            log.warning("whisper unavailable (%s) — proportional cut timing", e)
            words = None
        n = 0
        angles = ANGLES if side_mp4 else [a for a in ANGLES if a != "right_45"]
        for label, pace in VARIATIONS:
            try:
                sheet = cut_sheet(script, words, dur, pace, angles)
                out = assemble(sheet, str(home_mp4), side_mp4 or "", str(audio_mp3),
                               str(work / f"{label}.mp4"), orientation)
                client.files_upload_v2(channel=ch, thread_ts=thread_ts, file=out,
                                       filename=f"multiangle_{label.lower()}.mp4",
                                       title=f"Multi-angle — {label} ({len(sheet)} cuts)",
                                       initial_comment=(f":white_check_mark: *{label}* "
                                                        f"({len(sheet)} cuts)" if n == 0 else f"*{label}* ({len(sheet)} cuts)"))
                n += 1
            except Exception as e:
                log.error("variation %s failed: %s", label, e)
        if n == 0:
            post(":x: Assembly failed on all variations.")
        else:
            post(f":sparkles: Done — {n} director-cut variation(s) from one render. Pick your favorite.")
    except Exception as e:
        log.exception("multiangle render crashed")
        post(f":x: Multi-angle render hit an error: `{e}`")


# ─────────────────────────────────────────────────────────────────────────────
# Public hooks
# ─────────────────────────────────────────────────────────────────────────────
def handle_command(clean_text, say, client, event) -> bool:
    """Return True if this was a `make multiangle video` command (call BEFORE the generic
    `make video` branch — the generic regex also matches 'multiangle')."""
    if not re.search(r"\bmake\s+multi[\s-]?angle\s+video\b", clean_text, re.IGNORECASE):
        return False
    user = event.get("user")
    thread_ts = event.get("thread_ts") or event.get("ts")
    key = _H["connected_key"](user)
    if not key:
        say(text="Connect your HeyGen account first (`connect` or `set heygen key <key>`), then run "
                 "`make multiangle video`.", thread_ts=thread_ts)
        return True
    # one-shot A: explicit group OR look id, optional orientation token —
    #   `make multiangle video <group_or_look_id> [16:9|9:16|landscape|portrait] [| <script>]`
    m = re.search(r"make\s+multi[\s-]?angle\s+video\s+([0-9a-fA-F]{8,})"
                  r"(?:\s+(16:9|9:16|landscape|portrait))?(?:\s*\|\s*(.+))?$",
                  clean_text, re.IGNORECASE | re.DOTALL)
    # one-shot B: `make multiangle video auto [| <script>]` — bot picks a renderable identity
    a = re.search(r"make\s+multi[\s-]?angle\s+video\s+(?:auto|go)(?:\s*\|\s*(.+))?$",
                  clean_text, re.IGNORECASE | re.DOTALL)
    gid, script, orient_override = None, "", None
    if m:
        gid, script = m.group(1).lower(), (m.group(3) or "").strip()
        tok = (m.group(2) or "").lower()
        if tok in ("16:9", "landscape"):
            orient_override = "landscape"
        elif tok in ("9:16", "portrait"):
            orient_override = "portrait"
    elif a:
        script = (a.group(1) or "").strip()
        gid = _first_usable_group(key)
        if not gid:
            say(text=":x: No identity on your account has a renderable look. Create a photo or twin "
                     "avatar in HeyGen, then try again.", thread_ts=thread_ts)
            return True
    if gid:
        meta = {"gid": gid, "look": "auto", "channel": event.get("channel"),
                "thread_ts": thread_ts, "script": script, "orient_override": orient_override}
        say(text=":clapper: On it — rendering a multi-angle video (3 director-cut variations)…",
            thread_ts=thread_ts)
        # If a durable queue is wired (worker service up), enqueue so a bot redeploy can't kill
        # the render mid-flight; otherwise run in-process (unchanged default behavior).
        if _H.get("enqueue"):
            try:
                _H["enqueue"]("multiangle", {"meta": meta, "user": user})
            except Exception as e:
                log.error("enqueue failed (%s) — running in-process", e)
                threading.Thread(target=_render, args=(client, meta, user), daemon=True).start()
        else:
            threading.Thread(target=_render, args=(client, meta, user), daemon=True).start()
        return True
    try:
        post_identity_chooser(client, event.get("channel"), thread_ts, key)
    except Exception as e:
        log.error("identity list failed: %s", e)
        say(text=f":x: Couldn't list your avatars: `{e}`", thread_ts=thread_ts)
    return True


def register(app, helpers):
    """Wire the action/view handlers. helpers must provide connected_key(user)->key|None."""
    _H.update(helpers)

    @app.action("mvma_identity")
    def _identity(ack, body, client):
        ack()
        a = body["actions"][0]
        gid = a.get("value") or (a.get("selected_option") or {}).get("value")
        user = body["user"]["id"]
        ch = body["channel"]["id"]
        thread_ts = (body.get("message") or {}).get("thread_ts") or (body.get("message") or {}).get("ts")
        key = _H["connected_key"](user)
        post_look_chooser(client, ch, thread_ts, gid, key)

    @app.action("mvma_look")
    def _look(ack, body, client):
        ack()
        a = body["actions"][0]
        val = a.get("value") or (a.get("selected_option") or {}).get("value")
        gid, _, look_id = (val or "").partition("::")
        ch = body["channel"]["id"]
        thread_ts = (body.get("message") or {}).get("thread_ts") or (body.get("message") or {}).get("ts")
        client.views_open(trigger_id=body["trigger_id"],
                          view=build_script_modal(gid, look_id or "auto", ch, thread_ts))

    @app.view("mvma_submit")
    def _submit(ack, body, client, view):
        ack()
        meta = json.loads(view["private_metadata"])
        meta["script"] = (((view["state"]["values"].get("script") or {}).get("v") or {}).get("value") or "")
        user = body["user"]["id"]
        threading.Thread(target=_render, args=(client, meta, user), daemon=True).start()
