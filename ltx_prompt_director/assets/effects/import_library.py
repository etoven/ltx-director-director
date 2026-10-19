#!/usr/bin/env python3
"""Offline catalog validation, lookup and segment selection. No network calls."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import wave
from array import array

ROOT=Path(__file__).resolve().parent

def read(name):
    return json.loads((ROOT/name).read_text(encoding='utf-8'))

def asset(name):
    p=(ROOT/name).resolve()
    if not p.is_relative_to(ROOT):
        raise ValueError('Asset path escapes the package: '+name)
    if not p.is_file():
        raise ValueError('Missing file: '+name)
    return p

def validate():
    effects=read('effects.json');species=read('species.json');clips=read('sources/clips.json');sources=read('sources/footage.json');manifest=read('manifest.json');audio=read('sources/audio.json')
    ids={e['id'] for e in effects};sids={s['id'] for s in sources};cids={c['id'] for c in clips}
    aids={a['id']:a for a in audio}
    if len(ids)!=len(effects):raise ValueError('Duplicate effect IDs')
    if len(sids)!=len(sources) or len(cids)!=len(clips):raise ValueError('Duplicate source or clip IDs')
    if len(aids)!=len(audio):raise ValueError('Duplicate audio IDs')
    line_records=[json.loads(l) for l in (ROOT/'effects.jsonl').read_text().splitlines() if l.strip()]
    if line_records!=effects:raise ValueError('JSON and JSONL disagree')
    for e in effects:
        if not re.fullmatch(r'FX\d{3}',e['id']):raise ValueError('Invalid effect ID')
        for k in ['effect_name','category','anatomy','stage','minimax_segment','gemini_quick_fix','thumbnail']:
            if not isinstance(e.get(k),str) or not e[k].strip():raise ValueError(e['id']+' missing '+k)
        p=asset(e['thumbnail'])
        if p.read_bytes()[:2]!=b'\xff\xd8':raise ValueError('Invalid JPEG thumbnail: '+p.name)
        if not set(e['reference_ids'])<=sids or not set(e['reference_clip_ids'])<=cids:raise ValueError('Unresolved reference in '+e['id'])
        if {m['clip_id'] for m in e['reference_matches']}!=set(e['reference_clip_ids']):raise ValueError('Reference scope missing in '+e['id'])
        if e['audio_sample_ids']:raise ValueError('Standalone demo recordings are not included in this edition')
        for aid in e['source_audio_ids']:
            if aid not in aids or aids[aid]['kind']!='source_soundtrack':raise ValueError('Unresolved or wrong audio role in '+e['id'])
            if e['id'] not in aids[aid]['matched_effect_ids']:raise ValueError('Audio mapping disagrees in '+e['id'])
        if {aids[aid]['source_clip_id'] for aid in e['source_audio_ids']}!=set(e['reference_clip_ids']):raise ValueError('Source audio scope mismatch in '+e['id'])
    for c in clips:
        if c['source_id'] not in sids:raise ValueError('Unresolved clip source')
        asset(c['path']);asset(c['poster'])
        if not 0<c['actual_duration_seconds']<10:raise ValueError('Invalid excerpt duration')
    for s in species:asset(s['image'])
    for a in audio:
        asset(a['thumbnail'])
        if not set(a['matched_effect_ids'])<=ids:raise ValueError('Unknown audio effect')
        if a['kind']=='source_soundtrack':
            if a['source_clip_id'] not in cids or a['source_id'] not in sids:raise ValueError('Unknown audio source')
        else:raise ValueError('Only actual source soundtrack samples are included')
        with wave.open(str(asset(a['path'])),'rb') as w:
            if w.getsampwidth()!=2 or w.getcomptype()!='NONE':raise ValueError('Audio must be PCM 16-bit: '+a['id'])
            if w.getframerate()!=a['sample_rate'] or w.getnchannels()!=a['channels']:raise ValueError('Audio format mismatch')
            if w.getnframes()<=0 or abs(w.getnframes()/w.getframerate()-a['duration_seconds'])>0.0001:raise ValueError('Audio duration mismatch')
            samples=array('h',w.readframes(w.getnframes()))
            if sys.byteorder!='little':samples.byteswap()
            peak=max(abs(v) for v in samples)
            if peak==0:raise ValueError('Unexpected empty/silent sample: '+a['id'])
    for key,actual in [('effects',len(effects)),('thumbnails',len(effects)),('species',len(species)),('clips',len(clips)),('sources',len(sources)),('audio_samples',len(audio)),('source_audio_samples',len(audio))]:
        if manifest['counts'][key]!=actual:raise ValueError('Manifest count mismatch: '+key)
    verified=0
    for line in (ROOT/'checksums.sha256').read_text().splitlines():
        digest,name=line.split('  ',1);p=asset(name)
        if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise ValueError('Checksum mismatch: '+name)
        verified+=1
    print(f'Validated {len(effects)} effects, {len(effects)} thumbnails, {len(species)} presets, {len(clips)} excerpts, {len(audio)} WAV samples and {verified} file checksums.')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--validate',action='store_true')
    ap.add_argument('--list',nargs='?',const='',dest='query',help='List effects matching a word, or all effects when empty.')
    ap.add_argument('--id',help='Effect ID such as FX021.')
    ap.add_argument('--model',choices=['minimax','gemini'],default='minimax')
    ap.add_argument('--species',help='Species key from species.json, such as panther or horse.')
    ap.add_argument('--side',choices=['left','right'])
    ap.add_argument('--region',help='Explicit region, such as forearm or lower hindleg.')
    ap.add_argument('--stage',help='Visible target stage or hold state, in plain language.')
    ap.add_argument('--subject',default='<Subject 1>',help='Existing target subject label.')
    ap.add_argument('--reference-subject',default='<Subject 7>',help='Unused or already-defined effect subject label.')
    ap.add_argument('--reference-clip',help='A matching CLIPxxx ID; export does not upload it.')
    ap.add_argument('--with-sound',action='store_true',help='Include optional sound instruction text; does not add audio files.')
    ap.add_argument('--source-audio',action='store_true',help='Include the selected clip mixed soundtrack; requires --reference-clip and --bundle.')
    ap.add_argument('--bundle',action='store_true',help='Output a JSON selection bundle rather than just the segment.')
    args=ap.parse_args()
    if args.validate:
        validate();return
    if args.source_audio and (not args.reference_clip or not args.bundle):ap.error('--source-audio requires --reference-clip and --bundle.')
    effects=read('effects.json')
    if args.query is not None:
        q=args.query.lower()
        for e in effects:
            if q in (' '.join([e['id'],e['effect_name'],e['category'],e['anatomy'],e['minimax_segment'],*e['species']])).lower():
                print(f"{e['id']} | {e['effect_name']} | {e['category']} | {', '.join(e['species'])}")
        return
    if not args.id:
        ap.print_help();return
    e=next((e for e in effects if e['id']==args.id.upper()),None)
    if not e:ap.error('Unknown effect ID: '+args.id)
    audio={a['id']:a for a in read('sources/audio.json')}
    include_sound=args.with_sound or e['category']=='Sound'
    selected_audio=[]
    species=next((s for s in read('species.json') if s['id']==args.species),None) if args.species else None
    if args.species and not species:ap.error('Unknown species. Use a key from species.json.')
    if species and 'any' not in e['species'] and args.species not in e['species']:
        ap.error('This effect has a different species topology. Choose a compatible mechanic or omit --species.')
    if e['category']=='Sound' and (args.region or args.side or args.species):ap.error('Sound recipes do not accept body/side/species overrides.')
    key='minimax_segment' if args.model=='minimax' else 'gemini_quick_fix'
    text=e[key].replace('<Subject 1>',args.subject)
    clauses=[]
    if args.side or args.region:
        region=args.region or e['anatomy'];side=(args.side+' ') if args.side else ''
        clauses.append("Selected edit region: the subject's "+side+region+'.')
    if args.stage:clauses.append('Requested visible endpoint: '+args.stage+'. Stop and hold there.')
    if species:clauses.append('Animal endpoint for this region: '+species['appearance']+' '+species['anatomy'])
    clip=None
    if args.reference_clip:
        clip=next((c for c in read('sources/clips.json') if c['id']==args.reference_clip.upper()),None)
        if not clip:ap.error('Unknown reference clip ID')
        if clip['id'] not in e['reference_clip_ids']:ap.error('Clip is not a documented match for this effect. Inspect the catalog and choose a listed match.')
        asset(clip['path'])
        if args.subject==args.reference_subject:ap.error('Target and effect subject labels must differ.')
        clauses.append('Use '+args.reference_subject+' only for this scope: '+clip['transfer_scope'])
    text=' '.join(clauses+[text,'Match constraint: '+e['match_instruction']])
    if include_sound and e.get('sound_segment') and e['category']!='Sound':text+=' Optional sound: '+e['sound_segment']
    if not args.bundle:
        print(text);return
    sources={s['id']:s for s in read('sources/footage.json')}
    bundle=dict(effect_id=e['id'],effect_name=e['effect_name'],model_target=args.model,segment=text,thumbnail=e['thumbnail'],species_id=args.species,reference_media=[clip] if clip else [],source_links=[dict(id=rid,url=sources[rid]['url']) for rid in e['reference_ids']],upload_status='not uploaded; attach selected local media through the host workflow')
    if args.source_audio:selected_audio+=['SRC'+clip['id'][4:]]
    bundle['audio_media']=[audio[aid] for aid in selected_audio]
    for a in bundle['audio_media']:asset(a['path'])
    bundle['audio_role']='Suggested references only; not uploaded or enabled. Attach explicitly and preserve existing dialogue. Source soundtrack is a mixed track, not isolated foley.'
    if clip:
        bundle['reference_definition_to_add_after_attachment']=args.reference_subject+' is only the selected effect or motion in the '+clip['id']+' attachment. Transfer only this documented scope: '+clip['transfer_scope']
    print(json.dumps(bundle,indent=2,ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,OSError,json.JSONDecodeError) as exc:
        print('Import error: '+str(exc),file=sys.stderr);sys.exit(1)
