import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('merge_gate', Path(__file__).resolve().parents[1]/'skills/resolve-alert/scripts/merge_gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class MergeGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')
        (self.repo/'app.py').write_text('x = None\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'base')
        self.base = self.git('rev-parse', 'HEAD')
        (self.repo/'app.py').write_text('x = 1\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'fix')
        self.sha = self.git('rev-parse', 'HEAD')
        self.project = {'incident_response': {
            'repositories': {'app': {}}, 'dev': {'ready': True},
            'merge_policy': {'auto_merge_enabled': True, 'allowed_paths': ['app.py'], 'max_changed_files': 5, 'max_changed_lines': 20},
        }}
        self.evidence = {'sha': self.sha, 'base_sha': self.base, 'ci': {'passed': True, 'required_checks_passed': True, 'source_url': 'https://ci.example/run/1'},
                         'dev': {'sha': self.sha, 'passed': True, 'environment': 'dev', 'source_url': 'https://ci.example/dev/1'}}

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True).strip()

    def evaluate(self):
        return gate.evaluate(self.project, 'app', self.repo, self.base, self.evidence)

    def test_verified_allowlisted_fix_is_eligible(self):
        self.assertTrue(self.evaluate()['eligible'])

    def test_opt_out_or_stale_dev_evidence_blocks_merge(self):
        self.project['incident_response']['merge_policy']['auto_merge_enabled'] = False
        self.assertFalse(self.evaluate()['eligible'])
        self.project['incident_response']['merge_policy']['auto_merge_enabled'] = True
        self.evidence['dev']['sha'] = self.base
        self.assertFalse(self.evaluate()['eligible'])

    def test_infra_changes_are_blocked_even_when_allowlisted(self):
        (self.repo/'terraform').mkdir()
        (self.repo/'terraform/main.tf').write_text('# infrastructure\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'infra')
        self.project['incident_response']['merge_policy']['allowed_paths'] = ['*']
        self.sha = self.git('rev-parse', 'HEAD')
        self.evidence['sha'] = self.evidence['dev']['sha'] = self.sha
        result = self.evaluate()
        self.assertFalse(result['eligible'])
        self.assertTrue(any('Approval-required' in reason for reason in result['reasons']))

    def test_uncommitted_changes_block_merge(self):
        (self.repo/'app.py').write_text('x = 2\n')
        self.assertFalse(self.evaluate()['eligible'])


if __name__ == '__main__':
    unittest.main()
