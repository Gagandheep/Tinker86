import os

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt

import Assembler
from Assembler import AssemblerError
from Assembler.listing import ListingError, find_companion, load_source_map
from Microprocessor import InstructionNotImplemented
from Microprocessor.bios import (BIOS_DATA_SEGMENT, COLUMNS, CURSOR_POSITION, ROWS, SCAN_CODES, VIDEO_SEGMENT,
                                 BIOSServiceNotImplemented)
from Microprocessor.disassembler import data_byte, disassemble_memory
from Microprocessor.dos import DOS, DOSServiceNotImplemented
from Microprocessor.program import ProgramFormatError
from Microprocessor.watchpoints import MODES, AddressError, WatchList, Watchpoint, format_value, parse_address
from UI.files import File

SEGMENT_SIZE = 0x10000
RUN_BATCH_STEPS = 20000
DISASSEMBLY_BEFORE_IP = 0x80  # listing window when execution is outside the loaded program
DISASSEMBLY_AFTER_IP = 0x400


def parseNumber(text, bits):
    """Parse 0x.., 0b.. or decimal (optionally negative) text into an unsigned value."""
    text = text.strip()
    if not text or text in '+-':
        raise ValueError('empty value')
    negative = text[0] == '-'
    text = text.lstrip('+-')
    base = 16 if text[1:2] in ('x', 'X') else 2 if text[1:2] in ('b', 'B') else 10
    value = int(text, base)
    return (-value if negative else value) & ((1 << bits) - 1)


class RegisterDelegate(QtWidgets.QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = super(RegisterDelegate, self).createEditor(parent, option, index)
        if isinstance(editor, QtWidgets.QLineEdit):
            reg_ex = QtCore.QRegExp("(-)?((0x[0-9a-fA-F]{1,4})|(0b[01]{1,16})|[0-9]{1,5})")
            validator = QtGui.QRegExpValidator(reg_ex, editor)
            editor.setValidator(validator)
        return editor


class MemoryDelegate(QtWidgets.QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = super(MemoryDelegate, self).createEditor(parent, option, index)
        if isinstance(editor, QtWidgets.QLineEdit):
            reg_ex = QtCore.QRegExp("(-)?((0x[0-9a-fA-F]{1,2})|(0b[01]{1,8})|[0-9]{1,3})")
            validator = QtGui.QRegExpValidator(reg_ex, editor)
            editor.setValidator(validator)
        return editor


class MemoryModel(QtCore.QAbstractTableModel):
    """One 64 KB segment of the CPU's memory, 16 bytes per row, read on demand."""

    def __init__(self, microprocessor, watches=None, parent=None):
        super(MemoryModel, self).__init__(parent)
        self.microprocessor = microprocessor
        self.watches = watches
        self.segment = 0
        self.highlight = None  # physical address of the next instruction

    @property
    def start(self):
        return self.segment << 4

    def setSegment(self, segment):
        self.beginResetModel()
        self.segment = segment
        self.endResetModel()

    def addressesChanged(self, addresses):
        start = self.start
        for address in set(addresses):
            offset = (address - start) & 0xFFFFF
            if offset < SEGMENT_SIZE:
                index = self.index(offset >> 4, offset & 0xF)
                self.dataChanged.emit(index, index)

    def setHighlight(self, address):
        previous, self.highlight = self.highlight, address
        self.addressesChanged([a for a in (previous, address) if a is not None])

    def rowCount(self, parent=QtCore.QModelIndex()):
        return SEGMENT_SIZE // 16

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 16

    def address(self, index):
        return (self.start + index.row() * 16 + index.column()) & 0xFFFFF

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if role in (Qt.DisplayRole, Qt.EditRole):
            return '{:02X}'.format(self.microprocessor.memory[self.address(index)])
        if role == Qt.BackgroundRole:
            address = self.address(index)
            if address == self.highlight:
                return QtGui.QBrush(QtGui.QColor(255, 210, 90))
            if self.watches is not None and address in self.watches.watched:
                return QtGui.QBrush(QtGui.QColor(180, 215, 255))
        if role == Qt.TextAlignmentRole:
            return Qt.AlignCenter
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole:
            return False
        try:
            byte = parseNumber(value if value[:2].lower() in ('0x', '0b') or value[:1] == '-'
                               else '0x' + value, 8)
        except ValueError:
            return False
        self.microprocessor.memory[self.address(index)] = byte
        self.dataChanged.emit(index, index)
        return True

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return '{:X}'.format(section)
        return '{:04X}:{:04X}'.format(self.segment, section * 16)


CGA_PALETTE = ((0, 0, 0), (0, 0, 170), (0, 170, 0), (0, 170, 170), (170, 0, 0), (170, 0, 170), (170, 85, 0),
               (170, 170, 170), (85, 85, 85), (85, 85, 255), (85, 255, 85), (85, 255, 255), (255, 85, 85),
               (255, 85, 255), (255, 255, 85), (255, 255, 255))
# code page 437 glyphs for the control characters 00h-1Fh
CP437_CONTROL = ' \u263A\u263B\u2665\u2666\u2663\u2660\u2022\u25D8\u25CB\u25D9\u2642\u2640\u266A\u266B\u263C' \
                '\u25BA\u25C4\u2195\u203C\u00B6\u00A7\u25AC\u21A8\u2191\u2193\u2192\u2190\u221F\u2194\u25B2\u25BC'
CP437 = [CP437_CONTROL[i] if i < 32 else bytes([i]).decode('cp437') for i in range(256)]
SPECIAL_KEYS = {
    Qt.Key_Return: (0x0D, 0x1C), Qt.Key_Enter: (0x0D, 0x1C), Qt.Key_Backspace: (0x08, 0x0E),
    Qt.Key_Tab: (0x09, 0x0F), Qt.Key_Escape: (0x1B, 0x01), Qt.Key_Up: (0, 0x48), Qt.Key_Down: (0, 0x50),
    Qt.Key_Left: (0, 0x4B), Qt.Key_Right: (0, 0x4D), Qt.Key_Home: (0, 0x47), Qt.Key_End: (0, 0x4F),
    Qt.Key_PageUp: (0, 0x49), Qt.Key_PageDown: (0, 0x51), Qt.Key_Insert: (0, 0x52), Qt.Key_Delete: (0, 0x53),
}
SPECIAL_KEYS.update({Qt.Key_F1 + i: (0, 0x3B + i) for i in range(10)})
VIDEO_START = VIDEO_SEGMENT << 4
VIDEO_END = VIDEO_START + ROWS * COLUMNS * 2
CURSOR_ADDRESS = (BIOS_DATA_SEGMENT << 4) + CURSOR_POSITION


def biosKey(event):
    """(ASCII, scan code) for a Qt key event, or None for keys the PC keyboard lacks."""
    if event.key() in SPECIAL_KEYS:
        return SPECIAL_KEYS[event.key()]
    text = event.text()
    if len(text) == 1 and ord(text) < 256:
        return ord(text), SCAN_CODES.get(text.lower(), 0)
    return None


class ScreenWidget(QtWidgets.QWidget):
    """The 80x25 colour text screen at B800:0000; typed keys go to the BIOS keyboard."""

    keyTyped = QtCore.pyqtSignal()

    def __init__(self, microprocessor, bios, parent=None):
        super(ScreenWidget, self).__init__(parent)
        self.microprocessor = microprocessor
        self.bios = bios
        self.setFocusPolicy(Qt.StrongFocus)
        self.font = QtGui.QFont('monospace', 11)
        self.font.setStyleHint(QtGui.QFont.TypeWriter)
        metrics = QtGui.QFontMetrics(self.font)
        self.cellWidth = metrics.horizontalAdvance('M')
        self.cellHeight = metrics.height()
        self.ascent = metrics.ascent()
        self.setMinimumSize(COLUMNS * self.cellWidth + 4, ROWS * self.cellHeight + 4)
        self.colors = [QtGui.QColor(*rgb) for rgb in CGA_PALETTE]

    def sizeHint(self):
        return self.minimumSize()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), Qt.black)
        painter.setFont(self.font)
        memory = self.microprocessor.memory
        for row in range(ROWS):
            y = 2 + row * self.cellHeight
            start = VIDEO_START + row * COLUMNS * 2
            column = 0
            while column < COLUMNS:  # draw runs of cells with the same attribute
                attribute = memory[start + column * 2 + 1]
                end = column
                while end < COLUMNS and memory[start + end * 2 + 1] == attribute:
                    end += 1
                x = 2 + column * self.cellWidth
                width = (end - column) * self.cellWidth
                painter.fillRect(x, y, width, self.cellHeight, self.colors[(attribute >> 4) & 7])
                painter.setPen(self.colors[attribute & 0x0F])
                text = ''.join(CP437[memory[start + c * 2]] for c in range(column, end))
                for i, char in enumerate(text):
                    if char != ' ':
                        painter.drawText(x + i * self.cellWidth, y + self.ascent, char)
                column = end
        cursorRow, cursorColumn = memory[CURSOR_ADDRESS + 1], memory[CURSOR_ADDRESS]
        if cursorRow < ROWS and cursorColumn < COLUMNS:
            attribute = memory[VIDEO_START + (cursorRow * COLUMNS + cursorColumn) * 2 + 1]
            painter.fillRect(2 + cursorColumn * self.cellWidth, 2 + (cursorRow + 1) * self.cellHeight - 3,
                             self.cellWidth, 2, self.colors[attribute & 0x0F] if attribute & 0x0F else Qt.gray)
        if self.hasFocus():
            painter.setPen(QtGui.QColor(255, 210, 90))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def keyPressEvent(self, event):
        key = biosKey(event)
        if key is None:
            super(ScreenWidget, self).keyPressEvent(event)
            return
        self.bios.keyboard.push(*key)
        self.keyTyped.emit()

    def focusInEvent(self, event):
        self.update()
        super(ScreenWidget, self).focusInEvent(event)

    def focusOutEvent(self, event):
        self.update()
        super(ScreenWidget, self).focusOutEvent(event)


class DisassemblyModel(QtCore.QAbstractTableModel):
    """Linear disassembly of part of the code segment, with breakpoints and the current instruction."""

    COLUMNS = ('', 'Address', 'Bytes', 'Instruction')

    def __init__(self, microprocessor, breakpoints, parent=None):
        super(DisassemblyModel, self).__init__(parent)
        self.microprocessor = microprocessor
        self.breakpoints = breakpoints  # set of physical addresses, shared with the window
        self.rows = []
        self.rowByOffset = {}
        self.segment = None
        self.window = (0, 0)
        self.current = None  # offset of the next instruction
        self.dirty = True

    def physical(self, offset):
        return self.microprocessor.physical_address(self.segment, offset)

    def covers(self, addresses):
        start, end = self.physical(self.window[0]), self.physical(self.window[0]) + self.window[1] - self.window[0]
        return any(start <= address < end for address in addresses)

    def rebuild(self, segment, start, end, anchor):
        """Disassemble [start, end) of segment, making sure anchor starts an instruction."""
        self.beginResetModel()
        self.segment = segment
        self.window = (start, end)
        self.rows = []
        offset = start
        while offset < end:
            instruction = disassemble_memory(self.microprocessor, segment, offset)
            if offset < anchor < offset + instruction.length:
                # execution enters this instruction in the middle: show the bytes before it as data
                for o in range(offset, anchor):
                    self.rows.append(data_byte(o, self.microprocessor.read8(segment, o)))
                offset = anchor
                continue
            self.rows.append(instruction)
            offset += instruction.length
        self.rowByOffset = {row.offset: i for i, row in enumerate(self.rows)}
        self.dirty = False
        self.endResetModel()

    def setCurrent(self, offset):
        previous, self.current = self.current, offset
        for o in (previous, offset):
            self.offsetChanged(o)

    def offsetChanged(self, offset):
        row = self.rowByOffset.get(offset)
        if row is not None:
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(self.COLUMNS) - 1))

    def rowCount(self, parent=QtCore.QModelIndex()):
        return len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return len(self.COLUMNS)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        breakpoint = self.physical(row.offset) in self.breakpoints
        if role == Qt.DisplayRole:
            column = index.column()
            if column == 0:
                return '\u25CF' if breakpoint else ''
            if column == 1:
                return '{:04X}:{:04X}'.format(self.segment, row.offset)
            if column == 2:
                return row.data.hex(' ').upper()
            return row.text
        if role == Qt.ForegroundRole and index.column() == 0:
            return QtGui.QBrush(QtGui.QColor(200, 0, 0))
        if role == Qt.BackgroundRole:
            if row.offset == self.current:
                return QtGui.QBrush(QtGui.QColor(255, 210, 90))
            if breakpoint:
                return QtGui.QBrush(QtGui.QColor(255, 200, 200))
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.COLUMNS[section]
        return None


class LineNumberArea(QtWidgets.QWidget):
    def __init__(self, editor):
        super(LineNumberArea, self).__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QtCore.QSize(self.editor.gutterWidth(), 0)

    def paintEvent(self, event):
        self.editor.paintGutter(event)

    def mousePressEvent(self, event):
        self.editor.gutterClicked.emit(self.editor.lineAt(event.pos().y()))


class CodeEditor(QtWidgets.QPlainTextEdit):
    """Source editor with a gutter: line numbers, breakpoint dots and the current line while debugging."""

    gutterClicked = QtCore.pyqtSignal(int)  # 1-based line number
    MARKER_WIDTH = 16

    def __init__(self, parent=None):
        super(CodeEditor, self).__init__(parent)
        self.gutter = LineNumberArea(self)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)  # keep one source line per row
        self.breakpointLines = set()
        self.currentLine = None
        self.blockCountChanged.connect(self.updateGutterWidth)
        self.updateRequest.connect(self.updateGutter)
        self.updateGutterWidth()

    def gutterWidth(self):
        digits = len(str(max(1, self.blockCount())))
        return self.MARKER_WIDTH + self.fontMetrics().horizontalAdvance('9') * max(digits, 3) + 6

    def updateGutterWidth(self, *args):
        self.setViewportMargins(self.gutterWidth(), 0, 0, 0)

    def updateGutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.updateGutterWidth()

    def resizeEvent(self, event):
        super(CodeEditor, self).resizeEvent(event)
        area = self.contentsRect()
        self.gutter.setGeometry(QtCore.QRect(area.left(), area.top(), self.gutterWidth(), area.height()))

    def setFont(self, font):
        super(CodeEditor, self).setFont(font)
        self.updateGutterWidth()

    def lineAt(self, y):
        return self.cursorForPosition(QtCore.QPoint(0, int(y))).blockNumber() + 1

    def paintGutter(self, event):
        painter = QtGui.QPainter(self.gutter)
        painter.fillRect(event.rect(), self.palette().window())
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            bottom = top + self.blockBoundingRect(block).height()
            if block.isVisible() and bottom >= event.rect().top():
                line = block.blockNumber() + 1
                y = int(top)
                if line in self.breakpointLines:
                    painter.setBrush(QtGui.QColor(200, 0, 0))
                    painter.setPen(Qt.NoPen)
                    size = min(10, height - 4)
                    painter.drawEllipse(3, y + (height - size) // 2, size, size)
                if line == self.currentLine:
                    painter.setBrush(QtGui.QColor(230, 160, 0))
                    painter.setPen(Qt.NoPen)
                    middle = y + height // 2
                    painter.drawPolygon(QtGui.QPolygon([QtCore.QPoint(4, middle - 5), QtCore.QPoint(12, middle),
                                                        QtCore.QPoint(4, middle + 5)]))
                painter.setPen(self.palette().color(QtGui.QPalette.Disabled, QtGui.QPalette.Text))
                painter.drawText(self.MARKER_WIDTH, y, self.gutter.width() - self.MARKER_WIDTH - 4, height,
                                 Qt.AlignRight, str(line))
            block = block.next()
            top = bottom

    def setDebugInfo(self, breakpointLines, currentLine, follow=True):
        """Show breakpoints and the current line (None clears it); follow scrolls to it."""
        self.breakpointLines = set(breakpointLines)
        self.currentLine = currentLine
        selections = []
        for line, color in [(n, QtGui.QColor(255, 210, 210)) for n in sorted(self.breakpointLines)] + \
                           ([(currentLine, QtGui.QColor(255, 225, 140))] if currentLine else []):
            block = self.document().findBlockByNumber(line - 1)
            if not block.isValid():
                continue
            selection = QtWidgets.QTextEdit.ExtraSelection()
            selection.format.setBackground(color)
            selection.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
            selection.cursor = QtGui.QTextCursor(block)
            selections.append(selection)
        self.setExtraSelections(selections)
        if follow and currentLine:
            cursor = QtGui.QTextCursor(self.document().findBlockByNumber(currentLine - 1))
            self.setTextCursor(cursor)
            self.ensureCursorVisible()
        self.gutter.update()


class WatchpointDialog(QtWidgets.QDialog):
    """Asks for an address expression, size and when to stop."""

    def __init__(self, parent, expression='', size=0, mode='write'):
        super(WatchpointDialog, self).__init__(parent)
        self.setWindowTitle('Add Watchpoint')
        layout = QtWidgets.QFormLayout(self)
        self.expression = QtWidgets.QLineEdit(expression, self)
        self.expression.setPlaceholderText('symbol, DS:0010, ES:DI+2, 12345 ...')
        layout.addRow('Address:', self.expression)
        self.size = QtWidgets.QSpinBox(self)
        self.size.setRange(0, 16)
        self.size.setSpecialValueText('auto')
        self.size.setValue(size)
        layout.addRow('Bytes:', self.size)
        self.mode = QtWidgets.QComboBox(self)
        self.mode.addItems(MODES)
        self.mode.setCurrentText(mode)
        layout.addRow('Stop on:', self.mode)
        layout.addRow(QtWidgets.QLabel('Numbers are hexadecimal. "auto" uses the symbol\'s size, else 2 bytes.'))
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)


# noinspection PyAttributeOutsideInit,PyPep8Naming
class UI_MainWindow:
    debugMode = False
    SEGMENT_VIEWS_COUNT = 4
    SOURCE_MIN_WIDTH = 360  # the editor never gets narrower than this in debug mode
    DEBUG_WINDOW_WIDTH = 1280  # a smaller window grows to this (screen permitting) when debugging
    DEBUG_WINDOW_HEIGHT = 800

    def __init__(self, microprocessor):
        self.files = {}
        self.microprocessor = microprocessor
        self.microprocessor.Interface.memory_callback = self.updateTableMemories
        self.microprocessor.Interface.registers_callback = self.updateRegisters
        self.microprocessor.Interface.segments_callback = self.updateSegment
        self.dos = DOS(microprocessor, output=self.appendProgramOutput)
        self.resumeOnInput = False  # Run paused because the program waits for a key
        self.runTimer = None
        self.program = None
        self.breakpoints = set()  # physical addresses
        self.watches = WatchList(microprocessor)
        self.sourceMap = None  # Assembler.listing.SourceMap of the program being debugged
        self.sourceTab = None  # the tab showing that program's source
        self.temporaryBreakpoint = None  # used by Run to Cursor

    def setupUI(self, MainWindow):
        MainWindow.setObjectName("MainWindow")
        MainWindow.resize(self.fitToScreen(MainWindow, 1280, 800))
        MainWindow.setDocumentMode(True)
        self.mainWindow = MainWindow

        self.mainWidget = QtWidgets.QWidget(MainWindow)
        MainWindow.setCentralWidget(self.mainWidget)

        self.setupTabbedFilesWidget()
        self.setupDockWidgets(MainWindow)
        self.setupActions(MainWindow)
        self.setupMenuBar(MainWindow)
        self.setupToolBars(MainWindow)
        self.setupStatusBar(MainWindow)

        self.retranslateUI(MainWindow)

        QtCore.QMetaObject.connectSlotsByName(MainWindow)

        self.runTimer = QtCore.QTimer(MainWindow)
        self.runTimer.timeout.connect(self.runBatch)

        self.newTab()

    def retranslateUI(self, MainWindow):
        _translate = QtCore.QCoreApplication.translate
        MainWindow.setWindowTitle(_translate("MainWindow", "8086 Emulator"))
        self.menuFile.setTitle(_translate("MainWindow", "&File"))
        self.menuEdit.setTitle(_translate("MainWindow", "&Edit"))
        self.menuView.setTitle(_translate("MainWindow", "&View"))
        self.menuAssembler.setTitle(_translate("MainWindow", "&Assembler"))
        self.menuDebugger.setTitle(_translate("MainWindow", "&Debugger"))
        self.menuHelp.setTitle(_translate("MainWindow", "&Help"))

    def setupTabbedFilesWidget(self):
        self.tabbedFilesWidget = QtWidgets.QTabWidget(self.mainWidget)
        self.tabbedFilesWidget.setTabsClosable(True)
        self.tabbedFilesWidget.setMovable(True)
        self.tabbedFilesWidget.currentChanged.connect(self.tabUpdate)
        self.tabbedFilesWidget.tabCloseRequested.connect(self.closeTab)
        # keep the source readable when the debugger docks open around it
        self.tabbedFilesWidget.setMinimumWidth(self.SOURCE_MIN_WIDTH)
        self.gridLayoutTabbedFilesWidget = QtWidgets.QGridLayout(self.mainWidget)
        self.gridLayoutTabbedFilesWidget.addWidget(self.tabbedFilesWidget, 0, 1, 1, 1)

    def setupDockWidgets(self, MainWindow):
        self.setupDockWidgetOutput(MainWindow)
        self.setupDockWidgetRegisterView(MainWindow)
        self.setupDockWidgetSegmentViews(MainWindow)
        self.setupDockWidgetDisassembly(MainWindow)
        self.setupDockWidgetScreen(MainWindow)
        self.setupDockWidgetWatch(MainWindow)

    def setupDockWidgetOutput(self, MainWindow):
        self.dockOutput = QtWidgets.QDockWidget(MainWindow)
        self.dockOutput.setObjectName('dockOutput')
        self.dockOutput.setWindowTitle('Log')
        self.dockOutput.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
        self.dockOutputContents = QtWidgets.QWidget()
        self.gridLayoutDockOutput = QtWidgets.QGridLayout(self.dockOutputContents)
        self.textOutput = QtWidgets.QTextBrowser(self.dockOutputContents)
        self.textOutput.setFont(QtGui.QFont('monospace', 10))
        self.gridLayoutDockOutput.addWidget(self.textOutput, 0, 0, 1, 1)
        self.dockOutput.setWidget(self.dockOutputContents)
        MainWindow.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.dockOutput)

    def setupDockWidgetRegisterView(self, MainWindow):
        self.dockRegisterView = QtWidgets.QDockWidget(MainWindow)
        self.dockRegisterView.setObjectName('dockRegisterView')
        self.dockRegisterView.setWindowTitle('Registers')
        self.dockRegisterView.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
        self.dockRegisterViewContents = QtWidgets.QWidget()
        self.gridLayoutDockRegisterView = QtWidgets.QGridLayout(self.dockRegisterViewContents)
        self.tableRegisters = QtWidgets.QTableWidget(self.dockRegisterViewContents)
        self.tableRegisters.setItemDelegateForColumn(1, RegisterDelegate(self.tableRegisters))
        self.tableRegisters.setColumnCount(2)
        registers = self.microprocessor.Interface.registers
        self.tableRegisters.setRowCount(len(registers))
        self.tableRegisters.setHorizontalHeaderLabels(['Register', 'Value'])
        self.tableRegisters.verticalHeader().setVisible(False)
        for i, itemName in enumerate(registers):
            itemLabel = QtWidgets.QTableWidgetItem(itemName)
            itemLabel.setFlags(Qt.ItemIsEnabled)
            self.tableRegisters.setItem(i, 0, itemLabel)
            itemValue = QtWidgets.QTableWidgetItem()
            itemValue.setFont(QtGui.QFont('monospace', 10))
            self.tableRegisters.setItem(i, 1, itemValue)
        self.updateRegisters()
        self.tableRegisters.cellChanged.connect(self.registerTableUpdate)
        self.gridLayoutDockRegisterView.addWidget(self.tableRegisters, 0, 0, 1, 1)
        self.dockRegisterView.setWidget(self.dockRegisterViewContents)
        MainWindow.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.dockRegisterView)
        self.dockRegisterView.setVisible(False)

    def updateRegisters(self):
        if self.isRunning():
            return
        self.tableRegisters.blockSignals(True)
        for i, itemName in enumerate(self.microprocessor.Interface.registers):
            self.tableRegisters.item(i, 1).setText("0x{:04X}".format(getattr(self.microprocessor, itemName)))
        self.tableRegisters.blockSignals(False)
        self.updateStatus()

    def registerTableUpdate(self, row, column=1):
        name = self.microprocessor.Interface.registers[row]
        try:
            setattr(self.microprocessor, name, parseNumber(self.tableRegisters.item(row, 1).text(), 16))
        except ValueError:
            pass
        self.updateRegisters()
        if name in self.microprocessor.Interface.segments.values():
            self.updateSegment(None, None)
        self.updateHighlight()

    def setupDockWidgetSegmentViews(self, MainWindow):
        self.dockWidgetSegmentViews = []
        self.tableComboBoxes = []
        self.memoryModels = []
        self.tableMemories = []

        for i in range(self.SEGMENT_VIEWS_COUNT):
            dockSegmentView = QtWidgets.QDockWidget(MainWindow)
            dockSegmentView.setObjectName(f'dockSegmentView{i}')
            dockSegmentView.setVisible(False)
            self.dockWidgetSegmentViews.append(dockSegmentView)
            dockSegmentView.setWindowTitle(f'Segment View {i + 1}')
            dockSegmentView.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
            dockSegmentViewContents = QtWidgets.QWidget()
            gridLayoutDockSegmentView = QtWidgets.QGridLayout(dockSegmentViewContents)
            labelSegment = QtWidgets.QLabel('Segment:', dockSegmentViewContents)
            gridLayoutDockSegmentView.addWidget(labelSegment, 0, 0, 1, 1)
            combobox = QtWidgets.QComboBox(dockSegmentViewContents)
            combobox.addItems(self.microprocessor.Interface.segments.keys())
            combobox.setCurrentIndex(i % combobox.count())
            self.tableComboBoxes.append(combobox)
            gridLayoutDockSegmentView.addWidget(combobox, 0, 1, 1, 1)
            model = MemoryModel(self.microprocessor, self.watches, dockSegmentViewContents)
            self.memoryModels.append(model)
            tableMemory = QtWidgets.QTableView(dockSegmentViewContents)
            tableMemory.setModel(model)
            tableMemory.setItemDelegate(MemoryDelegate(tableMemory))
            tableMemory.setFont(QtGui.QFont('monospace', 9))
            tableMemory.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
            tableMemory.verticalHeader().setDefaultSectionSize(20)
            tableMemory.setContextMenuPolicy(Qt.CustomContextMenu)
            tableMemory.customContextMenuRequested.connect(
                lambda position, view=i: self.memoryContextMenu(view, position))
            self.tableMemories.append(tableMemory)
            combobox.currentTextChanged.connect(lambda text, view=i: self.showSegment(view))
            gridLayoutDockSegmentView.addWidget(tableMemory, 1, 0, 1, 2)
            dockSegmentView.setWidget(dockSegmentViewContents)
            MainWindow.addDockWidget(QtCore.Qt.BottomDockWidgetArea, dockSegmentView)
            MainWindow.tabifyDockWidget(self.dockOutput, dockSegmentView)
            self.showSegment(i)

    def setupDockWidgetDisassembly(self, MainWindow):
        self.dockDisassembly = QtWidgets.QDockWidget(MainWindow)
        self.dockDisassembly.setObjectName('dockDisassembly')
        self.dockDisassembly.setWindowTitle('Disassembly')
        self.dockDisassembly.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
        self.disassemblyModel = DisassemblyModel(self.microprocessor, self.breakpoints, self.dockDisassembly)
        self.tableDisassembly = QtWidgets.QTableView(self.dockDisassembly)
        self.tableDisassembly.setModel(self.disassemblyModel)
        self.tableDisassembly.setFont(QtGui.QFont('monospace', 10))
        self.tableDisassembly.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tableDisassembly.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tableDisassembly.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tableDisassembly.verticalHeader().setVisible(False)
        self.tableDisassembly.verticalHeader().setDefaultSectionSize(20)
        self.tableDisassembly.setShowGrid(False)
        header = self.tableDisassembly.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        header.resizeSection(0, 24)
        header.resizeSection(1, 90)
        header.resizeSection(2, 110)
        header.setStretchLastSection(True)
        self.tableDisassembly.doubleClicked.connect(lambda index: self.toggleBreakpoint(index.row()))
        self.dockDisassembly.setWidget(self.tableDisassembly)
        MainWindow.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.dockDisassembly)
        self.dockDisassembly.setVisible(False)

    def setupDockWidgetScreen(self, MainWindow):
        self.dockScreen = QtWidgets.QDockWidget(MainWindow)
        self.dockScreen.setObjectName('dockScreen')
        self.dockScreen.setWindowTitle('Screen')
        self.dockScreen.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
        self.screen = ScreenWidget(self.microprocessor, self.dos.bios, self.dockScreen)
        self.screen.keyTyped.connect(self.keyTyped)
        scroll = QtWidgets.QScrollArea(self.dockScreen)
        scroll.setWidget(self.screen)
        scroll.setAlignment(Qt.AlignCenter)
        self.dockScreen.setWidget(scroll)
        MainWindow.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.dockScreen)
        MainWindow.tabifyDockWidget(self.dockOutput, self.dockScreen)
        self.dockScreen.setVisible(False)

    def setupDockWidgetWatch(self, MainWindow):
        self.dockWatch = QtWidgets.QDockWidget(MainWindow)
        self.dockWatch.setObjectName('dockWatch')
        self.dockWatch.setWindowTitle('Watch')
        self.dockWatch.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
        contents = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(contents)
        layout.setContentsMargins(4, 4, 4, 4)
        self.tableWatch = QtWidgets.QTableWidget(0, 5, contents)
        self.tableWatch.setHorizontalHeaderLabels(['Expression', 'Address', 'Bytes', 'Stop on', 'Value'])
        self.tableWatch.verticalHeader().setVisible(False)
        self.tableWatch.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tableWatch.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tableWatch.horizontalHeader().setStretchLastSection(True)
        self.tableWatch.setFont(QtGui.QFont('monospace', 9))
        layout.addWidget(self.tableWatch)
        buttons = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton('Add...', contents)
        add.clicked.connect(lambda: self.addWatchpoint())
        remove = QtWidgets.QPushButton('Remove', contents)
        remove.clicked.connect(self.removeWatchpoint)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.dockWatch.setWidget(contents)
        MainWindow.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.dockWatch)
        self.dockWatch.setVisible(False)

    def symbolLookup(self, name):
        return self.sourceMap.symbol(name) if self.sourceMap is not None else None

    def addWatchpointFromText(self, expression, size=0, mode='write'):
        """Add a watchpoint; size 0 means the symbol's size (else 2). Raises AddressError."""
        address, hint = parse_address(expression, self.microprocessor, self.symbolLookup)
        watchpoint = self.watches.add(Watchpoint(expression, address, size or hint or 2, mode))
        self.refreshWatchTable()
        self.dockWatch.setVisible(True)
        return watchpoint

    def addWatchpoint(self, expression='', size=0, mode='write'):
        dialog = WatchpointDialog(self.mainWidget, expression, size, mode)
        while dialog.exec_() == QtWidgets.QDialog.Accepted:
            try:
                return self.addWatchpointFromText(dialog.expression.text(), dialog.size.value(),
                                                  dialog.mode.currentText())
            except AddressError as e:
                QtWidgets.QMessageBox.warning(self.mainWidget, 'Add Watchpoint', str(e))
        return None

    def removeWatchpoint(self):
        rows = sorted({index.row() for index in self.tableWatch.selectedIndexes()}, reverse=True)
        for row in rows:
            self.watches.remove(row)
        self.refreshWatchTable()

    def setWatchMode(self, row, mode):
        self.watches.items[row].mode = mode
        self.watches.changed()
        self.refreshMemoryViews()

    def refreshWatchTable(self):
        """Rebuild the Watch table (rows, mode selectors) and repaint watched memory."""
        self.tableWatch.setRowCount(len(self.watches.items))
        for row, watchpoint in enumerate(self.watches.items):
            self.tableWatch.setItem(row, 0, QtWidgets.QTableWidgetItem(watchpoint.expression))
            self.tableWatch.setItem(row, 1, QtWidgets.QTableWidgetItem(f'{watchpoint.address:05X}'))
            self.tableWatch.setItem(row, 2, QtWidgets.QTableWidgetItem(str(watchpoint.size)))
            combo = QtWidgets.QComboBox()
            combo.addItems(MODES)
            combo.setCurrentText(watchpoint.mode)
            combo.currentTextChanged.connect(lambda mode, r=row: self.setWatchMode(r, mode))
            self.tableWatch.setCellWidget(row, 3, combo)
            self.tableWatch.setItem(row, 4, QtWidgets.QTableWidgetItem())
        self.refreshWatchValues()
        self.refreshMemoryViews()

    def refreshWatchValues(self):
        for row, watchpoint in enumerate(self.watches.items):
            item = self.tableWatch.item(row, 4)
            if item is not None:
                item.setText(format_value(watchpoint.value(self.microprocessor.memory)))

    def refreshMemoryViews(self):
        for model in self.memoryModels:
            model.setSegment(model.segment)

    def memoryContextMenu(self, view, position):
        index = self.tableMemories[view].indexAt(position)
        if not index.isValid():
            return
        model = self.memoryModels[view]
        offset = index.row() * 16 + index.column()
        expression = f'{model.segment:04X}:{offset:04X}'
        menu = QtWidgets.QMenu(self.tableMemories[view])
        byte = menu.addAction(f'Watch byte at {expression}')
        word = menu.addAction(f'Watch word at {expression}')
        other = menu.addAction('Add Watchpoint...')
        chosen = menu.exec_(self.tableMemories[view].viewport().mapToGlobal(position))
        if chosen in (byte, word):
            self.addWatchpointFromText(expression, 1 if chosen is byte else 2)
        elif chosen is other:
            self.addWatchpoint(expression)

    def reportWatchHits(self, hits, cs, ip):
        for hit in hits:
            watchpoint = hit.watchpoint
            self.log(f'\nWatchpoint {watchpoint.expression} [{watchpoint.address:05X}] {hit.kind} by the '
                     f'instruction at {cs:04X}:{ip:04X}: {format_value(hit.old)} -> {format_value(hit.new)}\n')

    def keyTyped(self):
        if self.resumeOnInput and self.debugMode:
            self.resumeOnInput = False
            self.run()

    def disassemblyWindow(self):
        """The (start, end) offsets of the code segment to list."""
        cpu = self.microprocessor
        base = cpu.CS << 4
        here = base + cpu.IP
        if self.program is not None:
            start, end = self.program.image_range
            if start <= here < end:
                return max(0, start - base), min(0x10000, end - base)
        return max(0, cpu.IP - DISASSEMBLY_BEFORE_IP), min(0x10000, cpu.IP + DISASSEMBLY_AFTER_IP)

    def refreshDisassembly(self):
        if not self.debugMode:
            return
        cpu, model = self.microprocessor, self.disassemblyModel
        window = self.disassemblyWindow()
        if model.dirty or model.segment != cpu.CS or not window[0] <= cpu.IP < window[1] \
                or window != model.window or cpu.IP not in model.rowByOffset:
            model.rebuild(cpu.CS, window[0], window[1], cpu.IP)
        model.setCurrent(cpu.IP)
        row = model.rowByOffset.get(cpu.IP)
        if row is not None:
            self.tableDisassembly.scrollTo(model.index(row, 0), QtWidgets.QAbstractItemView.EnsureVisible)
        self.refreshSource()

    def sourceEditor(self):
        if self.sourceTab is None or self.sourceTab not in self.files:
            return None
        return self.sourceTab.findChild(CodeEditor)

    def currentSourceLine(self):
        if self.sourceMap is None:
            return None
        cpu = self.microprocessor
        return self.sourceMap.line_for_address(cpu.physical_address(cpu.CS, cpu.IP))

    def refreshSource(self, follow=True):
        editor = self.sourceEditor()
        if editor is None:
            return
        lines = {line for line, address in self.sourceMap.code_line_address.items() if address in self.breakpoints}
        editor.setDebugInfo(lines, self.currentSourceLine() if self.debugMode else None, follow)

    def sourceHasFocus(self):
        editor = self.sourceEditor()
        return editor is not None and editor.hasFocus()

    def toggleBreakpointAt(self, address):
        if address in self.breakpoints:
            self.breakpoints.remove(address)
        else:
            self.breakpoints.add(address)
        self.disassemblyModel.beginResetModel()
        self.disassemblyModel.endResetModel()
        self.refreshSource(follow=False)

    def toggleSourceBreakpoint(self, line):
        if self.sourceMap is None or not self.debugMode:
            return
        address = self.sourceMap.address_for_line(line)
        if address is None:
            self.log(f'\nNo code at or after line {line}\n')
            return
        self.toggleBreakpointAt(address)

    def sourceGutterClicked(self, tab, line):
        if tab is self.sourceTab:
            self.toggleSourceBreakpoint(line)

    def runToSourceLine(self, line):
        address = self.sourceMap.address_for_line(line) if self.sourceMap is not None else None
        if address is None or not self.debugMode:
            return
        self.temporaryBreakpoint = address
        self.run()

    def selectedDisassemblyRow(self):
        index = self.tableDisassembly.currentIndex()
        return index.row() if index.isValid() else None

    def toggleBreakpoint(self, row=None):
        """F9: on the source line with the cursor when the source has focus, else the disassembly row."""
        if type(row) is bool or row is None:
            if self.sourceHasFocus():
                self.toggleSourceBreakpoint(self.sourceEditor().textCursor().blockNumber() + 1)
                return
            row = self.selectedDisassemblyRow()
        if row is None or not 0 <= row < len(self.disassemblyModel.rows):
            return
        self.toggleBreakpointAt(self.disassemblyModel.physical(self.disassemblyModel.rows[row].offset))

    def clearBreakpoints(self):
        self.breakpoints.clear()
        self.disassemblyModel.beginResetModel()
        self.disassemblyModel.endResetModel()
        self.refreshSource(follow=False)

    def runToCursor(self):
        if self.sourceHasFocus():
            self.runToSourceLine(self.sourceEditor().textCursor().blockNumber() + 1)
            return
        row = self.selectedDisassemblyRow()
        if row is None or not self.debugMode:
            return
        self.temporaryBreakpoint = self.disassemblyModel.physical(self.disassemblyModel.rows[row].offset)
        self.run()

    def showSegment(self, view):
        register = self.microprocessor.Interface.segments[self.tableComboBoxes[view].currentText()]
        segment = getattr(self.microprocessor, register)
        if self.memoryModels[view].segment != segment:
            self.memoryModels[view].setSegment(segment)

    def updateTableMemories(self, addresses=None):
        if addresses is None or self.disassemblyModel.covers(addresses):
            self.disassemblyModel.dirty = True
        if self.isRunning():
            return
        if addresses is None or any(VIDEO_START <= a < VIDEO_END or a in (CURSOR_ADDRESS, CURSOR_ADDRESS + 1)
                                    for a in addresses):
            self.screen.update()
        for i, model in enumerate(self.memoryModels):
            if addresses is None:
                self.showSegment(i)
                model.setSegment(model.segment)  # repaint everything
            else:
                model.addressesChanged(addresses)

    def updateSegment(self, segment, segment_value):
        if self.isRunning():
            return
        for i in range(self.SEGMENT_VIEWS_COUNT):
            self.showSegment(i)

    def updateHighlight(self):
        cpu = self.microprocessor
        address = cpu.physical_address(cpu.CS, cpu.IP) if self.debugMode else None
        for model in self.memoryModels:
            model.setHighlight(address)

    def updateStatus(self):
        if self.debugMode:
            cpu = self.microprocessor
            if cpu.halted:
                state = 'halted'
            elif cpu.waiting_for_input:
                state = 'waiting for keyboard input (type in the Screen view)'
            else:
                state = 'running' if self.isRunning() else 'paused'
            self.statusbar.showMessage(f'CS:IP = {cpu.CS:04X}:{cpu.IP:04X}   {state}')

    def setupActions(self, MainWindow):
        def action(icon, text, slot, shortcut=None):
            newAction = QtWidgets.QAction(QtGui.QIcon.fromTheme(icon), text, MainWindow)
            newAction.triggered.connect(slot)
            if shortcut is not None:
                newAction.setShortcut(QtGui.QKeySequence(shortcut))
            MainWindow.addAction(newAction)
            return newAction

        self.actionNew = action('document-new', '&New', self.newTab, QtGui.QKeySequence.New)
        self.actionOpen = action('document-open', '&Open...', self.fileOpen, QtGui.QKeySequence.Open)
        self.actionSave = action('document-save', '&Save', self.fileSave, QtGui.QKeySequence.Save)
        self.actionSaveAs = action('document-save-as', 'Save &As...', self.fileSaveAs, QtGui.QKeySequence.SaveAs)
        self.actionClose = action('window-close', '&Close Tab', self.closeCurrentTab, QtGui.QKeySequence.Close)
        self.actionQuit = action('application-exit', '&Quit', MainWindow.close, QtGui.QKeySequence.Quit)
        self.actionBuild = action('run-build', 'Build', self.build, 'F7')
        self.actionDebug = action('debug-run', 'Debug', self.debug, 'F8')
        self.actionStep = action('go-next', 'Step', self.step, 'F10')
        self.actionRun = action('media-playback-start', 'Run', self.run, 'F5')
        self.actionStop = action('media-playback-pause', 'Pause', self.pause, 'Shift+F5')
        self.actionExitDebug = action('process-stop', 'Exit Debug', self.exitDebug)
        self.actionToggleBreakpoint = action('media-record', 'Toggle Breakpoint', self.toggleBreakpoint, 'F9')
        self.actionClearBreakpoints = action('edit-clear', 'Clear Breakpoints', self.clearBreakpoints)
        self.actionRunToCursor = action('go-jump', 'Run to Cursor', self.runToCursor, 'F4')
        self.actionAddWatchpoint = action('list-add', 'Add Watchpoint...', lambda: self.addWatchpoint(), 'Ctrl+Shift+W')
        self.actionAbout = action('help-about', '&About', self.about)

        self.actionDebug.setEnabled(False)
        for debugAction in self.debugActions():
            debugAction.setVisible(False)

    def debugActions(self):
        return (self.actionStep, self.actionRun, self.actionStop, self.actionRunToCursor,
                self.actionToggleBreakpoint, self.actionClearBreakpoints, self.actionAddWatchpoint,
                self.actionExitDebug)

    def setupMenuBar(self, MainWindow):
        self.menubar = QtWidgets.QMenuBar(MainWindow)

        self.menuFile = self.menubar.addMenu('&File')
        for fileAction in (self.actionNew, self.actionOpen, self.actionSave, self.actionSaveAs, self.actionClose):
            self.menuFile.addAction(fileAction)
        self.menuFile.addSeparator()
        self.menuFile.addAction(self.actionQuit)

        self.menuEdit = self.menubar.addMenu('&Edit')
        for text, slot, key in (('Undo', 'undo', QtGui.QKeySequence.Undo), ('Redo', 'redo', QtGui.QKeySequence.Redo),
                                ('Cut', 'cut', QtGui.QKeySequence.Cut), ('Copy', 'copy', QtGui.QKeySequence.Copy),
                                ('Paste', 'paste', QtGui.QKeySequence.Paste),
                                ('Select All', 'selectAll', QtGui.QKeySequence.SelectAll)):
            editAction = self.menuEdit.addAction(text)
            editAction.setShortcut(QtGui.QKeySequence(key))
            editAction.setShortcutContext(Qt.WidgetShortcut)  # the editor handles the keys itself
            editAction.triggered.connect(lambda checked=False, name=slot: self.editorAction(name))

        self.menuView = self.menubar.addMenu('&View')
        self.menuView.addAction(self.dockOutput.toggleViewAction())
        self.menuView.addAction(self.dockRegisterView.toggleViewAction())
        self.menuView.addAction(self.dockDisassembly.toggleViewAction())
        self.menuView.addAction(self.dockScreen.toggleViewAction())
        self.menuView.addAction(self.dockWatch.toggleViewAction())
        for dockSegmentView in self.dockWidgetSegmentViews:
            self.menuView.addAction(dockSegmentView.toggleViewAction())

        self.menuAssembler = self.menubar.addMenu('&Assembler')
        self.menuAssembler.addAction(self.actionBuild)
        self.menuAssembler.addSeparator()
        self.assemblerChoice = QtWidgets.QActionGroup(MainWindow)
        initial = (os.environ.get('ASSEMBLER') or 'auto').lower()
        for name, text in (('auto', 'Automatic (TASM if installed, else JWasm)'),
                           ('tasm', 'TASM + TLINK (DOSBox)'), ('jwasm', 'JWasm')):
            choice = self.menuAssembler.addAction(text)
            choice.setCheckable(True)
            choice.setData(name)
            choice.setChecked(name == initial or (name == 'auto' and initial not in Assembler.ASSEMBLERS))
            self.assemblerChoice.addAction(choice)

        self.menuDebugger = self.menubar.addMenu('&Debugger')
        self.menuDebugger.addAction(self.actionDebug)
        for debugAction in self.debugActions():
            self.menuDebugger.addAction(debugAction)

        self.menuHelp = self.menubar.addMenu('&Help')
        self.menuHelp.addAction(self.actionAbout)

        MainWindow.setMenuBar(self.menubar)

    def setupToolBars(self, MainWindow):
        self.toolbarFile = MainWindow.addToolBar("File")
        self.toolbarFile.setObjectName('toolbarFile')
        self.toolbarFile.addAction(self.actionNew)
        self.toolbarFile.addAction(self.actionOpen)
        self.toolbarFile.addAction(self.actionSave)

        self.toolbarDebug = MainWindow.addToolBar("Debug")
        self.toolbarDebug.setObjectName('toolbarDebug')
        for debugAction in (self.actionBuild, self.actionDebug, self.actionStep, self.actionRun, self.actionStop,
                            self.actionExitDebug):
            self.toolbarDebug.addAction(debugAction)

    def setupStatusBar(self, MainWindow):
        self.statusbar = QtWidgets.QStatusBar(MainWindow)
        MainWindow.setStatusBar(self.statusbar)

    # ------------------------------------------------------------------ editor

    def currentEditor(self):
        tab = self.tabbedFilesWidget.currentWidget()
        return None if tab is None else tab.findChild(QtWidgets.QPlainTextEdit)

    def editorAction(self, name):
        editor = self.currentEditor()
        if editor is not None:
            getattr(editor, name)()

    def tabTitle(self, file):
        title = 'New File' if file.path is None else os.path.basename(file.path)
        return title + ('*' if file.modified else '')

    def refreshTabTitle(self, tab):
        self.tabbedFilesWidget.setTabText(self.tabbedFilesWidget.indexOf(tab), self.tabTitle(self.files[tab]))

    def newTab(self, path=None):
        if type(path) is bool:
            path = None
        file = File(path)
        tab = QtWidgets.QWidget()
        tab.setObjectName(f"tab{file.id}")
        verticalLayout = QtWidgets.QVBoxLayout(tab)
        plainTextEdit = CodeEditor(tab)
        plainTextEdit.setFont(QtGui.QFont('monospace', 10))
        plainTextEdit.setPlainText(file.data)
        plainTextEdit.textChanged.connect(lambda: self.textEdited(tab))
        plainTextEdit.gutterClicked.connect(lambda line: self.sourceGutterClicked(tab, line))
        verticalLayout.addWidget(plainTextEdit)
        self.files[tab] = file
        self.tabbedFilesWidget.addTab(tab, self.tabTitle(file))
        self.tabbedFilesWidget.setCurrentWidget(tab)
        plainTextEdit.setFocus()

    def closeTab(self, index):
        """Close the tab at index, asking about unsaved changes. Returns False if cancelled."""
        tab = self.tabbedFilesWidget.widget(index)
        file = self.files[tab]
        if file.modified:
            ret = self.dialogUnsavedWarning(self.tabTitle(file).rstrip('*'))
            if ret == QtWidgets.QMessageBox.Save:
                if not self.fileSave(index):
                    return False
            elif ret != QtWidgets.QMessageBox.Discard:
                return False
        self.tabbedFilesWidget.removeTab(index)
        del self.files[tab]
        tab.deleteLater()
        return True

    def closeCurrentTab(self):
        if self.tabbedFilesWidget.count() > 0:
            self.closeTab(self.tabbedFilesWidget.currentIndex())

    def closeAllTabs(self):
        """Returns False if the user cancelled closing a modified file."""
        while self.tabbedFilesWidget.count() > 0:
            if not self.closeTab(0):
                return False
        return True

    def tabUpdate(self):
        hasTabs = self.tabbedFilesWidget.count() > 0
        self.actionSave.setEnabled(hasTabs)
        self.actionSaveAs.setEnabled(hasTabs)
        self.actionBuild.setEnabled(hasTabs and not self.debugMode)
        if hasTabs and not self.debugMode:
            file = self.files.get(self.tabbedFilesWidget.currentWidget())
            self.actionDebug.setEnabled(file is not None and file.exe is not None)

    def textEdited(self, tab):
        file = self.files.get(tab)
        if file is None:
            return
        modified = tab.findChild(QtWidgets.QPlainTextEdit).toPlainText() != file.data
        if modified != file.modified:
            file.modified = modified
            self.refreshTabTitle(tab)

    def fileSave(self, index=None, saveAs=False):
        if type(index) is bool:
            index = None
        tab = self.tabbedFilesWidget.widget(self.tabbedFilesWidget.currentIndex() if index is None else index)
        if tab is None:
            return False
        file = self.files[tab]
        path = file.path
        if saveAs or not file.saved:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self.mainWidget, 'Save File', path or '', 'Assembly (*.asm);;All files (*)')
            if not path:
                return False
        file.save(data=tab.findChild(QtWidgets.QPlainTextEdit).toPlainText(), path=path)
        file.modified = False
        self.refreshTabTitle(tab)
        return True

    def fileSaveAs(self):
        return self.fileSave(saveAs=True)

    def fileOpen(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self.mainWidget, 'Open Files', '', 'Assembly (*.asm);;All files (*)')
        for path in paths:
            for tab, file in self.files.items():
                if file.path is not None and os.path.samefile(file.path, path):
                    self.tabbedFilesWidget.setCurrentWidget(tab)
                    break
            else:
                self.newTab(path)

    # ------------------------------------------------------------- build/debug

    def log(self, text):
        self.textOutput.moveCursor(QtGui.QTextCursor.End)
        self.textOutput.insertPlainText(text)
        self.textOutput.moveCursor(QtGui.QTextCursor.End)

    def appendProgramOutput(self, text):
        self.log(text.replace('\r\n', '\n').replace('\r', ''))

    def build(self):
        self.textOutput.clear()
        tab = self.tabbedFilesWidget.currentWidget()
        if tab is None:
            return False
        file = self.files[tab]
        success, exe = False, None
        if self.fileSave():
            self.dockOutput.setVisible(True)
            self.dockOutput.raise_()
            try:
                success, log, exe, used = Assembler.build(file.path, self.selectedAssembler())
                log = f'[{"TASM + TLINK" if used == "tasm" else "JWasm"}]\n{log}'
            except AssemblerError as e:
                log = f'Build failed: {e}'
            self.textOutput.setText(log)
            self.textOutput.moveCursor(QtGui.QTextCursor.End)
        file.build_success = success
        file.exe = exe
        self.actionDebug.setEnabled(success)
        return success

    def selectedAssembler(self):
        checked = self.assemblerChoice.checkedAction()
        return checked.data() if checked is not None else 'auto'

    def setDebugMode(self, enabled):
        self.debugMode = enabled
        self.toolbarFile.setVisible(not enabled)
        self.actionBuild.setVisible(not enabled)
        self.actionDebug.setVisible(not enabled)
        for debugAction in self.debugActions():
            debugAction.setVisible(enabled)
        self.dockRegisterView.setVisible(enabled)
        self.dockDisassembly.setVisible(enabled)
        self.dockScreen.setVisible(enabled)
        self.dockWatch.setVisible(enabled)
        if enabled:
            self.makeRoomForDebugger()
            # leave room for the source: the bottom docks (log, screen, memory) scroll
            self.mainWindow.resizeDocks([self.dockOutput], [max(220, self.mainWindow.height() // 3)], Qt.Vertical)
        for dockSegmentView in self.dockWidgetSegmentViews:
            dockSegmentView.setVisible(enabled)
        for tab in self.files:
            tab.findChild(QtWidgets.QPlainTextEdit).setReadOnly(enabled)
        self.setRunControls()

    @staticmethod
    def fitToScreen(window, width, height):
        """The size (width, height), shrunk to fit the screen the window is on."""
        screen = window.screen() if hasattr(window, 'screen') else None
        screen = screen or QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(width, available.width())
            height = min(height, available.height())
        return QtCore.QSize(width, height)

    def makeRoomForDebugger(self):
        """Share the width between the source and the debugger docks.

        A small window is enlarged (within the screen) first, so the source,
        the registers/watch column and the disassembly all fit side by side.
        """
        window = self.mainWindow
        if not (window.isMaximized() or window.isFullScreen()):
            target = self.fitToScreen(window, max(window.width(), self.DEBUG_WINDOW_WIDTH),
                                      max(window.height(), self.DEBUG_WINDOW_HEIGHT))
            if target != window.size():
                window.resize(target)
        width = window.width()
        left = max(200, width * 22 // 100)
        right = max(260, min(480, width * 30 // 100))
        window.resizeDocks([self.dockRegisterView, self.dockDisassembly], [left, right], Qt.Horizontal)

    def setRunControls(self):
        running, halted = self.isRunning(), self.microprocessor.halted
        self.actionStep.setEnabled(self.debugMode and not running and not halted)
        self.actionRun.setEnabled(self.debugMode and not running and not halted)
        self.actionStop.setEnabled(self.debugMode and running)
        self.actionRunToCursor.setEnabled(self.debugMode and not running and not halted)
        self.updateStatus()

    def debug(self):
        tab = self.tabbedFilesWidget.currentWidget()
        file = self.files.get(tab)
        if file is None or file.exe is None:
            return
        self.textOutput.clear()
        self.dos.set_root(os.path.dirname(os.path.abspath(file.exe)))  # drive C: for DOS file access
        try:
            program = self.microprocessor.load_program(file.exe)
        except (OSError, ProgramFormatError) as e:
            self.log(f'Cannot load {file.exe}: {e}\n')
            return
        self.program = program
        self.loadSourceMap(tab, file, program)
        self.resumeOnInput = False
        self.dos.bios.keyboard.clear()
        self.setDebugMode(True)
        self.dockRegisterView.raise_()
        self.dockScreen.raise_()
        self.log(f'Loaded {os.path.basename(file.exe)} ({program.kind}, {program.size} bytes) at '
                 f'{self.microprocessor.CS:04X}:{self.microprocessor.IP:04X}; drive C: is {self.dos.files.root}\n')
        self.reresolveWatchpoints()
        self.microprocessor.debug()
        self.disassemblyModel.dirty = True
        self.refreshDisassembly()
        self.updateHighlight()
        self.setRunControls()

    def loadSourceMap(self, tab, file, program):
        """Use the TASM listing and TLINK map next to the .EXE to follow execution in the source."""
        self.sourceMap, self.sourceTab = None, None
        try:
            sourceMap = load_source_map(file.exe, program.load_segment)
        except (OSError, ListingError) as e:
            self.log(f'No source view: {e}\n')
            return
        if sourceMap is None:
            self.log('No source view: the .LST listing or .MAP file is missing (rebuild with F7)\n')
            return
        self.sourceMap, self.sourceTab = sourceMap, tab
        listing = find_companion(file.exe, '.lst')
        if file.modified or (file.path and os.path.getmtime(file.path) > os.path.getmtime(listing)):
            self.log('Warning: the source changed since it was built; rebuild (F7) so the lines match\n')

    def reresolveWatchpoints(self):
        """After loading, recompute addresses (symbols and segment registers may have moved)."""
        for watchpoint in self.watches.items:
            try:
                watchpoint.address, _ = parse_address(watchpoint.expression, self.microprocessor, self.symbolLookup)
            except AddressError:
                pass  # keep the old address
            watchpoint.last = watchpoint.value(self.microprocessor.memory)
        self.watches.changed()
        self.refreshWatchTable()

    def executeSteps(self, count, useBreakpoints=False):
        """Run up to count instructions; reports errors, breakpoints and program exit.
        Returns False when execution should stop."""
        cpu = self.microprocessor
        try:
            for i in range(count):
                if cpu.halted:
                    break
                cs, ip = cpu.CS, cpu.IP
                cpu.step()
                if cpu.waiting_for_input:
                    return False
                if self.watches.items:
                    hits = self.watches.check()
                    if hits:
                        self.reportWatchHits(hits, cs, ip)
                        return False
                if useBreakpoints:
                    here = cpu.physical_address(cpu.CS, cpu.IP)
                    if here == self.temporaryBreakpoint:
                        self.temporaryBreakpoint = None
                        return False
                    if here in self.breakpoints:
                        self.log(f'\nBreakpoint at {cpu.CS:04X}:{cpu.IP:04X}\n')
                        return False
        except (InstructionNotImplemented, DOSServiceNotImplemented, BIOSServiceNotImplemented) as e:
            self.log(f'\nStopped at {cpu.CS:04X}:{cpu.IP:04X}: {e}\n')
            return False
        if cpu.halted:
            if cpu.exit_code is not None:
                self.log(f'\nProgram exited with code {cpu.exit_code}\n')
            else:
                self.log(f'\nCPU halted at {cpu.CS:04X}:{cpu.IP:04X}\n')
            return False
        return True

    def step(self):
        if not self.debugMode or self.isRunning():
            return
        self.executeSteps(1)
        self.refreshDisassembly()
        self.refreshWatchValues()
        self.updateHighlight()
        self.setRunControls()

    def isRunning(self):
        return self.runTimer is not None and self.runTimer.isActive()

    def run(self):
        if self.debugMode and not self.microprocessor.halted:
            self.runTimer.start(0)
            self.setRunControls()

    def runBatch(self):
        keepGoing = self.executeSteps(RUN_BATCH_STEPS, useBreakpoints=True)
        self.screen.update()  # memory callbacks are muted while running
        if not keepGoing:
            if self.microprocessor.waiting_for_input:
                self.resumeOnInput = True
                self.dockScreen.raise_()
                self.screen.setFocus()
            self.stop()

    def stop(self):
        if self.isRunning():
            self.runTimer.stop()
            self.microprocessor.notify_all()
        self.temporaryBreakpoint = None
        self.refreshDisassembly()
        self.refreshWatchValues()
        self.updateHighlight()
        self.setRunControls()

    def pause(self):
        self.resumeOnInput = False
        self.stop()

    def exitDebug(self):
        self.runTimer.stop()
        self.resumeOnInput = False
        self.setDebugMode(False)
        editor = self.sourceEditor()
        if editor is not None:
            editor.setDebugInfo(set(), None, follow=False)
        self.sourceMap, self.sourceTab = None, None
        self.updateHighlight()
        self.statusbar.clearMessage()
        self.tabUpdate()

    def about(self):
        QtWidgets.QMessageBox.about(self.mainWidget, '8086 Emulator',
                                    'Edit, assemble (JWasm, or TASM in DOSBox) and debug 8086 programs.\n\n'
                                    'User guide: docs/USER_GUIDE.pdf\n'
                                    'https://github.com/Gagandheep/8086-Simulator')

    @staticmethod
    def dialogUnsavedWarning(name):
        msg = QtWidgets.QMessageBox()
        msg.setIcon(QtWidgets.QMessageBox.Warning)
        msg.setText(f'The document "{name}" has been modified.\nDo you want to save your changes or discard them?')
        msg.setWindowTitle("Close File")
        msg.setStandardButtons(
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel)
        return msg.exec_()
