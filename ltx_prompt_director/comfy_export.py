"""Export the supplied MiniMax ComfyUI graph with project media and timed guides."""
from __future__ import annotations
import base64
import copy
import json
import math
import uuid
from importlib.resources import files
from pathlib import Path

from .media import copy_media_for_export, safe_media_filename, unique_media_filename
from .minimax_reference import reference_inventory, reference_slots
from .project_archive import materialize_source

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
    # Timeline clips contribute their soundtrack directly to standalone ref_audio ports.
    counts['audio'] += sum(r['kind']=='video' and 'timeline_index' in r for r in inventory)
    if counts['image']>9 or counts['video']>3 or counts['audio']>3:
        raise ValueError('This ComfyUI node supports at most 9 image inputs, 3 video inputs and 3 standalone audio inputs. Reduce that media type before exporting.')
    for r in inventory:
        if 'reference_slot' in r and r['kind']=='audio' and not refs[r['reference_slot']].get('audio_path'):
            raise ValueError(f"No usable audio track in {r['name']}.")
    total=sum(s.duration for s in segments) or 15.0
    frame_count=max(5, math.ceil((max(5,round(total*FPS)+1)-5)/17)*17+5)
    destination=Path(comfy_root)/'input'
    destination.mkdir(parents=True,exist_ok=True)
    used={x.name.casefold() for x in destination.iterdir()}
    def copy_file(source,name):
        source = Path(source)
        if source.suffix and Path(name).suffix.lower() != source.suffix.lower():
            name = Path(name).stem + source.suffix
        target=copy_media_for_export(str(source),name,destination,used,'minimax')
        return target.name
    def image_file(data,name):
        mime=data.split(';',1)[0].removeprefix('data:')
        suffix={'image/jpeg':'.jpg','image/webp':'.webp','image/tiff':'.tiff','image/bmp':'.bmp'}.get(mime,'.png')
        name=unique_media_filename(safe_media_filename(Path(name).stem+suffix,'reference'),used)
        (destination/name).write_bytes(base64.b64decode(data.split(',',1)[1]))
        return name
    w=json.loads(files('ltx_prompt_director').joinpath('assets/minimax-comfy-workflow.json').read_text())
    original={n['id']:copy.deepcopy(n) for n in w['nodes']}
    guide_types={d['id'] for d in w['definitions']['subgraphs'] if d['name']=='MinMaxCondFrame'}
    g=Graph(w)
    g.remove({n['id'] for n in w['nodes'] if n['type'] in {'LoadImage','VHS_LoadVideo'}|guide_types or n['id'] in {451,596,597,598,599,600,587}})
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
    # The visible settings subgraph is the single source of output settings.
    settings=g.nodes[452]
    settings['widgets_values']=['16:9 (Widescreen)',0.5,32,False,total,20]
    settings['widgets_values_named']={'aspect_ratio':'16:9 (Widescreen)',
        'megapixels':0.5,'multiple':32,'value':False,'value_1':total,'value_2':20}
    g.connect(223,0,136,'width','INT')
    g.connect(224,0,136,'height','INT')
    g.connect(537,0,136,'length','INT')
    # Keep endpoint frames inside the tensor using the same duration formula everywhere.
    settings_def=next(d for d in w['definitions']['subgraphs'] if d['id']==settings['type'])
    expression=next(n for n in settings_def['nodes'] if n['type']=='ComfyMathExpression')
    formula='max(5, round(a * 24) + 1) + (5 - (max(5, round(a * 24) + 1) % 17)) % 17'
    _set_widget(expression,formula,'expression')
    _set_widget(g.nodes[138],prompt,'value')
    _set_widget(native,frame_count,'length',3)
    # Decode to images + audio directly for Video Helper Suite's output node.
    g.remove({92,585})
    decoder=g.nodes[382]
    definition=next(d for d in w['definitions']['subgraphs'] if d['id']==decoder['type'])
    definition['nodes']=[n for n in definition['nodes'] if n['type']!='CreateVideo']
    definition['links']=[l for l in definition['links'] if l['id'] not in {258,259,260}]
    definition['outputs']=[]
    decoder['outputs']=[]
    for slot,(source,kind,lid) in enumerate([(122,'IMAGE',258),(121,'AUDIO',259)]):
        definition['outputs'].append({'id':str(uuid.uuid4()),'name':kind,'type':kind,'linkIds':[lid]})
        definition['links'].append({'id':lid,'origin_id':source,'origin_slot':0,'target_id':-20,'target_slot':slot,'type':kind})
        decoder['outputs'].append({'name':kind,'type':kind,'links':[]})
    output=g.add({'type':'VHS_VideoCombine','pos':[0,0],'size':[320,360],'flags':{},'order':0,'mode':0,
        'inputs':[{'name':name,'type':kind,'link':None} for name,kind in
                  [('images','IMAGE'),('audio','AUDIO'),('meta_batch','VHS_BatchManager'),('vae','VAE')]],
        'outputs':[{'name':'Filenames','type':'VHS_FILENAMES','links':[]}],
        'properties':{'Node name for S&R':'VHS_VideoCombine','cnr_id':'comfyui-videohelpersuite'},
        'widgets_values':{'frame_rate':24,'loop_count':0,'filename_prefix':'AnimateDiff','format':'video/webm',
            'pix_fmt':'yuv420p','crf':15,'save_metadata':True,'trim_to_audio':True,'pingpong':False,'save_output':True}},
        'Video Combine')
    g.connect(decoder,0,output,'images','IMAGE');g.connect(decoder,1,output,'audio','AUDIO')
    # Local Set/Get pairs keep media wires out of the sampling and timing lanes.
    reference_getters=[]
    def reference_port(source,slot,name):
        kind=source['outputs'][slot]['type']
        key='LTX Director '+name
        setter=g.add({'type':'SetNode','pos':[0,0],'size':[220,60],'flags':{},'inputs':[{'name':kind,'type':kind,'link':None}],
            'outputs':[{'name':kind,'type':kind,'links':[]}],'properties':{'Node name for S&R':'SetNode'},'widgets_values':[key]},key)
        getter=g.add({'type':'GetNode','pos':[0,0],'size':[220,60],'flags':{},'inputs':[],
            'outputs':[{'name':kind,'type':kind,'links':[]}],'properties':{'Node name for S&R':'GetNode'},'widgets_values':[key]},key)
        setter['pos']=[source['pos'][0],source['pos'][1]+source.get('size',[300,500])[1]+30+(90 if slot==2 else 0)]
        reference_getters.append(getter)
        g.connect(source,slot,setter,kind,kind);g.connect(getter,0,136,name,kind)
    # Only still-image checkpoints need frame conditioning. Videos are native references.
    guide_def=copy.deepcopy(next(d for d in w['definitions']['subgraphs'] if d['id']==original[541]['type']))
    guide_def['id']=str(uuid.uuid4())
    w['definitions']['subgraphs'].append(guide_def)
    image_index=video_index=audio_index=0
    loaded={}
    for media_index,r in enumerate(inventory):
        kind=r['kind']
        ref=refs[r['reference_slot']] if 'reference_slot' in r else None
        segment=segments[r['timeline_index']] if 'timeline_index' in r else None
        x=420*media_index;y=400
        if kind=='text':continue
        if kind=='image':
            filename=image_file(ref['image'],ref['name']) if ref else copy_file(materialize_source(segment),segment.name)
            loader=g.add(original[338],r['label'],[x,y]);_set_widget(loader,filename,'image')
            reference_port(loader,0,'ref_images.ref_image_'+str(image_index));image_index+=1
            loaded[r.get('timeline_index',f"ref{r.get('reference_slot')}")]=(loader,0,None,r)
        elif kind=='video':
            filename=copy_file(ref['path'] if ref else materialize_source(segment),r['name'])
            loader=g.add(original[175],r['label'],[x,y])
            vals=loader['widgets_values'];vals.update(video=filename,force_rate=24,custom_width=0,custom_height=0,
                frame_load_cap=round(segment.duration*24) if segment else frame_count,skip_first_frames=segment.trim_start or 0 if segment else 0)
            vals.pop('videopreview',None);loader['widgets_values_named']=copy.deepcopy(vals)
            if segment:
                # Exactly the requested native hookup: no sampling or first-frame detour.
                g.connect(loader,0,136,'ref_videos.ref_video_'+str(video_index),'IMAGE')
                g.connect(loader,2,136,'ref_audios.ref_audio_'+str(audio_index),'AUDIO')
                audio_index+=1
                loader['pos']=[-1100+420*video_index,1800]
            else:
                reference_port(loader,0,'ref_videos.ref_video_'+str(video_index))
                if ref.get('audio_path'):
                    reference_port(loader,2,'ref_video_audios.ref_video_audio_'+str(video_index))
            video_index+=1
        else:
            filename=copy_file(ref['audio_path'],Path(ref['name']).stem+'.wav')
            node={'type':'LoadAudio','pos':[x,y],'size':[300,150],'flags':{},'order':0,'mode':0,'inputs':[],
                'outputs':[{'name':'AUDIO','type':'AUDIO','links':[]}],'properties':{'Node name for S&R':'LoadAudio'},'widgets_values':[filename]}
            loader=g.add(node,r['label']);reference_port(loader,0,'ref_audios.ref_audio_'+str(audio_index));audio_index+=1
    # Still images retain their timed guides; timeline videos never create guides.
    timed=[r for r in inventory if 'timeline_index' in r and r['kind']=='image']
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
            _set_widget(switch,False,'switch')
            g.connect(source,slot,579,'on_true','IMAGE')
            g.connect(source,slot,579,'on_false','IMAGE')
            source=g.nodes[581];slot=0
        guide=g.add(original[541],f"Condition {r['label']} · {seconds:.3f}s",[400+420*i,1700])
        guide['type']=guide_def['id']
        guide['properties'].pop('previewExposures',None)
        _set_widget(guide,seconds,'value')
        # If a checkpoint lands exactly outside the AV tensor, anchor the actual final frame.
        if round(seconds*24)>=frame_count:_set_widget(guide,(frame_count-1)/24,'value')
        g.connect(source,slot,guide,'image','IMAGE');g.connect(previous,0,guide,'positive_1','CONDITIONING')
        previous=guide['id'];guides.append({'label':r['label'],'seconds':seconds,'node_id':guide['id']})
    g.connect(previous,0,271,'CONDITIONING','CONDITIONING')
    # Remove unused definitions recursively, including all old frame-guide variants.
    needed={n['type'] for n in g.nodes.values()}
    definitions={d['id']:d for d in w['definitions']['subgraphs']}
    reachable=set()
    pending=list(needed & definitions.keys())
    while pending:
        key=pending.pop()
        if key in reachable:continue
        reachable.add(key)
        pending.extend(n['type'] for n in definitions[key]['nodes'] if n['type'] in definitions)
    w['definitions']['subgraphs']=[d for d in definitions.values() if d['id'] in reachable]
    # Arrange the retained graph in separate media, guide, settings and sampling lanes.
    positions={138:[-1000,0],283:[-1000,300],452:[-1000,450],
        516:[-600,450],515:[-600,530],517:[-600,610],513:[-600,690],514:[-600,770],
        518:[0,1700],271:[400+420*len(timed),1700],579:[0,1300],580:[0,1510],581:[400,1510],
        136:[0,2300],282:[-700,2300],217:[-700,2380],582:[-700,2460],284:[-700,2540],
        223:[-700,2620],224:[-700,2700],537:[-700,2780],273:[440,2300],213:[440,2380],
        381:[900,2300],125:[1200,2300],382:[1550,2300],output['id']:[1900,2300],
        285:[-1000,4000],207:[-600,4000],206:[-600,4080],281:[-600,4160],229:[-600,4240],228:[-600,4320],
        143:[0,4000],144:[0,4080],226:[0,4160],142:[350,4000],238:[700,4000],
        237:[0,4350],236:[0,4430],227:[0,4510],331:[0,4590],141:[350,4350],356:[700,4350],333:[1100,4350],334:[1100,4430]}
    g.remove({116,117,140})
    for key,pos in positions.items():
        if key in g.nodes:g.nodes[key]['pos']=pos
    g.nodes[138]['size']=[480,260]
    g.nodes[125]['size']=[310,310]
    for i,node in enumerate(reference_getters):node['pos']=[-360,2900+80*i]
    # Replace sample groups, reroutes and link geometry with bounds of actual retained nodes.
    def group(title,nodes,color):
        if not nodes:return None
        left=min(n['pos'][0] for n in nodes)-40;top=min(n['pos'][1] for n in nodes)-80
        right=max(n['pos'][0]+n.get('size',[300,200])[0] for n in nodes)+40
        bottom=max(n['pos'][1]+n.get('size',[300,200])[1] for n in nodes)+40
        return {'title':title,'bounding':[left,top,right-left,bottom-top],'color':color,'flags':{}}
    lanes=[('Prompt and settings',lambda n:n['pos'][0]<0 and n['pos'][1]<1000),
           ('Reference media',lambda n:n['pos'][0]>=0 and 400<=n['pos'][1]<1100),
           ('Conditioning frames',lambda n:1100<=n['pos'][1]<2200),
           ('Conditioning and sampling',lambda n:2200<=n['pos'][1]<4000),
           ('Models and sampler settings',lambda n:n['pos'][1]>=4000)]
    w['groups']=[item for title,predicate in lanes if (item:=group(title,[n for n in g.nodes.values() if predicate(n)],'#3f789e'))]
    for i,item in enumerate(w['groups'],1):item['id']=i
    w.pop('reroutes',None)
    for key in ('reroutes','linkExtensions'):w.get('extra',{}).pop(key,None)
    workflow=g.finish()
    workflow['id']=str(uuid.uuid4());workflow['revision']=0
    workflow['extra']['ltxDirector']={'format':'MiniMax H3 / ComfyUI','duration_seconds':total,'fps':24,'frame_count':frame_count,'guides':guides,
        'source_template':'Supplied final b rub workflow','asset_map':inventory}
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(workflow,indent=2,ensure_ascii=False)+'\n')
    return str(path)
