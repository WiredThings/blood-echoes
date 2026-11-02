"""Reject heavy compilation alongside any actual game host, not stale PID constants."""
from pathlib import PurePath
import ctypes,os,subprocess,sys

GAME_NAMES=frozenset(('bb-gpu','bb-probe','bb-probe-metal','bb-game-arm64','bb-game-bbcpu'))
GAME_PREFIXES=('bb-game-arm64-','bb-game-bbcpu-')

def _game_name(name):return name in GAME_NAMES or name.startswith(GAME_PREFIXES)

def _native_games(api=None):
    """Fresh kernel inventory, not a cached 'no game' flag.

    The builder calls this at every original boundary. Avoid spawning ps and
    constructing hundreds of Path objects for each individual metadata read.
    Unknown native API failures retain the original ps fallback.
    """
    if api is None:
        if sys.platform!='darwin':return None
        try:
            api=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
            api.proc_listallpids.argtypes=(ctypes.c_void_p,ctypes.c_int);api.proc_listallpids.restype=ctypes.c_int
            api.proc_pidpath.argtypes=(ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32);api.proc_pidpath.restype=ctypes.c_int
            api.proc_name.argtypes=(ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32);api.proc_name.restype=ctypes.c_int
        except (OSError,AttributeError):return None
    maximum=65536
    for _ in range(2):
        count=api.proc_listallpids(None,0)
        if not 0<count<maximum:return None
        capacity=min(maximum,max(count+256,count*2));pids=(ctypes.c_int*capacity)()
        actual=api.proc_listallpids(pids,ctypes.sizeof(pids))
        if actual<=0:return None
        if actual>=capacity:continue
        games=[];path=ctypes.create_string_buffer(4096);name=ctypes.create_string_buffer(256)
        for pid in pids[:actual]:
            if pid<=0:continue
            path.value=b''
            if api.proc_pidpath(pid,path,len(path))>0:
                text=os.fsdecode(path.value)
                if text.startswith('/')and _game_name(text.rsplit('/',1)[-1]):games.append((pid,text))
            else:
                # A process may disappear, or be unreadable. Do not silently
                # discard a visible game-shaped process when its path is denied.
                name.value=b''
                if api.proc_name(pid,name,len(name))>0 and _game_name(os.fsdecode(name.value)):return None
        return games
    return None

def parse_game_processes(text):
    games=[]
    for row in text.splitlines():
        fields=row.strip().split(None,1)
        if not fields:continue
        if len(fields)!=2 or not fields[0].isdigit():raise RuntimeError('invalid process inventory')
        pid=int(fields[0]);path=fields[1]
        if pid<=0 or not path.startswith('/'):continue
        name=PurePath(path).name
        if _game_name(name):
            games.append((pid,path))
    return games

def running_games():
    native=_native_games()
    if native is not None:return native
    return parse_game_processes(subprocess.check_output(['/bin/ps','-axo','pid=,comm='],text=True))

def require_no_games():
    games=running_games()
    if games:raise RuntimeError('compile refused while game hosts run: '+repr(games))
