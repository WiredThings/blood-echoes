"""Extract the two exact success blocks; real Storage/PsoCache are not replaced."""
from pathlib import Path
import hashlib,json
E=Path(__file__).resolve().parent
Q=E.parent/'parallel-pso-coverage-20261026'
patch=Q/'0001-persist-portable-derived-keys.patch'
assert hashlib.sha256(patch.read_bytes()).hexdigest()=='6992ce6a873101ef105670bd97510243b8d28080810322cee8dbbe5426c7b86a'
source=Q/'proposed/mtl_rasterizer.mm'
if not source.exists():
    matches=list((Q/'proposed').rglob('mtl_rasterizer.mm'))
    assert len(matches)==1,matches
    source=matches[0]
text=source.read_text()
records=[]
for iterator,name in [('it','no_depth'),('entry','velocity')]:
    start=text.index(f'if ({iterator}->second && !key.motion_vectors) {{')
    depth=0;end=None
    for pos in range(text.index('{',start),len(text)):
        if text[pos]=='{':depth+=1
        if text[pos]=='}':
            depth-=1
            if depth==0:end=pos+1;break
    assert end
    block=text[start:end]
    # Count entry to the *unchanged* success block, not a replacement Storage writer.
    # The actual RegisterPipelineData in the DSO performs its normal async Save.
    out='// Exact frozen added block; authored invocation observation only.\n'+block
    path=E/'generated'/f'{name}_success.inc';path.write_text(out+'\n')
    records.append(dict(name=name,source=str(source),start_line=text[:start].count('\n')+1,
                        block_sha256=hashlib.sha256(block.encode()).hexdigest()))
(E/'generated/blocks.json').write_text(json.dumps(records,indent=2)+'\n')
print(json.dumps(records,indent=2))
