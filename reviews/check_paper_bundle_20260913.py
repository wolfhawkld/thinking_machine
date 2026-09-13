"""Check the explicit bundle file set in an isolated temporary directory."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / 'paper-reproduction-20260913'


def main():
    manifest = json.loads((BUNDLE / 'manifest.json').read_text())
    names = list(manifest['files']) + ['manifest.json']
    assert set(names) == {'README.md', 'replay.py', 'dsl.py', 'prompts.json',
                          'microloop-evaluation.json', 'manifest.json'}
    with tempfile.TemporaryDirectory(prefix='paper-replay-20260913-') as directory:
        target = Path(directory)
        for name in names:
            shutil.copyfile(BUNDLE / name, target / name)
        result = subprocess.run([sys.executable, '-I', str(target / 'replay.py')],
                                cwd=target, env={'PATH': os.defpath, 'LANG': 'C.UTF-8'},
                                text=True, capture_output=True, check=True)
        report = json.loads(result.stdout)
        report['isolation'] = 'copied allowlist, clean environment, Python -I, same machine/interpreter'
        report['independent_machine_validation'] = False
    path = ROOT / 'paper-build-20260913/reproduction-check.json'
    path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
