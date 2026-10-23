#!/usr/bin/env python3
"""Fail-closed polaris CPU prefix check before packaging any payload.

Migrated from the retired ABL-shim chain's tools/polaris_cpu_topology.py.
Validates the serialized platform-stage ADT against the cpus=N boot argument;
it is not a claim that PSCI CPU_ON succeeds on the phone. The old chain proved
CPU1-3 (A55) PSCI/SGI with cpus=4; cores 4-7 (the A75 cluster) have never been
observed starting under XNU on polaris, on either chain.
"""
import argparse
import struct
from pathlib import Path


class TopologyError(ValueError):
    pass


def _decode(blob, offset=0):
    """Decode one ADT node; returns (name, {prop: bytes}, children, next offset)."""
    nprops, nchildren = struct.unpack_from('<II', blob, offset)
    offset += 8
    props = {}
    for _ in range(nprops):
        raw_name, size = struct.unpack_from('<32sI', blob, offset)
        offset += 36
        key = raw_name.split(b'\0', 1)[0].decode()
        if key in props or offset + size > len(blob):
            raise TopologyError('invalid property')
        props[key] = blob[offset:offset + size]
        offset += (size + 3) & ~3
    children = []
    for _ in range(nchildren):
        child_name, child_props, child_kids, offset = _decode(blob, offset)
        children.append((child_name, child_props, child_kids))
    name = props['name'].rstrip(b'\0').decode()
    return name, props, children, offset


def _u32(props, name, path):
    value = props.get(name)
    if value is None or len(value) != 4:
        raise TopologyError(f'{path}: missing/invalid {name}')
    return struct.unpack('<I', value)[0]


def validate(adt_bytes, cmdline):
    """Return the selected contiguous prefix, refusing ambiguous CPU boot args."""
    root_name, root, children, end = _decode(adt_bytes)
    if root_name != 'device-tree' or end != len(adt_bytes):
        raise TopologyError('not a polaris platform ADT')
    compatible = root.get('compatible') or b''
    if b'xiaomi,polaris\0' not in compatible or b'qcom,sdm845\0' not in compatible:
        raise TopologyError('not a Polaris/SDM845 ADT')
    tokens = cmdline.split()
    selections = [token for token in tokens if token.startswith('cpus=')]
    if len(selections) != 1 or any(token.startswith('cpumask=') for token in tokens):
        raise TopologyError('supply exactly one cpus=N, without cpumask')
    try:
        count = int(selections[0][5:])
    except ValueError as exc:
        raise TopologyError('invalid cpus=N') from exc
    if not 1 <= count <= 8:
        raise TopologyError('polaris cpus must be in 1..8')
    cpus = [(name, props, kids) for name, props, kids in children if name == 'cpus']
    if len(cpus) != 1:
        raise TopologyError('expected exactly one /cpus node')
    cores = cpus[0][2]
    if not 1 <= len(cores) <= 8:
        raise TopologyError('polaris ADT must describe a 1..8 CPU prefix')
    for index, (_, props, _) in enumerate(cores):
        path = f'/cpus/cpu{index}'
        if _u32(props, 'reg', path) != index << 8:
            raise TopologyError(f'CPU {index} MPIDR mismatch')
        expected = b'arm,cortex-a75\0' if index >= 4 else b'arm,cortex-a55\0'
        if props.get('compatible') != expected:
            raise TopologyError(f'CPU {index} cluster mismatch')
        if props.get('state') != (b'running\0' if index == 0 else b'waiting\0'):
            raise TopologyError(f'CPU {index} state mismatch')
    if count > len(cores):
        raise TopologyError(f'cpus={count} exceeds the {len(cores)} CPUs described by this ADT')
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('adt', type=Path)
    parser.add_argument('--cmdline', required=True)
    args = parser.parse_args()
    try:
        count = validate(args.adt.read_bytes(), args.cmdline)
    except (TopologyError, ValueError, IndexError, struct.error) as exc:
        parser.error(str(exc))
    gold = max(0, count - 4)
    print(f'polaris ADT prefix: {count}/8 selected; '
          f'{gold} A75 requested (NOT phone-verified)')


if __name__ == '__main__':
    main()
