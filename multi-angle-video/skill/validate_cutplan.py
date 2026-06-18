import sys, json, os
sys.path.insert(0, ".")
from common.cut_planner import build_cut_sheet
AV = ["straight_on","close_up","left_45","right_45","full_body","side_view"]
runs = [("s_short",10.27), ("s_mid",27.87), ("s_long",64.81)]
for name, dur in runs:
    d = f"outputs/{name}"
    script = open(f"{d}/script.txt").read().strip()
    print(f"\n{'='*70}\n{name}  (audio {dur}s, {len(script.split())} words)\n{'='*70}")
    sheet = build_cut_sheet(script, f"{d}/master.wav", AV,
                            whisper_model="base", out_path=f"{d}/cut_sheet.json")
    used = {}
    for c in sheet:
        used[c["angle"]] = used.get(c["angle"],0)+1
        dlen = c["t_end"]-c["t_start"]
        print(f"  [{c['idx']:2d}] {c['t_start']:5.1f}-{c['t_end']:5.1f}s ({dlen:4.1f}s) {c['angle']:12s} | {c.get('rationale','')[:46]}")
        print(f"        \"{c['text'][:80]}\"")
    print(f"  --> {len(sheet)} cuts, angle mix: {used}")
print("\nDONE")
