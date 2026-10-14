"""Preview/apply an initial local repository binding. No service restart or dispatch."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bus_repo_binding as binding


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projects-dir', required=True, help='Explicit instance projects directory')
    parser.add_argument('--project', required=True)
    parser.add_argument('--repo', required=True, help='Existing absolute Git working-tree root')
    parser.add_argument('--apply', action='store_true', help='Apply a previously reviewed preview')
    parser.add_argument('--expected', default='', help='Exact plan_id from the preview')
    parser.add_argument('--confirm-project', default='')
    parser.add_argument('--actor', default='')
    parser.add_argument('--reason', default='')
    args = parser.parse_args(argv)
    try:
        if args.apply:
            result = binding.apply(args.projects_dir, args.project, args.repo, expected=args.expected,
                                   confirm_project=args.confirm_project, actor=args.actor, reason=args.reason)
        else:
            if any((args.expected, args.confirm_project, args.actor, args.reason)):
                raise ValueError('Apply options require --apply; no binding was changed')
            result = binding.preview(args.projects_dir, args.project, args.repo)
    except (ValueError, OSError, TimeoutError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else 'Local state unavailable; no success confirmed'
        print(json.dumps({'ok': False, 'error': message, 'type': type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
