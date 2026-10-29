"""Offscreen smoke test of the main window (skipped when PyQt5 is not installed)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from PyQt5 import QtCore, QtWidgets
    from PyQt5.QtTest import QTest
except ImportError:  # pragma: no cover
    QtWidgets = None

from test_program import HELLO_CODE, HELLO_DATA, make_exe  # noqa: E402


@unittest.skipIf(QtWidgets is None, 'PyQt5 not installed')
class MainWindowTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from main import ApplicationWindow
        self.window = ApplicationWindow()
        self.ui = self.window.ui
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def tearDown(self):
        self.ui.dialogUnsavedWarning = lambda name: QtWidgets.QMessageBox.Discard
        self.window.close()

    def load_exe(self, image, **kwargs):
        exe = os.path.join(self.tmp.name, 'PROGRAM.EXE')
        with open(exe, 'wb') as f:
            f.write(make_exe(image, **kwargs))
        file = self.ui.files[self.ui.tabbedFilesWidget.currentWidget()]
        file.exe = exe
        self.ui.debug()

    def load_hello(self):
        self.load_exe(HELLO_CODE + HELLO_DATA, relocations=[(1, 0)], ss=4, sp=0x80)

    def run_until_stopped(self):
        while self.ui.isRunning():
            self.app.processEvents()

    def test_screen_shows_output_and_keyboard_resumes_run(self):
        # ah=0: int 16h (wait for key) ; ah=0Eh: int 10h (echo it) ; mov ax,4C00h ; int 21h
        code = bytes([0xB4, 0x00, 0xCD, 0x16, 0xB4, 0x0E, 0xCD, 0x10, 0xB8, 0x00, 0x4C, 0xCD, 0x21])
        self.load_exe(code, ss=1, sp=0x80)
        bios = self.ui.dos.bios
        self.ui.run()
        self.run_until_stopped()
        cpu = self.window.microprocessor
        self.assertTrue(cpu.waiting_for_input)
        self.assertTrue(self.ui.resumeOnInput)
        self.assertIn('waiting for keyboard input', self.ui.statusbar.currentMessage())
        QTest.keyClick(self.ui.screen, QtCore.Qt.Key_Q)
        self.run_until_stopped()
        self.assertTrue(cpu.halted)
        self.assertEqual(bios.screen_text(), 'q')
        self.assertIn('exited with code 0', self.ui.textOutput.toPlainText())

    def test_screen_paints(self):
        self.load_hello()
        self.ui.run()
        self.run_until_stopped()
        self.assertEqual(self.ui.dos.bios.screen_text(), 'Hello, 8086!')
        image = self.ui.screen.grab().toImage()
        self.assertFalse(image.isNull())

    def test_debug_step_and_run_to_exit(self):
        self.load_hello()
        cpu = self.window.microprocessor
        self.assertTrue(self.ui.debugMode)
        self.assertIn('Loaded PROGRAM.EXE', self.ui.textOutput.toPlainText())
        self.ui.step()
        self.assertEqual(cpu.IP, 3)
        registers = cpu.Interface.registers
        self.assertEqual(self.ui.tableRegisters.item(registers.index('IP'), 1).text(), '0x0003')
        self.ui.run()
        while self.ui.isRunning():
            self.app.processEvents()
        log = self.ui.textOutput.toPlainText()
        self.assertIn('Hello, 8086!', log)
        self.assertIn('exited with code 7', log)
        self.assertFalse(self.ui.actionStep.isEnabled())
        self.ui.exitDebug()
        self.assertFalse(self.ui.debugMode)

    def test_disassembly_and_breakpoints(self):
        self.load_hello()
        cpu = self.window.microprocessor
        model = self.ui.disassemblyModel
        texts = [row.text for row in model.rows[:6]]
        self.assertEqual(texts[:3], ['mov ax, 812h', 'mov ds, ax', 'mov dx, 0h'])
        self.assertEqual(model.current, 0)
        # breakpoint on 'int 21h' (row 4); Run stops there before printing
        self.ui.toggleBreakpoint(4)
        self.assertIn(model.physical(model.rows[4].offset), self.ui.breakpoints)
        self.assertEqual(model.data(model.index(4, 0)), '\u25CF')
        self.ui.run()
        while self.ui.isRunning():
            self.app.processEvents()
        self.assertEqual(cpu.IP, model.rows[4].offset)
        self.assertNotIn('Hello', self.ui.textOutput.toPlainText())
        self.assertIn('Breakpoint at', self.ui.textOutput.toPlainText())
        # Run continues past the breakpoint it is sitting on
        self.ui.toggleBreakpoint(4)
        self.ui.tableDisassembly.selectRow(6)  # second int 21h
        self.ui.runToCursor()
        while self.ui.isRunning():
            self.app.processEvents()
        self.assertEqual(cpu.IP, model.rows[6].offset)
        self.assertIn('Hello, 8086!', self.ui.textOutput.toPlainText())
        self.assertEqual(model.current, cpu.IP)

    def test_disassembly_resyncs_when_jumping_mid_instruction(self):
        self.load_hello()
        cpu = self.window.microprocessor
        cpu.IP = 1  # inside 'mov ax, ...'
        self.ui.refreshDisassembly()
        model = self.ui.disassemblyModel
        self.assertEqual(model.rows[0].text, 'db 0B8h')
        self.assertEqual(model.rows[1].offset, 1)
        self.assertEqual(model.current, 1)

    def test_edit_register_and_memory(self):
        self.load_hello()
        cpu = self.window.microprocessor
        row = cpu.Interface.registers.index('AX')
        self.ui.tableRegisters.item(row, 1).setText('0x1234')
        self.assertEqual(cpu.AX, 0x1234)
        self.ui.tableRegisters.item(row, 1).setText('-1')
        self.assertEqual(cpu.AX, 0xFFFF)
        model = self.ui.memoryModels[0]  # Code segment view
        self.assertEqual(model.segment, cpu.CS)
        index = model.index(0, 0)
        self.assertEqual(model.data(index), 'B8')
        model.setData(index, '90')
        self.assertEqual(cpu.memory[cpu.CS << 4], 0x90)

    def test_close_modified_tab_can_be_cancelled(self):
        editor = self.ui.currentEditor()
        editor.setPlainText('mov ax, 1')
        tab = self.ui.tabbedFilesWidget.currentWidget()
        self.assertTrue(self.ui.files[tab].modified)
        self.assertTrue(self.ui.tabbedFilesWidget.tabText(0).endswith('*'))
        self.ui.dialogUnsavedWarning = lambda name: QtWidgets.QMessageBox.Cancel
        self.assertFalse(self.ui.closeAllTabs())
        self.assertEqual(self.ui.tabbedFilesWidget.count(), 1)
        self.ui.dialogUnsavedWarning = lambda name: QtWidgets.QMessageBox.Discard
        self.assertTrue(self.ui.closeAllTabs())
        self.assertEqual(self.ui.files, {})

    def test_build_reports_missing_toolchain(self):
        path = os.path.join(self.tmp.name, 'hello.asm')
        with open(path, 'w') as f:
            f.write('end\n')
        self.ui.newTab(path)
        import Assembler
        old = Assembler.TASM_BIN
        Assembler.TASM_BIN = self.tmp.name  # no TASM.EXE here
        try:
            self.assertFalse(self.ui.build())
        finally:
            Assembler.TASM_BIN = old
        self.assertIn('Build failed', self.ui.textOutput.toPlainText())
        self.assertFalse(self.ui.actionDebug.isEnabled())


if __name__ == '__main__':
    unittest.main()
