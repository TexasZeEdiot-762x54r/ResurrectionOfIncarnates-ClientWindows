"""Keep Vs. Bot network waits in HTTP workers, outside native debugger hooks."""
from collections import OrderedDict
from copy import deepcopy
import json
import threading


def failed():
    return 200, {}, {'result': 'NG', 'data': {}}


def successful(reply):
    return (reply[0] == 200 and isinstance(reply[2], dict)
            and reply[2].get('result') == 'OK')


class Job:
    def __init__(self, data):
        self.data = deepcopy(data)
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.staged = False
        self.receipt = None


class Dispatcher:
    def __init__(self, authenticate, wait_seconds=15):
        self.authenticate = authenticate
        self.wait_seconds = wait_seconds
        self.lock = threading.Lock()
        self.jobs = OrderedDict()

    def respond(self, method, path, headers, body, dispatch):
        def normal():
            return dispatch(method, path, headers, body)
        if method != 'POST' or not path.endswith('/Dragon/game'):
            return normal()
        try:
            from profile_store import _strict_object
            request = json.loads(body, object_pairs_hook=_strict_object)
        except (ValueError, UnicodeError):
            return normal()  # Preserve the native replay parser.
        if not isinstance(request, dict):
            return normal()
        name, data = request.get('call'), request.get('data')
        if name not in ('nativeLPDeferred', 'nativeLP', 'endBotBattle') or not isinstance(data, dict):
            return normal()

        def call(payload):
            return dispatch(method, path, headers,
                            json.dumps({'call': 'nativeLP', 'data': payload}).encode())

        if name == 'nativeLPDeferred':
            try:
                self.authenticate(data)
                from roi_native_lp import hex_id
                launch = hex_id(data.get('launch'))
                battle = data.get('battle_id')
                if (len(body) > 16384 or data.get('action') != 'stage_bot'
                        or type(battle) is not str or not battle.isascii()
                        or not battle.isdigit() or not 0 < int(battle) < 2**63):
                    return failed()
            except (OSError, ValueError, PermissionError):
                return failed()
            key = (launch, battle)
            with self.lock:
                job = self.jobs.get(key)
                if job is not None:
                    if job.data != data:
                        return failed()
                else:
                    # Never evict a job whose final result has not been cached.
                    for old in list(self.jobs):
                        if len(self.jobs) < 8:
                            break
                        if self.jobs[old].receipt is not None:
                            del self.jobs[old]
                    if len(self.jobs) >= 8 or any(k[1] == battle for k in self.jobs):
                        return failed()
                    job = Job(data)
                    self.jobs[key] = job

                    def stage():
                        try:
                            opening = {k: data[k] for k in ('sv_token', 'launch')}
                            opening['action'] = 'open'
                            job.staged = successful(call(opening)) and successful(call(job.data))
                        except Exception:
                            job.staged = False
                        finally:
                            job.ready.set()

                    threading.Thread(target=stage, daemon=True, name='roi-reward-stage').start()
            return 200, {}, {'result': 'OK', 'data': {'enabled': True, 'queued': True}}

        if name == 'nativeLP' and data.get('action') == 'receipt':
            try:
                self.authenticate(data)
                key = (data.get('launch'), data.get('battle_id'))
                if (set(data) != {'sv_token','launch','action','battle_id','mode'}
                        or not all(type(v) is str for v in key)):
                    return failed()
            except (OSError, ValueError, PermissionError):
                return failed()
            with self.lock:
                job = self.jobs.get(key)
                cached = deepcopy(job.receipt) if job is not None else None
            if job is not None and data.get('mode') == 'bot':
                # No slow fallback inside the HUD hook. A successful endBotBattle
                # reply is released only after its committed receipt is cached.
                return cached if cached is not None else failed()
            return normal()

        if name != 'endBotBattle':
            return normal()
        battle = data.get('sv_battleId')
        if type(battle) not in (str, int):
            return normal()
        with self.lock:
            candidates = [job for key, job in self.jobs.items() if key[1] == str(battle)]
        if not candidates:
            return normal()  # Older clients retain their existing behavior.
        job = candidates[0]
        if not job.ready.wait(self.wait_seconds) or not job.staged:
            return failed()  # Never settle without the matching staged evidence.
        with job.lock:
            reply = normal()  # Existing authority validates reports and retries.
            if successful(reply):
                payload = {k: job.data[k] for k in ('sv_token', 'launch', 'battle_id')}
                payload.update(action='receipt', mode='bot')
                receipt = call(payload)
                if not successful(receipt):
                    return failed()  # Durable award remains available for retry.
                with self.lock:
                    job.receipt = deepcopy(receipt)
            return reply


def _authenticate(data):
    from roi_native_lp import authenticate
    authenticate(data)


DISPATCHER = Dispatcher(_authenticate)
