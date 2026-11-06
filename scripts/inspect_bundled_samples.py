"""Read-only inspection of bundled MDI ASCII samples. Never repair or truncate originals."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from jade_mcp.research import read_pattern, digest

parser=argparse.ArgumentParser()
parser.add_argument('--sample-dir',type=Path,default=Path('E:/MDI Jade 9/示范文件'))
args=parser.parse_args()
rows=[]
for source in sorted(args.sample_dir.glob('*.mdi')):
    row={'name':source.name,'sha256':digest(source)}
    try:
        p=read_pattern(str(source))
        row.update(ok=True,points=len(p.x),range=p.metadata['two_theta_range'],notes=p.metadata['notes'])
    except ValueError as exc:row.update(ok=False,error=str(exc))
    assert digest(source)==row['sha256']
    rows.append(row)
result={'files':rows,'succeeded':sum(r['ok'] for r in rows),'total':len(rows),'inputs_unchanged':True,
    'note':'A rejected malformed ASCII source can still be read/exported using JADE; no original was modified.'}
target=ROOT/'validation'/'full'/'bundled-ascii-inspection.local.json'
target.parent.mkdir(parents=True,exist_ok=True)
target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:result[k] for k in ['succeeded','total','inputs_unchanged']}))
