#!/usr/bin/env python3
"""Retarget a structurally valid iPad-identity ADT's SM8550 strings.

The local iPad ADT already has a UFS node, but it was copied from the sheng
SM8550 profile. This utility retargets the SoC, UFS and boot CPU compatible
strings only if the input uses this checkout's ADT node/property encoding and
is fully parseable. Equal-length replacement alone is not proof of a valid
ADT. The output still needs board-property review and boot validation.
"""
import argparse
from pathlib import Path
import struct

REPLACEMENTS = (
    (b'qcom,sm8550-ufshc\0', b'qcom,sdm845-ufshc\0'),
    (b'qcom,sm8550\0', b'qcom,sdm845\0'),
    (b'arm,cortex-a715\0', b'arm,cortex-a55\0\0'),
)


def decode_node(data, offset=0):
    if offset + 8 > len(data):
        raise ValueError('truncated ADT node header')
    property_count, child_count = struct.unpack_from('<II', data, offset)
    offset += 8
    properties = {}
    for _ in range(property_count):
        if offset + 36 > len(data):
            raise ValueError('truncated ADT property header')
        raw_name, size = struct.unpack_from('<32sI', data, offset)
        offset += 36
        name = raw_name.split(b'\0', 1)[0].decode('ascii')
        if not name or name in properties or offset + size > len(data):
            raise ValueError('invalid or duplicate ADT property')
        properties[name] = data[offset:offset + size]
        offset += (size + 3) & ~3
        if offset > len(data):
            raise ValueError('truncated ADT property value padding')
    if 'name' not in properties:
        raise ValueError('ADT node has no name property')
    children = []
    for _ in range(child_count):
        child, offset = decode_node(data, offset)
        children.append(child)
    return {'properties': properties, 'children': children}, offset


def validate_adt(data):
    root, end = decode_node(data)
    if end != len(data) or root['properties'].get('name') != b'device-tree\0':
        raise ValueError('input is not a complete ADT in this checkout\'s node format')
    compatible = root['properties'].get('compatible', b'')
    if b'qcom,sm8550\0' not in compatible:
        raise ValueError('expected an SM8550 source ADT')


def retarget(data):
    for before, after in REPLACEMENTS:
        if len(before) != len(after):
            raise ValueError('replacement changes the ADT layout')
    validate_adt(data)
    result = bytearray(data)
    counts = []
    for before, after in REPLACEMENTS:
        count = result.count(before)
        if count != 1:
            raise ValueError(f'expected one {before.rstrip(bytes([0]))!r} string, found {count}')
        result[:] = result.replace(before, after)
        counts.append(count)
    if any(before in result for before, _ in REPLACEMENTS):
        raise ValueError('SM8550 compatibility string remains')
    validate_adt(result)
    return bytes(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='existing iPad-identity ADT')
    parser.add_argument('--output', type=Path, required=True, help='new ADT path; input is preserved')
    args = parser.parse_args()
    try:
        data = retarget(args.input.read_bytes())
    except (OSError, ValueError) as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(f'{args.output}: retargeted ADT compatible strings to Polaris; {len(data)} bytes')


if __name__ == '__main__':
    main()
