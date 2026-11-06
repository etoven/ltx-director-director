"""One stdout channel shared by the worker and setup helpers before log redirection."""
import contextlib
import json
import sys

_OUTPUT = sys.stdout
_TERMINAL = None
_JOB_ID = None


def set_job_id(value):
    global _JOB_ID
    _JOB_ID=value


@contextlib.contextmanager
def capture_terminal():
    global _TERMINAL
    previous=_TERMINAL;events=[];_TERMINAL=events
    try:yield events
    finally:_TERMINAL=previous


def emit(kind, **values):
    if _TERMINAL is not None and kind in ('result','error'):
        _TERMINAL.append(dict(kind=kind,**values));return
    if _JOB_ID is not None:values['job_id']=_JOB_ID
    _OUTPUT.write('DIRECTOR_EVENT ' + json.dumps({'kind': kind, **values}, ensure_ascii=False) + '\n')
    _OUTPUT.flush()


def progress(message, percent=None, eta=None):
    emit('progress', message=message, percent=percent, eta_seconds=eta)
