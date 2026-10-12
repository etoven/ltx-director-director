"""Export the supplied MiniMax ComfyUI graph with project media and timed guides."""
from __future__ import annotations
import base64
import copy
import json
import math
import subprocess
import imageio_ffmpeg
import uuid
from importlib.resources import files
from pathlib import Path

from .media import copy_media_for_export, safe_media_filename, unique_media_filename
from .minimax_reference import reference_inventory, reference_slots
from .project_archive import materialize_source
from .reference_media import reference_audio

FPS = 24

class Graph:
    def __init__(self, workflow):
        self.w = workflow
        self.nodes = {n['id']:n for n in workflow['nodes']}
        self.node_id = max(workflow['last_node_id'],max(self.nodes))
        self.link_id = max([workflow['last_link_id']]+[x[0] for x in workflow['links']])
    def remove(self, ids):
        for key in ids:self.nodes.pop(key,None)
        self.w['links'] = [x for x in self.w['links'] if x[1] not in ids and x[3] not in ids]
    def add(self, node, title=None, pos=None):
        node = copy.deepcopy(node)
        self.node_id += 1
        node['id'] = self.node_id
        node['mode'] = 0
        if title:node['title'] = title
        if pos:node['pos'] = pos
        for item in node.get('inputs',[]):item['link'] = None
        for item in node.get('outputs',[]):item['links'] = []
        self.nodes[node['id']] = node
        return node
    def connect(self, source, output, target, name, kind=None):
        source = source['id'] if isinstance(source,dict) else source
        target = target['id'] if isinstance(target,dict) else target
        slot = next(i for i,x in enumerate(self.nodes[target]['inputs']) if x['name']==name)
        self.w['links'] = [x for x in self.w['links'] if not (x[3]==target and x[4]==slot)]
        self.link_id += 1
        self.w['links'].append([self.link_id,source,output,target,slot,kind or self.nodes[source]['outputs'][output]['type']])
    def finish(self):
        for node in self.nodes.values():
            for x in node.get('inputs',[]):x['link']=None
            for x in node.get('outputs',[]):x['links']=[]
        for link,origin,oslot,target,tslot,kind in self.w['links']:
            self.nodes[origin]['outputs'][oslot]['links'].append(link)
            self.nodes[target]['inputs'][tslot]['link']=link
        self.w.update(nodes=list(self.nodes.values()),last_node_id=self.node_id,last_link_id=self.link_id)
        return self.w

def _set_widget(node, value, name=None, index=0):
    values=node.get('widgets_values',[])
    if isinstance(values,dict):values[name]=value
    else:
        while len(values)<=index:values.append(None)
        values[index]=value
    node['widgets_values']=values
    if name:node.setdefault('widgets_values_named',{})[name]=value


def export_minimax_workflow(path, segments, prompt, references, comfy_root, width=1280, height=704):
    if not prompt.strip():raise ValueError('Write or generate the MiniMax production prompt before exporting.')
    refs=reference_slots(references)
    inventory=reference_inventory(segments,refs)
    counts={kind:sum(r['kind']==kind for r in inventory) for kind in ('image','video','audio')}
    if counts['image']>9 or counts['video']>3 or counts['audio']>3:
        raise ValueError('This ComfyUI node supports at most 9 image inputs, 3 video inputs and 3 standalone audio inputs. Reduce that media type before exporting.')
    for r in inventory:
        if 'reference_slot' in r and r['kind']=='audio' and not refs[r['reference_slot']].get('audio_path'):
            raise ValueError(f"No usable audio track in {r['name']}.")
    total=sum(s.duration for s in segments) or 15.0
    frame_count=max(5, math.ceil((max(5,round(total*FPS)+1)-5)/17)*17+5)
    destination=Path(comfy_root)/'input'/'whatdreamscost'
    destination.mkdir(parents=True,exist_ok=True)
    used={x.name.casefold() for x in destination.iterdir()}
    def copy_file(source,name):
        source = Path(source)
        if source.suffix and Path(name).suffix.lower() != source.suffix.lower():
            name = Path(name).stem + source.suffix
        target=copy_media_for_export(str(source),name,destination,used,'minimax')
        return 'whatdreamscost/'+target.name
    def image_file(data,name):
        mime=data.split(';',1)[0].removeprefix('data:')
        suffix={'image/jpeg':'.jpg','image/webp':'.webp','image/tiff':'.tiff','image/bmp':'.bmp'}.get(mime,'.png')
        name=unique_media_filename(safe_media_filename(Path(name).stem+suffix,'reference'),used)
        (destination/name).write_bytes(base64.b64decode(data.split(',',1)[1]))
        return 'whatdreamscost/'+name
    w=json.loads(files('ltx_prompt_director').joinpath('assets/minimax-comfy-workflow.json').read_text())
    original={n['id']:copy.deepcopy(n) for n in w['nodes']}
    guide_types={d['id'] for d in w['definitions']['subgraphs'] if d['name']=='MinMaxCondFrame'}
    g=Graph(w)
    g.remove({n['id'] for n in w['nodes'] if n['type'] in {'LoadImage','VHS_LoadVideo'}|guide_types or n['id'] in {596,597,598,599,600,587}})
    # Rebuild dynamic ports while preserving model/latent/sampling subgraphs.
    native=g.nodes[136]
    existing={x['name']:copy.deepcopy(x) for x in native['inputs']}
    old_links={x['name']:x.get('link') for x in native['inputs']}
    # Remove incoming links before changing slot indices.
    incoming={x[0]:x for x in w['links'] if x[3]==136}
    w['links']=[x for x in w['links'] if x[3]!=136]
    native['inputs']=[existing[k] for k in ('clip','vae','audio_vae')]
    for kind,prefix,count in [('IMAGE','ref_images.ref_image_',counts['image']),('IMAGE','ref_videos.ref_video_',counts['video']),
                              ('AUDIO','ref_video_audios.ref_video_audio_',counts['video']),('AUDIO','ref_audios.ref_audio_',counts['audio'])]:
        for i in range(count):native['inputs'].append({'name':prefix+str(i),'type':kind,'shape':7,'link':None})
    native['inputs'] += [existing[k] for k in ('prompt','width','height','length')]
    for name in ('clip','vae','audio_vae','prompt'):
        link=incoming.get(old_links.get(name))
        if link:g.connect(link[1],link[2],136,name,link[5])
    # Use the app's exact output settings, not the template's sample aspect selector.
    for setting_index,(name,value,target) in enumerate([('Width',width,516),('Height',height,515),('Length',frame_count,None)]):
        node=g.add(original[143],name,[ -800,4912+80*setting_index])
        _set_widget(node,int(value),'value')
        if target:g.connect(node,0,target,'INT','INT')
        g.connect(node,0,136,name.lower(),'INT')
    _set_widget(g.nodes[138],prompt,'value')
    _set_widget(g.nodes[452],total,'value_1',4)
    _set_widget(native,width,'width',1);_set_widget(native,height,'height',2);_set_widget(native,frame_count,'length',3)
    # Use one common supplied conditioning subgraph, exposing its native audio input.
    guide_def=copy.deepcopy(next(d for d in w['definitions']['subgraphs'] if d['id']==original[541]['type']))
    guide_def['id']=str(uuid.uuid4())
    new_link=max(x['id'] for x in guide_def['links'])+1
    inner=next(n for n in guide_def['nodes'] if n['type']=='MiniMaxH3AddGuide')
    inner['inputs'][5]['link']=new_link
    guide_def['inputs'].append({'id':str(uuid.uuid4()),'name':'audio','type':'AUDIO','shape':7,'linkIds':[new_link]})
    guide_def['links'].append({'id':new_link,'origin_id':-10,'origin_slot':3,'target_id':inner['id'],'target_slot':5,'type':'AUDIO'})
    guide_def['state']['lastLinkId']=max(guide_def['state']['lastLinkId'],new_link)
    w['definitions']['subgraphs'].append(guide_def)
    image_index=video_index=audio_index=0
    loaded={}
    for r in inventory:
        kind=r['kind']
        ref=refs[r['reference_slot']] if 'reference_slot' in r else None
        segment=segments[r['timeline_index']] if 'timeline_index' in r else None
        x=100+400*(len(loaded)%4);y=3600+650*(len(loaded)//4)
        if kind=='text':continue
        if kind=='image':
            filename=image_file(ref['image'],ref['name']) if ref else copy_file(materialize_source(segment),segment.name)
            loader=g.add(original[338],r['label'],[x,y]);_set_widget(loader,filename,'image')
            g.connect(loader,0,136,'ref_images.ref_image_'+str(image_index),'IMAGE');image_index+=1
            loaded[r.get('timeline_index',f"ref{r.get('reference_slot')}")]=(loader,0,None,r)
        elif kind=='video':
            filename=copy_file(ref['path'] if ref else materialize_source(segment),r['name'])
            loader=g.add(original[175],r['label'],[x,y])
            vals=loader['widgets_values'];vals.update(video=filename,force_rate=24,custom_width=width,custom_height=height,
                frame_load_cap=round(segment.duration*24) if segment else frame_count,skip_first_frames=segment.trim_start or 0 if segment else 0)
            vals.pop('videopreview',None);loader['widgets_values_named']=copy.deepcopy(vals)
            g.connect(loader,0,136,'ref_videos.ref_video_'+str(video_index),'IMAGE')
            if ref and ref.get('audio_path'):g.connect(loader,2,136,'ref_video_audios.ref_video_audio_'+str(video_index),'AUDIO')
            video_index+=1
            if segment:
                sample=g.add(original[451],'Conditioning video sample',[x+280,y]);_set_widget(sample,24,'num_frames',1)
                g.connect(loader,0,sample,'images','IMAGE');g.connect(loader,2,sample,'audio','AUDIO')
                loaded[r['timeline_index']]=(sample,0,(sample,1),r)
        else:
            filename=copy_file(ref['audio_path'],Path(ref['name']).stem+'.wav')
            node={'type':'LoadAudio','pos':[x,y],'size':[300,150],'flags':{},'order':0,'mode':0,'inputs':[],
                'outputs':[{'name':'AUDIO','type':'AUDIO','links':[]}],'properties':{'Node name for S&R':'LoadAudio'},'widgets_values':[filename]}
            loader=g.add(node,r['label']);g.connect(loader,0,136,'ref_audios.ref_audio_'+str(audio_index),'AUDIO');audio_index+=1
    # Opening guide exactly follows the supplied video/still LazySwitchKJ hookup.
    timed=[r for r in inventory if 'timeline_index' in r and r['kind'] in {'image','video'}]
    opening=next((r for r in timed if abs(r.get('checkpoint_time',r['start_time']))<1e-6),None)
    if not opening:
        g.remove({451,579,580,581,585})
    previous=518
    guides=[]
    for i,r in enumerate(timed):
        source,slot,audio,_=loaded[r['timeline_index']]
        seconds=r.get('checkpoint_time',r['start_time'])
        if r is opening:
            g.remove({451})
            switch=g.nodes[579]
            _set_widget(switch,r['kind']=='video','switch')
            g.connect(source,slot,579,'on_true','IMAGE')
            if r['kind']=='video':
                segment=segments[r['timeline_index']]
                source_time=max(0,segment.trim_start or 0)/24+max(0,segment.duration-1)
                name=unique_media_filename(safe_media_filename(Path(segment.name).stem+'-opening.png','opening'),used)
                frame=destination/name
                result=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-v','error','-ss',str(source_time),'-i',materialize_source(segment),'-frames:v','1',str(frame)],capture_output=True)
                if result.returncode or not frame.is_file():
                    # Very short clips use their available first full-resolution frame.
                    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-v','error','-i',materialize_source(segment),'-frames:v','1',str(frame)],check=True,capture_output=True)
                fallback=g.add(original[338],'Video opening still (fallback)',[650,4300])
                _set_widget(fallback,'whatdreamscost/'+name,'image')
                g.connect(fallback,0,579,'on_false','IMAGE')
            else:
                g.connect(source,slot,579,'on_false','IMAGE')
            source=g.nodes[581];slot=0
        guide=g.add(original[541],f"Condition {r['label']} · {seconds:.3f}s",[400+350*i,4700])
        guide['type']=guide_def['id']
        guide['properties'].pop('previewExposures',None)
        guide['inputs'].append({'name':'audio','type':'AUDIO','shape':7,'link':None})
        _set_widget(guide,seconds,'value')
        # If a checkpoint lands exactly outside the AV tensor, anchor the actual final frame.
        if round(seconds*24)>=frame_count:_set_widget(guide,(frame_count-1)/24,'value')
        g.connect(source,slot,guide,'image','IMAGE');g.connect(previous,0,guide,'positive_1','CONDITIONING')
        if audio:g.connect(audio[0],audio[1],guide,'audio','AUDIO')
        previous=guide['id'];guides.append({'label':r['label'],'seconds':seconds,'node_id':guide['id']})
    g.connect(previous,0,271,'CONDITIONING','CONDITIONING')
    workflow=g.finish()
    workflow['id']=str(uuid.uuid4());workflow['revision']=0
    workflow['extra']['ltxDirector']={'format':'MiniMax H3 / ComfyUI','duration_seconds':total,'fps':24,'frame_count':frame_count,'guides':guides,
        'source_template':'Supplied final b rub workflow','asset_map':inventory}
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(workflow,indent=2,ensure_ascii=False)+'\n')
    return str(path)
