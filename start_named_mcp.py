"""Start the loopback MCP server detached from tool/editor terminals (Windows).
No token is printed or placed on the child command line. Run without arguments for 8791.
"""
from pathlib import Path
import argparse,json,os,socket,subprocess,sys,urllib.request
HERE=Path(__file__).resolve().parent
# The workbench is a standalone project; cwd for the server is the project root itself.
ROOT=HERE

def start(port=8791):
    if os.name!='nt':raise RuntimeError('This Windows launcher is not for another operating system')
    with socket.socket() as s:
        if s.connect_ex(('127.0.0.1',port))==0:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=3) as response: h=json.load(response)
            if h.get('service')=='bus_mcp':
                # 只认服务身份，不认版本字符串：版本硬编码会让升级后的新服务被误判为「被占用」
                print('ALREADY_RUNNING',port);return None
            raise RuntimeError('Port occupied by a different or old service; no process was stopped')
    import first_run  # 新克隆的仓库：建运行目录、生成主令牌
    first_run.ensure()
    token_file=HERE/'console'/'mcp_token.txt'
    if not token_file.exists() or len(token_file.read_text(encoding='utf-8-sig').strip())<32:
        raise RuntimeError('Missing private MCP token file')
    log=HERE/'console'/f'mcp_service_{port}.log'
    env=dict(os.environ);env.pop('BUS_MCP_TOKEN',None)
    env['PYTHONUTF8']='1';env['PYTHONIOENCODING']='utf-8'
    # CREATE_NO_WINDOW instead of DETACHED_PROCESS: the server itself must have no console, and
    # its children are spawned through runtime_exec with CREATE_NO_WINDOW, so nothing ever pops up.
    flags=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_BREAKAWAY_FROM_JOB
    with log.open('ab',buffering=0) as stream:
        proc=subprocess.Popen([sys.executable,'-X','utf8',str(HERE/'bus_mcp.py'),'--host','127.0.0.1','--port',str(port),'--token-file',str(token_file)],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,env=env,creationflags=flags,close_fds=True)
    (HERE/'console'/f'mcp_process_{port}.json').write_text(json.dumps({'pid':proc.pid,'port':port,'script':str(HERE/'bus_mcp.py'),'mechanism':'no-window-new-process-group-breakaway'}),encoding='utf-8')
    print('STARTED',port,'PID',proc.pid)
    return proc.pid

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=int,default=8791);a=ap.parse_args();start(a.port)
