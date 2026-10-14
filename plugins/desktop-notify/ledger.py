"""Crash-conscious local notification ledger (not an exactly-once transport)."""
import copy
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid


class LedgerError(RuntimeError):
    pass


class LockedError(LedgerError):
    pass


class CapacityError(LedgerError):
    pass


class ConflictError(LedgerError):
    pass


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def cursor_key(value):
    if value == '':
        return ('', 0)
    try:
        day, offset = value.split(':', 1)
        datetime.date.fromisoformat(day)
        if int(offset) < 0:
            raise ValueError()
        return day, int(offset)
    except (AttributeError, ValueError):
        raise LedgerError('Invalid event cursor') from None


class Ledger:
    def __init__(self, state_dir, initialize=None, max_pending=1000, max_history=2000):
        self.root = Path(state_dir)
        self.path = self.root/'ledger.json'
        self.marker = self.root/'initialized'
        self._lock = None
        self._state = None
        self.max_pending = max(1, min(int(max_pending), 10000))
        self.max_history = max(1, min(int(max_history), 10000))
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._lock = (self.root/'ledger.lock').open('a+b')
            self._lock.seek(0, 2)
            if self._lock.tell() == 0:
                self._lock.write(b'0'); self._lock.flush()
            self._lock.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self._lock.close(); self._lock = None
                raise LockedError('Notification ledger lock is held; stop the plugin before local inspection') from None
            if self.path.exists():
                with self.path.open('rb') as stream:
                    raw = stream.read(128 * 1024 * 1024 + 1)
                if len(raw) > 128 * 1024 * 1024:
                    raise LedgerError('Ledger size limit exceeded; manual inspection required')
                wrapper = json.loads(raw.decode('utf-8'))
                payload = wrapper['payload']
                if wrapper.get('sha256') != hashlib.sha256(encoded(payload)).hexdigest():
                    raise LedgerError('Ledger checksum mismatch; never reset automatically')
                self._validate(payload)
                self._state = payload
                restored = copy.deepcopy(payload)
                changed = False
                for row in restored['records'].values():
                    if row['status'] == 'inflight':
                        row.update(status='unknown', revision=row['revision'] + 1, recovered_at=time.time())
                        changed = True
                if changed:
                    self._save(restored)
            else:
                if self.marker.exists() or initialize is None:
                    raise LedgerError('Initialized ledger missing; manual recovery required')
                cursor = initialize()
                cursor_key(cursor)
                self._save({'version': 1, 'cursor': cursor, 'records': {}, 'attempts': [], 'audit': []})
            self.marker.touch(exist_ok=True)
        except Exception as exc:
            self.close()
            if isinstance(exc, LedgerError):
                raise
            raise LedgerError('Cannot open notification ledger; do not reset or advance cursor') from None

    def _validate(self, state):
        if not isinstance(state, dict) or state.get('version') != 1:
            raise LedgerError('Unsupported ledger schema')
        cursor_key(state.get('cursor'))
        if not isinstance(state.get('records'), dict) or not isinstance(state.get('audit'), list) or not isinstance(state.get('attempts'), list):
            raise LedgerError('Invalid ledger collections')
        for key, row in state['records'].items():
            if not isinstance(key, str) or not isinstance(row, dict) or row.get('id') != key:
                raise LedgerError('Invalid notification record identity')
            if row.get('status') not in ('pending', 'inflight', 'unknown', 'handed_off', 'resolved'):
                raise LedgerError('Invalid notification state')
            if type(row.get('revision')) is not int or row['revision'] < 0 or not isinstance(row.get('event'), dict):
                raise LedgerError('Invalid notification revision/event')
            if row['event'].get('id') != key or not isinstance(row.get('queued_at'), (int, float)) or not math.isfinite(row['queued_at']):
                raise LedgerError('Invalid notification event/time')
        for attempt in state['attempts']:
            if not isinstance(attempt, dict) or not isinstance(attempt.get('at'), (int, float)) or not math.isfinite(attempt['at']):
                raise LedgerError('Invalid rate-limit history')

    def _save(self, state):
        self._validate(state)
        data = encoded({'payload': state, 'sha256': hashlib.sha256(encoded(state)).hexdigest()})
        tmp = self.root/('ledger-' + uuid.uuid4().hex + '.tmp')
        try:
            with tmp.open('xb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(tmp, self.path)
        except OSError:
            raise LedgerError('Ledger persistence failed; no further side effect is permitted') from None
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        self._state = state

    def close(self):
        if self._lock is not None:
            try:
                self._lock.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self._lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self._lock.close(); self._lock = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def inspect(self):
        if self._lock is None:
            raise LockedError('Ledger is closed; acquire the lock before reading or modifying')
        return copy.deepcopy(self._state)

    def pending(self):
        return [copy.deepcopy(row) for row in self._state['records'].values() if row['status'] == 'pending']

    def remaining(self):
        return max(0, self.max_pending - sum(row['status'] in ('pending', 'inflight', 'unknown') for row in self._state['records'].values()))

    def ingest(self, cursor, events, now):
        if cursor_key(cursor) < cursor_key(self._state['cursor']):
            raise LedgerError('Event cursor moved backwards; manual reconciliation required')
        if not isinstance(events, list):
            raise LedgerError('Invalid event batch')
        state = self.inspect()
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get('id'), str) or not event['id'] or len(event['id']) > 200:
                raise LedgerError('Event must have a stable bounded identifier')
            if len(encoded(event)) > 32000:
                raise LedgerError('Event exceeds bounded notification payload')
            key = event['id']
            if key not in state['records']:
                state['records'][key] = dict(id=key, event=copy.deepcopy(event), status='pending', revision=0, queued_at=now)
        if sum(row['status'] in ('pending', 'inflight', 'unknown') for row in state['records'].values()) > self.max_pending:
            raise CapacityError('Pending capacity reached; cursor was not advanced')
        terminal = sorted((row for row in state['records'].values() if row['status'] in ('handed_off', 'resolved')), key=lambda row: row.get('finished_at', 0))
        for row in terminal[:-self.max_history]:
            del state['records'][row['id']]
        state['cursor'] = cursor
        self._save(state)

    def begin(self, ids, now, per_hour):
        state = self.inspect()
        state['attempts'] = [a for a in state['attempts'] if now - a['at'] < 3600]
        if len(state['attempts']) >= max(0, int(per_hour)):
            return None
        if not ids or len(ids) != len(set(ids)):
            raise ConflictError('A nonempty unique pending batch is required')
        attempt = uuid.uuid4().hex
        for key in ids:
            row = state['records'].get(key)
            if not row or row['status'] != 'pending':
                raise ConflictError('Notification is no longer pending')
            row.update(status='inflight', revision=row['revision'] + 1, attempt=attempt)
        state['attempts'].append({'id': attempt, 'at': now})
        self._save(state)
        return attempt

    def finish(self, attempt, handed_off, now):
        state = self.inspect()
        rows = [row for row in state['records'].values() if row.get('attempt') == attempt and row['status'] == 'inflight']
        if not rows:
            raise ConflictError('No matching in-flight notification attempt')
        for row in rows:
            row.update(status='handed_off' if handed_off is True else 'unknown', revision=row['revision'] + 1, finished_at=now)
        self._save(state)

    def decide(self, key, revision, action, actor, reason, now):
        state = self.inspect()
        row = state['records'].get(key)
        if not row or row['status'] != 'unknown' or type(revision) is not int or row['revision'] != revision:
            raise ConflictError('Notification revision/state changed; inspect before deciding')
        if action not in ('retry', 'resolve') or not isinstance(actor, str) or not actor.strip() or not isinstance(reason, str) or not reason.strip():
            raise LedgerError('An explicit action, actor and reason are required')
        if len(actor) > 120 or len(reason) > 1000:
            raise LedgerError('Operator receipt exceeds length limit')
        if len(state['audit']) >= 10000:
            raise CapacityError('Operator audit requires explicit archival; no receipt was discarded')
        row.update(status='pending' if action == 'retry' else 'resolved', revision=row['revision'] + 1, finished_at=now)
        if action == 'retry':
            row['queued_at'] = now
        state['audit'].append(dict(id=key, expected_revision=revision, action=action, actor=actor, reason=reason, at=now))
        self._save(state)
