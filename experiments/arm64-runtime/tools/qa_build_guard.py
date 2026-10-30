"""Reject heavy compilation alongside any actual game host, not stale PID constants."""
from pathlib import PurePath
import subprocess

def parse_game_processes(text):
    games=[]
    for row in text.splitlines():
        fields=row.strip().split(None,1)
        if not fields:continue
        if len(fields)!=2 or not fields[0].isdigit():raise RuntimeError('invalid process inventory')
        pid=int(fields[0]);path=fields[1]
        if pid<=0 or not path.startswith('/'):continue
        name=PurePath(path).name
        if name in {'bb-gpu','bb-probe','bb-probe-metal','bb-game-arm64','bb-game-bbcpu'}or name.startswith(('bb-game-arm64-','bb-game-bbcpu-')):
            games.append((pid,path))
    return games

def running_games():
    return parse_game_processes(subprocess.check_output(['/bin/ps','-axo','pid=,comm='],text=True))

def require_no_games():
    games=running_games()
    if games:raise RuntimeError('compile refused while game hosts run: '+repr(games))
