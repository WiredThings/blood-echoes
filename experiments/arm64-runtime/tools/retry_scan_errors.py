"""Retry only failed physical files, preserving split-container dispatch.
Merges no counts blindly: partial original BXF rows may duplicate retry rows.
"""
from pathlib import Path
import argparse,json,time,struct
from full_game_catalog import Scanner,digest_path,dispatch_payload

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--prototype',type=Path);ap.add_argument('--prior-catalog',type=Path,required=True);ap.add_argument('--game-root',type=Path,required=True);ap.add_argument('--scan',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
 assert a.game_root.is_absolute()and a.game_root.resolve()==a.game_root
 assert a.out.is_absolute()and not a.out.exists()and not a.out.resolve().is_relative_to(a.game_root),'new output outside game root required'
 a.out.mkdir(parents=True);s=Scanner(a.game_root,a.prior_catalog,a.out,a.prototype);oldfiles={x['path']:x for x in map(json.loads,(a.scan/'files.jsonl').open())};errors=list(map(json.loads,(a.scan/'errors.jsonl').open()));recovered=[];start=time.monotonic()
 for row in errors:
  if row['error']!='unsupported DCX profile':continue
  where=row['path'];path=a.game_root/where
  if where not in oldfiles:s.error(where,'nested retry requires parent file dispatch');continue
  if digest_path(path)!=oldfiles[where]['sha256']:s.error(where,'input hash changed');continue
  try:
   dispatch_payload(s,path,where)
   recovered.append(where)
  except(ValueError,UnicodeError,struct.error)as error:s.error(where,error)
 for out in s.streams.values():out.close()
 report=dict(recovered_paths=recovered,recovered_count=len(recovered),counts=dict(s.count),errors=s.errors,gaps=s.gaps,seconds=time.monotonic()-start,new_unique_programs=sorted(s.programs),new_unique_codes=len(s.codes))
 (a.out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items()if k!='recovered_paths'},indent=2))
if __name__=='__main__':main()
