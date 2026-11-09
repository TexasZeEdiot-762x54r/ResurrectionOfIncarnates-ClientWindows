"""Map generated POSIX installation paths for the Windows Python inside Proton."""
import os


def native_path(value, windows=None):
    if not isinstance(value, str) or not value or any(c in value for c in '\0\r\n'):
        raise ValueError('Invalid configured path')
    windows = os.name == 'nt' if windows is None else windows
    if windows and value.startswith('/'):
        return 'Z:' + value.replace('/', '\\')
    return value


def library_path(config, default, windows=None):
    windows = os.name == 'nt' if windows is None else windows
    return native_path(config.get('libs_windows' if windows else 'libs', default), windows)
