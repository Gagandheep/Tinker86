from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt

from Assembler import tasm_tlink
from UI.files import File


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


# noinspection PyAttributeOutsideInit,PyPep8Naming
class UI_MainWindow:
    files = {}
    debugMode = False
    SEGMENT_VIEWS_COUNT = 4

    def __init__(self, microprocessor):
        self.microprocessor = microprocessor
        self.microprocessor.Interface.memory_callback = self.updateTableMemories
        self.microprocessor.Interface.registers_callback = self.updateRegisters
        self.microprocessor.Interface.segments_callback = self.updateSegment

    def setupUI(self, MainWindow):
        MainWindow.setObjectName("MainWindow")
        MainWindow.resize(750, 650)
        MainWindow.setDocumentMode(True)

        self.mainWidget = QtWidgets.QWidget(MainWindow)
        MainWindow.setCentralWidget(self.mainWidget)

        self.setupTabbedFilesWidget()
        self.setupDockWidgets(MainWindow)
        self.setupActions(MainWindow)
        self.setupMenuBar(MainWindow)
        self.setupToolBars(MainWindow)
        self.setupStatusBar(MainWindow)
        self.setupKeyboardShortcuts()

        self.retranslateUI(MainWindow)

        QtCore.QMetaObject.connectSlotsByName(MainWindow)

        self.newTab()

    def retranslateUI(self, MainWindow):
        _translate = QtCore.QCoreApplication.translate
        MainWindow.setWindowTitle(_translate("MainWindow", "8086 Emulator"))
        self.menuFile.setTitle(_translate("MainWindow", "&File"))
        self.menuEdit.setTitle(_translate("MainWindow", "Edit"))
        self.menuView.setTitle(_translate("MainWindow", "View"))
        self.menuAssembler.setTitle(_translate("MainWindow", "Assembler"))
        self.menuDebugger.setTitle(_translate("MainWindow", "Debugger"))
        self.menuSettings.setTitle(_translate("MainWindow", "Settings"))
        self.menuWindow.setTitle(_translate("MainWindow", "Window"))
        self.menuHelp.setTitle(_translate("MainWindow", "Help"))
        self.actionOpen.setText(_translate("MainWindow", "&Open"))
        self.actionNew.setText(_translate("MainWindow", "&New"))
        self.actionSave.setText(_translate("MainWindow", "&Save"))

    def setupTabbedFilesWidget(self):
        self.tabbedFilesWidget = QtWidgets.QTabWidget(self.mainWidget)
        self.tabbedFilesWidget.setTabsClosable(True)
        self.tabbedFilesWidget.setMovable(True)
        self.tabbedFilesWidget.currentChanged.connect(self.tabUpdate)
        self.tabbedFilesWidget.tabCloseRequested.connect(self.closeTab)
        self.gridLayoutTabbedFilesWidget = QtWidgets.QGridLayout(self.mainWidget)
        self.gridLayoutTabbedFilesWidget.addWidget(self.tabbedFilesWidget, 0, 1, 1, 1)

    def setupDockWidgets(self, MainWindow):
        self.setupDockWidgetProjectView(MainWindow)
        self.setupDockWidgetOutput(MainWindow)
        self.setupDockWidgetRegisterView(MainWindow)
        self.setupDockWidgetSegmentViews(MainWindow)

    def setupDockWidgetProjectView(self, MainWindow):
        self.dockProjectView = QtWidgets.QDockWidget(MainWindow)
        self.dockProjectView.setWindowTitle('Project')
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.dockProjectView.sizePolicy().hasHeightForWidth())
        self.dockProjectView.setSizePolicy(sizePolicy)
        self.dockProjectOverviewContents = QtWidgets.QWidget()
        self.gridLayoutDockProject = QtWidgets.QGridLayout(self.dockProjectOverviewContents)
        self.treeFilesWidget = QtWidgets.QTreeWidget(self.dockProjectOverviewContents)
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.treeFilesWidget.sizePolicy().hasHeightForWidth())
        self.treeFilesWidget.setSizePolicy(sizePolicy)
        self.treeFilesWidget.headerItem().setText(0, "1")
        self.gridLayoutDockProject.addWidget(self.treeFilesWidget, 0, 0, 1, 1)
        self.dockProjectView.setWidget(self.dockProjectOverviewContents)
        MainWindow.addDockWidget(QtCore.Qt.DockWidgetArea(1), self.dockProjectView)

    def setupDockWidgetOutput(self, MainWindow):
        self.dockOutput = QtWidgets.QDockWidget(MainWindow)
        self.dockOutput.setWindowTitle('Log')
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.dockOutput.sizePolicy().hasHeightForWidth())
        self.dockOutput.setSizePolicy(sizePolicy)
        self.dockOutput.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
        self.dockOutputContents = QtWidgets.QWidget()
        self.gridLayoutDockOutput = QtWidgets.QGridLayout(self.dockOutputContents)
        self.textOutput = QtWidgets.QTextBrowser(self.dockOutputContents)
        self.textOutput.setFont(QtGui.QFont('monospace', 10))
        self.gridLayoutDockOutput.addWidget(self.textOutput, 0, 0, 1, 1)
        self.dockOutput.setWidget(self.dockOutputContents)
        MainWindow.addDockWidget(QtCore.Qt.DockWidgetArea(8), self.dockOutput)

    def setupDockWidgetRegisterView(self, MainWindow):
        self.dockRegisterView = QtWidgets.QDockWidget(MainWindow)
        self.dockRegisterView.setWindowTitle('Registers')
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.dockRegisterView.sizePolicy().hasHeightForWidth())
        self.dockRegisterView.setSizePolicy(sizePolicy)
        self.dockRegisterView.setFeatures(QtWidgets.QDockWidget.AllDockWidgetFeatures)
        self.dockRegisterView.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
        self.dockRegisterViewContents = QtWidgets.QWidget()
        self.gridLayoutDockRegisterView = QtWidgets.QGridLayout(self.dockRegisterViewContents)
        self.tableRegisters = QtWidgets.QTableWidget(self.dockRegisterViewContents)
        self.tableRegisters.setItemDelegateForColumn(1, RegisterDelegate(self.tableRegisters))
        self.tableRegisters.setColumnCount(2)
        regCount = len(self.microprocessor.Interface.registers)
        self.tableRegisters.setRowCount(regCount)
        self.tableRegisters.setHorizontalHeaderLabels(['Register', 'Value'])
        self.tableRegisters.verticalHeader().setVisible(False)
        for i in range(regCount):
            itemName = self.microprocessor.Interface.registers[i]
            itemLabel = QtWidgets.QTableWidgetItem(itemName)
            itemLabel.setFlags(Qt.ItemIsSelectable)
            itemLabel.setForeground(Qt.white)
            self.tableRegisters.setItem(i, 0, itemLabel)
            itemValue = QtWidgets.QTableWidgetItem("0x{:04x}".format(getattr(self.microprocessor, itemName)))
            itemValue.setFont(QtGui.QFont('monospace', 10))
            self.tableRegisters.setItem(i, 1, itemValue)
        self.tableRegisters.cellChanged.connect(self.registerTableUpdate)
        self.gridLayoutDockRegisterView.addWidget(self.tableRegisters, 0, 0, 1, 1)
        self.dockRegisterView.setWidget(self.dockRegisterViewContents)
        MainWindow.addDockWidget(QtCore.Qt.DockWidgetArea(1), self.dockRegisterView)
        MainWindow.tabifyDockWidget(self.dockRegisterView, self.dockProjectView)
        self.dockRegisterView.setVisible(False)

    def updateRegisters(self):
        for i, itemName in enumerate(self.microprocessor.Interface.registers):
            item = self.tableRegisters.item(i, 1)
            item.setText("0x{:04x}".format(getattr(self.microprocessor, itemName)))

    def setupDockWidgetSegmentViews(self, MainWindow):
        self.dockWidgetSegmentViews = []
        self.dockSegmentViewContents = []
        self.gridLayoutDockSegmentViews = []
        self.tableComboBoxes = []
        self.tableMemories = []

        for i in range(self.SEGMENT_VIEWS_COUNT):
            dockSegmentView = QtWidgets.QDockWidget(MainWindow)
            dockSegmentView.setVisible(False)
            self.dockWidgetSegmentViews.append(dockSegmentView)
            dockSegmentView.setWindowTitle(f'Segment View {i + 1}')
            sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            sizePolicy.setHorizontalStretch(0)
            sizePolicy.setVerticalStretch(0)
            sizePolicy.setHeightForWidth(dockSegmentView.sizePolicy().hasHeightForWidth())
            dockSegmentView.setSizePolicy(sizePolicy)
            dockSegmentView.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
            dockSegmentViewContents = QtWidgets.QWidget()
            self.dockSegmentViewContents.append(dockSegmentViewContents)
            gridLayoutDockSegmentView = QtWidgets.QGridLayout(dockSegmentViewContents)
            self.gridLayoutDockSegmentViews.append(gridLayoutDockSegmentView)
            labelSegment = QtWidgets.QLabel(dockSegmentViewContents)
            labelSegment.setText('Segment:')
            gridLayoutDockSegmentView.addWidget(labelSegment, 0, 0, 1, 1)
            combobox = QtWidgets.QComboBox(dockSegmentViewContents)
            self.tableComboBoxes.append(combobox)
            combobox.addItems(self.microprocessor.Interface.segments.keys())
            gridLayoutDockSegmentView.addWidget(combobox, 0, 1, 1, 1)
            tableMemory = self.createTableMemory(dockSegmentViewContents)
            combobox.currentTextChanged.connect(self.comboboxUpdateFunctionGenerator(i))
            gridLayoutDockSegmentView.addWidget(tableMemory, 1, 0, 1, 2)
            dockSegmentView.setWidget(dockSegmentViewContents)
            MainWindow.addDockWidget(QtCore.Qt.DockWidgetArea(8), dockSegmentView)
        for i in range(self.SEGMENT_VIEWS_COUNT):
            MainWindow.tabifyDockWidget(self.dockOutput, self.dockWidgetSegmentViews[i])

    def comboboxUpdateFunctionGenerator(self, combobox):
        def func(text):
            segment = self.microprocessor.Interface.segments[text]
            tableMemory = self.tableMemories[combobox]
            segmentStart = getattr(self.microprocessor, segment) << 4
            colStart = segmentStart & 0xF
            rowStart = segmentStart >> 4
            tableMemory.blockSignals(True)
            tableMemory.setHorizontalHeaderLabels([hex(i % 16) for i in range(colStart, colStart + 16)])
            tableMemory.setVerticalHeaderLabels([hex(i) for i in range(rowStart, rowStart + 2 ** 16 // 16)])
            for i, value in enumerate(self.microprocessor.memory[segmentStart:segmentStart + 2 ** 16]):
                col = i % 16
                row = i // 16
                item = tableMemory.item(row, col)
                item.setText("0x{:02x}".format(value))
            tableMemory.blockSignals(False)
        return func

    def updateTableMemories(self, addresses=None):
        for i in range(self.SEGMENT_VIEWS_COUNT):
            combobox = self.tableComboBoxes[i]
            value = combobox.currentText()
            segment = self.microprocessor.Interface.segments[value]
            tableMemory = self.tableMemories[i]
            segmentStart = getattr(self.microprocessor, segment) << 4
            colStart = segmentStart & 0xF
            rowStart = segmentStart >> 4
            tableMemory.blockSignals(True)
            tableMemory.setHorizontalHeaderLabels([hex(j % 16) for j in range(colStart, colStart + 16)])
            tableMemory.setVerticalHeaderLabels([hex(j) for j in range(rowStart, rowStart + 2 ** 16 // 16)])
            for j, value in enumerate(self.microprocessor.memory[segmentStart:segmentStart + 2 ** 16]):
                col = j % 16
                row = j // 16
                item = tableMemory.item(row, col)
                item.setText("0x{:02x}".format(value))
            tableMemory.blockSignals(False)

    def createTableMemory(self, parent):
        tableMemory = QtWidgets.QTableWidget(parent)
        self.tableMemories.append(tableMemory)
        tableMemory.setColumnCount(16)
        tableMemory.setRowCount(2 ** 16 // 16)
        tableMemory.setItemDelegate(MemoryDelegate(tableMemory))
        tableMemory.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        segmentStart = getattr(self.microprocessor, 'CS') << 4
        colStart = segmentStart & 0xF
        rowStart = segmentStart >> 4
        tableMemory.setHorizontalHeaderLabels([hex(i % 16) for i in range(colStart, colStart + 16)])
        tableMemory.setVerticalHeaderLabels([hex(i) for i in range(rowStart, rowStart + 2**16//16)])
        for i, value in enumerate(self.microprocessor.memory[segmentStart:segmentStart + 2 ** 16]):
            col = i % 16
            row = i // 16
            item = QtWidgets.QTableWidgetItem("0x{:02x}".format(value))
            tableMemory.setItem(row, col, item)
        tableMemory.cellChanged.connect(self.segmentTableUpdateFunctionGenerator(tableMemory))
        return tableMemory

    def registerTableUpdate(self, row):
        value = self.tableRegisters.item(row, 1).text()
        negative = value[0] == '-'
        value = value.lstrip('+-')
        if len(value) > 1 and value[1] == 'b':
            base = 2
        elif len(value) > 1 and value[1] == 'x':
            base = 16
        else:
            base = 10
        value = int(value, base)
        value = -value if negative else value
        setattr(self.microprocessor, self.microprocessor.Interface.registers[row], value)
        item = self.tableRegisters.item(row, 1)
        item.setText("0x{:04x}".format(getattr(self.microprocessor, self.microprocessor.Interface.registers[row])))

    def segmentTableUpdateFunctionGenerator(self, table: QtWidgets.QTableWidget):
        def func(row, col):
            value = table.item(row, col).text()
            negative = value[0] == '-'
            value = value.lstrip('+-')
            if len(value) > 1 and value[1] == 'b':
                base = 2
            elif len(value) > 1 and value[1] == 'x':
                base = 16
            else:
                base = 10
            value = int(value, base)
            value = -value if negative else value
            r = int(table.verticalHeaderItem(row).text(), 16)
            c = int(table.horizontalHeaderItem(col).text(), 16)
            i = (r << 4) + c
            self.microprocessor.memory[i] = value
            item = table.item(row, col)
            item.setText("0x{:02x}".format(self.microprocessor.memory[i]))
        return func

    def updateSegment(self, segment, segment_value):
        segment_start = segment_value << 4
        for dockSegmentViewContent in self.dockSegmentViewContents:
            combobox, *_ = dockSegmentViewContent.findChildren(QtWidgets.QComboBox)
            if combobox.currentText() == segment:
                print(segment)
                colStart = segment_start & 0xF
                rowStart = segment_start >> 4
                tableMemory, *_ = dockSegmentViewContent.findChildren(QtWidgets.QTableWidget)
                # tableMemory = QtWidgets.QTableWidget()
                col = tableMemory.horizontalHeaderItem(0).text()
                row = tableMemory.verticalHeaderItem(0).text()
                if col == hex(colStart) and row == hex(rowStart):
                    print('lel')
                    continue
                tableMemory.blockSignals(True)
                tableMemory.setHorizontalHeaderLabels([hex(i % 16) for i in range(colStart, colStart + 16)])
                tableMemory.setVerticalHeaderLabels([hex(i) for i in range(rowStart, rowStart + 2 ** 16 // 16)])
                for i, value in enumerate(self.microprocessor.memory[segment_start:segment_start + 2 ** 16]):
                    col = i % 16
                    row = i // 16
                    item = tableMemory.item(row, col)
                    item.setText("0x{:02x}".format(value))
                tableMemory.blockSignals(False)

    def setupActions(self, MainWindow):
        self.actionOpen = QtWidgets.QAction(QtGui.QIcon(QtGui.QIcon.fromTheme('document-open')), "&Open", MainWindow)
        self.actionNew = QtWidgets.QAction(QtGui.QIcon(QtGui.QIcon.fromTheme('document-new')), "&New", MainWindow)
        self.actionSave = QtWidgets.QAction(QtGui.QIcon(QtGui.QIcon.fromTheme('document-save')), "&Save", MainWindow)
        self.actionBuild = QtWidgets.QAction(QtGui.QIcon(QtGui.QIcon.fromTheme('run-build')), "Build", MainWindow)
        self.actionDebug = QtWidgets.QAction(QtGui.QIcon(QtGui.QIcon.fromTheme('debug-run')), "Debug", MainWindow)
        self.actionExitDebug = QtWidgets.QAction(QtGui.QIcon(QtGui.QIcon.fromTheme('stop')), "Exit Debug", MainWindow)
        self.actionStep = QtWidgets.QAction(QtGui.QIcon(QtGui.QIcon.fromTheme('go-next')), "Step", MainWindow)

        self.actionDebug.setEnabled(False)
        self.actionExitDebug.setVisible(False)
        self.actionStep.setVisible(False)

        self.actionOpen.triggered.connect(self.fileOpen)
        self.actionNew.triggered.connect(self.newTab)
        self.actionSave.triggered.connect(self.fileSave)
        self.actionBuild.triggered.connect(self.build)
        self.actionDebug.triggered.connect(self.debug)
        self.actionExitDebug.triggered.connect(self.exitDebug)
        self.actionStep.triggered.connect(self.microprocessor.step)

    def setupMenuBar(self, MainWindow):
        self.menubar = QtWidgets.QMenuBar(MainWindow)
        self.menubar.setGeometry(QtCore.QRect(0, 0, 747, 30))

        self.menuFile = QtWidgets.QMenu(self.menubar)
        self.menuFile.addAction(self.actionOpen)
        self.menuFile.addAction(self.actionNew)
        self.menuFile.addAction(self.actionSave)
        self.menuEdit = QtWidgets.QMenu(self.menubar)
        self.menuView = QtWidgets.QMenu(self.menubar)
        self.menuAssembler = QtWidgets.QMenu(self.menubar)
        self.menuDebugger = QtWidgets.QMenu(self.menubar)
        self.menuSettings = QtWidgets.QMenu(self.menubar)
        self.menuWindow = QtWidgets.QMenu(self.menubar)
        self.menuHelp = QtWidgets.QMenu(self.menubar)

        self.menubar.addAction(self.menuFile.menuAction())
        self.menubar.addAction(self.menuEdit.menuAction())
        self.menubar.addAction(self.menuView.menuAction())
        self.menubar.addAction(self.menuAssembler.menuAction())
        self.menubar.addAction(self.menuDebugger.menuAction())
        self.menubar.addAction(self.menuSettings.menuAction())
        self.menubar.addAction(self.menuWindow.menuAction())
        self.menubar.addAction(self.menuHelp.menuAction())

        MainWindow.setMenuBar(self.menubar)

    def setupToolBars(self, MainWindow):
        self.toolbarFile = MainWindow.addToolBar("File")
        self.toolbarFile.addAction(self.actionNew)
        self.toolbarFile.addAction(self.actionOpen)
        self.toolbarFile.addAction(self.actionSave)

        self.toolbarDebug = MainWindow.addToolBar("Debug")
        self.toolbarDebug.addAction(self.actionBuild)
        self.toolbarDebug.addAction(self.actionDebug)
        self.toolbarDebug.addAction(self.actionExitDebug)
        self.toolbarDebug.addAction(self.actionStep)

    def setupStatusBar(self, MainWindow):
        self.statusbar = QtWidgets.QStatusBar(MainWindow)
        MainWindow.setStatusBar(self.statusbar)

    def setupKeyboardShortcuts(self):
        self.shortcutNew = QtWidgets.QShortcut(QtGui.QKeySequence(QtGui.QKeySequence.New), self.mainWidget)
        self.shortcutNew.activated.connect(self.newTab)

        self.shortcutSave = QtWidgets.QShortcut(QtGui.QKeySequence(QtGui.QKeySequence.Save), self.mainWidget)
        self.shortcutSave.activated.connect(self.fileSave)

        self.shortcutOpen = QtWidgets.QShortcut(QtGui.QKeySequence(QtGui.QKeySequence.Open), self.mainWidget)
        self.shortcutOpen.activated.connect(self.fileOpen)

        self.shortcutBuild = QtWidgets.QShortcut(QtGui.QKeySequence('F7'), self.mainWidget)
        self.shortcutBuild.activated.connect(self.build)

        self.shortcutDebug = QtWidgets.QShortcut(QtGui.QKeySequence('F8'), self.mainWidget)
        self.shortcutDebug.activated.connect(self.debug)
        self.shortcutDebug.setEnabled(False)

    def newTab(self, path=None):
        if type(path) is bool:
            path = None
        file = File(path)
        _translate = QtCore.QCoreApplication.translate
        tab = QtWidgets.QWidget()
        tab.setObjectName(f"tab{self.tabbedFilesWidget.count()}")
        verticalLayout = QtWidgets.QVBoxLayout(tab)
        verticalLayout.setObjectName("verticalLayout")
        plainTextEdit = QtWidgets.QPlainTextEdit(tab)
        plainTextEdit.setObjectName("plainTextEdit")
        plainTextEdit.setFont(QtGui.QFont('monospace', 10))
        plainTextEdit.textChanged.connect(self.textEdited)
        plainTextEdit.setPlainText(file.data)
        verticalLayout.addWidget(plainTextEdit)
        self.tabbedFilesWidget.addTab(tab, "")
        self.tabbedFilesWidget.setTabText(
            self.tabbedFilesWidget.indexOf(tab),
            _translate("MainWindow", "New File" if path is None else path.rsplit('/', maxsplit=1)[-1]))
        self.tabbedFilesWidget.setCurrentIndex(self.tabbedFilesWidget.indexOf(tab))
        self.files[tab] = file
        plainTextEdit.setFocus()

    def closeTab(self, index):
        name = self.tabbedFilesWidget.tabText(self.tabbedFilesWidget.currentIndex())
        if self.tabbedFilesWidget.tabText(self.tabbedFilesWidget.currentIndex())[-1] == '*':
            ret = self.dialogUnsavedWarning(name[:-1])
            if ret == QtWidgets.QMessageBox.Save:
                self.fileSave(index)
            elif ret != QtWidgets.QMessageBox.Discard:
                return
        self.tabbedFilesWidget.removeTab(index)

    def closeAllTabs(self):
        while self.tabbedFilesWidget.count() > 0:
            self.closeTab(0)

    def tabUpdate(self):
        if self.tabbedFilesWidget.count() > 0:
            self.actionSave.setEnabled(True)
            self.actionBuild.setEnabled(True)
        else:
            self.actionSave.setEnabled(False)
            self.actionBuild.setEnabled(False)

    def textEdited(self):
        tab = self.tabbedFilesWidget.widget(self.tabbedFilesWidget.currentIndex())
        if tab is not None:
            file = self.files[tab]
            text = tab.findChild(QtWidgets.QPlainTextEdit).toPlainText()
            if not file.modified and text != file.data:
                file.modified = True
                _translate = QtCore.QCoreApplication.translate
                self.tabbedFilesWidget.setTabText(
                    self.tabbedFilesWidget.currentIndex(),
                    _translate("MainWindow",
                               f"{self.tabbedFilesWidget.tabText(self.tabbedFilesWidget.currentIndex())}*"))

    def fileSave(self, index=None):
        tab = self.tabbedFilesWidget.widget(self.tabbedFilesWidget.currentIndex() if index is None else index)
        if tab is not None:
            file = self.files[tab]
            file.modified = False
            text = tab.findChild(QtWidgets.QPlainTextEdit).toPlainText()
            if file.saved:
                path = file.path
            else:
                path, _ = QtWidgets.QFileDialog.getSaveFileName(self.mainWidget, 'Save File')
            if path != '':
                file.save(data=text, path=path)
                _translate = QtCore.QCoreApplication.translate
                self.tabbedFilesWidget.setTabText(
                    self.tabbedFilesWidget.currentIndex(),
                    _translate("MainWindow",
                               self.tabbedFilesWidget.tabText(self.tabbedFilesWidget.currentIndex()).rstrip('*')))
                return True
        return False

    def fileOpen(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self.mainWidget, 'Open Files')
        opened = [file.path for file in self.files.values()]
        for path in paths:
            try:
                idx = opened.index(path)
                tab = list(self.files.keys())[idx]
                self.tabbedFilesWidget.setCurrentIndex(self.tabbedFilesWidget.indexOf(tab))
            except ValueError:
                self.newTab(path)

    def build(self):
        self.textOutput.clear()
        tab = self.tabbedFilesWidget.widget(self.tabbedFilesWidget.currentIndex())
        file = self.files[tab]
        success = False
        if self.fileSave():
            if self.dockOutput.isHidden():
                self.dockOutput.setVisible(True)
            success, log = tasm_tlink(file.path)
            log = log.split('\n', 2)[-1]
            self.textOutput.setText(log)
            self.textOutput.moveCursor(QtGui.QTextCursor.End)
        file.build_success = success
        file.exe = f'{file.path.rsplit(".", 1)[0]}.EXE' if success else None
        self.actionDebug.setEnabled(success)
        self.shortcutDebug.setEnabled(True)
        return success

    def debug(self):
        self.debugMode = True
        self.toolbarFile.setVisible(False)
        self.actionBuild.setVisible(False)
        self.actionDebug.setVisible(False)
        self.actionExitDebug.setVisible(True)
        self.actionStep.setVisible(True)
        self.dockRegisterView.setVisible(True)
        self.dockRegisterView.raise_()
        for i, dockSegmentView in enumerate(self.dockWidgetSegmentViews):
            dockSegmentView.setVisible(True)
            if i == 0:
                dockSegmentView.raise_()
        self.microprocessor.reset()
        tab = self.tabbedFilesWidget.widget(self.tabbedFilesWidget.currentIndex())
        file = self.files[tab]
        self.microprocessor.load_hex_from_exe(file.exe)
        self.microprocessor.debug()

    def exitDebug(self):
        self.debugMode = False
        self.toolbarFile.setVisible(True)
        self.actionBuild.setVisible(True)
        self.actionDebug.setVisible(True)
        self.actionExitDebug.setVisible(False)
        self.actionStep.setVisible(False)
        self.dockRegisterView.setVisible(False)
        for dockSegmentView in self.dockWidgetSegmentViews:
            dockSegmentView.setVisible(False)

    @staticmethod
    def dialogUnsavedWarning(name):
        msg = QtWidgets.QMessageBox()
        msg.setIcon(QtWidgets.QMessageBox.Warning)
        msg.setText(f'The document "{name}" has been modified.\nDo you want to save your changes or discard them?')
        msg.setWindowTitle("Close File")
        msg.setStandardButtons(
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel)
        return msg.exec_()
