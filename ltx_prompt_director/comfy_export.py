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
    counts={kind:sum(r['kind']==kind and 'reference_slot' in r for r in inventory)
            for kind in ('image','video','audio')}
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
    g.remove({n['id'] for n in w['nodes'] if n['type'] in {'LoadImage','VHS_LoadVideo'}|guide_types or n['id'] in {451,579,580,581,585,596,597,598,599,600,587}})
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
        key='Prompt Director '+name
        setter=g.add({'type':'SetNode','pos':[0,0],'size':[220,60],'flags':{},'inputs':[{'name':kind,'type':kind,'link':None}],
            'outputs':[{'name':kind,'type':kind,'links':[]}],'properties':{'Node name for S&R':'SetNode'},'widgets_values':[key]},key)
        getter=g.add({'type':'GetNode','pos':[0,0],'size':[220,60],'flags':{},'inputs':[],
            'outputs':[{'name':kind,'type':kind,'links':[]}],'properties':{'Node name for S&R':'GetNode'},'widgets_values':[key]},key)
        setter['pos']=[source['pos'][0],source['pos'][1]+source.get('size',[300,500])[1]+30+(90 if slot==2 else 0)]
        reference_getters.append(getter)
        g.connect(source,slot,setter,kind,kind);g.connect(getter,0,136,name,kind)
    # Expose soundtrack anchoring on the supplied timed guide subgraph.
    guide_def=copy.deepcopy(next(d for d in w['definitions']['subgraphs'] if d['id']==original[541]['type']))
    guide_def['id']=str(uuid.uuid4())
    inner=next(n for n in guide_def['nodes'] if n['type']=='MiniMaxH3AddGuide')
    audio_link=max(l['id'] for l in guide_def['links'])+1
    inner['inputs'][5]['link']=audio_link
    guide_def['inputs'].append({'id':str(uuid.uuid4()),'name':'audio','type':'AUDIO','shape':7,'linkIds':[audio_link]})
    guide_def['links'].append({'id':audio_link,'origin_id':-10,'origin_slot':3,'target_id':inner['id'],'target_slot':5,'type':'AUDIO'})
    guide_def['state']['lastLinkId']=max(guide_def['state']['lastLinkId'],audio_link)
    w['definitions']['subgraphs'].append(guide_def)
    image_index=video_index=audio_index=0
    loaded={}
    media_helpers={}
    for media_index,r in enumerate(inventory):
        kind=r['kind']
        ref=refs[r['reference_slot']] if 'reference_slot' in r else None
        segment=segments[r['timeline_index']] if 'timeline_index' in r else None
        x=420*media_index;y=400
        if kind=='text':continue
        if kind=='image':
            filename=image_file(ref['image'],ref['name']) if ref else copy_file(materialize_source(segment),segment.name)
            loader=g.add(original[338],r['label'],[x,y]);_set_widget(loader,filename,'image')
            if ref is not None:
                reference_port(loader,0,'ref_images.ref_image_'+str(image_index));image_index+=1
            loaded[r.get('timeline_index',f"ref{r.get('reference_slot')}")]=(loader,0,None,r)
        elif kind=='video':
            filename=copy_file(ref['path'] if ref else materialize_source(segment),r['name'])
            loader=g.add(original[175],r['label'],[x,y])
            vals=loader['widgets_values'];vals.update(video=filename,force_rate=24,custom_width=0,custom_height=0,
                frame_load_cap=round(segment.duration*24) if segment else frame_count,skip_first_frames=segment.trim_start or 0 if segment else 0)
            vals.pop('videopreview',None);loader['widgets_values_named']=copy.deepcopy(vals)
            if segment:
                # The full trimmed clip and its soundtrack share the timeline start anchor.
                loaded[r['timeline_index']]=(loader,0,(loader,2),r)
                # Match the generation canvas for full-batch audiovisual conditioning.
                helpers=[]
                for source_id,name in [(223,'custom_width'),(224,'custom_height')]:
                    getter=g.add(original[source_id])
                    loader['inputs'].append({'name':name,'type':'INT','widget':{'name':name},'link':None})
                    g.connect(getter,0,loader,name,'INT');helpers.append(getter['id'])
                media_helpers[loader['id']]=helpers
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
    # Every visual timeline item gets a timed guide; video soundtracks anchor with the clip.
    timed=[r for r in inventory if 'timeline_index' in r and r['kind'] in {'image','video'}]
    previous=518
    guides=[]
    for i,r in enumerate(timed):
        source,slot,audio,_=loaded[r['timeline_index']]
        seconds=r.get('checkpoint_time',r['start_time'])
        guide=g.add(original[541],f"Condition {r['label']} · {seconds:.3f}s",[400+420*i,1700])
        guide['type']=guide_def['id']
        guide['properties'].pop('previewExposures',None)
        guide['inputs'].append({'name':'audio','type':'AUDIO','shape':7,'link':None})
        guide['size']=[288,120]
        _set_widget(guide,seconds,'value')
        # If a checkpoint lands exactly outside the AV tensor, anchor the actual final frame.
        if round(seconds*24)>=frame_count:_set_widget(guide,(frame_count-1)/24,'value')
        g.connect(source,slot,guide,'image','IMAGE');g.connect(previous,0,guide,'positive_1','CONDITIONING')
        if audio:g.connect(audio[0],audio[1],guide,'audio','AUDIO')
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
    # Layout uses actual node sizes and explicit membership, never coordinate ranges.
    g.remove({116,117,140})
    g.nodes[138]['size']=[480,260]
    g.nodes[125]['size']=[310,310]
    native['size']=[368,max(416,80+24*len(native['inputs'])+120)]
    arranged=set()
    sections=[]
    def section(title):
        nodes=[]
        sections.append((title,nodes))
        return nodes
    def put(nodes,key,x,y):
        if key not in g.nodes:return 0
        node=g.nodes[key]
        node['pos']=[x,y]
        nodes.append(node);arranged.add(key)
        return node.get('size',[300,200])[1]
    def stack(nodes,keys,x,y,gap=16):
        for key in keys:
            if key in g.nodes:y+=put(nodes,key,x,y)+gap
        return y-gap
    prompt_nodes=section('Production prompt')
    put(prompt_nodes,138,0,0)
    put(prompt_nodes,283,0,300)
    settings_nodes=section('Video settings')
    put(settings_nodes,452,0,0)
    stack(settings_nodes,[516,515,517,513,514],420,0)
    model_nodes=section('Models and sampler settings')
    put(model_nodes,285,0,0)
    stack(model_nodes,[207,206,281,229,228],280,0)
    end=stack(model_nodes,[143,144,226],560,0)
    stack(model_nodes,[142,238],840,0)
    second_row=max(end,max(n['pos'][1]+n['size'][1] for n in model_nodes))+32
    stack(model_nodes,[237,236,227,331],0,second_row)
    put(model_nodes,141,280,second_row)
    bottom=put(model_nodes,356,560,second_row)+second_row+24
    stack(model_nodes,[333,334],560,bottom)
    media_nodes=section('Media inputs')
    loaders=[n for n in g.nodes.values() if n['type'] in {'LoadImage','VHS_LoadVideo','LoadAudio'}]
    row_y=0
    for offset in range(0,len(loaders),4):
        row_bottom=row_y
        for column,loader in enumerate(loaders[offset:offset+4]):
            x=column*340
            bottom=row_y+put(media_nodes,loader['id'],x,row_y)
            setters=[l[3] for l in w['links'] if l[1]==loader['id'] and g.nodes[l[3]]['type']=='SetNode']
            if setters:bottom=stack(media_nodes,setters,x,bottom+16)
            if media_helpers.get(loader['id']):bottom=stack(media_nodes,media_helpers[loader['id']],x,bottom+16)
            row_bottom=max(row_bottom,bottom)
        row_y=row_bottom+32
    guide_nodes=section('Conditioning frames')
    chain=[518]+[item['node_id'] for item in guides]+[271]
    row_y=0
    for offset in range(0,len(chain),4):
        row_bottom=row_y
        for column,key in enumerate(chain[offset:offset+4]):
            row_bottom=max(row_bottom,row_y+put(guide_nodes,key,column*380,row_y))
        row_y=row_bottom+24
    sampling_nodes=section('Conditioning and sampling')
    getters=[282,217,582,284,223,224,537]+[n['id'] for n in reference_getters]
    stack(sampling_nodes,getters,0,0)
    put(sampling_nodes,136,280,0)
    stack(sampling_nodes,[273,213],690,0)
    sampling_y=max(n['pos'][1]+n['size'][1] for n in sampling_nodes)+32
    put(sampling_nodes,381,0,sampling_y)
    put(sampling_nodes,125,200,sampling_y)
    put(sampling_nodes,382,550,sampling_y)
    put(sampling_nodes,output['id'],750,sampling_y)
    # Include every retained node in exactly one group, including future base additions.
    remaining=[key for key in g.nodes if key not in arranged]
    if remaining:
        bottom=max(n['pos'][1]+n['size'][1] for n in sampling_nodes)+24
        stack(sampling_nodes,remaining,0,bottom)
    # Pack complete group rectangles into adjacent rows, leaving only a small gutter.
    boxes=[]
    for title,nodes in sections:
        if not nodes or nodes is prompt_nodes:continue
        right=max(n['pos'][0]+n['size'][0] for n in nodes)
        bottom=max(n['pos'][1]+n['size'][1] for n in nodes)
        boxes.append((title,nodes,right+48,bottom+72))
    limit=max(2000,max(box[2] for box in boxes))
    x=y=row_height=0
    w['groups']=[]
    for title,nodes,width,height in boxes:
        if x and x+width>limit:
            x=0;y+=row_height+32;row_height=0
        for node in nodes:
            node['pos'][0]+=x+24;node['pos'][1]+=y+48
        w['groups'].append({'id':len(w['groups'])+1,'title':title,
            'bounding':[x,y,width,height],'color':'#3f789e','flags':{}})
        x+=width+32;row_height=max(row_height,height)
    # Reserve the whole left edge for a readable prompt, beside the packed group stack.
    prompt_height=max(1000, max(group['bounding'][1]+group['bounding'][3] for group in w['groups']))
    prompt_width=648
    for _,nodes,_,_ in boxes:
        for node in nodes:node['pos'][0]+=prompt_width+32
    for group in w['groups']:group['bounding'][0]+=prompt_width+32
    g.nodes[138]['size']=[600,prompt_height-168]
    g.nodes[138]['pos']=[24,48]
    g.nodes[283]['pos']=[24,prompt_height-96]
    w['groups'].insert(0,{'id':len(w['groups'])+1,'title':'Production prompt',
        'bounding':[0,0,prompt_width,prompt_height],'color':'#b58b2a','flags':{}})
    w.pop('reroutes',None)
    for key in ('reroutes','linkExtensions'):w.get('extra',{}).pop(key,None)
    workflow=g.finish()
    workflow['id']=str(uuid.uuid4());workflow['revision']=0
    workflow['extra']['ltxDirector']={'format':'MiniMax H3 / ComfyUI','duration_seconds':total,'fps':24,'frame_count':frame_count,'guides':guides,
        'source_template':'Supplied final b rub workflow','asset_map':inventory,
        'video_policy':'timeline clips use MiniMaxH3AddGuide only; full frame batch and audio at start index',
        'audio_policy':'timed source guidance; generate continuous sound across clip boundaries'}
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(workflow,indent=2,ensure_ascii=False)+'\n')
    return str(path)
