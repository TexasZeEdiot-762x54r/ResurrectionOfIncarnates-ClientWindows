"""Revival revision label for the 2.06 HUD; the login version is unchanged."""


def setup(d):
    def apply(debugger):
        address = 0x14187A9A0
        original = b' - %s\0'
        label = b' - R2\0'
        if (debugger.read(0x1401E5167, 7) != bytes.fromhex('48 8d 15 32 58 69 01')
                or debugger.read(address, len(original)) != original):
            debugger.log('VERSION_FOOTER skipped: unrecognized display formatter')
            return
        if not debugger.write(address, label) or debugger.read(address, len(label)) != label:
            debugger.log('VERSION_FOOTER could not install revision label')
    d.on_start(apply)
