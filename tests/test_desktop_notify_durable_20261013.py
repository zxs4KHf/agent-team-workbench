"""F05 regression contract. Only temporary state and injected transports; no HTTP/toast."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[1] / 'plugins' / 'desktop-notify'
sys.path.insert(0, str(PLUGIN))
spec = importlib.util.spec_from_file_location('durable_notify_main', PLUGIN / 'main.py')
N = importlib.util.module_from_spec(spec)
spec.loader.exec_module(N)


def event(key, kind='decision.merged'):
    return {'id': key, 'type': kind, 'data': {'title': key, 'project': 'fixture'}}


class DurableNotification(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # A tripwire, including tests which exercise main().
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(N, 'toast', side_effect=AssertionError('real toast forbidden')).start()
        mock.patch.object(N, 'get', side_effect=AssertionError('real HTTP forbidden')).start()

    def ledger(self, **kwargs):
        obj = N.Ledger(self.tmp.name, initialize=lambda: '2026-10-13:100', **kwargs)
        self.addCleanup(obj.close)
        return obj

    def ingest(self, ledger, ids=('a',), cursor='2026-10-13:200'):
        ledger.ingest(cursor, [event(key) for key in ids], now=1000)

    def test_restart_resumes_cursor_and_pending_without_initializing_again(self):
        ledger = self.ledger()
        self.ingest(ledger)
        ledger.close()
        with N.Ledger(self.tmp.name, initialize=lambda: self.fail('reinitialized')) as reopened:
            self.assertEqual(reopened.inspect()['cursor'], '2026-10-13:200')
            self.assertEqual([r['id'] for r in reopened.pending()], ['a'])

    def test_cursor_and_pending_commit_failure_is_atomic(self):
        ledger = self.ledger()
        with mock.patch('os.replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(N.LedgerError):
                self.ingest(ledger)
        ledger.close()
        with self.ledger() as reopened:
            self.assertEqual(reopened.inspect()['cursor'], '2026-10-13:100')
            self.assertEqual(reopened.pending(), [])

    def test_duplicate_ids_before_and_after_handoff_are_deduplicated(self):
        ledger = self.ledger()
        self.ingest(ledger, ('a', 'a'))
        self.assertEqual(len(ledger.pending()), 1)
        attempt = ledger.begin(['a'], now=1001, per_hour=12)
        ledger.finish(attempt, handed_off=True, now=1002)
        self.ingest(ledger, ('a',))
        self.assertEqual(ledger.pending(), [])
        self.assertEqual(ledger.inspect()['records']['a']['status'], 'handed_off')

    def test_quiet_hours_defer_across_restart(self):
        ledger = self.ledger()
        self.ingest(ledger)
        sent = []
        worker = N.Worker(ledger, {'batch_sec': 0, 'quiet_hours': '23-8'}, transport=lambda *msg: sent.append(msg) or True)
        self.assertEqual(worker.deliver(now=1100, hour=23), 'quiet')
        self.assertEqual(sent, [])
        ledger.close()
        reopened = self.ledger()
        worker = N.Worker(reopened, {'batch_sec': 0, 'quiet_hours': '23-8'}, transport=lambda *msg: sent.append(msg) or True)
        self.assertEqual(worker.deliver(now=1200, hour=8), 'handed_off')
        self.assertEqual(len(sent), 1)

    def test_rate_limit_is_persistent_and_defers(self):
        ledger = self.ledger()
        self.ingest(ledger)
        worker = N.Worker(ledger, {'batch_sec': 0, 'max_per_hour': 1}, transport=lambda *msg: True)
        worker.deliver(now=1001, hour=12)
        self.ingest(ledger, ('b',))
        self.assertEqual(worker.deliver(now=1002, hour=12), 'rate_limited')
        ledger.close()
        reopened = self.ledger()
        worker = N.Worker(reopened, {'batch_sec': 0, 'max_per_hour': 1}, transport=lambda *msg: True)
        self.assertEqual(worker.deliver(now=1003, hour=12), 'rate_limited')
        self.assertEqual([r['id'] for r in reopened.pending()], ['b'])
        self.assertEqual(worker.deliver(now=4601, hour=12), 'handed_off')

    def test_transport_observes_persisted_inflight_before_side_effect(self):
        ledger = self.ledger()
        self.ingest(ledger)
        def fake_transport(*msg):
            # The on-disk protocol is intentional here: verify the crash boundary.
            saved = json.loads((Path(self.tmp.name) / 'ledger.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['payload']['records']['a']['status'], 'inflight')
            return True
        worker = N.Worker(ledger, {'batch_sec': 0}, transport=fake_transport)
        self.assertEqual(worker.deliver(now=1001, hour=12), 'handed_off')
        row = ledger.inspect()['records']['a']
        self.assertEqual(row['status'], 'handed_off')
        self.assertNotIn('seen', row)

    def test_uncertain_exception_timeout_and_false_never_automatically_replay(self):
        for result in (RuntimeError('uncertain'), subprocess.TimeoutExpired('fake', 20), False, None):
            with self.subTest(result=repr(result)), tempfile.TemporaryDirectory() as state:
                with N.Ledger(state, initialize=lambda: '2026-10-13:100') as ledger:
                    self.ingest(ledger)
                    def fake_transport(*msg):
                        if isinstance(result, Exception):
                            raise result
                        return result
                    worker = N.Worker(ledger, {'batch_sec': 0}, transport=fake_transport)
                    self.assertEqual(worker.deliver(now=1001, hour=12), 'unknown')
                with N.Ledger(state, initialize=lambda: self.fail('reinitialized')) as ledger:
                    worker = N.Worker(ledger, {'batch_sec': 0}, transport=lambda *msg: self.fail('replayed'))
                    self.assertEqual(worker.deliver(now=5000, hour=12), 'idle')
                    self.assertEqual(ledger.inspect()['records']['a']['status'], 'unknown')

    def test_crash_before_transport_and_after_success_before_ack_become_unknown(self):
        for after_transport in (False, True):
            with self.subTest(after_transport=after_transport), tempfile.TemporaryDirectory() as state:
                with N.Ledger(state, initialize=lambda: '2026-10-13:100') as ledger:
                    self.ingest(ledger)
                    if after_transport:
                        worker = N.Worker(ledger, {'batch_sec': 0}, transport=lambda *msg: True)
                        real_replace = os.replace
                        calls = []
                        def replace(src, dst):
                            calls.append(dst)
                            if len(calls) == 2:
                                raise OSError('ack failed after side effect')
                            return real_replace(src, dst)
                        with mock.patch('os.replace', side_effect=replace), self.assertRaises(N.LedgerError):
                            worker.deliver(now=1001, hour=12)
                    else:
                        ledger.begin(['a'], now=1001, per_hour=12)
                with N.Ledger(state, initialize=lambda: self.fail('reset')) as reopened:
                    self.assertEqual(reopened.inspect()['records']['a']['status'], 'unknown')
                    self.assertEqual(reopened.pending(), [])

    def test_failed_inflight_save_does_not_invoke_transport(self):
        ledger = self.ledger()
        self.ingest(ledger)
        worker = N.Worker(ledger, {'batch_sec': 0}, transport=lambda *msg: self.fail('transport called'))
        with mock.patch('os.replace', side_effect=OSError('full')), self.assertRaises(N.LedgerError):
            worker.deliver(now=1001, hour=12)

    def test_corrupt_or_missing_initialized_ledger_fails_closed(self):
        ledger = self.ledger()
        ledger.close()
        path = Path(self.tmp.name) / 'ledger.json'
        for contents in ('{broken', '{}', ''):
            path.write_text(contents, encoding='utf-8')
            with self.assertRaises(N.LedgerError):
                self.ledger()
            self.assertEqual(path.read_text(encoding='utf-8'), contents)
        path.unlink()
        with self.assertRaises(N.LedgerError):
            self.ledger()

    def test_unreadable_ledger_does_not_initialize(self):
        ledger = self.ledger()
        ledger.close()
        with mock.patch('pathlib.Path.open', side_effect=PermissionError('denied')):
            with self.assertRaises(N.LedgerError):
                N.Ledger(self.tmp.name, initialize=lambda: self.fail('reset'))

    def test_capacity_refuses_entire_batch_without_advancing_cursor(self):
        ledger = self.ledger(max_pending=2)
        self.ingest(ledger, ('a',))
        with self.assertRaises(N.CapacityError):
            self.ingest(ledger, ('b', 'c'), cursor='2026-10-13:400')
        self.assertEqual(ledger.inspect()['cursor'], '2026-10-13:200')
        self.assertEqual([r['id'] for r in ledger.pending()], ['a'])
        self.ingest(ledger, ('b',), cursor='2026-10-13:300')
        worker = N.Worker(ledger, {'batch_sec': 0}, transport=lambda *msg: True)
        self.assertEqual(worker.poll('http://fixture', fetch=lambda url: self.fail('read past capacity'), now=1001), 'backpressure')
        worker.deliver(now=1002, hour=12)
        self.ingest(ledger, ('c',), cursor='2026-10-13:400')
        self.assertEqual([r['id'] for r in ledger.pending()], ['c'])

    def test_two_instances_and_cli_share_exclusive_lock(self):
        ledger = self.ledger()
        with self.assertRaises(N.LockedError):
            self.ledger()
        result = subprocess.run([sys.executable, '-B', str(PLUGIN / 'main.py'), 'inspect', '--state-dir', self.tmp.name], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('lock', result.stderr.lower())
        ledger.close()
        with self.ledger() as reopened:
            self.assertEqual(reopened.inspect()['cursor'], '2026-10-13:100')

    def test_explicit_retry_and_resolve_require_cas_and_audit(self):
        ledger = self.ledger()
        self.ingest(ledger)
        attempt = ledger.begin(['a'], now=1001, per_hour=12)
        ledger.finish(attempt, handed_off=False, now=1002)
        rev = ledger.inspect()['records']['a']['revision']
        ledger.decide('a', rev, 'retry', actor='fixture-operator', reason='accept duplicate risk', now=1003)
        with self.assertRaises(N.ConflictError):
            ledger.decide('a', rev, 'retry', actor='fixture-operator', reason='stale click', now=1004)
        attempt = ledger.begin(['a'], now=1005, per_hour=12)
        ledger.finish(attempt, handed_off=False, now=1006)
        with self.assertRaises(N.ConflictError):
            ledger.decide('a', rev, 'retry', actor='fixture-operator', reason='old request after new attempt', now=1007)
        rev = ledger.inspect()['records']['a']['revision']
        ledger.decide('a', rev, 'resolve', actor='fixture-operator', reason='no further notification wanted', now=1008)
        view = ledger.inspect()
        self.assertEqual(view['records']['a']['status'], 'resolved')
        self.assertEqual([a['action'] for a in view['audit']], ['retry', 'resolve'])
        self.assertEqual(view['audit'][0]['actor'], 'fixture-operator')
        self.assertEqual(view['audit'][0]['expected_revision'], 2)
        self.assertEqual(ledger.pending(), [])
        ledger.close()
        with self.ledger() as reopened:
            self.assertEqual(len(reopened.inspect()['audit']), 2)

    def test_local_cli_inspects_and_retries_without_network_or_transport(self):
        ledger = self.ledger()
        self.ingest(ledger)
        attempt = ledger.begin(['a'], now=1001, per_hour=12)
        ledger.finish(attempt, handed_off=False, now=1002)
        ledger.close()
        args = ['--state-dir', self.tmp.name]
        with mock.patch('sys.stdout'):
            self.assertEqual(N.main(['inspect', *args]), 0)
            self.assertEqual(N.main(['retry', 'a', *args, '--expected-revision', '2', '--actor', 'tester', '--reason', 'accept duplicate risk']), 0)
        with self.ledger() as reopened:
            self.assertEqual([r['id'] for r in reopened.pending()], ['a'])

    def test_empty_bootstrap_cursor_does_not_repeatedly_start_at_now(self):
        with N.Ledger(self.tmp.name, initialize=lambda: '') as ledger:
            self.assertEqual(ledger.inspect()['cursor'], '')
        with N.Ledger(self.tmp.name, initialize=lambda: self.fail('reset')) as ledger:
            seen = []
            worker = N.Worker(ledger, {}, transport=lambda *msg: self.fail('unexpected send'))
            worker.poll('http://fixture', fetch=lambda url: seen.append(url) or {'cursor': '2026-10-13:200', 'events': [event('a')]}, now=1000)
            self.assertNotIn('cursor=now', seen[0])
            self.assertEqual([r['id'] for r in ledger.pending()], ['a'])

    def test_poll_preserves_filter_and_already_accepted_types(self):
        ledger = self.ledger()
        worker = N.Worker(ledger, {'types': ['decision.merged'], 'batch_sec': 0}, transport=lambda *msg: True)
        worker.poll('http://fixture', fetch=lambda url: {'cursor': '2026-10-13:200', 'events': [event('a'), event('b', 'intake.added'), event('c', 'decision.dispatched')]}, now=1000)
        self.assertEqual([r['id'] for r in ledger.pending()], ['a'])
        worker = N.Worker(ledger, {'types': ['intake.added'], 'batch_sec': 0}, transport=lambda *msg: True)
        self.assertEqual(worker.deliver(now=1001, hour=12), 'handed_off')

    def test_invalid_event_id_keeps_cursor(self):
        ledger = self.ledger()
        with self.assertRaises(N.LedgerError):
            ledger.ingest('2026-10-13:200', [{'type': 'decision.merged'}], now=1000)
        self.assertEqual(ledger.inspect()['cursor'], '2026-10-13:100')


if __name__ == '__main__':
    unittest.main(verbosity=2)
