import sys, json, os, re
from pathlib import Path
sys.path.insert(0, str(Path.home()/".claude/skills/av-image-n-looks"))
from common.image_n import generate_look_pack
from google import genai
from PIL import Image

GAZE=("the person's head, eyes, and shoulders are ALL rotated together by about 45 degrees in the "
 "same direction; they look off along their own eyeline toward the front of the room, away from THIS "
 "camera; gaze follows the body; NO eye contact with this lens; natural three-quarter pose, NOT a "
 "90-degree profile; head-and-shoulders framing, same outfit, same background")
ANGLES=[{"slug":"left_45","prompt":f"a three-quarter camera angle 45 degrees to the person's left side; {GAZE}"},
        {"slug":"right_45","prompt":f"a three-quarter camera angle 45 degrees to the person's right side; {GAZE}"}]
PERSONAS={
 "p_priya":"https://resource2.heygen.ai/image/8f26408c1c844810a9c6afc9a6e76859/original.png",
 "p_marcus":"https://resource2.heygen.ai/image/2f63778d223e40f8832b8a820ff628f8/original.png",
}
client=genai.Client(api_key=os.environ["GEMINI_API_KEY"])
def gaze(path):
    r=client.models.generate_content(model="gemini-2.5-pro",contents=[
      'Portrait. JSON only: {"gaze":"at_camera"|"off_camera","turn":"three_quarter"|"profile"|"frontal"}',Image.open(path)])
    m=re.search(r"\{.*\}",r.text or "",re.S); return json.loads(m.group(0)) if m else {}
result={}
for slug,url in PERSONAS.items():
    best={}
    for att in range(4):
        out=Path(f"outputs/{slug}/angles/try{att}")
        res=generate_look_pack(out,scenes=ANGLES,mode="angles",reference_video_url=None,
            look_image_url=url,reference_image=f"outputs/{slug}/angles/base.png",aspect_ratio="9:16",username=f"ma_{slug}")
        arc=res.get("arcface",{}); frames={Path(f).stem:f for f in res.get("frames",[])}
        for a in ("left_45","right_45"):
            if a in best: continue
            f=frames.get(a)
            if not f: continue
            g=gaze(f); sc=arc.get(a)
            print(f"{slug} try{att} {a}: arc={sc} gaze={g}",flush=True)
            if sc and sc>=0.50 and g.get("gaze")=="off_camera" and g.get("turn")!="profile":
                best[a]={"frame":f,"arc":sc,"gaze":g}
        if len(best)==2: break
    # fallback: if not found, take best available off_camera or highest arc
    result[slug]=best
print("RESULT "+json.dumps(result))
