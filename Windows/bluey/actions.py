"""Validate action arguments before the UI asks the user to approve execution."""
from urllib.parse import urlsplit
from .protocol import ACTIONS, grid_point

APPS = {'notepad': 'notepad.exe', 'calculator': 'calc.exe', 'paint': 'mspaint.exe'}
KEYS = {'tab', 'enter', 'esc', 'backspace', 'delete', 'left', 'right', 'up', 'down', 'home', 'end', 'pageup', 'pagedown', 'space'}
SHORTCUTS = {'ctrl+a', 'ctrl+c', 'ctrl+v', 'ctrl+x', 'ctrl+z', 'ctrl+y', 'ctrl+f', 'ctrl+l', 'ctrl+t', 'ctrl+shift+t'}


def validate(name, args, width, height):
    if name not in ACTIONS or not isinstance(args, dict):
        raise ValueError('Unknown action')
    if name == 'click':
        grid_point(args, width, height)
        for field in ('double', 'right'):
            if field in args and not isinstance(args[field], bool):
                raise ValueError('Click flags must be booleans')
    elif name == 'drag':
        grid_point(args, width, height, 'from_')
        grid_point(args, width, height, 'to_')
    elif name == 'type_text':
        value = args.get('text')
        if not isinstance(value, str) or len(value) > 2000 or any(ord(c) > 126 or ord(c) < 32 for c in value):
            raise ValueError('This version accepts printable ASCII text, at most 2000 characters.')
        if 'press_return' in args and not isinstance(args['press_return'], bool):
            raise ValueError('press_return must be a boolean')
    elif name == 'press_keys':
        if args.get('keys') not in KEYS | SHORTCUTS:
            raise ValueError('Shortcut is not allowed')
    elif name == 'scroll':
        amount = args.get('amount', 3)
        if args.get('direction') not in ('up', 'down') or type(amount) is not int or not 1 <= amount <= 10:
            raise ValueError('Invalid scroll')
    elif name == 'open_app':
        if args.get('name') not in APPS:
            raise ValueError('Only notepad, calculator, and paint are supported')
    elif name == 'open_url':
        url = args.get('url')
        if not isinstance(url, str) or len(url) > 2048 or any(c.isspace() or ord(c) < 32 for c in url):
            raise ValueError('Invalid URL')
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            raise ValueError('Only HTTP(S) websites without embedded credentials are allowed')
        if parts.hostname in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('Local administration URLs are blocked')
    return args
