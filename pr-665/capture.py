"""Render actual selection output in the checked-out TUI without product edits."""

from pathlib import Path
import json
import subprocess
import sys

side = sys.argv[1]
root = Path(f'/tmp/vibesys-pr665-{side}')
out = Path('/tmp/vibesys-pr-screenshots/pr-665')
session = f'pr665-{side}'
tool = '/tmp/vibesys-tui-test-tools/tt'
commands = []

def tt(*args, output=None):
    command = [tool, '--session', session, *map(str, args)]
    commands.append(command)
    result = subprocess.run(command, cwd='/tmp', text=True, capture_output=True, timeout=40)
    if result.returncode:
        raise RuntimeError(f'{command}: {result.stdout}\n{result.stderr}')
    if output:
        Path(output).write_text(result.stdout)
    return result.stdout

try:
    tt('run', '--backend', 'alacritty', '--cols', '150', '--rows', '40', '--cwd', root,
       '--env', 'TERM=xterm-256color', '--env', 'COLORTERM=truecolor',
       '--env', 'LANG=C.UTF-8', '--env', 'PATH=/tmp/vibesys-issues-tools:/usr/bin:/bin',
       '/bin/bash', root / 'clients/tui/dev/mock-ui.sh',
       '--fixture', out / f'{side}-fixture.jsonl', '--theme', 'dark', '--speed', '0',
       output=out / f'{side}-session.json')
    tt('expect', 'text', 'VibeSys', '--timeout', '15000')
    tt('wait', 'idle', '--timeout', '15000')
    tt('submit', '/open-round 1')
    tt('expect', 'text', 'Transcript', '--timeout', '15000')
    tt('key', 'press', 'F4')
    tt('expect', 'text', 'Selected parent:', '--timeout', '15000')
    tt('expect', 'text', '100 seeded draws:', '--timeout', '15000')
    tt('wait', 'idle', '--timeout', '15000')
    tt('text', output=out / f'{side}.txt')
    tt('state', output=out / f'{side}-state.json')
    tt('screenshot', '-o', out / f'{side}.svg')
    tt('record', 'start', out / f'{side}.png', '--fps', '1', '--zoom', '0.5')
    tt('record', 'stop')
    print((out / f'{side}.txt').read_text())
finally:
    try:
        tt('close')
    except Exception:
        pass
    (out / f'{side}-capture.json').write_text(json.dumps({'commands': commands}, indent=2) + '\n')
