"""Reproducible stdio acceptance of every independent XRD tool; reads JADE without changing it.

Run from project: .venv/Scripts/python.exe scripts/acceptance_mcp.py
GUI workflow evidence is recorded separately; this does not certify material composition.
"""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[1]


async def main():
    evidence={'started_utc':datetime.now(timezone.utc).isoformat(),'calls':[],'checks':[]}
    xy=ROOT/'examples'/'synthetic_two_peaks.xy'
    cif=ROOT/'examples'/'illustrative_Mg.cif'
    inputs=[xy,cif]
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    installed='--installed' in sys.argv
    evidence['server_mode']='installed package' if installed else 'source run_server.py'
    async with stdio_client(StdioServerParameters(command=sys.executable,
            args=['-m','jade_mcp.server'] if installed else [str(ROOT/'run_server.py')],
            cwd=str(Path(sys.prefix)) if installed else str(ROOT),env={'PYTHONUTF8':'1'})) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            listing=await session.list_tools()
            evidence['tools']=[x.name for x in listing.tools]
            assert len(listing.tools)==32
            async def call(tool,args=None,error=False):
                r=await session.call_tool(tool,args or {})
                evidence['calls'].append({'tool':tool,'args':args or {},'expected_error':error,
                    'result':r.model_dump(mode='json')})
                assert bool(r.isError)==error,(tool,r.content)
                if error:return None
                return r.structuredContent or json.loads(next(x.text for x in r.content if x.type=='text'))
            def check(label,passed):
                assert passed,label
                evidence['checks'].append(label)
            await call('jade_capabilities')
            s=await call('jade_state')
            main=next((w for w in s['windows'] if w['text'].startswith('Jade 9')),None)
            if main:
                handle=main['handle']
                ready=await call('jade_wait_ready',{'window_handle':handle,'timeout_seconds':2})
                await call('jade_menus',{'window_handle':handle})
                await call('jade_find_menu',{'window_handle':handle,'query':'全峰图精细化|宏处理'})
                workflows=await call('jade_workflows',{'window_handle':handle})
                check('WPF resolves to real refinement command',any('全峰图精细化' in m['text'] for m in workflows['whole_pattern_refinement']))
                await call('jade_key',{'window_handle':handle,'key':'enter','observation_token':'expired-test-token'},error=True)
            inspected=await call('xrd_inspect',{'path':str(xy)})
            check('Synthetic input has 4001 points',inspected['metadata']['points']==4001)
            analysis=await call('xrd_analyze',{'path':str(xy),'wavelength_angstrom':1.5406})
            check('Two known peaks recovered',analysis['peak_count']==2 and np.allclose([p['two_theta_deg'] for p in analysis['peaks']],[32.15,44.4],atol=.03))
            fit=await call('xrd_fit_peaks',{'path':str(xy),'centers_deg':[32.15,44.4],'wavelength_angstrom':1.5406})
            check('Known fitted widths recovered',np.allclose([p['fwhm_deg'] for p in fit['peaks']],[.24,.36],rtol=.01))
            angles=np.array([30.,45.,60.,80.,100.])
            theta=np.deg2rad(angles/2)
            widths=np.hypot(np.rad2deg((.9*1.5406/600+4*.0012*np.sin(theta))/np.cos(theta)),.06)
            wh=await call('xrd_williamson_hall',{'two_theta_deg':angles.tolist(),'fwhm_deg':widths.tolist(),
                'instrument_fwhm_deg':[.06]*5,'wavelength_angstrom':1.5406})
            check('Known W-H size and strain recovered',abs(wh['domain_size_nm']-60)<1e-5 and abs(wh['microstrain']-.0012)<1e-8)
            width=float(np.rad2deg(.9*1.5406/(500*np.cos(np.deg2rad(20)))))
            size=await call('xrd_scherrer',{'two_theta_deg':40,'fwhm_deg':width,'wavelength_angstrom':1.5406})
            check('Scherrer returns nm correctly',abs(size['coherent_domain_size_nm']-50)<1e-8)
            hkls=np.array([[1,1,1],[2,0,0],[2,2,0],[3,1,1]])
            tt=np.rad2deg(2*np.arcsin(1.5406*np.sqrt((hkls**2).sum(axis=1))/(2*4.05)))
            lattice=await call('xrd_refine_lattice',{'two_theta_deg':tt.tolist(),'hkls':hkls.tolist(),
                'wavelength_angstrom':1.5406,'system':'cubic'})
            check('Cubic lattice recovered',abs(lattice['cell_angstrom']['a']-4.05)<1e-8)
            conversion=await call('xrd_convert',{'values':[30,45,80],'from_unit':'two_theta_deg','wavelength_angstrom':1.5406})
            back=await call('xrd_convert',{'values':conversion['q_inverse_angstrom'],'from_unit':'q_inverse_angstrom','wavelength_angstrom':1.5406})
            check('Bragg roundtrip',np.allclose(back['two_theta_deg'],[30,45,80]))
            sim=await call('xrd_simulate_cif',{'cif_path':str(cif),'wavelength_angstrom':1.5406})
            check('CIF produces reflections',len(sim['peaks'])>=5)
            await call('xrd_match_cifs',{'path':str(xy),'cif_paths':[str(cif)],'wavelength_angstrom':1.5406})
            export=await call('xrd_export_xy',{'path':str(xy),'format':'mdi','wavelength_angstrom':1.5406})
            restored=await call('xrd_inspect',{'path':export['output']})
            check('MDI preserves count and wavelength',restored['metadata']['points']==4001 and restored['metadata']['wavelength_angstrom']==1.5406)
            overlay=await call('xrd_compare_patterns',{'paths':[str(xy),export['output']]})
            batch=await call('xrd_batch_analyze',{'paths':[str(xy),str(xy)+'.missing'],'wavelength_angstrom':1.5406})
            check('Batch retains per-file failure',batch['succeeded']==1 and batch['total']==2 and not batch['files'][1]['ok'])
            saved=await call('xrd_read_result',{'result_json_path':str(Path(analysis['output_dir'])/'analysis.json')})
            check('Saved JSON matches returned result',saved['input']==analysis['input'] and saved['peaks']==analysis['peaks'])
            await call('xrd_read_result',{'result_json_path':str(ROOT/'pyproject.toml')},error=True)
            await call('xrd_scherrer',{'two_theta_deg':40,'fwhm_deg':.05,'instrument_fwhm_deg':.1,'wavelength_angstrom':1.5406},error=True)
            check('Input hashes unchanged',all(hashlib.sha256(p.read_bytes()).hexdigest()==before[str(p)] for p in inputs))
            evidence['input_sha256']=before
            evidence['artifact_directories']=[analysis['output_dir'],fit['output_dir'],wh['output_dir'],sim['output_dir'],overlay['output_dir'],batch['output_dir']]
    evidence['finished_utc']=datetime.now(timezone.utc).isoformat()
    target=ROOT/'validation'/'full'/('independent-mcp-acceptance-installed.local.json' if installed else 'independent-mcp-acceptance.local.json')
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'checks':len(evidence['checks']),'tool_calls':len(evidence['calls']),
        'distinct_tools':len({x['tool'] for x in evidence['calls']}),'evidence':str(target)},ensure_ascii=False))


if __name__=='__main__':asyncio.run(main())
