"""Restore native Library strings and point its URL table at the public OVH site."""
import struct

URLS = (
    (0x1418CFE88, 'story'), (0x1418CFEB8, 'howtoplay'),
    (0x1418CFEF0, 'stages'), (0x1418CFF20, 'characters'),
)
POINTERS = ((0x141B37C60,0x1418CFF20), (0x141B37C68,0x1418CFEF0),
            (0x141B37C70,0x1418CFEB8), (0x141B37C78,0x1418CFE88),
            (0x141B37C80,0x1418CFE88))


def replacements():
    for address, original, page in (
        (0x1418CFF90,'http://live.game.riseofincarnates.com/%s/support','support'),
        (0x1418CFFC8,'http://test.game.riseofincarnates.com/%s/updates','news'),
        (0x1418D0000,'http://live.game.riseofincarnates.com/%s/news','news'),
    ):
        old=original.encode('ascii')+b'\0'
        new=('http://127.0.0.1:52710/today/'+page).encode('ascii')+b'\0'
        if len(new)>len(old):
            raise ValueError('Today URL exceeds original string allocation')
        yield address,old,new.ljust(len(old),b'\0')
    for address, page in URLS:
        original = ('http://live.game.riseofincarnates.com/%s/'+page).encode('ascii')+b'\0'
        local = original
        if len(local)>len(original):
            raise ValueError('Library URL exceeds original string allocation')
        yield address, original, local.ljust(len(original), b'\0')


def setup(d):
    remote={}
    def arm(debugger):
        patches = list(replacements())
        for slot, target in POINTERS:
            if debugger.read(slot,8) not in (struct.pack('<Q',target),struct.pack('<Q',remote.get(target,target))):
                raise RuntimeError('Unexpected Library URL pointer table')
        for address, original, local in patches:
            if debugger.read(address,len(original)) not in (original,local):
                raise RuntimeError('Unexpected Library URL string; no URLs changed')
        for target,page in URLS:
            if target not in remote:
                slug={'characters':'character','stages':'stage'}.get(page,page)
                url=('https://riseofincarnates.artificerreviver.com/library/'+slug).encode('ascii')+b'\0'
                buffer=debugger.alloc(len(url))
                if not buffer or not debugger.write(buffer,url) or debugger.read(buffer,len(url))!=url:
                    raise RuntimeError('Could not allocate remote Library URL')
                remote[target]=buffer
        for address, original, local in patches:
            if not debugger.write(address,local) or debugger.read(address,len(local))!=local:
                raise RuntimeError('Could not install local Library URL')
        for slot,target in POINTERS:
            pointer=struct.pack('<Q',remote[target])
            if not debugger.write(slot,pointer) or debugger.read(slot,8)!=pointer:
                raise RuntimeError('Could not install remote Library URL pointer')
        debugger.log('LIBRARY remote OVH /library/character, stage, howtoplay, story URLs installed')
        debugger.log('TODAY News and Support URLs installed for the OVH website')
    d.on_start(arm)
