#!/usr/bin/env python3
"""Prepare the pinned public IOStorageFamily headers for NativeUfs builds."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'refer/IOStorageFamily'
REVISION = 'e45fbe30a7879e0b16e26dddb78538f1b00e0bff'
OUT = ROOT / 'build/storage-abi'

if not (SOURCE / '.git').is_dir():
    raise SystemExit(f'Missing {SOURCE}; fetch the pinned IOStorageFamily source first')
revision = subprocess.check_output(['git', '-C', SOURCE, 'rev-parse', 'HEAD'], text=True).strip()
if revision != REVISION:
    raise SystemExit(f'Unexpected public IOStorageFamily revision: {revision}')

headers = OUT / 'include/IOKit/storage'
headers.mkdir(parents=True, exist_ok=True)
copied = {}
for path in sorted(SOURCE.glob('*.h')):
    target = headers / path.name
    shutil.copyfile(path, target)
    copied[path.name] = hashlib.sha256(target.read_bytes()).hexdigest()
if 'IOBlockStorageDevice.h' not in copied:
    raise SystemExit('Pinned IOStorageFamily source has no IOBlockStorageDevice.h')

sdk = Path(subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip())
conditions = sdk / 'usr/include/TargetConditionals.h'
target = OUT / 'include/TargetConditionals.h'
shutil.copyfile(conditions, target)

report = dict(source_revision=revision, headers=copied,
              target_conditionals_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
              qualification=False)
(OUT / 'headers.json').write_text(json.dumps(report, indent=2) + '\n')
print(f'PASS: {len(copied)} pinned IOStorageFamily public headers prepared')
