"""Bounded local content-digest reuse; metadata detects ordinary file changes.

This is a scheduling hint, not code admission or authority to import a pipeline.
Callers still enumerate the complete selected file set, check its mapping and
validate the actual immutable snapshot/index before publishing a successful hint.
"""
from pathlib import Path
import hashlib,json,os,re,stat,tempfile

SCHEMA=1
MAX_FILES=100000
MAX_BYTES=32*1024*1024
SHA=re.compile(r'[0-9a-f]{64}')


def stat_stamp(s):
    return [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]


def stamp(path):
    p=Path(path).absolute()
    if p!=p.resolve(strict=True):raise ValueError('incremental source must be canonical, not linked')
    s=p.lstat()
    if not stat.S_ISREG(s.st_mode)or s.st_uid!=os.getuid():raise ValueError('incremental source must be an owned regular file')
    return stat_stamp(s)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def unique(items):
    result={}
    for key,value in items:
        if key in result:raise ValueError('duplicate incremental receipt field')
        result[key]=value
    return result


def replaceable(path):
    p=Path(path)
    if not os.path.lexists(p):return True
    s=p.lstat()
    return stat.S_ISREG(s.st_mode)and s.st_uid==os.getuid()and s.st_nlink==1


class Hashes:
    def __init__(self,path,binding,*,hasher,check=lambda:None,watch_roots=None,source_stamp=None,code_roots=False):
        self.path=Path(path).absolute();self.binding=binding;self.hasher=hasher;self.check=check
        self.previous={};self.seen={};self.hashed_files=0;self.hashed_bytes=0;self.reused_files=0
        self.source_stamp=source_stamp or stamp;self.code_roots=code_roots
        self.receipt_status='missing';self.watch_roots=watch_roots;self.journal_token=None;self.journal_valid=False
        try:
            before=stamp(self.path)
            if before[5]!=1 or stat.S_IMODE(before[2])!=0o600 or before[6]>MAX_BYTES:raise ValueError('incremental receipt mode/link/size')
            fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
            with os.fdopen(fd,'rb')as stream:
                if stat_stamp(os.fstat(stream.fileno()))!=before:raise ValueError('incremental receipt replaced')
                raw=stream.read(MAX_BYTES+1)
                if stat_stamp(os.fstat(stream.fileno()))!=before:raise ValueError('incremental receipt changed while reading')
            if len(raw)!=before[6] or len(raw)>MAX_BYTES or stamp(self.path)!=before:raise ValueError('incremental receipt changed')
            value=json.loads(raw,object_pairs_hook=unique);body=value['body']
            if set(value)!={'body','sha256'} or digest(body)!=value['sha256']:raise ValueError('incremental receipt digest')
            if set(body)not in({'schema','binding','files'},{'schema','binding','files','journal'}) or body['schema']!=SCHEMA or body['binding']!=binding:raise ValueError('incremental receipt binding')
            rows=body['files']
            if not isinstance(rows,dict)or len(rows)>MAX_FILES:raise ValueError('incremental receipt count')
            for name,row in rows.items():
                if not isinstance(name,str)or not Path(name).is_absolute()or set(row)!={'stamp','sha256','bytes'}:raise ValueError('incremental receipt entry')
                s=row['stamp']
                if not isinstance(s,list)or len(s)!=9 or any(type(x)is not int for x in s)or type(row['bytes'])is not int or row['bytes']<0 or row['bytes']!=s[6]or not isinstance(row['sha256'],str)or not SHA.fullmatch(row['sha256']):raise ValueError('incremental receipt stamp')
            self.previous=rows;self.receipt_status='verified';self.journal_token=body.get('journal')
        except FileNotFoundError:pass
        except (OSError,ValueError,TypeError,KeyError):self.receipt_status='invalid'
        if watch_roots:
            from startup_change_journal import query,unchanged
            journal_args={'code_roots':True}if code_roots else{}
            self.journal_valid=self.receipt_status=='verified' and unchanged(self.journal_token,watch_roots,**journal_args)
            # Anchor BEFORE reading/validating. Events during that pass remain
            # visible to the next launch; never advance past unvalidated changes.
            if not self.journal_valid:self.journal_token=query(watch_roots,**journal_args)


    def get(self,path,check=None):
        self.check();p=Path(path).absolute();key=str(p)
        cached=self.seen.get(key,self.previous.get(key))
        if self.journal_valid and cached is not None:
            if key not in self.seen:self.reused_files+=1
            self.seen[key]=cached
            if len(self.seen)>MAX_FILES:raise ValueError('incremental source file-count budget')
            return cached['sha256'],cached['bytes']
        before=self.source_stamp(p)
        if cached is not None and cached['stamp']==before:
            if key not in self.seen:self.reused_files+=1
            row=cached
        else:
            value,size=self.hasher(p,self.check if check is None else check)
            if self.source_stamp(p)!=before or size!=before[6]:raise ValueError('selected source contents changed during incremental hash')
            row=dict(stamp=before,sha256=value,bytes=size)
            self.hashed_files+=1;self.hashed_bytes+=size
        self.seen[key]=row
        if len(self.seen)>MAX_FILES:raise ValueError('incremental source file-count budget')
        return row['sha256'],row['bytes']

    def verify(self):
        if self.journal_valid:
            from startup_change_journal import unchanged
            self.check()
            if unchanged(self.journal_token,self.watch_roots,**({'code_roots':True}if self.code_roots else{})):return
            self.journal_valid=False;self.journal_token=None
        for name,row in self.seen.items():
            self.check()
            if self.source_stamp(name)!=row['stamp']:raise ValueError('selected source contents changed during incremental validation')

    def publish(self):
        self.verify()
        parent=self.path.parent
        if parent!=parent.resolve(strict=True)or parent.stat().st_uid!=os.getuid():raise ValueError('incremental receipt parent')
        if not replaceable(self.path):return False
        body=dict(schema=SCHEMA,binding=self.binding,files=self.seen)
        if self.journal_token is not None:body['journal']=self.journal_token
        raw=(json.dumps(dict(body=body,sha256=digest(body)),sort_keys=True,separators=(',',':'))+'\n').encode()
        if len(raw)>MAX_BYTES:return False
        fd,name=tempfile.mkstemp(prefix='.incremental-',dir=parent)
        try:
            with os.fdopen(fd,'wb')as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.replace(name,self.path)
        finally:
            if os.path.exists(name):os.unlink(name)
        return True

    def report(self):
        return dict(receipt=self.receipt_status,hashed_files=self.hashed_files,
                    hashed_bytes=self.hashed_bytes,reused_files=self.reused_files,
                    metadata_only=self.hashed_files==0,journal_reused=self.journal_valid)
