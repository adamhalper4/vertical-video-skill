import sys, subprocess, json, os, re, glob
from pathlib import Path
sys.path.insert(0, str(Path.home()/".claude/skills/av-image-n-looks"))
from common.image_n import generate_scene_video
from google import genai
from PIL import Image
JOBS=[("p_adam","right","https://www.heygenverse.com/s/e39e5d21-6b1c-422d-9b4a-1a4126ddc522/raw"),
      ("p_priya","left","https://www.heygenverse.com/s/20c73833-12ed-48d2-af69-ffee74014a09/raw")]
def P(side):
    return (f"The camera arcs smoothly around to the person's {side.upper()} and eases to a stop at a "
      "45-degree THREE-QUARTER angle, holding there for the second half of the shot. Keep the person's "
      "FACE clearly visible in three-quarter view (we still see most of the face — NOT a full profile, "
      "NOT the back of the head); the person looks ahead and forward, off-camera but toward the front of "
      "the room. Keep the EXACT SAME room and background unchanged — only the camera moves, so the "
      "background shifts across the frame with correct perspective and parallax. Do not invent a new background.")
c=genai.Client(api_key=os.environ["GEMINI_API_KEY"])
def deg(f):
    try:
        r=c.models.generate_content(model="gemini-2.5-pro",contents=['Seated person. Horizontal turn from facing camera. JSON only: {"turn_deg":<0-90 int>,"face_readable":true|false}. 0=facing,45=3/4,90=profile.',Image.open(f)])
        return json.loads(re.search(r"\{.*\}",r.text,re.S).group(0))
    except Exception: return {}
for persona,side,home in JOBS:
    slug={"left":"left_45","right":"right_45"}[side]
    od=Path(f"outputs/{persona}/orbit/{slug}_rr"); od.mkdir(parents=True,exist_ok=True)
    clip=str(od/"clip.mp4")
    generate_scene_video(None,[P(side)],Path(clip),look_image_url=home,look_image_role="first_frame",aspect_ratio="9:16",duration_s=6,username=f"reroll_{persona}_{slug}")
    ts=[round(0.8+0.15*i,2) for i in range(int((5.5-0.8)/0.15)+1)]
    for t in ts: subprocess.run(["ffmpeg","-y","-loglevel","error","-ss",str(t),"-i",clip,"-frames:v","1",f"{od}/s_{t}.jpg"])
    best=None
    for f in sorted(glob.glob(f"{od}/s_*.jpg")):
        d=deg(f); dg=d.get("turn_deg",-1)
        if d.get("face_readable") and 35<=dg<=60 and (best is None or abs(dg-45)<abs(best[1]-45)): best=(f,dg)
    if best:
        out=f"outputs/{persona}/orbit/{slug}.jpg"; subprocess.run(["cp",best[0],out])
        print(f"{persona}/{slug}: REROLL picked {best[1]}deg",flush=True)
    else:
        print(f"{persona}/{slug}: reroll STILL no 45",flush=True)
print("REROLL_DONE")
