import sys, json; sys.path.insert(0,'.')
from common.cut_planner import build_cut_sheet
AV=['straight_on','close_up','left_45','right_45']
PACE={'short':'social','mid':'balanced','long':'explainer'}
for persona in ['p_priya','p_marcus']:
    for ln in ['short','mid','long']:
        d=f'outputs/{persona}/{ln}'
        script=open(f'outputs/{persona}/scripts/{ln}.txt').read().strip()
        sheet=build_cut_sheet(script, f'{d}/master.wav', AV, pace=PACE[ln], whisper_model='base', out_path=f'{d}/cut_sheet.json')
        from collections import Counter; mix=Counter(c['angle'] for c in sheet)
        print(f"{persona}/{ln}: {len(sheet)} cuts {dict(mix)}", flush=True)
print("CUTPLANS_DONE")
