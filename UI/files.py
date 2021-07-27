class File:

    COUNT = 0

    def __init__(self, path=None):
        self.__path = path
        self.__saved = path is not None
        self.modified = False
        self.build_success = False
        self.exe = None

        if path is None:
            self.data = ''
        else:
            with open(path) as file:
                self.data = file.read()

        File.COUNT += 1

        self.id = File.COUNT

    @property
    def saved(self):
        return self.__saved

    @property
    def path(self):
        return self.__path

    def save(self, data=None, path=None):
        if path is not None:
            self.__path = path
        if data is not None:
            self.data = data
        with open(self.__path, 'w') as file:
            file.write(self.data)
