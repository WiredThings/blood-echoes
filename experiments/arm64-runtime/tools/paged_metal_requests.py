"""Disk-indexed requests, legacy flat plus new two-hex leaf directories.

No complete-directory Python dictionaries/lists. Every hardlink must be accounted
for within ONE leaf, so cross-shard or external links do not gain trust.
"""
from pathlib import Path
import json,os,re,sqlite3,stat,tempfile

PAGE_MAX=256
SQLITE_CACHE_KIB=2048
_FINAL=re.compile(r'^([0-9a-f]{64})\.(metal|json)$')
_TEMP=re.compile(r'^([0-9a-f]{64})\.(metal|json)\.tmp-([0-9]+)-([0-9]+)$')
_SHARD=re.compile(r'^[0-9a-f]{2}$')

class RequestIndex:
    def __init__(self,folder,scratch,guard,*,api=None):
        if api is None:
            import local_metal_builder as api
        self.api=api
        self.folder=Path(folder);self.guard=guard;self.max_batch_len=0;self.max_pin_count=0
        self.path=None;self.db=None;self.interrupted=None
        fd,name=tempfile.mkstemp(prefix='request-index-',suffix='.sqlite',dir=scratch);os.close(fd)
        self.path=Path(name)
        try:
            self.db=sqlite3.connect(name)
            self.db.set_progress_handler(self._progress,50000)
            self._sql('PRAGMA temp_store=FILE');self._sql('PRAGMA mmap_size=0')
            self._sql('PRAGMA cache_size=-'+str(SQLITE_CACHE_KIB))
            self._sql('CREATE TABLE entries(scope TEXT,name TEXT,key TEXT,extension TEXT,dev TEXT,ino TEXT,links INTEGER,temp INTEGER,bytes INTEGER,PRIMARY KEY(scope,name))')
            self.entries=0;self.shards=0;self._scan(self.folder,'',root=True)
            self.db.commit();guard.check(preflight=True)
            bad=self._sql('''SELECT 1 FROM entries GROUP BY scope,dev,ino
                HAVING MIN(links)!=COUNT(*) OR MAX(links)!=COUNT(*) OR COUNT(DISTINCT key||'.'||extension)!=1 LIMIT 1''').fetchone()
            if bad:raise self.api.Refused('request has unaccounted, cross-shard or cross-family hardlinks')
            self._sql('CREATE INDEX entries_key ON entries(key,scope,temp)')
            self._sql('''CREATE TABLE locations AS SELECT scope,key,
                SUM(CASE WHEN temp=0 AND extension='metal' THEN 1 ELSE 0 END) AS metal,
                SUM(CASE WHEN temp=0 AND extension='json' THEN 1 ELSE 0 END) AS json
                FROM entries GROUP BY scope,key HAVING SUM(CASE WHEN temp=0 THEN 1 ELSE 0 END)>0''')
            self._sql('CREATE INDEX locations_key ON locations(key,scope)');self.db.commit()
            self.request_count=self._sql('SELECT COUNT(DISTINCT key) FROM locations WHERE metal=1 AND json=1').fetchone()[0]
            self.deferred_count=self._sql('SELECT COUNT(*) FROM locations WHERE metal!=1 OR json!=1').fetchone()[0]
            self.ignored_temporaries=self._sql('SELECT COUNT(*) FROM entries WHERE temp=1').fetchone()[0]
            self.legacy_groups=self._sql('SELECT COUNT(*) FROM (SELECT 1 FROM entries GROUP BY scope,dev,ino HAVING COUNT(*)>1)').fetchone()[0]
            guard.check(preflight=True)
        except BaseException:
            self.close();raise
    def _progress(self):
        try:self.guard.check()
        except BaseException as error:self.interrupted=error;return 1
        return 0
    def _sql(self,*args):
        try:return self.db.execute(*args)
        except sqlite3.Error:
            if self.interrupted is not None:raise self.interrupted
            raise
    def _page(self,cursor,count):
        try:return cursor.fetchmany(count)
        except sqlite3.Error:
            if self.interrupted is not None:raise self.interrupted
            raise
    def _scan(self,folder,scope,*,root=False):
        self.api.check_directory(folder)
        with os.scandir(folder) as entries:
            for entry in entries:
                self.entries+=1
                if self.entries%PAGE_MAX==0:self.db.commit();self.guard.check(preflight=True)
                row=entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(row.st_mode):
                    if not root or not _SHARD.fullmatch(entry.name):raise self.api.Refused('unexpected request subdirectory')
                    self.shards+=1;self._scan(Path(entry.path),entry.name);continue
                final=_FINAL.fullmatch(entry.name);match=final or _TEMP.fullmatch(entry.name)
                if match is None:raise self.api.Refused('unexpected request entry')
                key,extension=match.group(1),match.group(2)
                if scope and not key.startswith(scope):raise self.api.Refused('request key in wrong shard')
                limit=self.api.SOURCE_MAX if extension=='metal' else self.api.JSON_MAX
                if not stat.S_ISREG(row.st_mode) or row.st_uid!=os.getuid() or not 0<=row.st_size<=limit or row.st_nlink<1:
                    raise self.api.Refused('unowned/nonregular/oversized request entry')
                self._sql('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?)',
                  (scope,entry.name,key,extension,str(row.st_dev),str(row.st_ino),row.st_nlink,int(final is None),row.st_size))
    def batches(self):
        cursor=self._sql('SELECT DISTINCT key FROM locations WHERE metal=1 AND json=1 ORDER BY key')
        while True:
            self.guard.check(preflight=True)
            rows=self._page(cursor,PAGE_MAX)
            if not rows:return
            self.max_batch_len=max(self.max_batch_len,len(rows));yield [x[0] for x in rows]
    def load_owned(self,key,cohort):
        scopes=self._sql('SELECT scope FROM locations WHERE key=? AND metal=1 AND json=1 ORDER BY scope',(key,))
        first=None
        for(scope,) in scopes:
            pins={name:(int(dev),int(ino),links) for name,dev,ino,links in self._sql(
                'SELECT name,dev,ino,links FROM entries WHERE scope=? AND key=? AND temp=0',(scope,key))}
            if len(pins)!=2:raise self.api.Refused('indexed complete request lost its pair')
            self.max_pin_count=max(self.max_pin_count,len(pins));self.guard.check(preflight=True)
            request,source=self.api.load_request(self.folder/scope,key,cohort,pins=pins)
            if first is None:first=(request,source)
            elif self.api.canonical(request)!=self.api.canonical(first[0]) or source!=first[1]:
                raise self.api.Refused('conflicting complete flat/shard request copies')
        if first is None:raise self.api.Refused('missing indexed request')
        return first
    def deferred(self):
        cursor=self._sql('SELECT scope,key FROM locations WHERE metal!=1 OR json!=1 ORDER BY scope,key')
        while True:
            rows=self._page(cursor,PAGE_MAX)
            if not rows:return
            self.guard.check(preflight=True)
            for scope,key in rows:yield dict(scope=scope,key=key,status='deferred-unpaired')
    def recovery(self):
        preview=[]
        for item in self.deferred():
            if len(preview)<PAGE_MAX:preview.append(item['key'] if not item['scope'] else item['scope']+'/'+item['key'])
            else:break
        return dict(ignored_temporaries=self.ignored_temporaries,deferred_unpaired=preview,
                    deferred_unpaired_count=self.deferred_count,deferred_preview_truncated=self.deferred_count>len(preview),
                    legacy_internal_link_groups=self.legacy_groups,requests_modified=False,
                    disk_indexed=True,page_max=PAGE_MAX,sqlite_cache_kib=SQLITE_CACHE_KIB,shards=self.shards)
    def close(self):
        if self.db is not None:self.db.close();self.db=None
        if self.path is not None:
            for suffix in ('','-journal','-wal','-shm'):
                try:Path(str(self.path)+suffix).unlink()
                except FileNotFoundError:pass
            self.path=None
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
