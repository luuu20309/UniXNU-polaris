#!/usr/bin/env python3
"""Fill the source provider's normal POST HMAC field after final kernel link.

Implements the source fipspost_get_hmac/hmacfiletool build contract: HMAC-SHA256
with a one-byte zero key over the first __TEXT[ _EXEC ]/__text section.
No selftest or integrity check is disabled. Only locally compiled input is used.
"""
import argparse
import hashlib
import hmac
import json
from pathlib import Path
import struct

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('kernel', type=Path)
args = parser.parse_args()
data = bytearray(args.kernel.read_bytes())
if struct.unpack_from('<4I', data) != (0xfeedfacf, 0x100000c, 0, 2):
    raise SystemExit('Expected a source-built ordinary ARM64 MH_EXECUTE kernel')
if b'_source_corecrypto14_bootstrap\0' not in data:
    raise SystemExit('Input does not contain the explicit source-provider bootstrap')
offset = 32
code = None
integrity = None
segments = []
for _ in range(struct.unpack_from('<I', data, 16)[0]):
    cmd, length = struct.unpack_from('<II', data, offset)
    if cmd == 0x19:
        name, va, _, fileoff, _ = struct.unpack_from('<16s4Q', data, offset + 8)
        name = name.rstrip(b'\0')
        segments.append((name, va, fileoff))
        for index in range(struct.unpack_from('<I', data, offset + 64)[0]):
            section = offset + 72 + index * 80
            section_name, segment_name, address, size, position = struct.unpack_from('<16s16sQQI', data, section)
            if section_name.rstrip(b'\0') == b'__text' and segment_name.rstrip(b'\0') in (b'__TEXT', b'__TEXT_EXEC') and code is None:
                code = (position, size, address)
            if section_name.rstrip(b'\0') == b'__fips_hmacs':
                if integrity is not None or size != 32:
                    raise SystemExit('Expected exactly one 32-byte POST HMAC field')
                integrity = position
    offset += length
if code is None or integrity is None:
    raise SystemExit('Missing real provider code or POST HMAC field')
base = next(va for name, va, _ in segments if name == b'__TEXT')
if code[2] - base != code[0]:
    raise SystemExit('Runtime header-relative text mapping differs from the POST file-offset contract')
if code[0] <= integrity < code[0] + code[1]:
    raise SystemExit('HMAC field must be outside the hashed instructions')
mac = hmac.digest(b'\0', data[code[0]:code[0] + code[1]], 'sha256')
data[integrity:integrity + 32] = mac
args.kernel.write_bytes(data)
report = {'kernel_sha256': hashlib.sha256(data).hexdigest(), 'code_file_offset': hex(code[0]),
          'code_bytes': code[1], 'hmac_file_offset': hex(integrity), 'hmac_sha256': mac.hex(),
          'runtime_selftests_executed': False}
args.kernel.with_suffix(args.kernel.suffix + '.crypto-hmac.json').write_text(json.dumps(report, indent=2) + '\n')
print('Source kernel POST integrity field populated; runtime selftests are still required')
