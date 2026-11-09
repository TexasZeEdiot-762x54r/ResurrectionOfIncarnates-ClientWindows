"""Short-lived SteamUser017 ticket session, isolated in the registration helper."""
import ctypes as c
import hashlib
import os
from pathlib import Path
import time

from steam_dir import _vcall

LIBRARY_SHA256 = 'b81e6554c17c3f68e0285b0d6ec5f43aa26e8f7195f5f881806539277a91d853'


class Identity:
    def __init__(self, dll):
        self.dll = Path(dll)
        self.api = None
        self.handles = []

    def __enter__(self):
        if c.sizeof(c.c_void_p) != 8 or hashlib.sha256(self.dll.read_bytes()).hexdigest() != LIBRARY_SHA256:
            raise ValueError('Unsupported Steam library for automatic registration')
        os.environ['SteamAppId'] = os.environ['SteamGameId'] = '258160'
        self.api = c.CDLL(str(self.dll))
        self.api.SteamAPI_Init.restype = c.c_bool
        self.api.SteamUser.restype = c.c_void_p
        self.api.SteamAPI_RunCallbacks.restype = None
        self.api.SteamAPI_Shutdown.restype = None
        self.api.SteamAPI_RegisterCallback.argtypes = [c.c_void_p, c.c_int]
        self.api.SteamAPI_RegisterCallback.restype = None
        self.api.SteamAPI_UnregisterCallback.argtypes = [c.c_void_p]
        self.api.SteamAPI_UnregisterCallback.restype = None
        previous = os.getcwd()
        try:
            os.chdir(self.dll.parent)
            ready = self.api.SteamAPI_Init()
        finally:
            os.chdir(previous)
        if not ready:
            self.api = None
            raise ValueError('Steam must be running and signed in')
        try:
            self.user = self.api.SteamUser()
            if not self.user or not _vcall(self.user, 1, c.c_bool):
                raise ValueError('Steam must be online')
            uid = c.c_uint64()
            _vcall(self.user, 2, c.c_void_p, (c.c_void_p, c.cast(c.byref(uid), c.c_void_p)))
            if uid.value >> 56 != 1:
                raise ValueError('Unexpected Steam account type')
            self.uid = str(uid.value)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def ticket(self):
        class Callback(c.Structure):
            _fields_ = [('vtable', c.POINTER(c.c_void_p)), ('flags', c.c_uint8), ('callback', c.c_int)]
        seen = {}
        def receive(this, pointer):
            values = c.cast(pointer, c.POINTER(c.c_uint32))
            seen[int(values[0])] = int(values[1])
        # Both overloaded Run entries handle the same event under the MSVC ABI.
        run = c.CFUNCTYPE(None, c.c_void_p, c.c_void_p)(receive)
        call = c.CFUNCTYPE(None, c.c_void_p, c.c_void_p, c.c_bool, c.c_uint64)(
            lambda this, pointer, failed, handle: receive(this, pointer))
        size = c.CFUNCTYPE(c.c_int, c.c_void_p)(lambda this: 8)
        table = (c.c_void_p * 3)(*(c.cast(fn, c.c_void_p).value for fn in (run, call, size)))
        callback = Callback(table, 0, 163)
        self.api.SteamAPI_RegisterCallback(c.byref(callback), 163)
        try:
            data = c.create_string_buffer(4096)
            length = c.c_uint32()
            handle = _vcall(self.user, 13, c.c_uint32, (c.c_void_p, c.cast(data, c.c_void_p)),
                            (c.c_int, len(data)), (c.POINTER(c.c_uint32), c.byref(length)))
            if not handle or not 0 < length.value <= len(data):
                raise ValueError('Steam ticket creation failed')
            self.handles.append(handle)
            deadline = time.monotonic() + 15
            while handle not in seen and time.monotonic() < deadline:
                self.api.SteamAPI_RunCallbacks()
                time.sleep(.05)
            if seen.get(handle) != 1:
                raise ValueError('Steam did not confirm the login ticket')
            return data.raw[:length.value].hex()
        finally:
            self.api.SteamAPI_UnregisterCallback(c.byref(callback))

    def __exit__(self, *args):
        if self.api is not None:
            for handle in self.handles:
                _vcall(self.user, 16, None, (c.c_uint32, handle))
            self.api.SteamAPI_Shutdown()
            self.api = None
