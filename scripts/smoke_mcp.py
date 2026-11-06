"""Live MCP integration test. --ui explicitly opts into JADE sample-file/menu interaction."""
import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[1]


async def main(ui=False):
    async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(ROOT/'run_server.py')],
            cwd=str(ROOT),env={'PYTHONUTF8':'1'})) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            tools=await session.list_tools()
            evidence={'tools':[tool.name for tool in tools.tools],'calls':[]}
            async def call(name,args=None):
                result=await session.call_tool(name,args or {})
                if result.isError:
                    raise RuntimeError(f'{name}: {result.content}')
                evidence['calls'].append({'tool':name,'ok':True})
                if result.structuredContent is not None:
                    return result.structuredContent
                return json.loads(next(item.text for item in result.content if item.type=='text'))
            capabilities=await call('jade_capabilities')
            assert capabilities['version']=='0.1.0'
            state=await call('jade_state')
            main_window=next(w for w in state['windows'] if w['text'].startswith('Jade 9'))
            handle=main_window['handle']
            menus=await call('jade_menus',{'window_handle':handle})
            assert len(menus['menus'])>=8
            workflows=await call('jade_workflows',{'window_handle':handle})
            assert workflows['peak_search']
            evidence['research_entry_points']={key:len(value) for key,value in workflows.items()}
            (ROOT/'validation').mkdir(exist_ok=True)
            (ROOT/'validation'/'menus.local.json').write_text(json.dumps(menus,ensure_ascii=False,indent=2),encoding='utf-8')
            image=await session.call_tool('jade_screenshot',{'window_handle':handle})
            assert not image.isError
            block=next(item for item in image.content if item.type=='image')
            (ROOT/'validation'/'jade-window.local.png').write_bytes(base64.b64decode(block.data))
            evidence['calls'].append({'tool':'jade_screenshot','ok':True})
            converted=await call('xrd_convert',{'values':[30,45],'from_unit':'two_theta_deg','wavelength_angstrom':1.5406})
            assert len(converted['d_angstrom'])==2
            if ui:
                # Close only JADE's startup project chooser; do not alter data or user settings here.
                state=await call('jade_state')
                chooser=next((w for w in state['windows'] if '选择工程标识' in w['text']),None)
                if chooser:
                    close=next(c for c in chooser['controls'] if c['text']=='关闭(&C)')
                    state=await call('jade_click_control',{'control_handle':close['handle'],
                        'observation_token':state['observation_token']})
                    assert not any(w['text']=='选择工程标识以运行JADE' for w in state['windows'])
                # Read available radiation choices, no selection change.
                main_window=next(w for w in state['windows'] if w['handle']==handle)
                radiation=next(c for c in main_window['controls'] if c['control_id']==116)
                choices=await call('jade_control_items',{'control_handle':radiation['handle']})
                assert choices['items']
                state=await call('jade_state')
                opened=await call('jade_open_file',{'path':str(ROOT/'examples'/'synthetic_two_peaks.mdi'),
                    'window_handle':handle,'observation_token':state['observation_token'],'import_mode':'clipboard'})
                await asyncio.sleep(1)
                state=await call('jade_state')
                evidence['after_open_titles']=[w['text'] for w in state['windows']]
                assert any('10.0/90.0/0.02' in title for title in evidence['after_open_titles']), 'Imported scan range was not confirmed'
                exported=await call('jade_export_pattern',{'window_handle':handle,'observation_token':state['observation_token']})
                evidence['jade_export']=exported
                main_observation=next(w for w in state['windows'] if w['handle']==handle)
                table=next(c for c in main_observation['controls'] if c['class_name']=='ListView20WndClass')
                rows=await call('jade_read_table',{'control_handle':table['handle'],'max_rows':2})
                assert rows['row_count']>0
                # Enter actual background-fit dialog, validate it appears, then Escape back out.
                state=await call('jade_state')
                menu=workflows['background'][0]
                dispatched=await call('jade_menu',{'window_handle':handle,'index_path':menu['index_path'],
                    'observation_token':state['observation_token']})
                await asyncio.sleep(.5)
                state=await call('jade_state')
                dialog=next((w for w in state['windows'] if '背景' in w['text']),None)
                assert dialog, 'Background dialog did not appear'
                evidence['background_dialog_title']=dialog['text']
                state=await call('jade_key',{'window_handle':dialog['handle'],'key':'escape',
                    'observation_token':state['observation_token']})
                assert not any('背景拟合和' in w['text'] for w in state['windows'])
                evidence['calls'].append({'verification':'background dialog opened and dismissed','ok':True})
            (ROOT/'validation'/('mcp-ui-smoke.local.json' if ui else 'mcp-smoke.local.json')).write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'tool_count':len(evidence['tools']),'tested_calls':len(evidence['calls']),
                              'ui_tested':ui,'research_entry_points':evidence['research_entry_points']},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--ui',action='store_true')
    asyncio.run(main(parser.parse_args().ui))
