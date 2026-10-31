import os
import sys

from PyQt5 import QtWidgets
from UI.ui import UI_MainWindow
from Microprocessor import Microprocessor

# Read-only files that ship with the app (inside the bundle for the portable build)
RESOURCES = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))


class ApplicationWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super(ApplicationWindow, self).__init__()

        self.microprocessor = Microprocessor()
        self.ui = UI_MainWindow(self.microprocessor)
        self.ui.setupUI(self)

    def closeEvent(self, event):
        if self.ui.closeAllTabs():
            event.accept()
        else:
            event.ignore()


def self_test(report_path=None):
    """Check an installed copy works: build examples/hello.asm with the bundled JWasm,
    run it in the emulator, and open the main window offscreen. Returns the exit status."""
    import shutil
    import tempfile
    import traceback
    import Assembler
    from Microprocessor.dos import DOS

    lines = []
    status = 0
    try:
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'HELLO.asm')
            shutil.copy(os.path.join(RESOURCES, 'examples', 'hello.asm'), source)
            lines.append(f'JWasm: {Assembler.jwasm_executable()}')
            success, log, exe, _ = Assembler.build(source, 'jwasm')
            if not success:
                raise RuntimeError(f'build failed:\n{log}')
            lines.append(f'built {os.path.basename(exe)}')
            cpu = Microprocessor()
            dos = DOS(cpu, output=lambda text: None, root=directory)
            cpu.load_program(exe)
            cpu.run()
            screen = dos.bios.screen_text()
            if cpu.exit_code != 0 or 'Hello from the 8086!' not in screen:
                raise RuntimeError(f'unexpected result: exit code {cpu.exit_code}, screen {screen.strip()!r}')
            lines.append('ran HELLO.EXE: output and exit code are correct')

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
        window = ApplicationWindow()
        window.show()
        app.processEvents()
        window.close()
        lines.append(f'main window opened (Qt platform {app.platformName()})')
        lines.append('self-test passed')
    except Exception:
        lines.append(traceback.format_exc())
        lines.append('self-test FAILED')
        status = 1
    text = '\n'.join(lines) + '\n'
    if report_path:
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(text)
    elif sys.stdout is not None:  # the windowed build has no console
        sys.stdout.write(text)
    return status


def main():
    if '--self-test' in sys.argv:
        index = sys.argv.index('--self-test')
        report = sys.argv[index + 1] if index + 1 < len(sys.argv) else None
        sys.exit(self_test(report))
    app = QtWidgets.QApplication(sys.argv)
    application = ApplicationWindow()
    application.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
