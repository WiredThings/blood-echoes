"""Real RenderList/fake-encoder controls; never creates a Metal device or game.

Reuse an explicitly selected renderer's dependency flags, compile only two test
sources into a fresh temp directory, then exercise enabled/disabled filtering.
The mutation control deliberately restores the former history-reset behavior.
"""
from pathlib import Path
import argparse,hashlib,json,os,shlex,subprocess,tempfile

D=Path(__file__).resolve().parents[1]
def need(value,why):
    if not value:raise ValueError(why)
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def run(build):
    build=build.absolute();need(build==build.resolve(strict=True),'canonical configured renderer required')
    commands=json.loads((build/'compile_commands.json').read_bytes())
    rows=[row for row in commands if row['file'].endswith('/mtl_render_list.mm')and'mtl-render-list-test'in row.get('command',str(row.get('arguments',[])))]
    need(len(rows)==1,'exact configured original render-list test role required');row=rows[0]
    reply=build/'.cmake/api/v1/reply';indexes=sorted(reply.glob('index-*.json'));need(indexes,'configured renderer File API required')
    index=json.loads(indexes[-1].read_bytes());model=next(item['jsonFile']for item in index['objects']if item['kind']=='codemodel')
    configurations=json.loads((reply/model).read_bytes())['configurations']
    targets=[target for configuration in configurations for target in configuration['targets']if target['name']=='mtl-render-list-test'];need(len(targets)==1,'exact configured test link role required')
    link=json.loads((reply/targets[0]['jsonFile']).read_bytes())['link']['commandFragments']
    libraries=[token for part in link if part['role']=='libraries'for token in shlex.split(part['fragment'])]
    argv=row.get('arguments')or shlex.split(row['command']);need(argv[0]in('/usr/bin/clang++','/usr/bin/c++'),'original Apple C++ compiler required')
    old=Path(row['file']);needle='/shadps4/video_core/renderer_metal/mtl_render_list.mm';old_api=str(old)[:-len(needle)]
    api=D/'vendor/metal_api';source=api/'shadps4/video_core/renderer_metal/mtl_render_list.mm';test=D/'vendor/tests/test_mtl_render_list.mm'
    flags=[];at=1
    while at<len(argv):
        value=argv[at];at+=1
        if value in('-o','-x'):at+=1;continue
        if value in('-c',str(old)):continue
        if value.startswith('-O'):continue
        flags.append(value.replace(old_api,str(api)))
    output=Path(tempfile.mkdtemp(prefix='blood-echoes-render-continuation-',dir='/private/tmp'))
    before={str(path):digest(path)for path in(source,test)}
    current=output/'current';command=[argv[0],*flags,'-O1',str(test),str(source),*libraries,'-o',str(current)]
    def compile_command(argv,name):
        result=subprocess.run(argv,capture_output=True,text=True,timeout=90);(output/name).write_text(result.stdout+result.stderr)
        need(result.returncode==0,'control fixture compile failed: '+str(output/name))
    compile_command(command,'compile-current.log')
    safe={key:os.environ[key]for key in('PATH','HOME','TMPDIR')if key in os.environ};results=[]
    for mode in('0','1'):
        result=subprocess.run([str(current),'--controls-only'],env={**safe,'BB_MTL_COALESCE_BARRIERS':mode},capture_output=True,text=True,timeout=15)
        (output/('controls-'+mode+'.log')).write_text(result.stdout+result.stderr)
        need(result.returncode==0 and'Metal device not created'in result.stdout,'current fake-encoder controls failed: '+str(output));results.append(dict(coalescing=mode,status=result.returncode,console=result.stdout))
    body=source.read_text();restore='    impl->barrier_since_draw = same_encoder_coverage;';need(body.count(restore)==1,'exact continuation mutation anchor absent')
    mutation=output/'history-reset.mm';mutation.write_text(body.replace(restore,'    // Mutation: preserve the former BeginDirect history reset.'))
    mutated=output/'mutated';changed=list(command);changed[changed.index(str(source))]=str(mutation);changed[-1]=str(mutated)
    compile_command(changed,'compile-mutated.log')
    result=subprocess.run([str(mutated),'--controls-only'],env={**safe,'BB_MTL_COALESCE_BARRIERS':'1'},capture_output=True,text=True,timeout=15)
    (output/'mutation.log').write_text(result.stdout+result.stderr)
    need(result.returncode==1 and'FAILED controls'in result.stdout,'history-reset mutation was not detected')
    need(all(digest(Path(path))==want for path,want in before.items()),'source changed during control run')
    report=dict(status='controls-passed',source_sha256=before[str(source)],test_sha256=before[str(test)],results=results,history_reset_mutation_detected=True,game_started=False,Metal_device_created=False,GUI_or_capture_used=False,
                qualification='Real command-list control flow only, not GPU pixels, GPU execution-time, FPS or full-scene memory acceptance.')
    (output/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(output);return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--renderer-build',type=Path,required=True);a=parser.parse_args();run(a.renderer_build)
