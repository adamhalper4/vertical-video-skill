"""Regenerate ONLY the 3/4 angle looks with corrected gaze-aligned prompts + a Gemini gaze gate.

Fix: the default angle prompts say 'head turned slightly toward camera' (and side_view=90 profile),
which makes the model swivel the face back to the lens. We instead force head+eyes+torso ALL
aligned at ~45 degrees, gaze OFF-camera, no eye contact, natural three-quarter (not profile),
and re-roll until a Gemini gaze gate confirms off_camera + three_quarter.
"""
import sys, json, os, re
from pathlib import Path
sys.path.insert(0, str(Path.home() / ".claude/skills/av-image-n-looks"))
from common.image_n import generate_look_pack
from google import genai
from PIL import Image

BASE_URL = "https://resource2.heygen.ai/image/1509bc0750fe44059a0d3b946599356a/original.png"
TWIN = "https://resource2.heygen.ai/avatar/v3/dd6d517d0af24181a86f2ca87f3d74dc/half/2.2/preview_video_target.mp4"

GAZE_CLAUSE = (
    "the person's head, eyes, and shoulders are ALL rotated together by about 45 degrees in the "
    "same direction; they look off along their own eyeline toward the front of the room, slightly "
    "away from THIS camera; their gaze follows the direction their body faces; they make NO eye "
    "contact with this lens and do NOT turn their face back toward the camera; a natural candid "
    "three-quarter pose, clearly NOT a full 90-degree profile; head-and-shoulders framing"
)
ANGLES = [
    {"slug": "left_45", "label": "Left 45",
     "prompt": f"a three-quarter camera angle positioned 45 degrees to the person's left side; {GAZE_CLAUSE}"},
    {"slug": "right_45", "label": "Right 45",
     "prompt": f"a three-quarter camera angle positioned 45 degrees to the person's right side; {GAZE_CLAUSE}"},
]

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def gaze_check(path: str) -> dict:
    img = Image.open(path)
    r = client.models.generate_content(model="gemini-2.5-pro", contents=[
        "Look at this portrait of a person. Answer two questions:\n"
        "(1) GAZE: are they making eye contact with the camera (looking into the lens), or is their "
        "gaze directed off to the side / aligned with their turned body?\n"
        "(2) TURN: is the body a natural three-quarter turn (~45 deg), a near-full profile (~90 deg), "
        "or basically frontal?\n"
        'Reply ONLY as JSON: {"gaze":"at_camera"|"off_camera","turn":"three_quarter"|"profile"|"frontal"}.',
        img])
    m = re.search(r"\{.*\}", r.text or "", re.S)
    return json.loads(m.group(0)) if m else {"gaze": "?", "turn": "?"}


best: dict = {}
for attempt in range(4):
    out = Path(f"outputs/imagen_angles_v2/try{attempt}")
    res = generate_look_pack(out, scenes=ANGLES, mode="angles", reference_video_url=TWIN,
                             look_image_url=BASE_URL, reference_image="outputs/angles/straight_on.png",
                             aspect_ratio="9:16", username="multiangle_adam_v2")
    arc = res.get("arcface", {})
    frames = {Path(f).stem: f for f in res.get("frames", [])}
    for slug in ("left_45", "right_45"):
        if slug in best:
            continue
        f = frames.get(slug)
        if not f:
            continue
        g = gaze_check(f)
        a = arc.get(slug)
        print(f"try{attempt} {slug}: arcface={a} gaze={g}", flush=True)
        if a and a >= 0.55 and g.get("gaze") == "off_camera" and g.get("turn") != "profile":
            best[slug] = {"frame": f, "arcface": a, "gaze": g}
    if len(best) == 2:
        break

print("BEST " + json.dumps(best))
