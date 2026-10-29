import os

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt

from Assembler import AssemblerError, tasm_tlink
from Microprocessor import InstructionNotImplemented
from Microprocessor.disassembler import data_byte, disassemble_memory
from Microprocessor.dos import DOS, DOSServiceNotImplemented
from Microprocessor.program import ProgramFormatError
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

    def __init__(self, microprocessor, parent=None):
        super(MemoryModel, self).__init__(parent)
        self.microprocessor = microprocessor
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
        if role == Qt.BackgroundRole and self.address(index) == self.highlight:
            return QtGui.QBrush(QtGui.QColor(255, 210, 90))
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


# noinspection PyAttributeOutsideInit,PyPep8Naming
class UI_MainWindow:
    debugMode = False
    SEGMENT_VIEWS_COUNT = 4

    def __init__(self, microprocessor):
        self.files = {}
        self.microprocessor = microprocessor
        self.microprocessor.Interface.memory_callback = self.updateTableMemories
        self.microprocessor.Interface.registers_callback = self.updateRegisters
        self.microprocessor.Interface.segments_callback = self.updateSegment
        self.dos = DOS(microprocessor, output=self.appendProgramOutput, input=self.promptProgramInput)
        self.runTimer = None
        self.program = None
        self.breakpoints = set()  # physical addresses
        self.temporaryBreakpoint = None  # used by Run to Cursor

    def setupUI(self, MainWindow):
        MainWindow.setObjectName("MainWindow")
        MainWindow.resize(900, 700)
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
        self.gridLayoutTabbedFilesWidget = QtWidgets.QGridLayout(self.mainWidget)
        self.gridLayoutTabbedFilesWidget.addWidget(self.tabbedFilesWidget, 0, 1, 1, 1)

    def setupDockWidgets(self, MainWindow):
        self.setupDockWidgetOutput(MainWindow)
        self.setupDockWidgetRegisterView(MainWindow)
        self.setupDockWidgetSegmentViews(MainWindow)
        self.setupDockWidgetDisassembly(MainWindow)

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
            model = MemoryModel(self.microprocessor, dockSegmentViewContents)
            self.memoryModels.append(model)
            tableMemory = QtWidgets.QTableView(dockSegmentViewContents)
            tableMemory.setModel(model)
            tableMemory.setItemDelegate(MemoryDelegate(tableMemory))
            tableMemory.setFont(QtGui.QFont('monospace', 9))
            tableMemory.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
            tableMemory.verticalHeader().setDefaultSectionSize(20)
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

    def selectedDisassemblyRow(self):
        index = self.tableDisassembly.currentIndex()
        return index.row() if index.isValid() else None

    def toggleBreakpoint(self, row=None):
        if type(row) is bool or row is None:
            row = self.selectedDisassemblyRow()
        if row is None or not 0 <= row < len(self.disassemblyModel.rows):
            return
        offset = self.disassemblyModel.rows[row].offset
        address = self.disassemblyModel.physical(offset)
        if address in self.breakpoints:
            self.breakpoints.remove(address)
        else:
            self.breakpoints.add(address)
        self.disassemblyModel.offsetChanged(offset)

    def clearBreakpoints(self):
        self.breakpoints.clear()
        self.disassemblyModel.beginResetModel()
        self.disassemblyModel.endResetModel()

    def runToCursor(self):
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
            state = 'halted' if cpu.halted else 'running' if self.isRunning() else 'paused'
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
        self.actionStop = action('media-playback-pause', 'Pause', self.stop, 'Shift+F5')
        self.actionExitDebug = action('process-stop', 'Exit Debug', self.exitDebug)
        self.actionToggleBreakpoint = action('media-record', 'Toggle Breakpoint', self.toggleBreakpoint, 'F9')
        self.actionClearBreakpoints = action('edit-clear', 'Clear Breakpoints', self.clearBreakpoints)
        self.actionRunToCursor = action('go-jump', 'Run to Cursor', self.runToCursor, 'F4')
        self.actionAbout = action('help-about', '&About', self.about)

        self.actionDebug.setEnabled(False)
        for debugAction in self.debugActions():
            debugAction.setVisible(False)

    def debugActions(self):
        return (self.actionStep, self.actionRun, self.actionStop, self.actionRunToCursor,
                self.actionToggleBreakpoint, self.actionClearBreakpoints, self.actionExitDebug)

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
        for dockSegmentView in self.dockWidgetSegmentViews:
            self.menuView.addAction(dockSegmentView.toggleViewAction())

        self.menuAssembler = self.menubar.addMenu('&Assembler')
        self.menuAssembler.addAction(self.actionBuild)

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
        plainTextEdit = QtWidgets.QPlainTextEdit(tab)
        plainTextEdit.setFont(QtGui.QFont('monospace', 10))
        plainTextEdit.setPlainText(file.data)
        plainTextEdit.textChanged.connect(lambda: self.textEdited(tab))
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

    def promptProgramInput(self):
        text, ok = QtWidgets.QInputDialog.getText(self.mainWidget, 'Program input', 'The program is waiting for input:')
        return text if ok else None

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
                success, log, exe = tasm_tlink(file.path)
            except AssemblerError as e:
                log = f'Build failed: {e}'
            self.textOutput.setText(log)
            self.textOutput.moveCursor(QtGui.QTextCursor.End)
        file.build_success = success
        file.exe = exe
        self.actionDebug.setEnabled(success)
        return success

    def setDebugMode(self, enabled):
        self.debugMode = enabled
        self.toolbarFile.setVisible(not enabled)
        self.actionBuild.setVisible(not enabled)
        self.actionDebug.setVisible(not enabled)
        for debugAction in self.debugActions():
            debugAction.setVisible(enabled)
        self.dockRegisterView.setVisible(enabled)
        self.dockDisassembly.setVisible(enabled)
        if enabled:
            self.mainWindow.resizeDocks([self.dockDisassembly], [480], Qt.Horizontal)
        for dockSegmentView in self.dockWidgetSegmentViews:
            dockSegmentView.setVisible(enabled)
        for tab in self.files:
            tab.findChild(QtWidgets.QPlainTextEdit).setReadOnly(enabled)
        self.setRunControls()

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
        try:
            program = self.microprocessor.load_program(file.exe)
        except (OSError, ProgramFormatError) as e:
            self.log(f'Cannot load {file.exe}: {e}\n')
            return
        self.program = program
        self.setDebugMode(True)
        self.dockRegisterView.raise_()
        self.dockOutput.raise_()
        self.log(f'Loaded {os.path.basename(file.exe)} ({program.kind}, {program.size} bytes) at '
                 f'{self.microprocessor.CS:04X}:{self.microprocessor.IP:04X}\n')
        self.microprocessor.debug()
        self.disassemblyModel.dirty = True
        self.refreshDisassembly()
        self.updateHighlight()
        self.setRunControls()

    def executeSteps(self, count, useBreakpoints=False):
        """Run up to count instructions; reports errors, breakpoints and program exit.
        Returns False when execution should stop."""
        cpu = self.microprocessor
        try:
            for i in range(count):
                if cpu.halted:
                    break
                cpu.step()
                if useBreakpoints:
                    here = cpu.physical_address(cpu.CS, cpu.IP)
                    if here == self.temporaryBreakpoint:
                        self.temporaryBreakpoint = None
                        return False
                    if here in self.breakpoints:
                        self.log(f'\nBreakpoint at {cpu.CS:04X}:{cpu.IP:04X}\n')
                        return False
        except (InstructionNotImplemented, DOSServiceNotImplemented) as e:
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
        self.updateHighlight()
        self.setRunControls()

    def isRunning(self):
        return self.runTimer is not None and self.runTimer.isActive()

    def run(self):
        if self.debugMode and not self.microprocessor.halted:
            self.runTimer.start(0)
            self.setRunControls()

    def runBatch(self):
        if not self.executeSteps(RUN_BATCH_STEPS, useBreakpoints=True):
            self.stop()

    def stop(self):
        if self.isRunning():
            self.runTimer.stop()
            self.microprocessor.notify_all()
        self.temporaryBreakpoint = None
        self.refreshDisassembly()
        self.updateHighlight()
        self.setRunControls()

    def exitDebug(self):
        self.runTimer.stop()
        self.setDebugMode(False)
        self.updateHighlight()
        self.statusbar.clearMessage()
        self.tabUpdate()

    def about(self):
        QtWidgets.QMessageBox.about(self.mainWidget, '8086 Emulator',
                                    'Edit, assemble (TASM in DOSBox) and step through 8086 programs.')

    @staticmethod
    def dialogUnsavedWarning(name):
        msg = QtWidgets.QMessageBox()
        msg.setIcon(QtWidgets.QMessageBox.Warning)
        msg.setText(f'The document "{name}" has been modified.\nDo you want to save your changes or discard them?')
        msg.setWindowTitle("Close File")
        msg.setStandardButtons(
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel)
        return msg.exec_()
