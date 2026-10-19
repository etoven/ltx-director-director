"""One stdout channel shared by the worker and setup helpers before log redirection."""
import json
import sys

_OUTPUT = sys.stdout


def emit(kind, **values):
    _OUTPUT.write('DIRECTOR_EVENT ' + json.dumps({'kind': kind, **values}, ensure_ascii=False) + '\n')
    _OUTPUT.flush()


def progress(message, percent=None, eta=None):
    emit('progress', message=message, percent=percent, eta_seconds=eta)
