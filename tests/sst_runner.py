"""Run the SingleStepTests 8088 suite against the Microprocessor core.

Each test vector holds the CPU state before and after one instruction,
captured from real hardware. The 8088 shares the 8086 instruction set,
so these vectors are the ISA reference for this emulator.

    tests/fetch_sst.sh                      # download the vectors once
    python tests/sst_runner.py              # documented opcodes, 300 vectors each
    python tests/sst_runner.py --limit 0    # every vector
    python tests/sst_runner.py --opcodes 00 F6.7 --verbose
"""
import argparse
import collections
import gzip
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from Microprocessor import Microprocessor  # noqa: E402

REGS = ['ax', 'bx', 'cx', 'dx', 'cs', 'ss', 'ds', 'es', 'sp', 'bp', 'si', 'di', 'ip', 'flags']
FLAG_NAMES = [('CF', 0x1), ('PF', 0x4), ('AF', 0x10), ('ZF', 0x40), ('SF', 0x80),
              ('TF', 0x100), ('IF', 0x200), ('DF', 0x400), ('OF', 0x800)]
DEFINED_FLAGS = 0x0FD5


def load_metadata(tests_dir):
    with open(os.path.join(tests_dir, 'metadata.json')) as f:
        return json.load(f)['opcodes']


def metadata_for(meta, name):
    """name is an opcode file stem such as '00' or 'F6.7'."""
    opcode, _, reg = name.partition('.')
    entry = meta.get(opcode, {})
    if reg and 'reg' in entry:
        entry = entry['reg'].get(reg, {})
    return entry.get('status', 'normal'), entry.get('flags-mask', 0xFFFF)


def load_state(cpu, regs, ram):
    cpu.reset(clear_memory=True)
    for name in REGS:
        setattr(cpu, 'FLAG' if name == 'flags' else name.upper(), regs[name])
    for address, value in ram:
        cpu.memory[address] = value


def pushed_flags_masks(test, expected, flag_mask):
    """Byte masks for the FLAGS word pushed by a divide-error trap.

    DIV/IDIV leave most flags undefined, and the values the 8088 pushes when
    it traps come from its internal microcode loop. Only the defined bits of
    that pushed word are compared, like the flags register itself.
    """
    if flag_mask == 0xFFFF or not test['name'].startswith(('div', 'idiv', 'aam')):
        return {}
    initial = test['initial']['regs']
    if expected['cs'] == initial['cs'] and expected['ip'] == (initial['ip'] + len(test['bytes'])) & 0xFFFF:
        return {}  # no trap
    mask = DEFINED_FLAGS & flag_mask
    base = expected['ss'] << 4
    low = (base + ((expected['sp'] + 4) & 0xFFFF)) & 0xFFFFF
    high = (base + ((expected['sp'] + 5) & 0xFFFF)) & 0xFFFFF
    return {low: mask & 0xFF, high: (mask >> 8) | 0xF0}


def check(cpu, test, flag_mask):
    """Return None on success, else a short description of the mismatch."""
    expected = dict(test['initial']['regs'])
    expected.update(test['final']['regs'])
    wrong = []
    for name in REGS:
        got = getattr(cpu, 'FLAG' if name == 'flags' else name.upper())
        want = expected[name]
        if name == 'flags':
            mask = DEFINED_FLAGS & flag_mask
            diff = (got ^ want) & mask
            if diff:
                wrong.append('flags:' + '/'.join(n for n, bit in FLAG_NAMES if diff & bit))
        elif got != want:
            wrong.append(name)
    ram = dict(test['initial']['ram'])
    ram.update(test['final']['ram'])
    masks = pushed_flags_masks(test, expected, flag_mask)
    if any((cpu.memory[address] ^ value) & masks.get(address, 0xFF) for address, value in ram.items()):
        wrong.append('mem')
    return 'wrong ' + ','.join(wrong) if wrong else None


def run_test(cpu, test, flag_mask):
    load_state(cpu, test['initial']['regs'], test['initial']['ram'])
    try:
        cpu.step()
    except Exception as e:  # report, don't abort the whole run
        return f'exception {type(e).__name__}: {e}'
    return check(cpu, test, flag_mask)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tests-dir', default=os.path.join(ROOT, 'tests', 'data', '8088', 'v1'))
    parser.add_argument('--limit', type=int, default=300, help='vectors per opcode, 0 for all')
    parser.add_argument('--opcodes', nargs='*', help='opcode file stems to run, e.g. 00 F6.7')
    parser.add_argument('--all-statuses', action='store_true',
                        help='also run undocumented/undefined/fpu opcodes')
    parser.add_argument('--verbose', action='store_true', help='print failure reasons per opcode')
    parser.add_argument('--fail-under', type=float, default=None,
                        help='exit non-zero if the documented pass rate (%%) is below this')
    args = parser.parse_args(argv)

    if not os.path.isdir(args.tests_dir):
        sys.exit(f'test vectors not found in {args.tests_dir}; run tests/fetch_sst.sh first')

    meta = load_metadata(args.tests_dir)
    cpu = Microprocessor()
    names = sorted(f[:-len('.json.gz')] for f in os.listdir(args.tests_dir) if f.endswith('.json.gz'))
    if args.opcodes:
        wanted = {o.upper() for o in args.opcodes}
        names = [n for n in names if n in wanted or n.split('.')[0] in wanted]

    total = passed = 0
    failing = []
    for name in names:
        status, flag_mask = metadata_for(meta, name)
        if status not in ('normal', 'alias') and not args.all_statuses:
            continue
        with gzip.open(os.path.join(args.tests_dir, name + '.json.gz')) as f:
            tests = json.load(f)
        if args.limit:
            tests = tests[:args.limit]
        reasons = collections.Counter()
        examples = {}
        ok = 0
        for test in tests:
            result = run_test(cpu, test, flag_mask)
            if result is None:
                ok += 1
            else:
                reasons[result] += 1
                examples.setdefault(result, test['name'])
        total += len(tests)
        passed += ok
        line = f'{name:6} {status:8} {ok:5}/{len(tests):<5}'
        if ok != len(tests):
            failing.append(name)
            if args.verbose:
                line += '  ' + ' | '.join(f'{c}x {r} [{examples[r]}]' for r, c in reasons.most_common(3))
        print(line, flush=True)

    rate = 100.0 * passed / total if total else 0.0
    print(f'\npassed {passed}/{total} ({rate:.2f}%)')
    if failing:
        print('failing:', ' '.join(failing))
    if args.fail_under is not None and rate < args.fail_under:
        sys.exit(1)


if __name__ == '__main__':
    main()
