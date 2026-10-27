"""Bounded private metadata products for the EXISTING static readers.

No game bytes, compiled code or artifact admission. Keys include exact source
and cross-file dependencies plus the actual parser ABI. Original per-record
results are immutable; bad rows remain and can be superseded by a fresh parse.
Only a preparation child opens this database; it is closed before game launch.
"""
from pathlib import Path
import hashlib,json,os,sqlite3,stat

MAX_ROW=4*1024*1024
MAX_DATABASE=512*1024*1024

def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()

def identity(value):
    sha=hashlib.sha256()
    for piece in json.JSONEncoder(sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).iterencode(value):sha.update(piece.encode())
    return sha.hexdigest()

class Products:
    def __init__(self,root,parser_identity):
        if not isinstance(parser_identity,str)or len(parser_identity)!=64 or any(c not in '0123456789abcdef'for c in parser_identity):raise ValueError('actual parser identity required')
        root=Path(root).absolute();root.mkdir(mode=0o700,exist_ok=True)
        s=root.lstat()
        if root!=root.resolve(strict=True)or not stat.S_ISDIR(s.st_mode)or s.st_uid!=os.getuid()or s.st_mode&0o022:raise ValueError('owned canonical metadata product root')
        self.path=root/'metadata-v1.sqlite';self.parser=parser_identity;self.db=None
        self.hits=0;self.misses=0;self.published=0;self.rejected=0;self.oversized=0
        for name in('metadata-v1.sqlite','metadata-v1.sqlite-journal','metadata-v1.sqlite-wal','metadata-v1.sqlite-shm'):
            p=root/name
            if os.path.lexists(p):
                s=p.lstat()
                if not stat.S_ISREG(s.st_mode)or s.st_uid!=os.getuid()or s.st_nlink!=1 or s.st_mode&0o022 or s.st_size>MAX_DATABASE:raise ValueError('unsafe metadata product database')
        fd=os.open(self.path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        try:
            pin=os.fstat(fd)
            if not stat.S_ISREG(pin.st_mode)or pin.st_uid!=os.getuid()or pin.st_nlink!=1 or pin.st_mode&0o022:raise ValueError('metadata database changed before open')
            self.db=sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=2)
            after=self.path.lstat()
            if (after.st_dev,after.st_ino)!=(pin.st_dev,pin.st_ino):raise ValueError('metadata database replaced during open')
            self.db.execute('PRAGMA journal_mode=DELETE');self.db.execute('PRAGMA mmap_size=0')
            self.db.execute('PRAGMA cache_size=-2048');self.db.execute('PRAGMA temp_store=FILE')
            self.db.execute('CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY, key TEXT NOT NULL, sha TEXT NOT NULL, payload BLOB NOT NULL)')
            self.db.execute('CREATE INDEX IF NOT EXISTS products_key ON products(key,id)');self.db.commit()
        except BaseException:
            self.close();raise
        finally:os.close(fd)

    def key(self,inputs):
        return identity(dict(schema=1,parser=self.parser,inputs=inputs))

    def get(self,inputs):
        key=self.key(inputs)
        try:
            # Inspect bounded latest candidates. Never trust a matching lookup
            # key or an SQLite row alone; verify exact input/record bytes.
            cursor=self.db.execute('SELECT sha,payload FROM products WHERE key=? AND length(payload)<=? ORDER BY id DESC LIMIT 4',(key,MAX_ROW))
            for expected,raw in cursor:
                if not isinstance(raw,bytes)or hashlib.sha256(raw).hexdigest()!=expected:self.rejected+=1;continue
                try:
                    record=json.loads(raw)
                    if set(record)!={'schema','parser','inputs','value'}or type(record['schema'])is not int or record['schema']!=1 or record['parser']!=self.parser or record['inputs']!=inputs:raise ValueError('metadata product binding')
                    self.hits+=1;return record['value']
                except(ValueError,TypeError,KeyError):self.rejected+=1
        except sqlite3.DatabaseError:self.rejected+=1
        self.misses+=1;return None

    def put(self,inputs,value):
        # Preserve the original reader's field/stream order on replay. Product
        # lookup is canonical; its value is the exact original serialization
        # order, so a cache hit need not perturb downstream metadata receipts.
        raw=bytearray()
        for piece in json.JSONEncoder(separators=(',',':'),ensure_ascii=False,allow_nan=False).iterencode(
                dict(schema=1,parser=self.parser,inputs=inputs,value=value)):
            part=piece.encode()
            if len(raw)+len(part)>MAX_ROW:self.oversized+=1;return False
            raw.extend(part)
        if self.path.stat().st_size+len(raw)+65536>MAX_DATABASE:
            self.oversized+=1;return False
        raw=bytes(raw)
        try:
            # INSERT only. A corrupt or old record is never erased/overwritten.
            self.db.execute('INSERT INTO products(key,sha,payload) VALUES(?,?,?)',
                (self.key(inputs),hashlib.sha256(raw).hexdigest(),raw));self.db.commit()
            self.published+=1;return True
        except sqlite3.DatabaseError:self.rejected+=1;return False

    def report(self):
        return dict(hits=self.hits,misses=self.misses,published=self.published,rejected=self.rejected,
            oversized=self.oversized,artifact_authority=False)

    def close(self):
        if self.db is not None:self.db.close();self.db=None
