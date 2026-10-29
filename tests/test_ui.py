"""Offscreen smoke test of the main window (skipped when PyQt5 is not installed)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from PyQt5 import QtWidgets
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

    def load_hello(self):
        exe = os.path.join(self.tmp.name, 'HELLO.EXE')
        with open(exe, 'wb') as f:
            f.write(make_exe(HELLO_CODE + HELLO_DATA, relocations=[(1, 0)], ss=4, sp=0x80))
        file = self.ui.files[self.ui.tabbedFilesWidget.currentWidget()]
        file.exe = exe
        self.ui.debug()

    def test_debug_step_and_run_to_exit(self):
        self.load_hello()
        cpu = self.window.microprocessor
        self.assertTrue(self.ui.debugMode)
        self.assertIn('Loaded HELLO.EXE', self.ui.textOutput.toPlainText())
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
