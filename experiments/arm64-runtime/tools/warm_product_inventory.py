"""Bounded disk metadata for large warm-start proofs, never artifact authority."""
from pathlib import Path
from contextlib import contextmanager
import json,os,sqlite3,stat,tempfile
from urllib.parse import quote
from launcher_game_catalog import directory,file_sha

MAX_ROWS=100000
MAX_INDEX_BYTES=64*1024*1024
APPLICATION_ID=0x42455750

def root(context):
    generation=directory(context['generation'])
    parent=directory(generation.parent)
    if parent.name!='automatic-game-inputs':raise ValueError('warm index selected preparation root')
    target=parent/'warm-product-inventories-v1'
    if not target.exists():target.mkdir(mode=0o700)
    target=directory(target)
    if target.stat().st_mode&0o077:raise ValueError('private warm index directory')
    return target

def plain(path,maximum):
    path=Path(path);s=path.lstat()
    if path!=path.resolve(strict=True)or not stat.S_ISREG(s.st_mode)or s.st_uid!=os.getuid()or s.st_nlink!=1 or s.st_mode&0o077 or not 0<s.st_size<=maximum:raise ValueError('owned bounded warm index')
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

class Writer:
    def __init__(self,context,check):
        self.root=root(context);self.check=check;self.count=0;self.connection=None
        fd,name=tempfile.mkstemp(prefix='.inventory-',suffix='.sqlite',dir=self.root);os.close(fd)
        self.path=Path(name)
        try:
            self.connection=sqlite3.connect(self.path)
            self.connection.execute('PRAGMA temp_store=FILE');self.connection.execute('PRAGMA mmap_size=0')
            self.connection.execute('PRAGMA cache_size=-512');self.connection.execute('PRAGMA journal_mode=OFF')
            self.connection.execute('PRAGMA application_id='+str(APPLICATION_ID));self.connection.execute('PRAGMA user_version=1')
            self.connection.execute('CREATE TABLE records(path TEXT PRIMARY KEY, value TEXT NOT NULL)')
        except BaseException:self.close();raise
    def add(self,path,row):
        self.count+=1
        if self.count>MAX_ROWS:raise ValueError('warm product count budget')
        raw=json.dumps(row,separators=(',',':'),allow_nan=False)
        if not Path(path).is_absolute()or len(os.fsencode(path))>16384 or len(raw)>65536:raise ValueError('bounded warm index row')
        self.connection.execute('INSERT INTO records VALUES(?,?)',(path,raw))
        if self.count%256==0:
            self.check();self.connection.commit()
            if self.path.stat().st_size>MAX_INDEX_BYTES:raise ValueError('warm index byte budget')
    def finish(self):
        self.connection.commit();self.connection.close();self.connection=None
        with self.path.open('rb')as stream:os.fsync(stream.fileno())
        pin=plain(self.path,MAX_INDEX_BYTES);digest,size=file_sha(self.path,self.check)
        if plain(self.path,MAX_INDEX_BYTES)!=pin:raise ValueError('warm index changed while registering')
        final=self.root/(digest+'.sqlite')
        try:os.link(self.path,final)
        except FileExistsError:
            previous=plain(final,MAX_INDEX_BYTES)
            if file_sha(final,self.check)!=(digest,size)or plain(final,MAX_INDEX_BYTES)!=previous:raise ValueError('existing warm index changed')
        self.path.unlink();self.path=None
        plain(final,MAX_INDEX_BYTES)
        return dict(storage='sqlite-v1',schema=1,path=str(final),sha256=digest,bytes=size,rows=self.count)
    def close(self):
        if self.connection is not None:self.connection.close();self.connection=None
        if self.path is not None:
            self.path.unlink(missing_ok=True);self.path=None # This instance's unpublished scratch only.

class Reader:
    def __init__(self,descriptor,context,check):
        self.connection=None
        if set(descriptor)!={'storage','schema','path','sha256','bytes','rows'}or descriptor['storage']!='sqlite-v1'or type(descriptor['schema'])is not int or descriptor['schema']!=1:raise ValueError('warm index descriptor')
        digest=descriptor['sha256'];path=Path(descriptor['path'])
        import re
        if not isinstance(digest,str)or not re.fullmatch('[0-9a-f]{64}',digest)or path.parent!=root(context)or path.name!=digest+'.sqlite':raise ValueError('warm index selected location')
        if type(descriptor['rows'])is not int or not 1<=descriptor['rows']<=MAX_ROWS or type(descriptor['bytes'])is not int:raise ValueError('warm index row/size bound')
        before=plain(path,MAX_INDEX_BYTES)
        if file_sha(path,check)!=(digest,descriptor['bytes'])or plain(path,MAX_INDEX_BYTES)!=before:raise ValueError('warm index digest/identity')
        try:
            self.connection=sqlite3.connect('file:'+quote(str(path),safe='/')+'?mode=ro&immutable=1',uri=True)
            self.connection.execute('PRAGMA mmap_size=0');self.connection.execute('PRAGMA cache_size=-512');self.connection.execute('PRAGMA trusted_schema=OFF')
            if self.connection.execute('PRAGMA application_id').fetchone()[0]!=APPLICATION_ID or self.connection.execute('PRAGMA user_version').fetchone()[0]!=1:raise ValueError('warm index format')
            schema=self.connection.execute("SELECT sql FROM sqlite_master WHERE type='table'").fetchall()
            if schema!=[('CREATE TABLE records(path TEXT PRIMARY KEY, value TEXT NOT NULL)',)]:raise ValueError('warm index schema')
            if self.connection.execute('SELECT COUNT(*) FROM records').fetchone()[0]!=descriptor['rows']:raise ValueError('warm index count')
            self.path=path;self.pin=before
        except BaseException:self.close();raise
    def get(self,path):
        if plain(self.path,MAX_INDEX_BYTES)!=self.pin:raise ValueError('warm index changed while reading')
        row=self.connection.execute('SELECT value FROM records WHERE path=?',(path,)).fetchone()
        if row is None:return None
        if len(row[0])>65536:raise ValueError('warm index row budget')
        return json.loads(row[0])
    def close(self):
        if self.connection is not None:self.connection.close();self.connection=None

@contextmanager
def rows(descriptor,context,check):
    # Retain old small inline receipts unchanged. No migration can upgrade an
    # invalid old proof or grant a native cache import permission.
    if not isinstance(descriptor,dict):raise ValueError('warm proof row storage')
    if 'storage'not in descriptor:
        if not all(isinstance(k,str)and k.startswith('/')and isinstance(v,list)for k,v in descriptor.items()):raise ValueError('legacy warm proof row shape')
        yield descriptor;return
    reader=None
    try:
        reader=Reader(descriptor,context,check)
        yield reader
    except sqlite3.DatabaseError as error:
        raise ValueError('invalid warm index database')from error
    finally:
        if reader is not None:reader.close()
