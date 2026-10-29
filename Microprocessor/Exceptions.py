class InstructionNotImplemented(Exception):

    def __init__(self, op_code):
        self.op_code = op_code
        message = f'opcode 0x{op_code:02X} is not implemented'
        super().__init__(message)
