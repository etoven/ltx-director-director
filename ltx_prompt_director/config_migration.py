"""First-run migration of legacy Qt settings, workspaces and managed project data."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import uuid

LEGACY_IDENTITIES=(('LTXDirectorDirector','Prompt Director'),
                   ('LTXDirectorDirector','LTX Director - Director'),
                   ('LTXPromptDirector','LTX Prompt Director'))


def migration_locations():
    from PySide6.QtCore import QCoreApplication,QStandardPaths
    locations=(QStandardPaths.StandardLocation.AppDataLocation,
               QStandardPaths.StandardLocation.AppConfigLocation,
               QStandardPaths.StandardLocation.CacheLocation)
    organization=QCoreApplication.organizationName();name=QCoreApplication.applicationName()
    targets={location:Path(QStandardPaths.writableLocation(location)) for location in locations}
    mappings=[]
    try:
        for old_org,old_name in LEGACY_IDENTITIES:
            QCoreApplication.setOrganizationName(old_org);QCoreApplication.setApplicationName(old_name)
            for location in locations:
                source=Path(QStandardPaths.writableLocation(location));target=targets[location]
                if source!=target and source.is_dir():mappings.append((source,target))
    finally:
        QCoreApplication.setOrganizationName(organization);QCoreApplication.setApplicationName(name)
    # Earlier standalone installations also used these unscoped folder names.
    for location,generic in ((locations[0],QStandardPaths.StandardLocation.GenericDataLocation),
                             (locations[1],QStandardPaths.StandardLocation.GenericConfigLocation)):
        root=Path(QStandardPaths.writableLocation(generic))
        for folder in ('ltxdirectordirector','ltx-director-director'):
            source=root/folder
            if source.is_dir() and source!=targets[location]:mappings.append((source,targets[location]))
    mappings=list(dict.fromkeys(mappings))
    return mappings,targets[locations[0]]/'.legacy-migration-v1.json'


def rebase_paths(value,mappings):
    if isinstance(value,dict):return {key:rebase_paths(item,mappings) for key,item in value.items()}
    if isinstance(value,list):return [rebase_paths(item,mappings) for item in value]
    if not isinstance(value,str):return value
    # Match complete root prefixes only; external media and arbitrary prompt
    # text are not rewritten. Longer nested roots take precedence.
    for source,target in sorted(mappings,key=lambda pair:len(str(pair[0])),reverse=True):
        root=str(source)
        if value==root:return str(target)
        for separator in ('/', '\\'):
            if value.startswith(root+separator):return str(target)+value[len(root):]
    return value


def rebase_setting(value,mappings):
    if isinstance(value,str) and value.lstrip().startswith(('{','[')):
        try:
            parsed=json.loads(value);updated=rebase_paths(parsed,mappings)
            return json.dumps(updated,ensure_ascii=False) if updated!=parsed else value
        except ValueError:pass
    return rebase_paths(value,mappings)


def migrate_settings(mappings=()):
    from PySide6.QtCore import QSettings
    current=QSettings()
    if current.allKeys():return
    for organization,name in LEGACY_IDENTITIES:
        legacy=QSettings(organization,name)
        for key in legacy.allKeys():
            if not current.contains(key):current.setValue(key,rebase_setting(legacy.value(key),mappings))
    current.sync()
    if current.status()!=type(current).Status.NoError:
        raise OSError('Could not write the migrated Prompt Director preferences.')


def _atomic_write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.migrate-tmp')
    try:
        with temporary.open('wb') as stream:stream.write(data)
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def _stock_workspace(path):
    from importlib.resources import files
    from .workspaces import validate_definition
    try:
        stock=json.loads(files('ltx_prompt_director').joinpath('workspace_templates',path.name).read_text())
        return validate_definition(json.loads(path.read_text()))==validate_definition(stock)
    except (FileNotFoundError,ValueError,KeyError):return False


def copy_legacy_data(mappings,progress=None):
    """Merge copies without deleting originals or overwriting new user files."""
    created=set();copied=0
    for source,target in mappings:
        if not source.is_dir():continue
        for old in sorted(source.rglob('*')):
            if old.is_symlink():continue
            relative=old.relative_to(source);new=target/relative
            if old.is_dir():new.mkdir(parents=True,exist_ok=True);continue
            if not old.is_file():continue
            if new.exists():
                # a44 may have created untouched defaults before the legacy
                # workspace directory was found. Prefer an older custom template.
                replace=(new in created and relative.parts[0]=='workspaces' and new.suffix=='.json'
                         and _stock_workspace(new) and not _stock_workspace(old))
                if not replace:continue
            if progress:progress(f'Migrating existing files… {copied+1}')
            new.parent.mkdir(parents=True,exist_ok=True)
            temporary=new.with_name('.'+new.name+'.'+uuid.uuid4().hex+'.migrate-tmp')
            try:
                with old.open('rb') as incoming,temporary.open('wb') as outgoing:
                    while chunk:=incoming.read(4*1024*1024):
                        outgoing.write(chunk)
                        if progress:progress(f'Migrating existing files… {copied+1}')
                if new.suffix.lower() in {'.json','.ltxd'}:
                    try:
                        value=json.loads(temporary.read_text('utf-8'));updated=rebase_paths(value,mappings)
                        if updated!=value:temporary.write_text(json.dumps(updated,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                    except (UnicodeError,ValueError):pass
                shutil.copystat(old,temporary);os.replace(temporary,new)
            finally:temporary.unlink(missing_ok=True)
            created.add(new);copied+=1
    # Rebase catalog entries, project metadata and reference paths to the copied
    # files. Portable archives retain their embedded originals unchanged.
    for path in created:
        if path.suffix.lower() not in {'.json','.ltxd'}:continue
        try:
            raw=path.read_text(encoding='utf-8');value=json.loads(raw)
        except (UnicodeError,ValueError):continue
        updated=rebase_paths(value,mappings)
        if updated!=value:_atomic_write(path,(json.dumps(updated,ensure_ascii=False,indent=2)+'\n').encode())
    return copied


def migrate_configuration(progress=None):
    mappings,marker=migration_locations()
    if marker.is_file():return False
    copied=copy_legacy_data(mappings,progress)
    migrate_settings(mappings)
    _atomic_write(marker,(json.dumps({'version':1,'copied_files':copied},indent=2)+'\n').encode())
    return True
