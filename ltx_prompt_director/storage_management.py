"""Platform cache housekeeping and portable ZIP backups of the complete setup."""
from __future__ import annotations
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import tempfile
import uuid
import zipfile
from .config_migration import rebase_paths,rebase_setting,_atomic_write


def setup_roots():
    from PySide6.QtCore import QStandardPaths
    return {'data':Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)),
            'configuration':Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation))}


def cache_roots():
    from PySide6.QtCore import QStandardPaths
    from .media import APP_CACHE
    values=[APP_CACHE,Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))]
    return list(dict.fromkeys(values))


def disk_usage(roots):
    size=count=0
    for root in roots:
        if not root.is_dir():continue
        for path in root.rglob('*'):
            if path.is_symlink():continue
            try:
                if path.is_file():size+=path.stat().st_size;count+=1
            except OSError:pass
    available=shutil.disk_usage(next((root for root in roots if root.exists()),Path.home())).free
    return {'bytes':size,'files':count,'free':available}


def clear_cache(roots,protected=()):
    protected={Path(path).absolute() for path in protected if path}
    freed=0
    for root in roots:
        if not root.is_dir():continue
        for path in root.rglob('*'):
            if path.is_symlink() or not path.is_file() or path.absolute() in protected:continue
            try:freed+=path.stat().st_size;path.unlink()
            except FileNotFoundError:pass
    return freed


def settings_snapshot(settings):
    return {key:settings.value(key) for key in settings.allKeys()}


def _pending(root):return Path(root)/'.pending-setup-import.zip'


def export_setup(output,roots,values):
    from PySide6.QtCore import QSettings
    unique={};seen=set()
    for key,root in roots.items():
        if root.absolute() not in seen:unique[key]=root;seen.add(root.absolute())
    roots=unique
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_name('.'+output.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with tempfile.TemporaryDirectory() as directory:
            preferences=Path(directory)/'settings.ini'
            settings=QSettings(str(preferences),QSettings.Format.IniFormat)
            for key,value in values.items():settings.setValue(key,value)
            settings.sync()
            if settings.status()!=QSettings.Status.NoError:raise OSError('Could not export application preferences.')
            if not preferences.exists():preferences.write_text('[General]\n')
            manifest={'app':'prompt-director','format':1,'roots':{key:str(path) for key,path in roots.items()}}
            with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as archive:
                archive.writestr('setup.json',json.dumps(manifest,indent=2))
                archive.write(preferences,'settings.ini')
                for key,root in roots.items():
                    if not root.exists():continue
                    for path in sorted(root.rglob('*')):
                        if path.is_symlink() or not path.is_file() or path in (output,temporary):continue
                        relative=path.relative_to(root)
                        if any(part.startswith(('.pending-','.reset-backup-','.setup-rollback-')) for part in relative.parts):continue
                        archive.write(path,str(PurePosixPath(key,*relative.parts)))
        os.replace(temporary,output)
    finally:temporary.unlink(missing_ok=True)
    return str(output)


def validate_setup(path):
    try:return _validate_setup(path)
    except (zipfile.BadZipFile,KeyError,UnicodeError) as error:
        raise ValueError('Choose an intact complete Prompt Director setup ZIP.') from error


def _validate_setup(path):
    with zipfile.ZipFile(path) as archive:
        manifest=json.loads(archive.read('setup.json'))
        if not isinstance(manifest,dict) or manifest.get('app')!='prompt-director' or manifest.get('format')!=1:
            raise ValueError('Choose a complete Prompt Director setup ZIP.')
        roots=manifest.get('roots')
        if not isinstance(roots,dict) or 'data' not in roots or any(key not in ('data','configuration') or not isinstance(value,str) for key,value in roots.items()):
            raise ValueError('The backup is missing its configuration folder locations.')
        if 'settings.ini' not in archive.namelist():raise ValueError('The backup is missing settings.ini.')
        seen=set()
        for member in archive.infolist():
            if member.filename in seen:raise ValueError('The backup contains duplicate file paths.')
            seen.add(member.filename)
            relative=PurePosixPath(member.filename)
            if relative.is_absolute() or '..' in relative.parts or '\\' in member.filename or any(':' in part or part.startswith(('.pending-','.setup-rollback-','.reset-backup-')) for part in relative.parts):
                raise ValueError('The backup contains an invalid file path.')
            if member.filename not in ('setup.json','settings.ini') and (not relative.parts or relative.parts[0] not in ('configuration','data')):
                raise ValueError('The backup contains an unsupported file path.')
            if member.filename not in ('setup.json','settings.ini') and len(relative.parts)<2 and not member.is_dir():
                raise ValueError('The backup contains an invalid file path.')
            if (member.external_attr>>16)&0o170000==0o120000:raise ValueError('The backup contains a symbolic link.')
        corrupt=archive.testzip()
        if corrupt:raise ValueError(f'The backup contains a damaged file: {corrupt}')
        return manifest


def stage_import(path,data_root):
    validate_setup(path)
    target=_pending(data_root);target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
    try:shutil.copyfile(path,temporary);os.replace(temporary,target)
    finally:temporary.unlink(missing_ok=True)
    (Path(data_root)/'.pending-settings-reset.json').unlink(missing_ok=True)
    return str(target)


def stage_reset(data_root):
    root=Path(data_root);root.mkdir(parents=True,exist_ok=True)
    _atomic_write(root/'.pending-settings-reset.json',b'{"reset":true}\n')
    _pending(root).unlink(missing_ok=True)


def apply_pending_setup(roots=None,settings=None):
    """Restore on startup, before active widgets or project jobs can write files."""
    from PySide6.QtCore import QSettings
    roots=roots or setup_roots();settings=settings or QSettings()
    data=roots['data'];pending=_pending(data);reset=data/'.pending-settings-reset.json'
    if reset.is_file():
        workspaces=data/'workspaces'
        backup=None;values=settings_snapshot(settings)
        if workspaces.exists():
            backup=data/('.reset-backup-'+uuid.uuid4().hex);backup.mkdir()
            workspaces.rename(backup/'workspaces')
        try:
            settings.clear();settings.sync()
            if settings.status()!=QSettings.Status.NoError:raise OSError('Could not reset preferences.')
        except Exception:
            if backup:(backup/'workspaces').rename(workspaces);backup.rmdir()
            for key,value in values.items():settings.setValue(key,value)
            settings.sync();raise
        reset.unlink();return 'reset'
    if not pending.is_file():return None
    manifest=validate_setup(pending)
    mappings=[(Path(old),roots[key]) for key,old in manifest.get('roots',{}).items() if key in roots]
    rollback=data/('.setup-rollback-'+uuid.uuid4().hex);rollback.mkdir(parents=True)
    changed=[];values=settings_snapshot(settings)
    try:
        with tempfile.TemporaryDirectory() as temporary,zipfile.ZipFile(pending) as archive:
            temporary=Path(temporary)
            ini=temporary/'settings.ini';ini.write_bytes(archive.read('settings.ini'))
            restored=QSettings(str(ini),QSettings.Format.IniFormat)
            for member in archive.infolist():
                relative=PurePosixPath(member.filename)
                if member.is_dir() or relative.parts[0] not in roots:continue
                destination=roots[relative.parts[0]].joinpath(*relative.parts[1:])
                if not destination.resolve().is_relative_to(roots[relative.parts[0]].resolve()):raise ValueError('The restore path leaves the configuration folder.')
                destination.parent.mkdir(parents=True,exist_ok=True)
                old=rollback/relative
                if destination.exists():old.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(destination,old)
                changed.append((destination,old if old.exists() else None))
                # Stream large projects/media into an atomic replacement.
                replacement=destination.with_name('.'+destination.name+'.'+uuid.uuid4().hex+'.tmp')
                try:
                    with archive.open(member) as source,replacement.open('wb') as target:shutil.copyfileobj(source,target,1024*1024)
                    if destination.suffix.lower() in ('.json','.ltxd'):
                        try:
                            parsed=json.loads(replacement.read_text('utf-8'))
                            replacement.write_text(json.dumps(rebase_paths(parsed,mappings),ensure_ascii=False,indent=2)+'\n')
                        except (ValueError,UnicodeError):pass
                    os.replace(replacement,destination)
                finally:replacement.unlink(missing_ok=True)
            settings.clear()
            for key in restored.allKeys():settings.setValue(key,rebase_setting(restored.value(key),mappings))
            settings.sync()
            if settings.status()!=QSettings.Status.NoError:raise OSError('Could not restore preferences.')
        pending.unlink();shutil.rmtree(rollback);return 'import'
    except Exception:
        for destination,old in reversed(changed):
            if old is None:destination.unlink(missing_ok=True)
            else:os.replace(old,destination)
        settings.clear()
        for key,value in values.items():settings.setValue(key,value)
        settings.sync()
        shutil.rmtree(rollback,ignore_errors=True)
        raise
