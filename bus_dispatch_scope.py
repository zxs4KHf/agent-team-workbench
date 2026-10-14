"""Project identity and build repository checks before dispatch side effects."""
import os
from pathlib import Path


def canonical(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def resolve(mode, project, parent_task, repo_dir, infer):
    import bus_projects as P
    project = "" if project is None else project
    repo_dir = "" if repo_dir is None else repo_dir
    if not isinstance(project, str) or not isinstance(repo_dir, str):
        raise ValueError('project and repo_dir must be text')
    slug = project.strip()
    source = 'explicit' if slug else ''
    parent = P.project_for_task(parent_task) if parent_task else ''
    if slug and parent and slug != parent:
        raise ValueError('Explicit project conflicts with parent task; use a deliberate cross-project handoff')
    if not slug and parent:
        slug, source = parent, 'parent-task'
    if mode == 'build':
        if not slug:
            raise ValueError('Build requires explicit project or a project-bound parent task; no workspace was created')
        record = P.load_project(slug)  # validates slug and does not create unknown projects
        registered = record.get('repo_dir')
        if not isinstance(registered, str) or not registered.strip() or not os.path.isabs(registered):
            raise ValueError('Build project needs an explicit absolute repo_dir binding in project settings')
        expected = canonical(registered)
        actual = canonical(repo_dir) if repo_dir else expected
        if actual != expected:
            raise ValueError('Build repo_dir does not match the project repository binding')
        root = Path(expected)
        if not root.is_dir() or not (root/'.git').exists():
            raise ValueError('Build repository must already exist as a Git worktree; dispatch does not initialize repositories')
        return slug, expected, source
    if slug:
        P.project_dir(slug)  # validate name only; legacy non-build labels need not be registered
    else:
        slug = str(infer() or '').strip()
        source = 'inferred-read-context' if slug else ''
    return slug, repo_dir, source
