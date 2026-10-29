from PyQt5 import QtWidgets
from UI.ui import UI_MainWindow
import sys
from Microprocessor import Microprocessor


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


def main():
    app = QtWidgets.QApplication(sys.argv)
    application = ApplicationWindow()
    application.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
