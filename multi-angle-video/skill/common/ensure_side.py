import sys,json
from pathlib import Path
W=Path("/Users/adamhalper/skill_build/batch2/work")
BR=1.5
def fix(name):
    p=W/f"{name}_cut.json"; sh=json.loads(p.read_text())
    # B) opens on close_up -> split a short establishing HOME in front
    if sh and sh[0]["angle"]=="close_up" and (sh[0]["t_end"]-sh[0]["t_start"])>=2.4:
        cu=sh[0]; mid=round(cu["t_start"]+1.4,2)
        sh=[{"angle":"straight_on","t_start":cu["t_start"],"t_end":mid,"rationale":"establish home"},
            {"angle":"close_up","t_start":mid,"t_end":cu["t_end"],"rationale":cu.get("rationale","")}]+sh[1:]
    # A) no side anywhere -> turn the longest close_up (prev=home) into ZOOM(bridge)+SIDE
    if not any(c["angle"]=="right_45" for c in sh):
        cand=[i for i in range(len(sh)) if sh[i]["angle"]=="close_up" and i>0 and sh[i-1]["angle"]=="straight_on"
              and (sh[i]["t_end"]-sh[i]["t_start"])>=BR+1.6]
        if cand:
            i=max(cand,key=lambda i:sh[i]["t_end"]-sh[i]["t_start"])
            cu=sh[i]; split=round(cu["t_start"]+BR,2)
            sh=sh[:i]+[{"angle":"close_up","t_start":cu["t_start"],"t_end":split,"rationale":"masking close-up before side reveal"},
                       {"angle":"right_45","t_start":split,"t_end":cu["t_end"],"rationale":"contrast / side excursion"}]+sh[i+1:]
    for j,c in enumerate(sh): c["idx"]=j
    p.write_text(json.dumps(sh))
    seq=" -> ".join({'straight_on':'HOME','close_up':'ZOOM','right_45':'SIDE'}[c['angle']] for c in sh)
    print(f"{name}: {seq}")
for n in sys.argv[1:]: fix(n)
