"""Address expressions and data watchpoints."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Microprocessor import Microprocessor  # noqa: E402
from Microprocessor.watchpoints import AddressError, WatchList, Watchpoint, format_value, parse_address  # noqa: E402

CODE = 0x1000


def cpu_with(code):
    cpu = Microprocessor()
    cpu.CS, cpu.DS, cpu.ES, cpu.SS, cpu.SP = CODE, 0x2000, 0x2800, 0x3000, 0x100
    cpu.memory[CODE << 4:(CODE << 4) + len(code)] = bytes(code)
    return cpu


class AddressExpressions(unittest.TestCase):

    def setUp(self):
        self.cpu = cpu_with([])
        self.cpu.SI = 0x10

    def test_forms(self):
        cpu = self.cpu
        self.assertEqual(parse_address('DS:0010', cpu), (0x20010, None))
        self.assertEqual(parse_address('es:si+2', cpu), (0x28012, None))
        self.assertEqual(parse_address('1234:5h', cpu), (0x12345, None))
        self.assertEqual(parse_address('10h', cpu), (0x20010, None))  # bare offset: DS
        self.assertEqual(parse_address('si-1', cpu), (0x2000F, None))
        self.assertEqual(parse_address('12345', cpu), (0x12345, None))  # five digits: physical
        self.assertEqual(parse_address('0xB8000', cpu), (0xB8000, None))

    def test_symbols(self):
        symbols = {'MSG': (0x8120, 1), 'COUNT': (0x8130, 2)}.get
        self.assertEqual(parse_address('msg', self.cpu, lambda n: symbols(n.upper())), (0x8120, 1))
        self.assertEqual(parse_address('count+2', self.cpu, lambda n: symbols(n.upper())), (0x8132, 2))
        with self.assertRaises(AddressError):
            parse_address('nosuch', self.cpu, lambda n: symbols(n.upper()))

    def test_errors(self):
        for text in ('', 'zz:10', 'ds:', '1:2:3', 'DS:12345', '1000000'):
            with self.assertRaises(AddressError, msg=text):
                parse_address(text, self.cpu)


class Watching(unittest.TestCase):

    def test_write_watchpoint_reports_old_and_new_value(self):
        cpu = cpu_with([0xC7, 0x06, 0x10, 0x00, 0x34, 0x12, 0x90])  # mov word ptr [10h], 1234h ; nop
        watches = WatchList(cpu)
        watch = watches.add(Watchpoint('DS:10', 0x20010, 2, 'write'))
        cpu.step()
        hits = watches.check()
        self.assertEqual([(h.watchpoint, h.kind, h.old, h.new) for h in hits],
                         [(watch, 'written', b'\0\0', b'\x34\x12')])
        cpu.step()
        self.assertEqual(watches.check(), [])
        self.assertEqual(format_value(watch.value(cpu.memory)), '34 12  (1234h)')

    def test_read_watchpoint_tracks_reads_only_while_needed(self):
        cpu = cpu_with([0xA0, 0x11, 0x00, 0xA2, 0x11, 0x00])  # mov al, [11h] ; mov [11h], al
        watches = WatchList(cpu)
        self.assertFalse(cpu.track_reads)
        watches.add(Watchpoint('11', 0x20010, 2, 'read'))
        self.assertTrue(cpu.track_reads)
        cpu.step()
        self.assertEqual([h.kind for h in watches.check()], ['read'])
        cpu.step()
        self.assertEqual(watches.check(), [])  # a write does not trigger a read watch
        watches.remove(0)
        self.assertFalse(cpu.track_reads)

    def test_access_and_never_modes(self):
        cpu = cpu_with([0xA2, 0x00, 0x00, 0xA0, 0x00, 0x00])  # mov [0], al ; mov al, [0]
        watches = WatchList(cpu)
        watches.add(Watchpoint('a', 0x20000, 1, 'access'))
        watches.add(Watchpoint('b', 0x20000, 1, 'never'))
        cpu.step()
        self.assertEqual([h.watchpoint.expression for h in watches.check()], ['a'])
        cpu.step()
        self.assertEqual([h.kind for h in watches.check()], ['read'])

    def test_instruction_fetches_and_stack_writes(self):
        cpu = cpu_with([0x50])  # push ax: writes the stack, fetch must not count as a read
        watches = WatchList(cpu)
        watches.add(Watchpoint('code', CODE << 4, 1, 'read'))
        watches.add(Watchpoint('stack', (0x3000 << 4) + 0xFE, 2, 'write'))
        cpu.step()
        self.assertEqual([h.watchpoint.expression for h in watches.check()], ['stack'])


if __name__ == '__main__':
    unittest.main()
