#!/usr/bin/env python3
"""Cohesion-aware orbit (Adam: Nicholas side looked wrong — no background overlap with home).
Improvements over batch1 orbit_profiles.py:
  - CONTINUITY prompt: the same wall/furniture/objects stay visible & continuous in the side view;
    it's the same room seen from the side, NOT a new location. Don't invent/replace the background.
  - smaller orbit band (66-82deg, favor lower) so less new wall is revealed -> more overlap.
  - COHESION SCORE: Gemini compares the side frame's background to the HOME frame and rates overlap
    0-1 (shared objects/wall/furniture). Pick maximizes 0.6*overlap + 0.4*arcface among clean frames.
  - keeps artifact gate (no face-on-wall / extra-person / warped) + insightface 2nd-face check.
Usage: orbit_profiles2.py <name1> [name2 ...]   (names from batch2/manifest.json)
Expects batch2/work/<name>_frame.jpg + manifest home_url. Writes <name>_rprof.jpg + records orbit meta.
"""
import os,sys,json,re,subprocess
from pathlib import Path
sys.path.insert(0,os.path.expanduser("~/.claude/skills/av-image-n-looks"))
from common.image_n import generate_scene_video
from google import genai
from PIL import Image
import numpy as np, cv2
from insightface.app import FaceAnalysis
key=re.search(r'GEMINI_API_KEY=(\S+)',open(os.path.expanduser("~/.config/vertical-deep-dive/.env")).read()).group(1)
client=genai.Client(api_key=key)
W=Path(os.path.expanduser("~/skill_build/batch2/work"))
MAN=os.path.expanduser("~/skill_build/batch2/manifest.json")
M={m["name"]:m for m in json.load(open(MAN))}
app=FaceAnalysis(name="buffalo_l",providers=["CPUExecutionProvider"]);app.prepare(ctx_id=-1,det_size=(640,640))
def faces(f):
    im=cv2.imread(f)
    if im is None: return []
    return sorted(app.get(im),key=lambda x:(x.bbox[2]-x.bbox[0])*(x.bbox[3]-x.bbox[1]),reverse=True)
def emb(f):
    fs=faces(f); return fs[0].normed_embedding if fs else None
TECH="Clear stable facial features, not distorted, natural proportions, consistency frame after frame. 4K HD, no blur, stable. No music."
def P1(side):
    return (f"8 seconds. Fly-on-the-wall observational documentary. The same person seated in the same room. "
            f"Camera makes ONE slow lateral move from the front around to about 75 degrees on their {side} side, then holds. {TECH} "
            f"They speak to someone in front of them and are UNAWARE of this side camera; they never turn toward it or look at this lens; "
            f"head and body stay forward; one eye and the nose stay visible (never the back of the head). "
            f"CRITICAL: this is the SAME room seen from the side — the same wall, the same furniture and the same objects behind them stay "
            f"VISIBLE and CONTINUOUS in this side view. Do NOT invent or replace the background; keep the existing scene elements so the shot "
            f"clearly reads as the same place. No posters, framed photos, portraits or any duplicate of the person on the wall. "
            f"Same person, same outfit, same room every frame. Medium shot.")
GATE=('Look at this single video frame of a person filmed from the side. JSON only: '
      '{"turn_deg":<0-100,90=full profile>,"recognizable":true|false,"looking_at_this_camera":true|false,'
      '"face_on_wall_or_poster":true|false,"extra_person":true|false,"warped_background":true|false}')
def gate(fp):
    try:
        r=client.models.generate_content(model="gemini-2.5-pro",contents=[GATE,Image.open(fp)])
        return json.loads(re.search(r"\{.*\}",r.text,re.S).group(0))
    except Exception as e: return {"_err":str(e)[:80]}
def cohesion(home_fp,side_fp):
    try:
        r=client.models.generate_content(model="gemini-2.5-pro",contents=[
            "Image 1 is a person filmed head-on in a room. Image 2 is the SAME person filmed from the side. "
            "Judging ONLY the background/room (ignore the person), how much does Image 2's setting share recognizable "
            "objects/wall/furniture/colors with Image 1 — i.e. does it read as the SAME room continued, not a new place? "
            'JSON only: {"overlap":<0.0-1.0>,"same_room":true|false}',
            Image.open(home_fp),Image.open(side_fp)])
        return json.loads(re.search(r"\{.*\}",r.text,re.S).group(0))
    except Exception: return {"overlap":0.0,"same_room":False}
def run(name):
    m=M[name]; ar="16:9" if m["ori"]=="landscape" else "9:16"; home_url=m.get("home_url")
    home_fp=str(W/f"{name}_frame.jpg"); he=emb(home_fp)
    if not home_url or he is None: print(f"{name}: MISSING home_url/frame",flush=True); return
    best=None; rejects=0
    for att in range(3):
        cd=W/f"{name}_orbit{att}"; clip=cd/"clip.mp4"; cd.mkdir(parents=True,exist_ok=True)
        if not clip.exists():
            generate_scene_video(None,[P1("right")],clip,look_image_url=home_url,look_image_role="first_frame",
                                 aspect_ratio=ar,duration_s=8,username=f"v8_{name}{att}")
        sd=cd/"scan"; sd.mkdir(exist_ok=True)
        dur=float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",str(clip)],capture_output=True,text=True).stdout or 8)
        t=1.0
        while t<dur-0.05:
            fp=sd/f"f_{round(t,2)}.jpg"
            subprocess.run(["ffmpeg","-y","-loglevel","error","-ss",str(round(t,2)),"-i",str(clip),"-frames:v","1",str(fp)],check=True)
            d=gate(str(fp)); deg=d.get("turn_deg",-1)
            bad = d.get("face_on_wall_or_poster") or d.get("extra_person") or d.get("warped_background") \
                  or d.get("looking_at_this_camera",True) or not d.get("recognizable",False) or not (66<=deg<=82)
            if bad: rejects+=1; t+=0.3; continue
            ff=faces(str(fp))
            if len(ff)>=2 and (ff[1].bbox[2]-ff[1].bbox[0])*(ff[1].bbox[3]-ff[1].bbox[1])>4000: rejects+=1; t+=0.3; continue
            e=ff[0].normed_embedding if ff else None
            af=float(np.dot(he,e)) if e is not None else -1
            co=cohesion(home_fp,str(fp)); ov=float(co.get("overlap",0.0))
            score=0.6*ov+0.4*af
            if best is None or score>best[0]: best=(score,deg,af,ov,str(fp),att)
            t+=0.3
        if best and best[3]>=0.45 and best[2]>=0.5: break   # good overlap + identity -> stop
    if best:
        subprocess.run(["cp",best[4],str(W/f"{name}_rprof.jpg")],check=True)
        meta=dict(score=round(best[0],3),deg=best[1],arc=round(best[2],3),overlap=round(best[3],2),att=best[5],rejects=rejects)
        print(f"{name}: PROFILE deg={best[1]} arc={best[2]:.2f} overlap={best[3]:.2f} score={best[0]:.2f} att={best[5]} rej={rejects}",flush=True)
    else:
        meta=dict(score=None,rejects=rejects); print(f"{name}: NO CLEAN PROFILE rej={rejects}",flush=True)
    allm=json.load(open(MAN))
    for x in allm:
        if x["name"]==name: x["orbit"]=meta
    json.dump(allm,open(MAN,"w"),indent=1)
if __name__=="__main__":
    for n in sys.argv[1:]: run(n)
    print("ORBIT2 DONE")
