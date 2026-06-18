import os, json, re, glob, subprocess
from google import genai
from PIL import Image
c=genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MISSES={
 "p_adam/right_45":"outputs/p_adam/orbit/right_45/clip.mp4",
 "p_priya/left_45":"outputs/p_priya/orbit/left_45/clip.mp4",
 "p_marcus/left_45":"outputs/p_marcus/orbit/left_45/clip.mp4",
}
def deg(f):
    try:
        r=c.models.generate_content(model="gemini-2.5-pro",contents=[
          'Seated person. Horizontal turn from facing camera. JSON only: {"turn_deg":<0-90 int>,"face_readable":true|false}. 0=facing,45=three-quarter,90=profile.',Image.open(f)])
        return json.loads(re.search(r"\{.*\}",r.text,re.S).group(0))
    except Exception: return {}
for key,clip in MISSES.items():
    wd=os.path.dirname(clip)+"/scan2"; os.makedirs(wd,exist_ok=True)
    ts=[round(0.8+0.15*i,2) for i in range(int((5.0-0.8)/0.15)+1)]
    for t in ts: subprocess.run(["ffmpeg","-y","-loglevel","error","-ss",str(t),"-i",clip,"-frames:v","1",f"{wd}/s_{t}.jpg"])
    best=None
    for f in sorted(glob.glob(f"{wd}/s_*.jpg")):
        d=deg(f); dg=d.get("turn_deg",-1)
        if d.get("face_readable") and 35<=dg<=60 and (best is None or abs(dg-45)<abs(best[1]-45)): best=(f,dg)
    slug=key.split("/")[1]; persona=key.split("/")[0]
    if best:
        out=f"outputs/{persona}/orbit/{slug}.jpg"; subprocess.run(["cp",best[0],out])
        print(f"{key}: RESCAN picked {best[1]}deg -> {out}",flush=True)
    else:
        print(f"{key}: STILL no 45 (will need re-roll)",flush=True)
print("RESCAN_DONE")
