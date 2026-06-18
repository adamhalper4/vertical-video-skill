import sys, json
from pathlib import Path
sys.path.insert(0, ".")
from common.multicam import assemble
for name in ["s_short","s_mid","s_long"]:
    d = Path("outputs")/name
    sheet = json.loads((d/"cut_sheet.json").read_text())
    cams = {p.stem: str(p) for p in (d/"cams").glob("*.mp4")}
    used = sorted({c["angle"] for c in sheet})
    missing = [a for a in used if a not in cams]
    if missing:
        print(f"{name}: MISSING cams {missing}; have {list(cams)} -- skipping"); continue
    out = assemble(sheet, cams, str(d/"master.wav"), str(d/"final.mp4"), dimension=(720,1280))
    import subprocess
    dur = subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(out)]).decode().strip()
    print(f"{name}: final -> {out}  ({len(sheet)} cuts, {dur}s, angles {used})")
print("ASSEMBLE_DONE")
