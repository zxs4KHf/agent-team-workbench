"""Project-store transactions; no production data migration at import time."""
import functools
import os
import re
from bus_locks import file_mutex


def directory(projects, slug):
    if not isinstance(slug, str) or not re.fullmatch(r'[A-Za-z0-9一-鿿][A-Za-z0-9一-鿿_-]{0,127}', slug):
        raise ValueError('Invalid project identifier')
    base = os.path.normcase(os.path.realpath(projects))
    full = os.path.realpath(os.path.join(projects, slug))
    if os.path.commonpath([base, os.path.normcase(full)]) != base:
        raise ValueError('Project path is outside the project store')
    return full


def mutex(projects, slug):
    path = directory(projects, slug)
    if not os.path.isfile(os.path.join(path, 'project.json')):
        raise ValueError('Project does not exist: ' + slug)
    return file_mutex(os.path.join(path, '.project.mutex'))


def serialized(projects_getter):
    """Keep the complete read/modify/write under one reentrant project lock."""
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            first = args[0] if args else kwargs.get(fn.__code__.co_varnames[0])
            slug = first.get('slug') if isinstance(first, dict) else first
            with mutex(projects_getter(), slug):
                return fn(*args, **kwargs)
        return wrapped
    return decorate
