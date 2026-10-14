"""Conservative automatic staging of source paths (not content DLP).

Inspect both index/worktree and intermediate commits. Never clean, reset or
silently include local runtime data. Existing manually committed source is kept.
"""
import os
from pathlib import Path, PurePosixPath
import subprocess

RUNTIME_ROOTS = frozenset(('log','logs','console','peers','projects','outbox','inbox',
                          'events','feedback','intake','attachments','local-data'))
PRIVATE_PARTS = frozenset(('.runtime','.scratch','.wt','.git','.ssh','.private',
                          'node_modules','__pycache__','browser-profile','collected'))
PRIVATE_NAMES = frozenset((
    'auth.json', 'cookies.json', 'cookies.txt',
    'credentials.json', 'id_ed25519', 'id_rsa',
    'local.md', 'mcp_token.txt', 'plugins.local.json',
    'privacy.local.txt', 'settings.json', 'storage-state.json',
    'storage_state.json',
))
SOURCE_ROOTS = frozenset(('src','tests','test','web','assets','plugins','scripts','tools','docs',
                         'lib','include','cmd','config','configs','migrations','app','server',
                         'client','public','.github','.agents','.claude','.context','adapters'))
SOURCE_SUFFIXES = frozenset(('.py','.js','.mjs','.cjs','.ts','.tsx','.jsx','.json','.md','.txt',
                            '.toml','.yaml','.yml','.ini','.cfg','.css','.scss','.html','.svg',
                            '.png','.jpg','.jpeg','.gif','.webp','.ico','.bat','.ps1','.vbs',
                            '.sh','.rs','.go','.c','.h','.cpp','.hpp','.cs','.java','.kt',
                            '.lua','.sql','.xml','.lock','.example','.template'))
SOURCE_NAMES = frozenset(('.gitignore','.gitattributes','.editorconfig','dockerfile',
                         'makefile','license','procfile'))


def _git(repo, *args, data=None):
    # Do not let another tool's repository/index environment redirect submission.
    env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    env.update(PYTHONIOENCODING='utf-8', GIT_TERMINAL_PROMPT='0')
    result = subprocess.run(['git','-C',os.fspath(repo),*args],input=data,capture_output=True,
                            timeout=45,env=env,creationflags=0x08000000 if os.name=='nt' else 0)
    if result.returncode:
        raise ValueError('Submission Git inspection or staging failed; original files retained')
    return result.stdout


def _paths(raw):
    return [p.decode('utf-8') for p in raw.split(b'\0') if p]


def _protected(path):
    p=PurePosixPath(path);parts=tuple(x.lower() for x in p.parts);name=p.name.lower()
    if not parts or p.is_absolute() or '..' in parts or '\\' in path or any(ord(c)<32 for c in path):
        return True
    return (parts[0] in RUNTIME_ROOTS or any(x in PRIVATE_PARTS for x in parts)
            or name in PRIVATE_NAMES or name.endswith(('.pem','.key','.p12','.pfx','.db','.sqlite','.sqlite3','.log'))
            or (name.startswith('.env') and not name.endswith(('.example','.template')))
            or name.endswith('.local.json') or name.endswith('.local.toml'))


def _new_source(path):
    p=PurePosixPath(path)
    return ((len(p.parts)==1 or p.parts[0].lower() in SOURCE_ROOTS)
            and (p.suffix.lower() in SOURCE_SUFFIXES or p.name.lower() in SOURCE_NAMES))


def _check_paths(paths, new=()):
    bad=[p for p in paths if _protected(p)]
    unknown=[p for p in new if not _protected(p) and not _new_source(p)]
    if bad or unknown:
        raise ValueError('Automatic submission blocked: protected or unclassified paths '
                         f'({len(set(bad+unknown))}); inspect local Git status and classify explicitly')


def stage_source_changes(repo, base):
    """Validate everything first, then stage only the exact literal path set.

    Removals of legacy runtime files are allowed; adding/modifying them in any
    outgoing commit is rejected even if later deleted. No permission override.
    """
    head=_git(repo,'rev-parse','HEAD').decode().strip()
    _git(repo,'merge-base','--is-ancestor',base,head)
    # The blank pretty format uses newlines between commits; valid Git paths
    # containing control characters are deliberately outside auto-submission.
    history=_paths(_git(repo,'log','-m','--format=','--name-only','--no-renames','-z',
                        '--diff-filter=ACMRT',base+'..'+head,'--'))
    history=[p.lstrip('\n') for p in history if p.lstrip('\n')]
    introduced=_paths(_git(repo,'log','-m','--format=','--name-only','--no-renames','-z',
                           '--diff-filter=A',base+'..'+head,'--'))
    introduced=[p.lstrip('\n') for p in introduced if p.lstrip('\n')]
    _check_paths(history,new=introduced)
    unstaged=_paths(_git(repo,'diff','--name-only','--no-renames','-z','HEAD','--'))
    indexed=_paths(_git(repo,'diff','--cached','--name-only','--no-renames','-z','--'))
    untracked=_paths(_git(repo,'ls-files','--others','--exclude-standard','-z'))
    # Both working tree and index are checked: staged private files cannot hide
    # behind an unstaged deletion. Only actual deletion entries get the exemption.
    dirty_non_deletions=_paths(_git(repo,'diff','--name-only','--no-renames','-z','--diff-filter=ACMRT','HEAD','--'))
    staged_non_deletions=_paths(_git(repo,'diff','--cached','--name-only','--no-renames','-z','--diff-filter=ACMRT','--'))
    # A new file already in the index is no longer "untracked". Classify added
    # paths from BOTH views; a worktree deletion must not hide an index addition.
    dirty_added=_paths(_git(repo,'diff','--name-only','--no-renames','-z','--diff-filter=A','HEAD','--'))
    staged_added=_paths(_git(repo,'diff','--cached','--name-only','--no-renames','-z','--diff-filter=A','--'))
    _check_paths(dirty_non_deletions+staged_non_deletions+untracked,
                 new=untracked+dirty_added+staged_added)
    paths=sorted(set(unstaged+indexed+untracked))
    if len(paths)>10000:raise ValueError('Automatic submission path budget exceeded')
    root=Path(repo).resolve()
    for path in paths:
        file=root/path
        if file.exists() or file.is_symlink():
            if file.is_symlink() or any(parent.is_symlink() for parent in file.parents if parent!=root and root in parent.parents):
                raise ValueError('Automatic submission of changed symbolic links is not supported')
    if _git(repo,'rev-parse','HEAD').decode().strip()!=head:
        raise ValueError('Build HEAD changed during submission preflight')
    if paths:
        _git(repo,'--literal-pathspecs','add','--pathspec-from-file=-','--pathspec-file-nul',
             data=b'\0'.join(p.encode('utf-8') for p in paths)+b'\0')
    return bool(_git(repo,'diff','--cached','--name-only','-z'))


def changed_paths(repo, base, head):
    return _paths(_git(repo,'diff','--name-only','--no-renames','-z',base,head,'--'))
