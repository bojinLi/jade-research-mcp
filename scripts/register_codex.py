"""Add only this MCP entry; back up and verify the complete original config without printing its contents."""
import argparse
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tomllib

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,default=Path.home()/'.codex'/'config.toml')
    parser.add_argument('--backup-dir',type=Path,required=True)
    parser.add_argument('--jade-exe',type=Path,default=Path(r'E:\MDI Jade 9\jade9c.exe'))
    args=parser.parse_args()
    python=ROOT/'.venv'/'Scripts'/'python.exe'
    if not python.is_file() or not args.jade_exe.is_file():raise SystemExit('Install the environment and JADE first')
    env={'PYTHONUTF8':'1','JADE_EXE':str(args.jade_exe.resolve()),'JADE_RESULTS_DIR':str(ROOT/'results')}
    settings={'command':str(python),'args':[str(ROOT/'run_server.py')],'cwd':str(ROOT),
              'startup_timeout_sec':30,'tool_timeout_sec':180,'enabled':True,'env':env}
    quote=lambda value:json.dumps(value,ensure_ascii=False)
    snippet='\n[mcp_servers.jade]\n'+'\n'.join(f'{key} = {quote(value) if not isinstance(value,bool) else str(value).lower()}'
            for key,value in settings.items() if key!='env')+'\n\n[mcp_servers.jade.env]\n'+\
            '\n'.join(f'{key} = {quote(value)}' for key,value in env.items())+'\n'
    (ROOT/'codex-config.toml').write_text(snippet.lstrip(),encoding='utf-8')
    (ROOT/'mcp-client-config.json').write_text(json.dumps({'mcpServers':{'jade':{
        'command':str(python),'args':[str(ROOT/'run_server.py')],'env':env}}},ensure_ascii=False,indent=2),encoding='utf-8')
    config=args.config.resolve()
    original=config.read_bytes() if config.exists() else b''
    before=tomllib.loads(original.decode('utf-8-sig'))
    existing=before.get('mcp_servers',{}).get('jade')
    if existing is not None:
        if existing==settings:
            print(json.dumps({'server':'jade','status':'already configured'},ensure_ascii=False));return
        raise SystemExit('A different jade MCP entry already exists; it was left unchanged')
    text=original.decode('utf-8-sig')
    updated=(text.rstrip()+'\n'+snippet).encode('utf-8')
    parsed=tomllib.loads(updated.decode('utf-8'))
    preserved=copy.deepcopy(parsed)
    del preserved['mcp_servers']['jade']
    if 'mcp_servers' not in before and not preserved['mcp_servers']:del preserved['mcp_servers']
    if preserved!=before:raise RuntimeError('Unrelated configuration changed; refusing to write')
    backup=args.backup_dir.resolve()
    backup.mkdir(parents=True,exist_ok=True)
    filename=backup/f'config.before-jade.{datetime.now():%Y%m%d-%H%M%S}.toml'
    if original:filename.write_bytes(original)
    config.parent.mkdir(parents=True,exist_ok=True)
    temp=config.with_name('config.jade-mcp.tmp')
    if temp.exists():raise RuntimeError('Temporary config file already exists; refusing to replace it')
    if config.exists() and config.read_bytes()!=original:raise RuntimeError('Config changed during setup; retry after checking it')
    try:
        with temp.open('xb') as stream:stream.write(updated)
        os.replace(temp,config)
        if tomllib.loads(config.read_text(encoding='utf-8'))!=parsed:raise RuntimeError('Config verification failed')
    finally:
        if temp.exists():temp.unlink()
    report={'server':'jade','status':'configured','backup':str(filename),'preserved_other_settings':True,
            'original_sha256':hashlib.sha256(original).hexdigest(),'entry':settings}
    (ROOT/'validation'/'setup.local.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:report[key] for key in ['server','status','preserved_other_settings']},ensure_ascii=False))


if __name__=='__main__':main()
