"""Read a bounded public snapshot from immutable Git blobs, never the working tree.
No remote operations or source-repository writes. This is not a secret-content detector.
"""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

RUNTIME_ROOTS = frozenset({'log','logs','inbox','outbox','peers','console','projects',
                          'attachments','events','intake','history','tmp','feedback','profiles','workbench'})
PRIVATE_PARTS = frozenset({'.git','.wt','.runtime','.scratch','.private','.ssh',
                           'node_modules','__pycache__','.workbench-locks','.workbuddy'})
PRIVATE_NAMES = frozenset((
    'auth.json', 'cookies.json', 'cookies.txt',
    'credentials.json', 'id_ed25519', 'id_rsa',
    'local.md', 'mcp_token.txt', 'plugins.local.json',
    'privacy.local.txt', 'settings.json', 'storage-state.json',
    'storage_state.json',
))
PRIVATE_SUFFIXES = frozenset({'.pem','.key','.p12','.pfx','.db','.sqlite','.sqlite3','.log','.mutex','.lock','.bak'})
MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_FILES = 10000


def git_env():
    env = {k:v for k,v in os.environ.items() if not k.upper().startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1')
    return env


def git_bytes(root, *args, input=None):
    p = subprocess.run(['git','-C',str(root),*args], input=input, capture_output=True,
                       env=git_env(), timeout=60)
    if p.returncode:
        raise ValueError('Unable to read the requested Git snapshot; source unchanged')
    return p.stdout


def checked_path(name):
    if not isinstance(name,str) or not name or '\\' in name or ':' in name:
        raise ValueError('Non-portable snapshot path')
    path = PurePosixPath(name)
    parts = name.split('/')
    if path.is_absolute() or any(p in ('','.','..') for p in parts):
        raise ValueError('Invalid snapshot path')
    for part in parts:
        if part.endswith((' ','.')) or re.search(r'[\x00-\x1f<>"|?*]',part):
            raise ValueError('Non-portable snapshot path')
        if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',part):
            raise ValueError('Reserved snapshot path')
    return path


def mandatory_excluded(name):
    parts = name.lower().split('/')
    leaf = parts[-1]
    return (parts[0] in RUNTIME_ROOTS or parts[0].startswith('agentworkbench_intake_')
            or any(p in PRIVATE_PARTS for p in parts)
            or name.lower().startswith(('arena/data/','arena/leader/','plugins/arena/server/data/'))
            or (parts[0]=='adapters' and leaf.endswith('.state.json'))
            or '.bak-' in leaf
            or leaf in PRIVATE_NAMES or '.local.' in leaf
            or (leaf.startswith('.env') and not leaf.endswith(('.example','.sample','.template')))
            or PurePosixPath(leaf).suffix in PRIVATE_SUFFIXES)


def configured_excluded(name, patterns):
    return any(name == p or (p.endswith('/') and name.startswith(p)) for p in patterns)


def tree(root, commit):
    rows = []
    for record in git_bytes(root,'ls-tree','-r','-l','-z',commit).split(b'\0'):
        if not record:continue
        header, name = record.split(b'\t',1)
        mode, kind, oid, size = header.split()
        rows.append((name.decode('utf-8'),mode.decode(),kind.decode(),oid.decode(),
                     int(size) if size != b'-' else None))
    if len(rows)>MAX_FILES:raise ValueError('Snapshot file count exceeds the bound')
    return rows


def policy(root, entries):
    item = next((r for r in entries if r[0]=='scripts/export_public.json'),None)
    if not item or item[1] not in ('100644','100755') or item[4]>1024*1024:
        raise ValueError('Missing or invalid committed public export policy')
    try:cfg=json.loads(git_bytes(root,'cat-file','blob',item[3]))
    except (ValueError,UnicodeError):raise ValueError('Invalid committed export policy') from None
    if not isinstance(cfg,dict):raise ValueError('Export policy must be an object')
    for key in ('exclude','scan_allow','generic_skip'):
        value=cfg.get(key,[])
        if not isinstance(value,list) or not all(isinstance(v,str) for v in value):
            raise ValueError('Invalid export policy list: '+key)
    return cfg


def materialize(root, ref, destination):
    """Create a NEW local snapshot directory and return pinned policy/file hashes.

    Select first, read all selected bounded blobs, then write. Existing outputs,
    symlinks, gitlinks, portable filename collisions and invalid refs fail closed.
    Credentials and runtime paths cannot be re-enabled by export policy exclusions.
    """
    destination=Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Snapshot destination must not already exist')
    if not isinstance(ref,str) or not ref or '\x00' in ref:
        raise ValueError('A Git reference is required')
    commit=git_bytes(root,'rev-parse','--verify','--end-of-options',ref+'^{commit}').decode().strip()
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',commit):raise ValueError('Invalid commit identity')
    entries=tree(root,commit);cfg=policy(root,entries);selected=[];seen=set();total=0
    for name,mode,kind,oid,size in entries:
        if mandatory_excluded(name) or configured_excluded(name,cfg.get('exclude',[])):continue
        checked_path(name)
        if mode not in ('100644','100755') or kind!='blob':
            raise ValueError('Symlinks and submodules are not supported in public snapshots')
        if name.casefold() in seen:raise ValueError('Case-colliding snapshot paths')
        seen.add(name.casefold())
        if size is None or size>MAX_FILE:raise ValueError('Snapshot file exceeds the bound')
        total+=size
        if total>MAX_TOTAL:raise ValueError('Snapshot total exceeds the bound')
        selected.append((name,mode,oid,size))
    if not selected:raise ValueError('Public snapshot would be empty')
    # Object IDs come from the pinned Git tree, not pathspecs or user command strings.
    stream=io.BytesIO(git_bytes(root,'cat-file','--batch',input=''.join(r[2]+'\n' for r in selected).encode('ascii')))
    payloads=[]
    for name,mode,oid,size in selected:
        if stream.readline().strip().split()!=[oid.encode(),b'blob',str(size).encode()]:
            raise ValueError('Snapshot blob metadata mismatch')
        body=stream.read(size)
        if len(body)!=size or stream.read(1)!=b'\n':raise ValueError('Truncated snapshot blob')
        payloads.append((name,mode,body))
    if stream.read(1):raise ValueError('Unexpected snapshot data')
    destination.mkdir(parents=True,exist_ok=False)
    files={}
    for name,mode,body in payloads:
        target=destination.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as f:f.write(body)
        if os.name!='nt':target.chmod(0o755 if mode=='100755' else 0o644)
        files[name]={'sha256':hashlib.sha256(body).hexdigest(),'mode':mode,'bytes':len(body)}
    return {'commit':commit,'files':files,'config':cfg,'bytes':total,
            'excluded_count':len(entries)-len(selected),'history_exported':False}
