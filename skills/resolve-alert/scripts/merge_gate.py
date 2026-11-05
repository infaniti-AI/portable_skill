#!/usr/bin/env python3
"""Read-only eligibility check. A passing result never merges or grants approval."""
import argparse
import fnmatch
import json
from pathlib import Path
import subprocess


def matches(path, pattern):
    return fnmatch.fnmatchcase(path, pattern) or (pattern.startswith('**/') and fnmatch.fnmatchcase(path, pattern[3:]))


def evaluate(project, repository, repo, base, evidence):
    reasons = []
    response = project['incident_response']
    policy = response['merge_policy']
    if repository not in response['repositories']:
        reasons.append('Unknown repository')
    if not policy.get('auto_merge_enabled', False):
        reasons.append('Automatic merge is not enabled for this project')
    if not response.get('dev', {}).get('ready', False):
        reasons.append('Dev deployment is not configured')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
    sha = git('rev-parse', 'HEAD')
    base_sha = git('rev-parse', '--verify', '--end-of-options', base+'^{commit}')
    if evidence.get('base_sha') != base_sha:
        reasons.append('Base branch changed or verified base SHA is missing')
    if git('status', '--porcelain'):
        reasons.append('Checkout has uncommitted changes')
    paths = git('diff', '--name-only', '--diff-filter=ACDMRTUXB', base_sha+'...HEAD').splitlines()
    if not paths:
        reasons.append('No committed changes')
    if len(paths) > policy.get('max_changed_files', 0):
        reasons.append('Too many changed files')
    forbidden = policy.get('approval_required_paths', []) + [
        '.github/**', '**/migrations/**', '**/auth/**', '**/rls*', '**/billing*',
        '**/encryption*', '**/deletion*', '**/webhook*', '**/subscription*',
        '**/terraform/**', 'terraform/**', '**/*lock*', 'requirements*', '**/package.json', 'package.json',
    ]
    allowed = policy.get('allowed_paths', [])
    for path in paths:
        if any(matches(path, pattern) for pattern in forbidden):
            reasons.append('Approval-required path: '+path)
        if not any(matches(path, pattern) for pattern in allowed):
            reasons.append('Path is not allowlisted: '+path)
    changed_lines = 0
    for line in git('diff', '--numstat', base_sha+'...HEAD').splitlines():
        added, removed, _ = line.split('\t', 2)
        if '-' in (added, removed):
            reasons.append('Binary changes require approval')
        else:
            changed_lines += int(added)+int(removed)
    if changed_lines > policy.get('max_changed_lines', 0):
        reasons.append('Change exceeds line limit')
    ci, dev = evidence.get('ci', {}), evidence.get('dev', {})
    if evidence.get('sha') != sha or not ci.get('passed') or not ci.get('required_checks_passed') or not ci.get('source_url'):
        reasons.append('Required CI evidence for HEAD is missing or failed')
    if dev.get('sha') != sha or not dev.get('passed') or dev.get('environment') not in {'dev', 'development', 'staging', 'preview'} or not dev.get('source_url'):
        reasons.append('Verified non-production deployment evidence for HEAD is missing or failed')
    return {'eligible': not reasons, 'sha': sha, 'reasons': reasons, 'production_release_requires_approval': policy.get('production_release_requires_approval', True)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['project', 'repository', 'repo', 'base', 'evidence']:
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    try:
        result = evaluate(json.loads(Path(args.project).read_text()), args.repository, args.repo, args.base, json.loads(Path(args.evidence).read_text()))
    except (KeyError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'eligible': False, 'reasons': ['Gate could not verify inputs: '+type(exc).__name__]}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result['eligible'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
