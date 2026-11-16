"""Request-local project titles carried outside prompt operation return values."""
from contextvars import ContextVar
from dataclasses import dataclass
import copy
import json

ACTIVE_NAME=ContextVar('project_naming',default=None)


@dataclass
class NamedResult:
    value: object
    naming: dict


def schema_with_name(schema):
    result=copy.deepcopy(schema)
    if ACTIVE_NAME.get() is not None:
        result['properties']['projectName']={'anyOf':[{'type':'string'},{'type':'null'}],
            'description':'Short descriptive project title, 2 to 8 words; null if no meaningful title can be inferred.'}
        result['required']=[*result.get('required',[]),'projectName']
    return result


def extract(raw):
    naming=ACTIVE_NAME.get()
    if naming is None:return raw
    value=json.loads(raw)
    title=value.pop('projectName',None)
    if isinstance(title,str) and title.strip():naming['title']=' '.join(title.split()).strip('"\'')[:80]
    return json.dumps(value,ensure_ascii=False)
