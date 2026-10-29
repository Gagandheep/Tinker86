class InstructionNotImplemented(Exception):

    def __init__(self, op_code):
        self.op_code = op_code
        message = f'opcode 0x{op_code:02X} is not implemented'
        super().__init__(message)


class WaitingForInput(Exception):
    """Raised by an interrupt service that needs a key that has not been typed yet.

    The CPU rewinds to the start of the instruction and sets
    waiting_for_input; executing it again retries the service.
    """
