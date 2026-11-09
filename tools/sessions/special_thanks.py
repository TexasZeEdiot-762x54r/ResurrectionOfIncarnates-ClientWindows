"""Display the first painted frame of the static Records credits page.

The shared widget skips its first four observed paint notifications. A fast
static page can finish before that warm-up ends and leave the GPU texture black.
Prime only this initialized, exact-URL panel; other web widgets remain native.
"""
import struct

INITIALIZED=0x1403EB103
EXPECTED=bytes.fromhex('488bc34883c4205bc3')
WINDOW_VTABLE=0x1418CDC20
WEB_VTABLE=0x1418EB0D0
URL=b'https://riseofincarnates.artificerreviver.com/thanks'
EXTERNAL_HANDLERS={
    0x1403EB300:bytes.fromhex('48895c240848896c24104889742418574883ec20'),
    0x1403EB260:bytes.fromhex('40534881ec40020000'),
}


def disable_external_open(debugger):
    # These functions belong only to the old Twitch panel: its consent/click
    # callback and ShellExecute helper. Never patch the shared browser engine.
    previous={at:debugger.read(at,len(code)) for at,code in EXTERNAL_HANDLERS.items()}
    for at,code in EXTERNAL_HANDLERS.items():
        if previous[at] not in (code,b'\xc3'+code[1:]):
            raise ValueError('Unknown Records external-open handler')
    changed=[]
    try:
        for at,code in EXTERNAL_HANDLERS.items():
            if previous[at][0]==0xc3:continue
            changed.append(at)
            if not debugger.write(at,b'\xc3') or debugger.read(at,1)!=b'\xc3':
                raise OSError('External-open suppression failed')
    except BaseException:
        for at in changed:debugger.write(at,previous[at][:1])
        raise


def panel_browser(debugger,window):
    def pointer(at):
        value=debugger.read(at,8)
        return struct.unpack('<Q',value)[0] if len(value)==8 else 0
    if pointer(window)!=WINDOW_VTABLE:return 0
    controls=pointer(window+0x50)
    browser=pointer(controls+0x18) if controls else 0
    if not browser or pointer(browser)!=WEB_VTABLE:return 0
    string=pointer(browser+0x140) if browser else 0
    return browser if string and debugger.read(string,len(URL)+1)==URL+b'\0' else 0


def prime_first_frame(debugger,window):
    browser=panel_browser(debugger,window)
    if not browser:return False
    at=browser+0x1cc
    previous=debugger.read(at,4)
    if len(previous)!=4:return False
    seen=struct.unpack('<I',previous)[0]
    if seen>=4:return False
    primed=struct.pack('<I',4)
    if not debugger.write(at,primed) or debugger.read(at,4)!=primed:
        debugger.write(at,previous)
        raise OSError('First-paint counter verification failed')
    return True


def setup(d):
    notices=0
    def initialized(hit):
        nonlocal notices
        if notices>=8:return
        notices+=1
        try:
            primed=prime_first_frame(d,hit.rbx)
            d.log('SPECIAL_THANKS exact Records credits panel first-paint warm-up bypass='+str(primed))
        except (OSError,ValueError,RuntimeError,struct.error):
            d.log('SPECIAL_THANKS first-paint compatibility unavailable; other browser panels unchanged')
    def arm(debugger):
        try:
            disable_external_open(debugger)
            debugger.log('SPECIAL_THANKS panel-wide external browser launch disabled')
        except (OSError,ValueError,RuntimeError):
            debugger.log('SPECIAL_THANKS external-open suppression unavailable: unknown native code')
        if debugger.read(INITIALIZED,len(EXPECTED))!=EXPECTED:
            debugger.log('SPECIAL_THANKS first-paint fix disabled: unknown Records panel code')
            return
        debugger.bp(INITIALIZED,initialized)
        debugger.log('SPECIAL_THANKS exact-panel first-paint fix armed; Gallery and other browser panels unchanged')
    d.on_start(arm)
