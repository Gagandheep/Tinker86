#!/usr/bin/env python3
"""Take the screenshots used by README.md and docs/USER_GUIDE.md.

    xvfb-run -a -s "-screen 0 1600x1000x24" python tools/screenshots.py [OUTPUT_DIR]

Opens examples/sum.asm in the real main window, builds it with JWasm
(tools/build_jwasm.sh), debugs it and saves PNGs of each stage into
docs/images/ (or OUTPUT_DIR). Run it under a real X server (Xvfb is fine) so
that fonts and icons are drawn as on a desktop.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5 import QtCore, QtGui, QtWidgets  # noqa: E402

WIDTH, HEIGHT = 1440, 900


def settle(app, rounds=20):
    for _ in range(rounds):
        app.processEvents()
        QtCore.QThread.msleep(10)


def save(widget, directory, name, height=None):
    path = os.path.join(directory, name)
    picture = widget.grab()
    if height:
        picture = picture.copy(0, 0, picture.width(), height)
    picture.save(path)
    print('wrote', os.path.relpath(path, ROOT))


def find_line(editor, text):
    for number, line in enumerate(editor.toPlainText().splitlines(), 1):
        if text in line:
            return number
    raise ValueError(text)


def main():
    output = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'docs', 'images'))
    os.makedirs(output, exist_ok=True)
    os.environ.setdefault('ASSEMBLER', 'jwasm')

    app = QtWidgets.QApplication(sys.argv[:1])
    app.setStyle('Fusion')
    if not QtGui.QIcon.hasThemeIcon('document-open'):
        for theme in ('breeze', 'Adwaita', 'Humanity', 'gnome'):
            QtGui.QIcon.setThemeName(theme)
            if QtGui.QIcon.hasThemeIcon('document-open'):
                break
    font = app.font()
    font.setPointSize(10)
    app.setFont(font)

    from main import ApplicationWindow
    workdir = os.path.join(tempfile.gettempdir(), 'examples')
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir)
    source = os.path.join(workdir, 'SUM.asm')
    shutil.copy(os.path.join(ROOT, 'examples', 'sum.asm'), source)

    window = ApplicationWindow()
    ui = window.ui
    window.resize(WIDTH, HEIGHT)
    window.show()
    ui.closeAllTabs()
    ui.newTab(source)
    settle(app)

    # 1. Editing and building
    ui.build()
    settle(app)
    save(window, output, 'build.png')

    # Assembler menu, opened from the menu bar
    ui.menuAssembler.popup(window.mapToGlobal(QtCore.QPoint(0, 0)))
    settle(app)
    save(ui.menuAssembler, output, 'assembler-menu.png')
    ui.menuAssembler.close()

    # 2. Debugging: a breakpoint in the source, a watchpoint on "total"
    ui.debug()
    window.resize(WIDTH, HEIGHT)
    settle(app)
    editor = ui.sourceEditor()
    ui.toggleSourceBreakpoint(find_line(editor, 'mov     dx, offset label1'))
    ui.addWatchpointFromText('total')
    for _ in range(3):  # stop at the third write of total
        ui.run()
        while ui.isRunning():
            app.processEvents()
    ui.dockOutput.raise_()
    ui.tableComboBoxes[0].setCurrentIndex(1)  # the data segment
    window.resizeDocks([ui.dockRegisterView], [420], QtCore.Qt.Horizontal)
    window.resizeDocks([ui.dockRegisterView, ui.dockWatch], [330, 230], QtCore.Qt.Vertical)
    ui.tableWatch.resizeColumnsToContents()
    ui.tableWatch.horizontalHeader().setStretchLastSection(True)
    ui.tableDisassembly.setFocus()
    settle(app)
    save(window, output, 'debugger.png')
    save(ui.dockWatch, output, 'watch.png')
    save(ui.dockRegisterView, output, 'registers.png')
    save(ui.dockDisassembly, output, 'disassembly.png')

    # Memory view with the watched variable marked
    ui.dockWidgetSegmentViews[0].raise_()
    settle(app)
    save(ui.dockWidgetSegmentViews[0], output, 'memory.png')

    # Add Watchpoint dialog
    from UI.ui import WatchpointDialog
    dialog = WatchpointDialog(ui.mainWidget, 'numbers+4', 2, 'write')
    dialog.show()
    settle(app)
    save(dialog, output, 'watchpoint-dialog.png')
    dialog.close()

    # 3. Run to the end: the output is on the Screen view
    ui.watches.items.clear()
    ui.watches.changed()
    ui.refreshWatchTable()
    ui.clearBreakpoints()
    ui.run()
    while ui.isRunning():
        app.processEvents()
    ui.dockScreen.raise_()
    settle(app)
    save(ui.screen, output, 'screen.png', height=ui.screen.height() // 5)  # the top lines
    ui.dockOutput.raise_()
    settle(app)
    save(ui.dockOutput, output, 'log.png')

    ui.exitDebug()
    ui.dialogUnsavedWarning = lambda name: QtWidgets.QMessageBox.Discard
    window.close()
    shutil.rmtree(workdir, ignore_errors=True)


if __name__ == '__main__':
    main()
