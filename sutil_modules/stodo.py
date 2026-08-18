#!/usr/bin/env python3
import argparse
import datetime as dt
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCLIPPLE = os.environ.get('SCLIPPLE', 'sclipple')
STODO_DIR = Path(os.environ.get('STODO_DIR', str(Path.home()/'.local/share/sclipple-todo'))).expanduser()
SENTINEL = '.stodo-root'
SELF = str(Path(__file__).resolve())
VALID_KEY = re.compile(r'^[A-Za-z0-9_-]+$')
TERMINAL = {'done','archived','trash'}

class Error(Exception): pass

def fail(msg): raise Error(msg)

def now(): return dt.datetime.now().astimezone().strftime('%Y-%m-%dT%H:%M:%S%z')
def today(): return dt.date.today()

def run(cmd, *, capture=False, check=True, input_text=None):
    p = subprocess.run(cmd, text=True, input=input_text,
                       stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None)
    if check and p.returncode:
        msg=(p.stderr or '').strip() if capture else ''
        fail(msg or f'command failed: {cmd[0]}')
    return p

def check_root(require=True):
    if not STODO_DIR.is_absolute(): fail(f'STODO_DIR must be absolute: {STODO_DIR}')
    if require:
        if not STODO_DIR.is_dir(): fail(f'not initialized: {STODO_DIR}; run: stodo init')
        if not (STODO_DIR/SENTINEL).is_file(): fail(f'refusing unmarked directory: {STODO_DIR}')

def init_root():
    if STODO_DIR.exists() and not STODO_DIR.is_dir(): fail(f'not a directory: {STODO_DIR}')
    if STODO_DIR.exists() and not (STODO_DIR/SENTINEL).exists() and any(STODO_DIR.iterdir()):
        fail(f'refusing to mark non-empty directory: {STODO_DIR}')
    STODO_DIR.mkdir(parents=True, exist_ok=True)
    (STODO_DIR/SENTINEL).write_text('stodo private TODO database\n', encoding='utf-8')
    print(STODO_DIR)

def sclipple(*args, capture=False):
    check_root()
    return run([SCLIPPLE, '--directory', str(STODO_DIR), *map(str,args)], capture=capture)

def list_keys():
    # Use sclipple itself as metadata authority. Parse first column of ls.
    p=run([SCLIPPLE,'--directory',str(STODO_DIR),'ls','-t','task'], capture=True, check=False)
    if p.returncode:
        return []
    out=[]
    for line in p.stdout.splitlines():
        line=line.strip()
        m=re.fullmatch(r'\[([A-Za-z0-9_-]+)\]', line)
        if m:
            out.append(m.group(1))
    return out

def slugify(title):
    s=title.lower().encode('ascii','ignore').decode()
    s=re.sub(r'[^a-z0-9]+','-',s).strip('-')
    return s[:48].rstrip('-')

def unique_key(base, keys):
    if base not in keys: return base
    n=2
    while f'{base}-{n}' in keys: n+=1
    return f'{base}-{n}'

def fallback_key(keys):
    n=1
    while f'task-{n}' in keys: n+=1
    return f'task-{n}'

def resolve_key(token, keys=None):
    keys = list_keys() if keys is None else keys
    if token in keys: return token
    matches=[k for k in keys if k.startswith(token)]
    if not matches: fail(f"unknown KEY or prefix '{token}'")
    if len(matches)>1: fail(f"ambiguous KEY prefix '{token}': {', '.join(matches)}")
    return matches[0]

def selectors(args, *, require=False):
    parts=[]
    keys=list_keys()
    for k in getattr(args,'keys',[]) or []: parts.append(resolve_key(k, keys))
    for t in getattr(args,'tags',[]) or []: parts += ['-t',t]
    if getattr(args,'tag_match',None): parts += ['--tag-match',args.tag_match]
    if not parts:
        if require: fail('explicit KEY or -t TAG selector required')
        parts=['-t','task']
    return parts

def safe_file(path):
    p=Path(path)
    try: rp=p.resolve(strict=True)
    except FileNotFoundError: fail(f'missing callback file: {p}')
    notes=(STODO_DIR/'notes').resolve()
    if rp.parent != notes: fail(f'refusing path outside private notes directory: {p}')
    if p.is_symlink() or not rp.is_file(): fail(f'refusing unsafe task file: {p}')
    return rp

def parse_task(path):
    p=safe_file(path)
    text=p.read_text(encoding='utf-8')
    head, sep, body=text.partition('\n---\n')
    d={}
    for ln in head.splitlines():
        if ': ' in ln:
            k,v=ln.split(': ',1); d[k]=v
    d['_body']=body if sep else ''
    d['_path']=p
    d['_key']=p.stem
    return d

def write_task(t):
    p=t['_path']; body=t.get('_body','')
    fields=['title','created','due','priority','status','status_since','completed','depends']
    data=''.join(f'{k}: {t.get(k,"-")}\n' for k in fields)+'---\n'+body
    fd,tmp=tempfile.mkstemp(prefix='.stodo-',dir=p.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f: f.write(data)
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def callback_cmd(name,*extra):
    return f'{shlex_quote(sys.executable)} {shlex_quote(SELF)} __{name}' + ''.join(' '+shlex_quote(x) for x in extra)

def shlex_quote(s):
    import shlex; return shlex.quote(str(s))

def hook(name, sels, *extra):
    sclipple('--editor', callback_cmd(name,*extra), *sels)

def event(key, action, detail=''):
    f=STODO_DIR/'.stodo-events.tsv'
    with f.open('a',encoding='utf-8') as h:
        h.write(f'{now()}\t{key}\t{action}\t{detail}\n')

def cmd_add(a):
    keys=list_keys(); words=list(a.title)
    if words and words[0]=='--': words=words[1:]
    title=' '.join(words).strip()
    if not title: fail('missing title')
    if a.key:
        if not VALID_KEY.match(a.key): fail('KEY must contain only ASCII letters, digits, _ or -')
        if a.key in keys: fail(f'KEY already exists: {a.key}')
        key=a.key
    else:
        base=slugify(title)
        key=unique_key(base,keys) if base else fallback_key(keys)
    cmd=[SCLIPPLE,'--directory',str(STODO_DIR),'add',key,'-t','task']
    for t in a.tags: cmd += ['-t',t]
    p=run(cmd,capture=True)
    env=os.environ.copy(); env.update({
        'STODO_INIT_TITLE':title,'STODO_INIT_CREATED':now(),'STODO_INIT_DUE':a.due,
        'STODO_INIT_PRIORITY':a.priority,'STODO_INIT_STATUS':a.status})
    cb=f'{shlex_quote(sys.executable)} {shlex_quote(SELF)} __init'
    q=subprocess.run([SCLIPPLE,'--directory',str(STODO_DIR),'--editor',cb,key],env=env,text=True,
                     stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if q.returncode: fail(q.stderr.strip() or 'initialization failed')
    event(key,'add',title); print(key)

def cmd_open(a): sclipple(*selectors(a))
def cmd_ls(a): sclipple('ls',*selectors(a))
def cmd_raw(a): sclipple('--editor','cat',*selectors(a))
def cmd_agenda(a): hook('view',selectors(a),'agenda')
def cmd_status_view(a,status): hook('view',selectors(a),status)
def cmd_ready(a): hook('view',selectors(a),'ready')
def cmd_blocked(a): hook('view',selectors(a),'blocked')
def cmd_overdue(a): hook('view',selectors(a),'overdue')
def cmd_stats(a): hook('stats',selectors(a))

def cmd_transition(a,status):
    sels=selectors(a,require=True); hook('setstatus',sels,status)
def cmd_setfield(a,field,value): hook('setfield',selectors(a,require=True),field,value)
def cmd_note(a):
    key=resolve_key(a.key); hook('note',[key],a.text)
def cmd_depend(a):
    key=resolve_key(a.key); deps=[resolve_key(x) for x in a.deps]
    hook('setfield',[key],'depends',' '.join(deps) if deps else '-')
def cmd_undepend(a): cmd_setfield(argparse.Namespace(keys=[a.key],tags=[],tag_match=None),'depends','-')
def cmd_tag(a,remove=False):
    keys=[resolve_key(k) for k in a.keys]
    if not keys: fail('KEY required')
    sclipple('untag' if remove else 'tag',*keys,'-t',a.tag)
    for k in keys: event(k,'untag' if remove else 'tag',a.tag)
def cmd_archive(a): cmd_transition(a,'archived')
def cmd_remove(a): cmd_transition(a,'trash')
def cmd_restore(a): cmd_transition(a,'next')
def cmd_purge(a):
    sels=selectors(a,require=True)
    # sclipple owns deletion/index consistency.
    sclipple('rm',*sels)
def cmd_history(a):
    f=STODO_DIR/'.stodo-events.tsv'
    if not f.exists(): return
    key=resolve_key(a.key) if a.key else None
    for ln in f.read_text(encoding='utf-8').splitlines():
        if not key or ('\t'+key+'\t') in ln: print(ln)

def dep_done(key):
    keys=list_keys()
    if key not in keys: return False
    # Ask sclipple to cat exact task, avoiding knowledge of index; parse temporary stdout.
    p=sclipple('--editor','cat',key,capture=True)
    for ln in p.stdout.splitlines():
        if ln.startswith('status: '): return ln[8:] in TERMINAL
    return False

def is_blocked(t):
    deps=[x for x in t.get('depends','-').split() if x!='-']
    return any(not dep_done(k) for k in deps)

def due_date(v):
    try: return dt.date.fromisoformat(v)
    except Exception: return None

def render(tasks, mode):
    rows=[]
    for t in tasks:
        st=t.get('status','?'); blocked=is_blocked(t)
        due=due_date(t.get('due','-'))
        ok=False
        if mode=='agenda': ok=st not in {'archived','trash'}
        elif mode in {'inbox','today','waiting','done','next'}: ok=(st==mode)
        elif mode=='ready': ok=(st in {'next','today','inbox'} and not blocked)
        elif mode=='blocked': ok=(st not in TERMINAL and blocked)
        elif mode=='overdue': ok=(st not in TERMINAL and due is not None and due < today())
        if ok: rows.append(t)
    rank={'A':0,'B':1,'C':2}
    rows.sort(key=lambda t:(rank.get(t.get('priority'),9), due_date(t.get('due','-')) or dt.date.max, t['_key']))
    print(f"{'STATUS':10} {'P':1} {'DUE':10} {'KEY':24} TITLE")
    print(f"{'-'*10} {'-':1} {'-'*10} {'-'*24} {'-'*5}")
    for t in rows:
        st=t.get('status','?') + ('*' if is_blocked(t) else '')
        print(f"{st:10.10} {t.get('priority','-'):1.1} {t.get('due','-'):10.10} {t['_key']:24.24} {t.get('title','')}")

def cb_init(files):
    if len(files)!=1: fail('__init expects one file')
    p=safe_file(files[0]);
    if p.stat().st_size: fail('refusing to initialize non-empty file')
    t={'_path':p,'_body':'','title':os.environ['STODO_INIT_TITLE'],'created':os.environ['STODO_INIT_CREATED'],
       'due':os.environ['STODO_INIT_DUE'],'priority':os.environ['STODO_INIT_PRIORITY'],
       'status':os.environ['STODO_INIT_STATUS'],'status_since':os.environ['STODO_INIT_CREATED'],
       'completed':'-','depends':'-'}
    write_task(t)

def cb_view(mode,files): render([parse_task(f) for f in files],mode)
def cb_stats(files):
    ts=[parse_task(f) for f in files]; counts={}
    for t in ts: counts[t.get('status','other')]=counts.get(t.get('status','other'),0)+1
    ready=sum(t.get('status') in {'next','today','inbox'} and not is_blocked(t) for t in ts)
    overdue=sum(t.get('status') not in TERMINAL and (due_date(t.get('due','-')) or dt.date.max)<today() for t in ts)
    print(f'total\t{len(ts)}'); print(f'ready\t{ready}'); print(f'overdue\t{overdue}')
    for k in sorted(counts): print(f'{k}\t{counts[k]}')
def cb_setstatus(status,files):
    ts=now()
    for f in files:
        t=parse_task(f); old=t.get('status','-'); t['status']=status; t['status_since']=ts
        t['completed']=ts if status=='done' else ('-' if old=='done' else t.get('completed','-'))
        write_task(t); event(t['_key'],'status',f'{old}->{status}')
def cb_setfield(field,value,files):
    if field not in {'due','priority','depends'}: fail(f'unsupported field: {field}')
    for f in files:
        t=parse_task(f); old=t.get(field,'-'); t[field]=value; write_task(t); event(t['_key'],field,f'{old}->{value}')
def cb_note(text,files):
    ts=now()
    for f in files:
        t=parse_task(f); body=t.get('_body','')
        if body and not body.endswith('\n'): body+='\n'
        t['_body']=body+f'{ts}  {text}\n'; write_task(t); event(t['_key'],'note',text)

def add_selector(p, keys=True):
    if keys: p.add_argument('keys',nargs='*')
    p.add_argument('-t','--tag',dest='tags',action='append',default=[])
    p.add_argument('--tag-match',choices=['and','or'])

def parser():
    p=argparse.ArgumentParser(prog='stodo')
    sp=p.add_subparsers(dest='cmd',required=True)
    sp.add_parser('init')
    a=sp.add_parser('add'); a.add_argument('-k','--key'); a.add_argument('-p','--priority',default='B',choices=['A','B','C']); a.add_argument('-d','--due',default='-'); a.add_argument('-s','--status',default='inbox'); a.add_argument('-t','--tag',dest='tags',action='append',default=[]); a.add_argument('title',nargs=argparse.REMAINDER)
    for name in ['open','ls','raw','agenda','inbox','today','waiting','completed','queued','ready','blocked','overdue','stats']:
        q=sp.add_parser(name); add_selector(q)
    for name in ['start','next','wait','done','archive','remove','restore']:
        q=sp.add_parser(name); add_selector(q)
    for name,field in [('set-due','due'),('set-priority','priority')]:
        q=sp.add_parser(name); q.add_argument('value'); add_selector(q)
    q=sp.add_parser('note'); q.add_argument('key'); q.add_argument('text')
    q=sp.add_parser('depend'); q.add_argument('key'); q.add_argument('deps',nargs='+')
    q=sp.add_parser('undepend'); q.add_argument('key')
    for name in ['tag','untag']:
        q=sp.add_parser(name); q.add_argument('tag'); q.add_argument('keys',nargs='+')
    q=sp.add_parser('purge'); add_selector(q)
    q=sp.add_parser('history'); q.add_argument('key',nargs='?')
    return p

def main(argv):
    # hidden callback API: callback-specific args precede selected filenames.
    if argv and argv[0].startswith('__'):
        check_root(); cmd=argv[0]
        if cmd=='__init': cb_init(argv[1:]); return
        if cmd=='__view': cb_view(argv[1],argv[2:]); return
        if cmd=='__stats': cb_stats(argv[1:]); return
        if cmd=='__setstatus': cb_setstatus(argv[1],argv[2:]); return
        if cmd=='__setfield': cb_setfield(argv[1],argv[2],argv[3:]); return
        if cmd=='__note': cb_note(argv[1],argv[2:]); return
        fail(f'unknown callback: {cmd}')
    a=parser().parse_args(argv)
    if a.cmd=='init': return init_root()
    check_root()
    if a.cmd=='add': return cmd_add(a)
    if a.cmd=='open': return cmd_open(a)
    if a.cmd=='ls': return cmd_ls(a)
    if a.cmd=='raw': return cmd_raw(a)
    if a.cmd=='agenda': return cmd_agenda(a)
    if a.cmd in {'inbox','today','waiting'}: return cmd_status_view(a,a.cmd)
    if a.cmd=='completed': return cmd_status_view(a,'done')
    if a.cmd=='queued': return cmd_status_view(a,'next')
    if a.cmd=='ready': return cmd_ready(a)
    if a.cmd=='blocked': return cmd_blocked(a)
    if a.cmd=='overdue': return cmd_overdue(a)
    if a.cmd=='stats': return cmd_stats(a)
    if a.cmd=='start': return cmd_transition(a,'today')
    if a.cmd=='next': return cmd_transition(a,'next')
    if a.cmd=='wait': return cmd_transition(a,'waiting')
    if a.cmd=='done': return cmd_transition(a,'done')
    if a.cmd=='archive': return cmd_archive(a)
    if a.cmd=='remove': return cmd_remove(a)
    if a.cmd=='restore': return cmd_restore(a)
    if a.cmd=='set-due': return cmd_setfield(a,'due',a.value)
    if a.cmd=='set-priority': return cmd_setfield(a,'priority',a.value)
    if a.cmd=='note': return cmd_note(a)
    if a.cmd=='depend': return cmd_depend(a)
    if a.cmd=='undepend': return cmd_undepend(a)
    if a.cmd=='tag': return cmd_tag(a)
    if a.cmd=='untag': return cmd_tag(a,True)
    if a.cmd=='purge': return cmd_purge(a)
    if a.cmd=='history': return cmd_history(a)

if __name__=='__main__':
    try: main(sys.argv[1:])
    except Error as e:
        print(f'stodo: {e}',file=sys.stderr); sys.exit(1)
    except KeyboardInterrupt: sys.exit(130)
