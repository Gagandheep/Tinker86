class InstructionNotImplemented(Exception):

    def __init__(self, op_code):
        self.op_code = op_code
        message = f'{op_code} not implemented in class!'
        super().__sizeof__(message)
