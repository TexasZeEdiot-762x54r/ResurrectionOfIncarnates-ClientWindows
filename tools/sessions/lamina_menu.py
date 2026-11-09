"""Experimental, event-only Lamina menu. No executable or archive file writes.

Profile keeps Character Select and adds a separate native Lamina Select button.
Customize character selection is unchanged. This does not enable Lamina combat,
the unfinished Customize tab, XP policy, or color editing. Ability requests
are committed by this profile owner at menu/loadout events.
The debugger owns lamina_profile.json; other processes may read, not mutate it.
Call setup(d) from the experimental play session, before process creation.
"""
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[2]
MANAGER = 0x141B87458
ACTIVE = 0x3CD18
PROFILE_BUTTON = 0x1403C97A9
LABEL_CALL = 0x1403C97DB
OPEN = 0x14037D950
CONFIRM = 0x14037E310
SELECTOR_VTABLE = 0x1418BEBF0
LABEL = b'Lamina Select\0'
APPEND, CREATE_LAMINA = 0x1403C97FF, 0x1403C624B
POSITION, SET_TEXT, FINISH = 0x1403C630E, 0x1403C6326, 0x1403C634B
REFRESH, CLOSE_END, RELEASE = 0x1403CA0A2, 0x1403CA170, 0x1403CA160
LEFT_EDGES = (0x1407D4AD0, 0x1407D4AD5)
MOUSE_POSITION, WINDOW_MANAGER = 0x1407D4B47, 0x141B874A8
PANEL_FRAME, ROW_TEXT, ACTIVE_TICK = 0x14037CC58, 0x14037CCA6, 0x14037E010
CLOSING, CLEANUP = 0x14037DDC0, 0x14037DD80
BLOCK_HASHES = {
    0x1407D48C0: (734, '184da8701c127c89f2c7d896d936762bd2c7c426515e4463897a05c2aed247d3'),
    0x14037CC20: (172, '78e03830f82ab6470445308d9c1e5bc5ff7bb542f7866c881ce0e3dd0acf2860'),
    ACTIVE_TICK: (22, '77aba1a052b42560af109e0952b76fd8ec06b792f68faaae82644a8aa93c265b'),
    CREATE_LAMINA: (256, '7ae64faa6d6df182c3bb00853bf70fecdb5e5f1e3b308b53b03ebd7ac744cc89'),
    0x1403C9738: (206, '28346ee32abea11ffd1247ceb90ba48c2d3d2e24b832dfb5faa75d20d955f859'),
    0x1403CA154: (39, '25b4235c6e44f5f8e1b7b4f2af1297195f42713c10ac0c6d70affc6bc736822c'),
}
GUARDS = {
    LEFT_EDGES[0]: bytes.fromhex('418930'), LEFT_EDGES[1]: bytes.fromhex('41c7000f000000'),
    MOUSE_POSITION: bytes.fromhex('8b437c'), ACTIVE_TICK: bytes.fromhex('488b8b98030000'),
    PANEL_FRAME: bytes.fromhex('e873065a00'), ROW_TEXT: bytes.fromhex('ff9018010000'),
    CLOSING: bytes.fromhex('48895c2418564883'), CLEANUP: bytes.fromhex('40534883ec20488b'),
    APPEND: bytes.fromhex('4c8b1d52dc7b01'),
    POSITION: bytes.fromhex('488d542420'),
    SET_TEXT: bytes.fromhex('e885fb0d00'),
    FINISH: bytes.fromhex('488b7c2440'),
    REFRESH: bytes.fromhex('c6835903000001488b5c24304883c4205fc3'),
    CLOSE_END: bytes.fromhex('488b5c2430'),
    PROFILE_BUTTON: bytes.fromhex('488d05e0020000'),
    LABEL_CALL: bytes.fromhex('e800c60d00'),
    OPEN: bytes.fromhex('48895c2410574881ec70'),
    CONFIRM: bytes.fromhex('40534883ec204863c2488b1538918001'),
    0x1403C6470: bytes.fromhex('40534883ec20ba02000000488bd9488b0d83107c01448d42'),
    0x140120470: bytes.fromhex('48895c24084889742410574883ec20'),
    0x1401205C3: bytes.fromhex('e818430d00'),
    0x1401E8A80: bytes.fromhex('40555357488bec4883ec404863416c48'),
    0x1401E8DBC: bytes.fromhex('498b4500ff90f0000000'),
    0x140791120: bytes.fromhex('48895c240848897424105557415441554156'),
    0x140791330: bytes.fromhex('33ff8bdf448be7ff153b3c7900'),
}
BUILDERS = (0x140120470, 0x1401E8A80, 0x140791120)
PAYLOADS = (0x1401205C3, 0x1401E8DBC, 0x140791330)


def _pointer(value):
    return type(value) is int and 0x10000 <= value < 0x7FFFFFF00000


def _read(d, address, size):
    if not _pointer(address):
        raise ValueError('Invalid native address')
    value = d.read(address, size)
    if len(value) != size:
        raise OSError('Native memory unavailable')
    return value


def _q(d, address):
    return struct.unpack('<Q', _read(d, address, 8))[0]


def _u32(d, address):
    return struct.unpack('<I', _read(d, address, 4))[0]


def _writes(d, changes):
    previous = [(a, _read(d, a, len(value))) for a, value in changes]
    try:
        for address, value in changes:
            if not d.write(address, value) or _read(d, address, len(value)) != value:
                raise OSError('Native write failed')
    except Exception:
        for address, value in reversed(previous):
            d.write(address, value)
        raise


def refresh_thunk(slot):
    # This point is inside the native refresh frame, with shadow space reserved.
    # Mirror the Character button's visibility only for our registered sibling.
    code = bytearray(b'\x49\xbb' + struct.pack('<Q', slot))
    code.extend(bytes.fromhex('49 39 1b 75 00'))
    owner_jump = len(code) - 1
    code.extend(bytes.fromhex('49 8b 4b 08 48 85 c9 74 00'))
    button_jump = len(code) - 1
    code.extend(bytes.fromhex('48 8b 83 50 03 00 00 8a 80 28 02 00 00 '
                              '88 81 28 02 00 00 48 8b 01 ff 90 d0 00 00 00'))
    for pos in (owner_jump, button_jump):
        code[pos] = len(code) - pos - 1
    return bytes(code) + GUARDS[REFRESH]


def action_row(x, y, width, height):
    """Mouse client pixels to the panel's 1920x1080 authored HUD coordinates."""
    if not (0 < width <= 32768 and 0 < height <= 32768 and 0 <= x < width and 0 <= y < height):
        return None
    scale = min(width / 1920, height / 1080)
    u = (x - (width - 1920 * scale) / 2) / scale
    v = (y - (height - 1080 * scale) / 2) / scale
    if 1310 <= u < 1748 and 348 <= v < 570:
        return int((v - 348) // 74)
    return None


def game_client_point(hwnd, pid, x, y, user=None):
    if user is None:
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetForegroundWindow.restype = wt.HWND
        user.GetAncestor.argtypes = [wt.HWND, wt.UINT]
        user.GetAncestor.restype = wt.HWND
        user.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        user.IsWindowVisible.argtypes = [wt.HWND]
        user.ClientToScreen.argtypes = user.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
        user.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]

    def owned(window):
        owner = wt.DWORD()
        return window and user.GetWindowThreadProcessId(window, ctypes.byref(owner)) and owner.value == pid

    foreground = user.GetForegroundWindow()
    if not owned(hwnd) or not owned(foreground):
        return None
    # In fullscreen the mouse device calls ScreenToClient on a hidden child.
    # Its visible parent owns focus and renders the selector.
    root = user.GetAncestor(hwnd, 2)  # GA_ROOT
    if not owned(root) or user.GetAncestor(foreground, 2) != root:
        return None
    render = hwnd if user.IsWindowVisible(hwnd) else root
    pt, rect = wt.POINT(x, y), wt.RECT()
    if (not user.IsWindowVisible(render) or not user.ClientToScreen(hwnd, ctypes.byref(pt))
            or not user.ScreenToClient(render, ctypes.byref(pt))
            or not user.GetClientRect(render, ctypes.byref(rect))):
        return None
    return pt.x, pt.y, rect.right - rect.left, rect.bottom - rect.top


def _isolation(d):
    root = ROOT.resolve()
    tools = root / 'tools'
    if root != ROOT or Path(__file__).resolve() != tools / 'sessions/lamina_menu.py':
        raise RuntimeError('Lamina session requires its independent experimental installation')
    if Path(d.exe).resolve() != root / 'game/exe/roi.exe':
        raise RuntimeError('Refusing non-experimental executable')
    config = json.loads((tools / 'config.json').read_text(encoding='utf-8-sig'))
    required = {'lobby_mode': 'server', 'lobby_transport': 'tls-public',
                'lobby_server': '127.0.0.1:52711', 'lobby_listen': '127.0.0.1'}
    if any(config.get(k) != v for k, v in required.items()):
        raise RuntimeError('Lamina requires experimental loopback settings')
    from paths import GAME
    if GAME.resolve() != root / 'game':
        raise RuntimeError('Experimental game path mismatch')
    if config.get('release_mode') not in ('vpn','internet','lan') or not config.get('public_lobby_host'):
        raise RuntimeError('Active public credentials/configuration in experimental copy')
    import roi_lamina
    import profile_store
    for module in (roi_lamina, profile_store):
        if Path(module.__file__).resolve().parent != tools:
            raise RuntimeError('State module loaded outside experimental copy')
    store = tools / 'lamina_profile.json'
    if store.resolve().parent != tools:
        raise RuntimeError('Experimental state path escapes its tools directory')
    return store, roi_lamina, profile_store


def _process_path(d):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    query = kernel.QueryFullProcessImageNameW
    query.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    query.restype = wt.BOOL
    buffer = ctypes.create_unicode_buffer(32768)
    size = wt.DWORD(len(buffer))
    if not query(d.hproc, 0, buffer, ctypes.byref(size)):
        raise OSError('Cannot verify experimental process image')
    if Path(buffer.value).resolve() != ROOT / 'game/exe/roi.exe':
        raise RuntimeError('Actual process is not the experimental executable')


class Session:
    def __init__(self, d, store, state_module, profile_store):
        self.d = d
        self.store = store
        self.state_module = state_module
        self.profile_store = profile_store
        self.label_address = None
        self.slot = None
        self.pending = None
        self.last_selection = None
        self.selector = self.view = self.mouse = self.choice = self.committing = None
        self.temporary = {}
        self.click_notes = 0

    def snapshot(self):
        import lamina_ability
        result = lamina_ability.consume(self.store, self.state_module, self.profile_store)
        if result:
            self.d.log('LAMINA_ABILITY', json.dumps(result, sort_keys=True))
        value = self.state_module.state(self.profile_store.read(self.store))
        selected = value['selected_id']
        variant = value['laminas'][str(selected)]['variant']
        if type(selected) is not int or not 1 <= selected <= 15:
            raise ValueError('Invalid selected Lamina')
        if type(variant) is not int or not 0 <= variant <= 2:
            raise ValueError('Invalid Lamina combat variant')
        return value, selected, variant

    def manager(self):
        manager = _q(self.d, MANAGER)
        if not _pointer(manager) or manager % 8:
            raise ValueError('Lamina manager not initialized')
        _read(self.d, manager + ACTIVE, 4)
        return manager

    def inject(self, hit=None):
        import release_lamina
        if not release_lamina.allowed():
            return
        _, selected, _ = self.snapshot()
        manager = self.manager()
        _writes(self.d, [(manager + ACTIVE, struct.pack('<I', selected))])

    def hook_once_or_scoped(self, addr, fn):
        if addr in self.temporary:
            return
        if addr in self.d.bps or self.d.read(addr, len(GUARDS[addr])) != GUARDS[addr]:
            raise ValueError('Action Data hook conflict')
        handler = self.guarded(fn)
        self.d.bp(addr, handler)
        self.temporary[addr] = handler

    def unhook(self, addr):
        handler = self.temporary.pop(addr, None)
        if handler is not None:
            original, active = self.d.bps[addr]
            if active is not handler or not self.d.write(addr, original):
                raise ValueError('Action Data hook ownership changed')
            del self.d.bps[addr]

    def clear_rows(self):
        for addr in tuple(self.temporary):
            self.unhook(addr)
        self.selector = self.view = self.mouse = self.choice = self.committing = None

    def open_rows(self, hit):
        self.clear_rows()
        self.click_notes = 0
        value, selected, _ = self.snapshot()
        _writes(self.d, [(self.manager() + ACTIVE, struct.pack('<I', selected))])
        if (_q(self.d, hit.rcx) == SELECTOR_VTABLE
                and _read(self.d, hit.rcx + 0x3A4, 1) == b'\0'):
            self.selector, self.view = hit.rcx, value
            for addr in LEFT_EDGES:
                self.hook_once_or_scoped(addr, self.press)

    def close_rows(self, hit):
        if hit.rcx == self.selector:
            self.clear_rows()

    def preview(self):
        sel = self.selector
        if not sel or _q(self.d, sel) != SELECTOR_VTABLE or _u32(self.d, sel + 0x3A0) != 1:
            return None
        item = _u32(self.d, sel + 0x340)
        if 1 <= item <= 15:
            return item

    def press(self, hit):
        if (hit.rdx & 0xffffffff) == 1 and self.preview() and not self.choice:
            self.mouse = (hit.tid, hit.rbx)
            self.hook_once_or_scoped(MOUSE_POSITION, self.position_ready)

    def position_ready(self, hit):
        if self.mouse != (hit.tid, hit.rbx):
            return
        self.mouse = None
        self.unhook(MOUSE_POSITION)
        # Resume the displaced load without single-stepping a removed breakpoint.
        x, y = struct.unpack('<ii', _read(self.d, hit.rbx + 0x7C, 8))
        hit.setreg('rax', x & 0xffffffff)
        hit.setreg('rip', MOUSE_POSITION + 3)
        item = self.preview()
        if not item:
            return
        window = _q(self.d, _q(self.d, WINDOW_MANAGER) + 0x118)
        point = game_client_point(window, self.d.pid, x, y)
        row = action_row(*point) if point else None
        if self.click_notes < 8:
            self.click_notes += 1
            self.d.log(f'LAMINA_MENU row press id={item} client_point={point} row={row}')
        if row is None or not self.state_module._unlocked(self.view, item, row):
            return
        self.choice = (self.selector, item, row)
        self.hook_once_or_scoped(ACTIVE_TICK, self.commit_tick)

    def commit_tick(self, hit):
        if hit.rbx != self.selector:
            return
        self.unhook(ACTIVE_TICK)
        sel, item, row = self.choice
        self.choice = None
        if self.preview() != item:
            hit.setreg('rcx', _q(self.d, sel + 0x398))
            hit.setreg('rip', ACTIVE_TICK + 7)
            return
        self.committing = (sel, item, row, hit.tid)
        hit.setreg('rcx', sel)
        hit.setreg('rdx', item)
        hit.setreg('rip', self.row_thunk)

    def panel_matches(self, panel, item):
        return (self.view and 1 <= item <= 15 and self.selector
                and _q(self.d, self.selector + 0x358) == panel
                and _u32(self.d, self.selector + 0x340) == item)

    def panel_frame(self, hit):
        item = hit.rbp
        if self.panel_matches(hit.rcx, item):
            hit.setreg('rdx', max(i for i in range(3) if self.state_module._unlocked(self.view, item, i)))

    def row_text(self, hit):
        item, row = hit.rbp, hit.rdi
        if not 0 <= row < 3 or not self.panel_matches(hit.rbx - 0x290 - row * 8, item):
            return
        if self.view['laminas'][str(item)]['variant'] != row:
            return
        # Keep the game's localized description, prefixing only the saved row.
        raw, terminator, _ = self.d.read(hit.rdx, 512).partition(b'\0')
        if raw and terminator:
            addr = self.row_buffers + row * 520
            _writes(self.d, [(addr, b'Active: ' + raw + b'\0')])
            hit.setreg('rdx', addr)

    def confirm(self, hit):
        selected = hit.rdx & 0xFFFFFFFF
        if not 1 <= selected <= 15:
            raise ValueError('Invalid selector row')
        if _q(self.d, hit.rcx) != SELECTOR_VTABLE:
            raise ValueError('Unexpected Lamina selector instance')
        if _u32(self.d, hit.rcx + 0x3A0) != 1:
            return  # Ignore a repeated click after the close transition began.
        manager = self.manager()
        before = _read(self.d, manager + ACTIVE, 4)
        with self.profile_store.LOCK:
            value, _, _ = self.snapshot()
            variant = value['laminas'][str(selected)]['variant']
            request = self.committing
            if (request and request[:2] == (hit.rcx, selected)
                    and request[3] == hit.tid and _q(self.d, hit.rsp) == self.row_thunk + 12):
                variant = request[2]
                if not self.state_module._unlocked(value, selected, variant):
                    return
                self.d.log(f'LAMINA_MENU action row id={selected} ability={variant + 1}')
            _writes(self.d, [(manager + ACTIVE, struct.pack('<I', selected))])
            try:
                self.state_module.select(self.store, selected, variant)
            except Exception:
                _writes(self.d, [(manager + ACTIVE, before)])
                raise
        import match_lamina
        match_lamina.submit(selected,variant)
        self.last_selection = selected
        self.d.log(f'LAMINA_MENU selected id={selected} variant={variant}')

    def append(self, hit):
        import release_lamina
        if not release_lamina.allowed():
            return
        if self.pending or _q(self.d, self.slot) or _u32(self.d, hit.rbx + 0x388):
            return
        original = _q(self.d, hit.rbx + 0x350)
        if not _pointer(original):
            raise ValueError('Missing original Character Select button')
        self.pending = (hit.tid, hit.rbx, hit.rsp, original)
        hit.setreg('rip', CREATE_LAMINA)

    def building(self, hit):
        return self.pending and self.pending[:3] == (hit.tid, hit.rbx, hit.rsp)

    def position(self, hit):
        if self.building(hit):
            # Recovered button center is (50,71); leave 12 units below its
            # 142-unit height. Moving the native control also moves its hitbox.
            _writes(self.d, [(hit.rsp + 0x20, struct.pack('<fff', 30, 318, 0))])

    def label(self, hit):
        if self.building(hit):
            hit.setreg('rdx', self.label_address)

    def finish_button(self, hit):
        if not self.building(hit):
            return
        original = self.pending[3]
        button = _q(self.d, hit.rbx + 0x350)
        if (not _pointer(button) or button == original or
                _q(self.d, button + 0x270) != 0x1403C6470 or
                _q(self.d, button + 0x268) != hit.rbx):
            raise ValueError('Unexpected Lamina button or callback')
        _writes(self.d, [(hit.rbx + 0x350, struct.pack('<Q', original)),
                         (self.slot, struct.pack('<QQ', hit.rbx, button))])
        self.pending = None
        hit.setreg('r11', _q(self.d, MANAGER))
        hit.setreg('rip', APPEND + len(GUARDS[APPEND]))
        self.d.log('LAMINA_MENU separate Profile buttons created')

    def release_button(self, hit):
        if _q(self.d, self.slot) != hit.rbx:
            return
        button = _q(self.d, self.slot + 8)
        if not _pointer(button) or _q(self.d, hit.rbx + 0x350):
            raise ValueError('Unexpected Profile cleanup order')
        _writes(self.d, [(self.slot, bytes(16))])
        hit.setreg('rcx', button)
        hit.setreg('rip', RELEASE)

    def payload(self, hit, address):
        import release_lamina
        if not release_lamina.allowed():
            return
        self.inject()
        _, selected, variant = self.snapshot()
        if address == 0x1401205C3:
            base = hit.rbx
            _read(self.d, base, 0x13)
        elif address == 0x1401E8DBC:
            base = hit.rbp - 0x20
            if not _pointer(hit.rsp) or not hit.rsp <= base < hit.rsp + 0x1000:
                raise ValueError('Unexpected initial-scene stack layout')
            _read(self.d, base, 0x13)
        elif address == 0x140791330:
            base = hit.rsp + 0x40
            _read(self.d, base, 0x13)
        else:
            raise ValueError('Unknown loadout event')
        # Own-profile builders only; do not patch the shared combat getters.
        _writes(self.d, [(base + 0x10, bytes((variant, selected)))])
        self.d.log(f'LAMINA_MENU own-loadout id={selected} variant={variant} builder={address:#x}')

    def guarded(self, fn):
        def handler(hit):
            try:
                return fn(hit)
            except Exception as exc:
                self.d.log('LAMINA_MENU stopped:', type(exc).__name__)
                self.d.stop = True
                raise
        return handler

    def arm(self):
        addresses = (APPEND, POSITION, SET_TEXT, FINISH, CLOSE_END, OPEN, CONFIRM,
                     PANEL_FRAME, ROW_TEXT, CLOSING, CLEANUP) + BUILDERS + PAYLOADS
        if any(a in self.d.bps for a in (*addresses, REFRESH)):
            raise RuntimeError('Lamina breakpoint conflicts with another session')
        if any(self.d.read(a, len(raw)) != raw for a, raw in GUARDS.items()):
            raise RuntimeError('Lamina native code signature mismatch')
        if any(hashlib.sha256(self.d.read(a, size)).hexdigest() != expected
               for a, (size, expected) in BLOCK_HASHES.items()):
            raise RuntimeError('Profile builder or destructor signature mismatch')
        self.snapshot()  # Validate state before changing any code.
        self.slot = self.d.alloc(2048)
        if not _pointer(self.slot):
            raise RuntimeError('Could not allocate Profile button state')
        self.label_address = self.slot + 16
        entry = self.slot + 48
        self.row_thunk, self.row_buffers = self.slot + 160, self.slot + 256
        # ACTIVE_TICK is on the selector's own thread with call shadow space.
        confirm_jump = (b'\x48\xb8' + struct.pack('<Q', CONFIRM) + b'\xff\xd0\x48\xb8'
                        + struct.pack('<Q', ACTIVE_TICK) + b'\xff\xe0')
        _writes(self.d, [(self.slot, bytes(16)), (self.label_address, LABEL),
                         (self.row_thunk, confirm_jump),
                         (entry, refresh_thunk(self.slot))])
        changed = []
        armed = []
        try:
            replacement = bytes.fromhex('ff2500000000') + struct.pack('<Q', entry)
            _writes(self.d, [(REFRESH, replacement + b'\xcc' * (len(GUARDS[REFRESH]) - len(replacement)))])
            changed.append(REFRESH)
            handlers = {APPEND: self.append, POSITION: self.position, SET_TEXT: self.label,
                        FINISH: self.finish_button, CLOSE_END: self.release_button,
                        OPEN: self.open_rows, CONFIRM: self.confirm, CLOSING: self.close_rows,
                        CLEANUP: self.close_rows, PANEL_FRAME: self.panel_frame, ROW_TEXT: self.row_text}
            handlers.update({a: self.inject for a in BUILDERS})
            handlers.update({a: (lambda h, at=a: self.payload(h, at)) for a in PAYLOADS})
            for address, fn in handlers.items():
                armed.append(address)
                self.d.bp(address, self.guarded(fn))
        except Exception:
            for address in reversed(armed):
                entry = self.d.bps.pop(address, None)
                if entry and entry[0] is not None:
                    self.d.write(address, entry[0])
            for address in reversed(changed):
                self.d.write(address, GUARDS[address])
            self.d.stop = True
            raise
        self.d.log('LAMINA_MENU runtime Profile selector armed; event hooks only; local sidecar owner')


def setup(d):
    store, module, profile_store = _isolation(d)
    session = Session(d, store, module, profile_store)

    def start(debugger):
        _isolation(debugger)
        _process_path(debugger)
        session.arm()

    d.on_start(start)
    return session
