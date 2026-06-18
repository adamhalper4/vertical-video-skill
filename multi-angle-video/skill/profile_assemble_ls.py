#!/usr/bin/env python3
"""Assemble a profile multi-cam video from frame-aligned cameras + a cut sheet.
Usage: profile_assemble.py <cut.json> <home.mp4> <left.mp4> <right.mp4> <audio.wav> <out.mp4>
All camera clips share ONE master-audio timeline; cut [t_start,t_end] indexes that timeline.
straight_on->home, close_up->center zoom on home, left_45->left clip, right_45->right clip."""
from __future__ import annotations
import sys, json, subprocess, tempfile
from pathlib import Path

cut, home, left, right, audio, out = sys.argv[1:7]
sheet = json.loads(Path(cut).read_text())
SRC = {"straight_on": home, "left_45": left, "right_45": right, "close_up": home}
W, H, Z = 1280, 720, 1.45

def _face_xy(mp4):
    """nose-x / eye-y fraction in HOME → keep eyes at the same screen height through the zoom.
    Fallback (0.5, 0.42)."""
    try:
        import cv2
        from insightface.app import FaceAnalysis
        f = Path(tempfile.mkdtemp())/"f.jpg"
        subprocess.run(["ffmpeg","-y","-loglevel","error","-ss","0.6","-i",mp4,"-frames:v","1",str(f)],check=True)
        im = cv2.imread(str(f)); h, w = im.shape[:2]
        app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"]); app.prepare(ctx_id=-1, det_size=(640,640))
        fs = app.get(im)
        if not fs: return 0.5, 0.42
        k = max(fs, key=lambda x:(x.bbox[2]-x.bbox[0])*(x.bbox[3]-x.bbox[1])).kps
        return min(max(float(k[2][0])/w,0),1), min(max(float((k[0][1]+k[1][1])/2)/h,0),1)
    except Exception:
        return 0.5, 0.42

if any(c["angle"]=="close_up" for c in sheet):
    fx, fy = _face_xy(home)
else:
    fx, fy = 0.5, 0.42
Wz, Hz = round(W*Z), round(H*Z)
cx = int(min(max(round(fx*(Wz-W)),0),Wz-W)); cy = int(min(max(round(fy*(Hz-H)),0),Hz-H))
CLOSEUP_VF = f"scale={Wz}:{Hz},crop={W}:{H}:{cx}:{cy}"  # eye-level-aware 1.45x punch-in
ENC = ["-c:v","libx264","-preset","medium","-crf","20","-pix_fmt","yuv420p","-r","30",
       "-vf","scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720","-an"]
tmp = Path(tempfile.mkdtemp(prefix="prof_")); segs=[]
for c in sheet:
    a=c["angle"]; t0=float(c["t_start"]); dur=float(c["t_end"])-t0
    seg=tmp/f"s_{c['idx']:02d}.mp4"
    cmd=["ffmpeg","-y","-loglevel","error","-ss",f"{t0:.3f}","-i",SRC[a],"-t",f"{dur:.3f}"]
    if a=="close_up":
        cmd+=["-c:v","libx264","-preset","medium","-crf","20","-pix_fmt","yuv420p","-r","30","-vf",CLOSEUP_VF,"-an"]
    else:
        cmd+=ENC
    cmd+=[str(seg)]; subprocess.run(cmd,check=True); segs.append(seg)
    print(f"  {c['idx']:02d} {a:11s} {t0:6.2f}-{c['t_end']:.2f}",flush=True)
(tmp/"l.txt").write_text("".join(f"file '{s}'\n" for s in segs))
vid=tmp/"v.mp4"
subprocess.run(["ffmpeg","-y","-loglevel","error","-f","concat","-safe","0","-i",str(tmp/"l.txt"),"-c","copy",str(vid)],check=True)
Path(out).parent.mkdir(parents=True,exist_ok=True)
subprocess.run(["ffmpeg","-y","-loglevel","error","-i",str(vid),"-i",audio,"-c:v","copy","-c:a","aac","-b:a","160k","-shortest",out],check=True)
print("->",out)
