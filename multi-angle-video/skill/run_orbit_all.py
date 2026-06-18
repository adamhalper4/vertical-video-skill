import sys, subprocess, json, os, re, glob
from pathlib import Path
sys.path.insert(0, str(Path.home()/".claude/skills/av-image-n-looks"))
from common.image_n import generate_scene_video
from google import genai
from PIL import Image

HOMES={
 "p_adam":  "https://www.heygenverse.com/s/e39e5d21-6b1c-422d-9b4a-1a4126ddc522/raw",
 "p_priya": "https://www.heygenverse.com/s/20c73833-12ed-48d2-af69-ffee74014a09/raw",
 "p_marcus":"https://www.heygenverse.com/s/2e3d0996-a99b-4f42-81ed-46f2de6ca33b/raw",
}
def prompt(side):
    return (f"The camera arcs smoothly around to the person's {side.upper()}, sweeping from straight-on "
      "toward a side view, while the person stays seated and keeps talking. Keep the EXACT SAME room and "
      "background unchanged — the identical window and everything behind them — only the camera position "
      "moves, so the background slides across the frame with correct perspective and parallax. Do NOT "
      "invent or change the background; only move the camera to the side.")
client=genai.Client(api_key=os.environ["GEMINI_API_KEY"])
def pick45(clipdir, clip):
    for t in [round(1.0+0.3*i,1) for i in range(13)]:
        subprocess.run(["ffmpeg","-y","-loglevel","error","-ss",str(t),"-i",clip,"-frames:v","1",f"{clipdir}/s_{t}.jpg"])
    best=None
    for f in sorted(glob.glob(f"{clipdir}/s_*.jpg")):
        try:
            r=client.models.generate_content(model="gemini-2.5-pro",contents=[
              'Seated person. Estimate horizontal turn from facing camera. JSON only: '
              '{"turn_deg":<0-90 int>,"face_readable":true|false}. 0=facing,45=three-quarter,90=profile.',Image.open(f)])
            d=json.loads(re.search(r"\{.*\}",r.text,re.S).group(0))
        except Exception as e: d={}
        deg=d.get("turn_deg",-1)
        if d.get("face_readable") and 38<=deg<=58 and (best is None or abs(deg-45)<abs(best[1]-45)):
            best=(f,deg)
    return best

result={}
for persona,home in HOMES.items():
    result[persona]={}
    for side,slug in [("left","left_45"),("right","right_45")]:
        od=Path(f"outputs/{persona}/orbit/{slug}"); od.mkdir(parents=True,exist_ok=True)
        clip=str(od/"clip.mp4")
        generate_scene_video(None,[prompt(side)],Path(clip),look_image_url=home,look_image_role="first_frame",
                             aspect_ratio="9:16",duration_s=7,username=f"orbit_{persona}_{slug}")
        b=pick45(str(od),clip)
        if b:
            picked=f"outputs/{persona}/orbit/{slug}.jpg"; subprocess.run(["cp",b[0],picked])
            result[persona][slug]={"frame":picked,"deg":b[1]}
            print(f"{persona} {slug}: picked {b[1]}deg",flush=True)
        else:
            print(f"{persona} {slug}: NO 45deg frame found",flush=True)
print("RESULT "+json.dumps(result))
