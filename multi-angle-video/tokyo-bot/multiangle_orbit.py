"""V8 cohesion / identity / artifact-gated side-profile frame pick.

Ported from the skill's `common/orbit_cohesion.py` — this is V8 "Recommended step 1"
(gate every generated angle frame BEFORE proceeding, with ≤1 re-roll). Generation is
stochastic, so instead of blindly taking the orbit's best-looking frame we validate every
candidate:
  - Gemini gate: reject looking_at_this_camera / not recognizable / turn∉[66,82] / artifacts
    (face-on-wall, extra person, warped background)
  - insightface 2nd-face check (no duplicate person)
  - ArcFace identity vs the home frame
  - Gemini cohesion: background overlap with home (same room continued, not a new place)
Score = 0.6·overlap + 0.4·ArcFace among clean frames; stop when overlap≥0.45 and ArcFace≥0.5.
Budget ≤1 re-roll (2 orbit generations total).
"""
from __future__ import annotations
import os, re, json, subprocess, logging
from pathlib import Path

from .lookgen.image_n import generate_scene_video

log = logging.getLogger(__name__)

_GATE = ('Look at this single video frame of a person filmed from the side. JSON only: '
         '{"turn_deg":<0-100,90=full profile>,"recognizable":true|false,"looking_at_this_camera":true|false,'
         '"face_on_wall_or_poster":true|false,"extra_person":true|false,"warped_background":true|false}')

_COHESION = (
    "Image 1 is a person filmed head-on in a room. Image 2 is the SAME person filmed from the side. "
    "Judging ONLY the background/room (ignore the person), how much does Image 2's setting share recognizable "
    "objects/wall/furniture/colors with Image 1 — i.e. does it read as the SAME room continued, not a new place? "
    'JSON only: {"overlap":<0.0-1.0>,"same_room":true|false}')

_APP = None


def _client():
    from google import genai
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _gate(client, fp):
    from PIL import Image
    try:
        r = client.models.generate_content(model="gemini-2.5-pro", contents=[_GATE, Image.open(fp)])
        return json.loads(re.search(r"\{.*\}", r.text, re.S).group(0))
    except Exception as e:
        return {"_err": str(e)[:80]}


def _cohesion(client, home_fp, side_fp):
    from PIL import Image
    try:
        r = client.models.generate_content(model="gemini-2.5-pro",
                                            contents=[_COHESION, Image.open(home_fp), Image.open(side_fp)])
        return json.loads(re.search(r"\{.*\}", r.text, re.S).group(0))
    except Exception:
        return {"overlap": 0.0, "same_room": False}


def _faces(fp):
    global _APP
    import cv2
    if _APP is None:
        from insightface.app import FaceAnalysis
        _APP = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        _APP.prepare(ctx_id=-1, det_size=(640, 640))
    im = cv2.imread(str(fp))
    if im is None:
        return []
    return sorted(_APP.get(im), key=lambda x: (x.bbox[2]-x.bbox[0])*(x.bbox[3]-x.bbox[1]), reverse=True)


def pick_side_frame(home_mp4, ref_url, scene_prompt, ar, work, attempts=2):
    """Generate the orbit, gate every candidate frame, return (best_frame_path, meta).

    attempts=2 → ≤1 re-roll (the V8 budget). Returns (None, meta) if nothing clears the gates.
    """
    import numpy as np
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    home_fp = work / "home_frame.jpg"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.6", "-i", str(home_mp4),
                    "-frames:v", "1", str(home_fp)], check=True)
    ff_home = _faces(home_fp)
    he = ff_home[0].normed_embedding if ff_home else None
    client = _client()
    best = None
    rejects = 0
    for att in range(attempts):
        cd = work / f"orbit{att}"; cd.mkdir(exist_ok=True)
        clip = cd / "clip.mp4"
        if not clip.exists():
            generate_scene_video(ref_url, [scene_prompt], clip, aspect_ratio=ar,
                                 duration_s=8, username=f"mvma_orbit{att}")
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "csv=p=0", str(clip)], capture_output=True, text=True).stdout or 8)
        sd = cd / "scan"; sd.mkdir(exist_ok=True)
        cleared = False
        t = 1.0
        while t < dur - 0.05:
            fp = sd / f"f_{round(t, 2)}.jpg"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(round(t, 2)),
                            "-i", str(clip), "-frames:v", "1", str(fp)], check=True)
            d = _gate(client, fp); deg = d.get("turn_deg", -1)
            bad = (d.get("face_on_wall_or_poster") or d.get("extra_person") or d.get("warped_background")
                   or d.get("looking_at_this_camera", True) or not d.get("recognizable", False)
                   or not (66 <= deg <= 82))
            if bad:
                rejects += 1; t += 0.3; continue
            ff = _faces(fp)
            if len(ff) >= 2 and (ff[1].bbox[2]-ff[1].bbox[0])*(ff[1].bbox[3]-ff[1].bbox[1]) > 4000:
                rejects += 1; t += 0.3; continue
            e = ff[0].normed_embedding if ff else None
            af = float(np.dot(he, e)) if (e is not None and he is not None) else -1.0
            ov = float(_cohesion(client, str(home_fp), str(fp)).get("overlap", 0.0))
            score = 0.6 * ov + 0.4 * af
            if best is None or score > best[0]:
                best = (score, deg, af, ov, str(fp), att)
            t += 0.3
            if ov >= 0.45 and af >= 0.5:   # clearly good — no need to keep scanning this clip
                cleared = True; break
        if cleared:
            break
    if best:
        out = work / "side_frame.jpg"
        subprocess.run(["cp", best[4], str(out)], check=True)
        meta = dict(score=round(best[0], 3), turn_deg=best[1], arcface=round(best[2], 3),
                    overlap=round(best[3], 2), attempt=best[5], rejects=rejects)
        log.info("multiangle orbit gate: %s", meta)
        return str(out), meta
    return None, dict(score=None, rejects=rejects)
