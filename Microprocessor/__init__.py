import numpy as np
from numpy import int8, int16, uint8, uint16, uint32
from .Exceptions import *

np.seterr(all="ignore")


def parity(n):
    ret = 0
    while n:
        ret = ~ret
        n = n & (n - 1)
    return not ret


class Instruction:

    def __init__(self, op_code, function, multi_byte=True, special_byte_2=False):
        self.__op_code = op_code
        self.__function = function
        self.__multi_byte = multi_byte
        self.__special_byte_2 = special_byte_2

    @property
    def op_code(self):
        return self.__op_code

    @property
    def function(self):
        return self.__function

    @property
    def multi_byte(self):
        return self.__multi_byte

    @property
    def special_byte_2(self):
        return self.__special_byte_2


# noinspection PyUnusedLocal,PyAttributeOutsideInit
class Microprocessor:

    class Interface:
        segments = {'Code': 'CS', 'Data': 'DS', 'Stack': 'SS', 'Extra': 'ES'}
        registers = ['AX', 'BX', 'CX', 'DX', 'IP', 'SP', 'BP', 'SI', 'DI', 'CS', 'DS', 'SS', 'ES', 'FLAG']

        memory_callback = lambda: None
        registers_callback = lambda: None
        segments_callback = lambda *x: None

    reg_table = {
        False: {0: 'AL', 1: 'CL', 2: 'DL', 3: 'BL', 4: 'AH', 5: 'CH', 6: 'DH', 7: 'BH'},
        True: {0: 'AX', 1: 'CX', 2: 'DX', 3: 'BX', 4: 'SP', 5: 'BP', 6: 'SI', 7: 'DI'}
    }
    rm_table = {0: ['BX', 'SI'], 1: ['BX', 'DI'], 2: ['BP', 'SI'], 3: ['BP', 'DI'],
                4: ['SI'], 5: ['DI'], 6: ['BP'], 7: ['BX']}
    seg_table = {0: 'ES', 1: 'CS', 2: 'SS', 3: 'DS'}
    __seg_override = -1

    instruction_length = 0
    __target_function = None
    __target_kwargs = None
    __halt = False

    def __init__(self):

        self.reset()

        self.instruction_data = {
            0x00: Instruction(0x00, self.GRP0_R_RM),
            0x01: Instruction(0x01, self.GRP0_R_RM),
            0x02: Instruction(0x02, self.GRP0_R_RM),
            0x03: Instruction(0x03, self.GRP0_R_RM),
            0x04: Instruction(0x04, self.GRP0_I_A, special_byte_2=True),
            0x05: Instruction(0x05, self.GRP0_I_A, special_byte_2=True),
            0x06: Instruction(0x06, self.PUSH_S),
            0x07: Instruction(0x07, self.POP_S),
            0x08: Instruction(0x08, self.GRP0_R_RM),
            0x09: Instruction(0x09, self.GRP0_R_RM),
            0x0A: Instruction(0x0A, self.GRP0_R_RM),
            0x0B: Instruction(0x0B, self.GRP0_R_RM),
            0x0C: Instruction(0x0C, self.GRP0_I_A, special_byte_2=True),
            0x0D: Instruction(0x0D, self.GRP0_I_A, special_byte_2=True),
            0x0E: Instruction(0x0E, self.PUSH_S),
            0x0F: Instruction(0x0F, self.POP_S),
            0x10: Instruction(0x10, self.GRP0_R_RM),
            0x11: Instruction(0x11, self.GRP0_R_RM),
            0x12: Instruction(0x12, self.GRP0_R_RM),
            0x13: Instruction(0x13, self.GRP0_R_RM),
            0x14: Instruction(0x14, self.GRP0_I_A, special_byte_2=True),
            0x15: Instruction(0x15, self.GRP0_I_A, special_byte_2=True),
            0x16: Instruction(0x16, self.PUSH_S),
            0x17: Instruction(0x17, self.POP_S),
            0x18: Instruction(0x18, self.GRP0_R_RM),
            0x19: Instruction(0x19, self.GRP0_R_RM),
            0x1A: Instruction(0x1A, self.GRP0_R_RM),
            0x1B: Instruction(0x1B, self.GRP0_R_RM),
            0x1C: Instruction(0x1C, self.GRP0_I_A, special_byte_2=True),
            0x1D: Instruction(0x1D, self.GRP0_I_A, special_byte_2=True),
            0x1E: Instruction(0x1E, self.PUSH_S),
            0x1F: Instruction(0x1F, self.POP_S),
            0x20: Instruction(0x20, self.GRP0_R_RM),
            0x21: Instruction(0x21, self.GRP0_R_RM),
            0x22: Instruction(0x22, self.GRP0_R_RM),
            0x23: Instruction(0x23, self.GRP0_R_RM),
            0x24: Instruction(0x24, self.GRP0_I_A, special_byte_2=True),
            0x25: Instruction(0x25, self.GRP0_I_A, special_byte_2=True),
            0x26: Instruction(0x26, self.SO),
            0x27: Instruction(0x27, self.DAA, multi_byte=False),
            0x28: Instruction(0x28, self.GRP0_R_RM),
            0x29: Instruction(0x29, self.GRP0_R_RM),
            0x2A: Instruction(0x2A, self.GRP0_R_RM),
            0x2B: Instruction(0x2B, self.GRP0_R_RM),
            0x2C: Instruction(0x2C, self.GRP0_I_A, special_byte_2=True),
            0x2D: Instruction(0x2D, self.GRP0_I_A, special_byte_2=True),
            0x2E: Instruction(0x2E, self.SO),
            0x2F: Instruction(0x2F, self.DAS, multi_byte=False),
            0x30: Instruction(0x30, self.GRP0_R_RM),
            0x31: Instruction(0x31, self.GRP0_R_RM),
            0x32: Instruction(0x32, self.GRP0_R_RM),
            0x33: Instruction(0x33, self.GRP0_R_RM),
            0x34: Instruction(0x34, self.GRP0_I_A, special_byte_2=True),
            0x35: Instruction(0x35, self.GRP0_I_A, special_byte_2=True),
            0x36: Instruction(0x36, self.SO),
            0x37: Instruction(0x37, self.AAA, multi_byte=False),
            0x38: Instruction(0x38, self.GRP0_R_RM),
            0x39: Instruction(0x39, self.GRP0_R_RM),
            0x3A: Instruction(0x3A, self.GRP0_R_RM),
            0x3B: Instruction(0x3B, self.GRP0_R_RM),
            0x3C: Instruction(0x3C, self.GRP0_I_A, special_byte_2=True),
            0x3D: Instruction(0x3D, self.GRP0_I_A, special_byte_2=True),
            0x3E: Instruction(0x3E, self.SO),
            0x3F: Instruction(0x3F, self.AAS, multi_byte=False),
            0x40: Instruction(0x40, self.INC_R, multi_byte=False),
            0x41: Instruction(0x41, self.INC_R, multi_byte=False),
            0x42: Instruction(0x42, self.INC_R, multi_byte=False),
            0x43: Instruction(0x43, self.INC_R, multi_byte=False),
            0x44: Instruction(0x44, self.INC_R, multi_byte=False),
            0x45: Instruction(0x45, self.INC_R, multi_byte=False),
            0x46: Instruction(0x46, self.INC_R, multi_byte=False),
            0x47: Instruction(0x47, self.INC_R, multi_byte=False),
            0x48: Instruction(0x48, self.DEC_R, multi_byte=False),
            0x49: Instruction(0x49, self.DEC_R, multi_byte=False),
            0x4A: Instruction(0x4A, self.DEC_R, multi_byte=False),
            0x4B: Instruction(0x4B, self.DEC_R, multi_byte=False),
            0x4C: Instruction(0x4C, self.DEC_R, multi_byte=False),
            0x4D: Instruction(0x4D, self.DEC_R, multi_byte=False),
            0x4E: Instruction(0x4E, self.DEC_R, multi_byte=False),
            0x4F: Instruction(0x4F, self.DEC_R, multi_byte=False),
            0x50: Instruction(0x50, self.PUSH_R),
            0x51: Instruction(0x51, self.PUSH_R),
            0x52: Instruction(0x52, self.PUSH_R),
            0x53: Instruction(0x53, self.PUSH_R),
            0x54: Instruction(0x54, self.PUSH_R),
            0x55: Instruction(0x55, self.PUSH_R),
            0x56: Instruction(0x56, self.PUSH_R),
            0x57: Instruction(0x57, self.PUSH_R),
            0x58: Instruction(0x58, self.POP_R),
            0x59: Instruction(0x59, self.POP_R),
            0x5A: Instruction(0x5A, self.POP_R),
            0x5B: Instruction(0x5B, self.POP_R),
            0x5C: Instruction(0x5C, self.POP_R),
            0x5D: Instruction(0x5D, self.POP_R),
            0x5E: Instruction(0x5E, self.POP_R),
            0x5F: Instruction(0x5F, self.POP_R),
            0x70: Instruction(0x70, self.JO, special_byte_2=True),
            0x71: Instruction(0x71, self.JNO, special_byte_2=True),
            0x72: Instruction(0x72, self.JB, special_byte_2=True),
            0x73: Instruction(0x73, self.JNB, special_byte_2=True),
            0x74: Instruction(0x74, self.JZ, special_byte_2=True),
            0x75: Instruction(0x75, self.JNZ, special_byte_2=True),
            0x76: Instruction(0x76, self.JBE, special_byte_2=True),
            0x77: Instruction(0x77, self.JA, special_byte_2=True),
            0x78: Instruction(0x78, self.JS, special_byte_2=True),
            0x79: Instruction(0x79, self.JNS, special_byte_2=True),
            0x7A: Instruction(0x7A, self.JPE, special_byte_2=True),
            0x7B: Instruction(0x7B, self.JPO, special_byte_2=True),
            0x7C: Instruction(0x7C, self.JL, special_byte_2=True),
            0x7D: Instruction(0x7D, self.JGE, special_byte_2=True),
            0x7E: Instruction(0x7E, self.JLE, special_byte_2=True),
            0x7F: Instruction(0x7F, self.JG, special_byte_2=True),
            0x80: Instruction(0x80, self.GRP1),  # ADD_ADC_SUB_SSB_CMP_AND_OR_XOR_I_RM
            0x81: Instruction(0x81, self.GRP1),  # ADD_ADC_SUB_SSB_CMP_AND_OR_XOR_I_RM
            0x82: Instruction(0x82, self.GRP1),  # ADD_ADC_SUB_SSB_CMP_I_RM
            0x83: Instruction(0x83, self.GRP1),  # ADD_ADC_SUB_SSB_CMP_I_RM
            0x84: Instruction(0x84, self.TEST_R_RM),
            0x85: Instruction(0x85, self.TEST_R_RM),
            0x86: Instruction(0x86, self.XCHG_R_RM),
            0x87: Instruction(0x87, self.XCHG_R_RM),
            0x88: Instruction(0x88, self.MOV_R_RM),
            0x89: Instruction(0x89, self.MOV_R_RM),
            0x8A: Instruction(0x8A, self.MOV_R_RM),
            0x8B: Instruction(0x8B, self.MOV_R_RM),
            0x8C: Instruction(0x8C, self.MOV_RM_S),
            0x8D: Instruction(0x8D, self.LEA),
            0x8E: Instruction(0x8E, self.MOV_RM_S),
            0x8F: Instruction(0x8F, self.POP_RM),
            0x90: Instruction(0x90, self.NOP, multi_byte=False),
            0x91: Instruction(0x91, self.XCHG_R_A),
            0x92: Instruction(0x92, self.XCHG_R_A),
            0x93: Instruction(0x93, self.XCHG_R_A),
            0x94: Instruction(0x94, self.XCHG_R_A),
            0x95: Instruction(0x95, self.XCHG_R_A),
            0x96: Instruction(0x96, self.XCHG_R_A),
            0x97: Instruction(0x97, self.XCHG_R_A),
            0x98: Instruction(0x98, self.CBW, multi_byte=False),
            0x99: Instruction(0x99, self.CWD, multi_byte=False),
            0x9A: Instruction(0x9A, self.CALL_DI, special_byte_2=True),
            0x9B: Instruction(0x9B, self.WAIT, multi_byte=False),
            0x9C: Instruction(0x9C, self.PUSHF),
            0x9D: Instruction(0x9D, self.POPF),
            0x9E: Instruction(0x9E, self.SAHF),
            0x9F: Instruction(0x9F, self.LAHF),
            0xA0: Instruction(0xA0, self.MOV_M_A, special_byte_2=True),
            0xA1: Instruction(0xA1, self.MOV_M_A, special_byte_2=True),
            0xA2: Instruction(0xA2, self.MOV_M_A, special_byte_2=True),
            0xA3: Instruction(0xA3, self.MOV_M_A, special_byte_2=True),
            0xA4: Instruction(0xA4, self.MOVS, multi_byte=False),
            0xA5: Instruction(0xA5, self.MOVS, multi_byte=False),
            0xA6: Instruction(0xA6, self.CMPS, multi_byte=False),
            0xA7: Instruction(0xA7, self.CMPS, multi_byte=False),
            0xA8: Instruction(0xA8, self.TEST_I_A, special_byte_2=True),
            0xA9: Instruction(0xA9, self.TEST_I_A, special_byte_2=True),
            0xAA: Instruction(0xAA, self.STOS, multi_byte=False),
            0xAB: Instruction(0xAB, self.STOS, multi_byte=False),
            0xAC: Instruction(0xAC, self.LODS, multi_byte=False),
            0xAD: Instruction(0xAD, self.LODS, multi_byte=False),
            0xAE: Instruction(0xAE, self.SCAS, multi_byte=False),
            0xaf: Instruction(0xaf, self.SCAS, multi_byte=False),
            0xB0: Instruction(0xB0, self.MOV_I_R, special_byte_2=True),
            0xB1: Instruction(0xB1, self.MOV_I_R, special_byte_2=True),
            0xB2: Instruction(0xB2, self.MOV_I_R, special_byte_2=True),
            0xB3: Instruction(0xB3, self.MOV_I_R, special_byte_2=True),
            0xB4: Instruction(0xB4, self.MOV_I_R, special_byte_2=True),
            0xB5: Instruction(0xB5, self.MOV_I_R, special_byte_2=True),
            0xB6: Instruction(0xB6, self.MOV_I_R, special_byte_2=True),
            0xB7: Instruction(0xB7, self.MOV_I_R, special_byte_2=True),
            0xB8: Instruction(0xB8, self.MOV_I_R, special_byte_2=True),
            0xB9: Instruction(0xB9, self.MOV_I_R, special_byte_2=True),
            0xBA: Instruction(0xBA, self.MOV_I_R, special_byte_2=True),
            0xBB: Instruction(0xBB, self.MOV_I_R, special_byte_2=True),
            0xBC: Instruction(0xBC, self.MOV_I_R, special_byte_2=True),
            0xBD: Instruction(0xBD, self.MOV_I_R, special_byte_2=True),
            0xBE: Instruction(0xBE, self.MOV_I_R, special_byte_2=True),
            0xBF: Instruction(0xBF, self.MOV_I_R, special_byte_2=True),
            0xC2: Instruction(0xC2, self.RET_S_I_SP, special_byte_2=True),
            0xC3: Instruction(0xC3, self.RET_S, multi_byte=False),
            0xC4: Instruction(0xC4, self.LES),
            0xC5: Instruction(0xC5, self.LDS),
            0xC6: Instruction(0xC6, self.MOV_I_RM),
            0xC7: Instruction(0xC7, self.MOV_I_RM),
            0xCA: Instruction(0xCA, self.RET_I_I_SP, special_byte_2=True),
            0xCB: Instruction(0xCB, self.RET_I, multi_byte=False),
            0xCC: Instruction(0xCC, self.INT_3, multi_byte=False),
            0xCD: Instruction(0xCD, self.INT_S, special_byte_2=True),
            0xCE: Instruction(0xCE, self.INTO, multi_byte=False),
            0xcf: Instruction(0xcf, self.IRET, multi_byte=False),
            0xD0: Instruction(0xD0, self.GRP2),  # SHL_SHR_SAR_ROL_ROR_RCL_RCR
            0xD1: Instruction(0xD1, self.GRP2),  # SHL_SHR_SAR_ROL_ROR_RCL_RCR
            0xD2: Instruction(0xD2, self.GRP2),  # SHL_SHR_SAR_ROL_ROR_RCL_RCR
            0xD3: Instruction(0xD3, self.GRP2),  # SHL_SHR_SAR_ROL_ROR_RCL_RCR),
            0xD4: Instruction(0xD4, self.AAM),
            0xD5: Instruction(0xD5, self.AAD),
            0xD7: Instruction(0xD7, self.XLAT),
            0xD8: Instruction(0xD8, self.ESC),
            0xD9: Instruction(0xD9, self.ESC),
            0xDA: Instruction(0xDA, self.ESC),
            0xDB: Instruction(0xDB, self.ESC),
            0xDC: Instruction(0xDC, self.ESC),
            0xDD: Instruction(0xDD, self.ESC),
            0xDE: Instruction(0xDE, self.ESC),
            0xDF: Instruction(0xDF, self.ESC),
            0xE0: Instruction(0xE0, self.LOOPNZ, special_byte_2=True),
            0xE1: Instruction(0xE1, self.LOOPZ, special_byte_2=True),
            0xE2: Instruction(0xE2, self.LOOP, special_byte_2=True),
            0xE3: Instruction(0xE3, self.JCXZ, special_byte_2=True),
            0xE4: Instruction(0xE4, self.IN_F, special_byte_2=True),
            0xE5: Instruction(0xE5, self.IN_F, special_byte_2=True),
            0xE6: Instruction(0xE6, self.OUT_F, special_byte_2=True),
            0xE7: Instruction(0xE7, self.OUT_F, special_byte_2=True),
            0xE8: Instruction(0xE8, self.CALL_DS, special_byte_2=True),
            0xE9: Instruction(0xE9, self.JMP_DS, special_byte_2=True),
            0xEA: Instruction(0xEA, self.JMP_DI, special_byte_2=True),
            0xEB: Instruction(0xEB, self.JMP_DS, special_byte_2=True),
            0xEC: Instruction(0xEC, self.IN_V),
            0xED: Instruction(0xED, self.IN_V),
            0xEE: Instruction(0xEE, self.OUT_V),
            0xEF: Instruction(0xEF, self.OUT_V),
            0xF0: Instruction(0xF0, self.LOCK, multi_byte=False),
            0xF2: Instruction(0xF2, self.REP, multi_byte=False),
            0xF3: Instruction(0xF3, self.REP, multi_byte=False),
            0xF4: Instruction(0xF4, self.HLT, multi_byte=False),
            0xF5: Instruction(0xF5, self.CMC, multi_byte=False),
            0xF6: Instruction(0xF6, self.GRP3A),  # NEG_MUL_IMUL_DIV_IDIV_NOT_TEST
            0xF7: Instruction(0xF7, self.GRP3B),  # NEG_MUL_IMUL_DIV_IDIV_NOT_TEST
            0xF8: Instruction(0xF8, self.CLC, multi_byte=False),
            0xF9: Instruction(0xF9, self.STC, multi_byte=False),
            0xFA: Instruction(0xFA, self.CLI, multi_byte=False),
            0xFB: Instruction(0xFB, self.STI, multi_byte=False),
            0xFC: Instruction(0xFC, self.CLD, multi_byte=False),
            0xFD: Instruction(0xFD, self.STD, multi_byte=False),
            0xFE: Instruction(0xFE, self.GRP4),  # INC_DEC_RM),
            0xFF: Instruction(0xFF, self.GRP5)  # PUSH_INC_DEC_RM_CALL_IS_CALL_II_JMP_IS_JMP_II),
        }

    @property
    def AL(self):
        return self.main_registers[0]

    @AL.setter
    def AL(self, value):
        self.main_registers[0] = value
        self.Interface.registers_callback()

    @property
    def AH(self):
        return self.main_registers[1]

    @AH.setter
    def AH(self, value):
        self.main_registers[1] = value
        self.Interface.registers_callback()

    @property
    def BL(self):
        return self.main_registers[2]

    @BL.setter
    def BL(self, value):
        self.main_registers[2] = value
        self.Interface.registers_callback()

    @property
    def BH(self):
        return self.main_registers[3]

    @BH.setter
    def BH(self, value):
        self.main_registers[3] = value
        self.Interface.registers_callback()

    @property
    def CL(self):
        return self.main_registers[4]

    @CL.setter
    def CL(self, value):
        self.main_registers[4] = value
        self.Interface.registers_callback()

    @property
    def CH(self):
        return self.main_registers[5]

    @CH.setter
    def CH(self, value):
        self.main_registers[5] = value
        self.Interface.registers_callback()

    @property
    def DL(self):
        return self.main_registers[6]

    @DL.setter
    def DL(self, value):
        self.main_registers[6] = value
        self.Interface.registers_callback()

    @property
    def DH(self):
        return self.main_registers[7]

    @DH.setter
    def DH(self, value):
        self.main_registers[7] = value
        self.Interface.registers_callback()

    @property
    def AX(self):
        return self.combine_bytes(self.AH, self.AL)

    @AX.setter
    def AX(self, value):
        self.AH, self.AL = self.split_word(value)
        self.Interface.registers_callback()

    @property
    def BX(self):
        return self.combine_bytes(self.BH, self.BL)

    @BX.setter
    def BX(self, value):
        self.BH, self.BL = self.split_word(value)
        self.Interface.registers_callback()

    @property
    def CX(self):
        return self.combine_bytes(self.CH, self.CL)

    @CX.setter
    def CX(self, value):
        self.CH, self.CL = self.split_word(value)
        self.Interface.registers_callback()

    @property
    def DX(self):
        return self.combine_bytes(self.DH, self.DL)

    @DX.setter
    def DX(self, value):
        self.DH, self.DL = self.split_word(value)
        self.Interface.registers_callback()

    @property
    def IP(self):
        return self.pointers[0]

    @IP.setter
    def IP(self, value):
        self.pointers[0] = value
        self.Interface.registers_callback()

    @property
    def SP(self):
        return self.pointers[1]

    @SP.setter
    def SP(self, value):
        self.pointers[1] = value
        self.Interface.registers_callback()

    @property
    def BP(self):
        return self.pointers[2]

    @BP.setter
    def BP(self, value):
        self.pointers[2] = value
        self.Interface.registers_callback()

    @property
    def SI(self):
        return self.pointers[3]

    @SI.setter
    def SI(self, value):
        self.pointers[3] = value
        self.Interface.registers_callback()

    @property
    def DI(self):
        return self.pointers[4]

    @DI.setter
    def DI(self, value):
        self.pointers[4] = value
        self.Interface.registers_callback()

    @property
    def CS(self):
        return self.segment_registers[0]

    @CS.setter
    def CS(self, value):
        self.segment_registers[0] = value
        self.Interface.registers_callback()
        self.Interface.segments_callback('Code', value)

    @property
    def DS(self):
        return self.segment_registers[1]

    @DS.setter
    def DS(self, value):
        self.segment_registers[1] = value
        self.Interface.registers_callback()
        self.Interface.segments_callback('Data', value)

    @property
    def SS(self):
        return self.segment_registers[2]

    @SS.setter
    def SS(self, value):
        self.segment_registers[2] = value
        self.Interface.registers_callback()
        self.Interface.segments_callback('Stack', value)

    @property
    def ES(self):
        return self.segment_registers[3]

    @ES.setter
    def ES(self, value):
        self.segment_registers[3] = value
        self.Interface.registers_callback()
        self.Interface.segments_callback('Extra', value)

    @property
    def FLAG(self):
        return self._FLAG

    @FLAG.setter
    def FLAG(self, value):
        self._FLAG = uint16(value)
        self.Interface.registers_callback()

    def reset(self):
        self.main_registers = np.zeros(8, dtype=uint8)
        self.pointers = np.zeros(5, dtype=uint16)
        self.segment_registers = np.zeros(4, dtype=uint16)
        self._FLAG = uint16(0x200)
        self.memory = np.zeros(2 ** 20, dtype=uint8)
        self.CS = 0x5FFF
        self.DS = 0x40

    def calc_address(self, segment_reg, offset):
        if self.__seg_override >= 0:
            segment_reg = self.seg_table[self.__seg_override]
            self.__seg_override = -1
        return (segment_reg << 4) + offset

    @staticmethod
    def combine_bytes(high_byte, low_byte):
        return ((high_byte << 8) + low_byte).astype(uint16)

    @staticmethod
    def split_word(word):
        return uint8(word >> 8), uint8(word & 0x00FF)

    @staticmethod
    def split_mod_reg_rm(byte):
        mask = 0x7
        return (byte >> 6) & mask, (byte >> 3) & mask, byte & mask

    def load_hex_from_exe(self, exe, start=None):
        if start is None:
            start = self.CS << 4
        with open(exe, 'br') as f:
            data = np.fromfile(f, dtype=uint8)
        program_length = self.combine_bytes(data[0x3], data[0x2])
        program_start = self.combine_bytes(data[0x9], data[0x8]) << 4
        self.SS = self.CS + self.combine_bytes(data[0x0F], data[0x0E])
        self.SP = self.combine_bytes(data[0x11], data[0x10])
        self.IP = self.combine_bytes(data[0x15], data[0x14])
        self.CS += self.combine_bytes(data[0x17], data[0x16])
        program = data[program_start:] if program_length == 0 else data[program_start:program_start+program_length]
        self.memory[start:start + len(program)] = program
        offset_start = self.combine_bytes(data[0x19], data[0x18])
        for i in range(offset_start, program_start, 4):
            value = self.combine_bytes(data[i+1], data[i])
            if value == 0:
                break
            addr = (self.CS << 4) + value
            self.write_memory(addr, self.read_memory(addr) + self.CS)
        self.Interface.memory_callback()

    def debug(self):
        self.decode()
        return self.instruction_length

    def step(self):
        if not self.__halt:
            self.__target_function(**self.__target_kwargs)
            self.IP += self.instruction_length
            self.decode()
        return self.instruction_length

    def fetch(self):
        phy_addr = self.calc_address(self.CS, self.IP + self.instruction_length)
        self.instruction_length += 1
        return self.memory[phy_addr]

    def decode(self):
        self.instruction_length = 0
        byte1 = self.fetch()
        byte1_inst = self.instruction_data[byte1]
        if byte1_inst.multi_byte and not byte1_inst.special_byte_2:
            byte2 = self.fetch()
            mod, reg, rm = self.split_mod_reg_rm(byte2)
            kwargs = {'mod': mod, 'reg': reg, 'rm': rm}
        else:
            kwargs = {}
        byte1_inst.function(op_code=byte1, **kwargs)

    def read_memory(self, address, word=True):
        return self.combine_bytes(self.memory[address + 1], self.memory[address]) if word else self.memory[address]

    def write_memory(self, address, data, word=True):
        if word:
            high_byte, low_byte = self.split_word(data)
            self.memory[address + 1] = high_byte
        else:
            low_byte = data
        self.memory[address] = low_byte
        self.Interface.memory_callback()

    def set_flags(self, **kwargs):
        for name, value in kwargs.items():
            if name == 'CF':
                if value:
                    self.FLAG |= 0x1
                else:
                    self.FLAG &= 0xFFFE
            elif name == 'PF':
                if value:
                    self.FLAG |= 0x4
                else:
                    self.FLAG &= 0xFFFB
            elif name == 'AF':
                if value:
                    self.FLAG |= 0x10
                else:
                    self.FLAG &= 0xFFEF
            elif name == 'ZF':
                if value:
                    self.FLAG |= 0x40
                else:
                    self.FLAG &= 0xFFBF
            elif name == 'SF':
                if value:
                    self.FLAG |= 0x80
                else:
                    self.FLAG &= 0xFF7F
            elif name == 'OF':
                if value:
                    self.FLAG |= 0x800
                else:
                    self.FLAG &= 0xF7FF
            elif name == 'TF':
                if value:
                    self.FLAG |= 0x100
                else:
                    self.FLAG &= 0xFEFF
            elif name == 'IF':
                if value:
                    self.FLAG |= 0x200
                else:
                    self.FLAG &= 0xFDFF
            elif name == 'DF':
                if value:
                    self.FLAG |= 0x400
                else:
                    self.FLAG &= 0xFBFF
            else:
                raise Exception
        self.FLAG = uint16(self.FLAG)

    def get_flag(self, flag):
        if flag == 'CF':
            return bool(self.FLAG & 0x1)
        elif flag == 'PF':
            return bool(self.FLAG & 0x4)
        elif flag == 'AF':
            return bool(self.FLAG & 0x10)
        elif flag == 'ZF':
            return bool(self.FLAG & 0x40)
        elif flag == 'SF':
            return bool(self.FLAG & 0x80)
        elif flag == 'OF':
            return bool(self.FLAG & 0x800)
        elif flag == 'TF':
            return bool(self.FLAG & 0x100)
        elif flag == 'IF':
            return bool(self.FLAG & 0x200)
        elif flag == 'DF':
            return bool(self.FLAG & 0x400)
        else:
            raise Exception

    # types: 0:reg, 1:mem, 2:imm, 3:seg
    def __read_operands(self, dst, src, dst_type, src_type, w):
        if dst_type == src_type == 1:
            self.__seg_override = 0

        if dst_type == 0:
            dst_op = self.__getattribute__(self.reg_table[w][dst])
        elif dst_type == 1:
            phy_addr = self.calc_address(self.DS, dst)
            dst_op = self.read_memory(phy_addr, w)
        elif dst_type == 3:
            dst_op = self.__getattribute__(self.seg_table[dst])
        else:
            raise Exception

        if src_type == 0:
            src_op = self.__getattribute__(self.reg_table[w][src])
        elif src_type == 1:
            phy_addr = self.calc_address(self.DS, src)
            src_op = self.read_memory(phy_addr, w)
        elif src_type == 3:
            src_op = self.__getattribute__(self.seg_table[dst])
        else:
            src_op = src
        return dst_op, src_op

    def __write_back(self, dst, dst_type, data, w):
        if dst_type == 0:
            self.__setattr__(self.reg_table[w][dst], uint16(data) if w else uint8(data))
        elif dst_type == 3:
            self.__setattr__(self.seg_table[dst], data)
        else:
            phy_addr = self.calc_address(self.DS, dst)
            self.write_memory(phy_addr, uint16(data) if w else uint8(data), w)

    def __set_target(self, function, **kwargs):
        self.__target_function = function
        self.__target_kwargs = kwargs

    def __set_attribute(self, name, value):
        self.__setattr__(name, value)

    @staticmethod
    def __sign_extend(value):
        neg = bool(value & 0x80)
        return uint16(uint16(value) | (0xFF00 if neg else 0x0000))

    # modes: 0:add, 1:or, 2: adc, 3:ssb, 4:and, 5:sub, 6:xor, 7:cmp, 8:test
    def __calc(self, op1, op2, w, mode):
        arith = mode in [0, 2, 3, 5, 7]

        if mode in [0, 2]:
            res = uint32(op1) + op2 + (self.get_flag('CF') if mode == 2 else 0)
        elif mode in [3, 5, 7]:
            res = uint32(op1) - op2 - (self.get_flag('CF') if mode == 3 else 0)
        elif mode == 1:
            res = np.logical_or(op1, op2)
        elif mode == 4 or mode == 8:
            res = np.logical_and(op1, op2)
        elif mode == 6:
            res = np.logical_xor(op1, op2)
        else:
            raise Exception

        flag_kwargs = {}
        if arith:
            flag_kwargs['AF'] = bool(((op1 & 0xF) + (op2 & 0xF)) & 0x10)
        if w:
            flag_kwargs['CF'] = bool(res & 0x10000) if arith else False
            flag_kwargs['PF'] = parity(uint16(res))
            flag_kwargs['ZF'] = (res & 0xFFFF) == 0
            flag_kwargs['SF'] = bool(res & 0x8000)
            flag_kwargs['OF'] = bool(((op1 & 0x7FFF) + (op2 & 0x7FFF)) & 0x8000) if arith else False
        else:
            flag_kwargs['CF'] = bool(res & 0x100) if arith else False
            flag_kwargs['PF'] = parity(uint8(res))
            flag_kwargs['ZF'] = (res & 0xFF) == 0
            flag_kwargs['SF'] = bool(res & 0x80)
            flag_kwargs['OF'] = bool(((op1 & 0x7F) + (op2 & 0x7F)) & 0x80) if arith else False

        self.set_flags(**flag_kwargs)
        return res

    def __arith_logic(self, dst, src, dst_type, src_type, w, mode):
        op1, op2 = self.__read_operands(dst, src, dst_type, src_type, w)

        res = self.__calc(op1, op2, w, mode)

        if mode != 7 and mode != 8:
            self.__write_back(dst, dst_type, res, w)

    def __push(self, data):
        self.SP -= uint16(2)
        phy_addr = self.calc_address(self.SS, self.SP)
        self.write_memory(phy_addr, data)

    # dst_types: 0: prop(name), 1:mem
    def __pop(self, dst, dst_type):
        phy_addr = self.calc_address(self.SS, self.SP)
        if dst_type == 0:
            self.__setattr__(dst, self.read_memory(phy_addr))
        elif dst_type == 1:
            pass
        self.SP += uint16(2)

    def __daa_das(self, mode):
        n = [0, 5][mode]

        if (self.AL & 0xF) > 0x9 or self.get_flag('AF'):
            self.AL = self.__calc(self.AL, uint8(0x6), False, n)
        else:
            self.set_flags(AF=False)
        if (self.AL & 0xF0) > 0x90 or self.get_flag('CF'):
            self.AL = self.__calc(self.AL, uint8(0x60), False, n)
        else:
            self.set_flags(CF=False)

    def __aaa_aas(self, mode):
        n = [0, 5][mode]

        if (self.AL & 0xF) > 0x9 or self.get_flag('AF'):
            self.AL = self.__calc(self.AL, uint8(0x6), False, n)
            self.AH = self.__calc(self.AH, uint8(0x1), False, n)
            self.set_flags(CF=True, AF=True)
        else:
            self.set_flags(CF=False, AF=False)
        self.AL = np.logical_and(self.AL, uint8(0xF))

    def __aad(self):  # TODO: Update FLAG
        self.AL += self.AH * 10
        self.AH = 0

    def __aam(self):  # TODO: Update FLAG
        self.AH = self.AL // 10
        self.AL %= 10

    def __jmp(self, flag, offset):
        if flag:
            self.IP = uint16(self.IP + offset)

    # mode: 0:loop, 1:loope, 2:loopne
    def __loop(self, mode, offset):
        self.CX -= 1
        condition = self.CX != 0
        if mode:
            zf = self.get_flag('ZF')
            condition = condition and (zf if mode == 1 else not zf)
        self.__jmp(condition, offset)

    def __mov(self, dst, src, dst_type, src_type, w):
        _, data = self.__read_operands(dst, src, dst_type, src_type, w)
        self.__write_back(dst, dst_type, data, w)

    def __xchg(self, reg_mem, reg):  # TODO
        pass

    def __nop(self):
        pass

    def __stos(self, w):
        phy_addr = self.calc_address(self.ES, self.DI)
        self.write_memory(phy_addr, self.AX if w else self.AL, w)
        if self.get_flag('DF'):
            self.DI -= 2 if w else 1
        else:
            self.DI += 2 if w else 1

    def __ldos(self, w):
        phy_addr = self.calc_address(self.DS, self.SI)
        data = self.read_memory(phy_addr, w)
        if w:
            self.AX = data
        else:
            self.AL = data
        if self.get_flag('DF'):
            self.DI -= 2 if w else 1
        else:
            self.DI += 2 if w else 1

    def __scas(self, w):
        es_data = self.calc_address(self.ES, self.DI)
        self.__calc(self.AX if w else self.AL, es_data, w, 7)
        if self.get_flag('DF'):
            self.DI -= 2 if w else 1
        else:
            self.DI += 2 if w else 1

    def __movs(self, w):
        dst = self.DI
        src = self.SI
        dst_type = 1
        src_type = 1
        self.__mov(dst, src, dst_type, src_type, w)

    def __cmps(self, w):
        self.__arith_logic(self.DI, self.SI, 1, 1, w, 7)
        df = self.get_flag('DF')
        if df:
            self.SI -= uint16(2 if w else 1)
            self.DI -= uint16(2 if w else 1)
        else:
            self.SI += uint16(2 if w else 1)
            self.DI += uint16(2 if w else 1)

    def __sahf(self):
        kwargs = {}
        ah = self.AH
        kwargs['SF'] = bool(ah & 0b10000000)
        kwargs['ZF'] = bool(ah & 0b01000000)
        kwargs['AF'] = bool(ah & 0b00010000)
        kwargs['PF'] = bool(ah & 0b00000100)
        kwargs['CF'] = bool(ah & 0b00000001)
        self.set_flags()

    def __lahf(self):
        ah = 0b00000010
        if self.get_flag('SF'):
            ah |= 0b10000000
        if self.get_flag('ZF'):
            ah |= 0b01000000
        if self.get_flag('AF'):
            ah |= 0b00010000
        if self.get_flag('PF'):
            ah |= 0b00000100
        if self.get_flag('CF'):
            ah |= 0b00000001
        self.AH = ah

    def __r_rm(self, mod, reg, rm, d=True):
        index_regs = self.rm_table[reg]
        src = sum(self.__getattribute__(ir) for ir in index_regs)
        src_type = 1

        if mod == 0:
            if rm == 6:
                byte3 = self.fetch()
                byte4 = self.fetch()
                src = self.combine_bytes(byte4, byte3)
        elif mod == 1:
            src += uint16(8)
        elif mod == 2:
            src += uint16(16)
        elif mod == 3:
            src = rm
            src_type = 0
        else:
            raise Exception(f'mod:{mod} reg:{reg} rm:{rm}')
        dst = reg
        dst_type = 0
        if not d:
            src, dst, src_type, dst_type = dst, src, dst_type, src_type
        return src, dst, src_type, dst_type

    # Instructions
    def GRP0_R_RM(self, op_code, mod, reg, rm):
        d = bool(op_code & 0x02)
        w = bool(op_code & 0x01)
        mode = op_code >> 3

        src, dst, src_type, dst_type = self.__r_rm(mod, reg, rm, d)

        self.__set_target(self.__arith_logic, src=src, dst=dst, src_type=src_type, dst_type=dst_type, w=w, mode=mode)

    def GRP0_I_A(self, op_code):
        w = bool(op_code & 0x01)
        mode = op_code >> 3

        src = self.fetch()
        if w:
            byte3 = self.fetch()
            src = self.combine_bytes(byte3, src)
        src_type = 2
        dst = 0
        dst_type = 0

        self.__set_target(self.__arith_logic, src=src, dst=dst, src_type=src_type, dst_type=dst_type, w=w, mode=mode)

    def PUSH_S(self, op_code):
        data = self.__getattribute__(self.seg_table[op_code >> 0x3])
        self.__set_target(self.__push, data=data)

    def POP_S(self, op_code):
        dst = self.seg_table[(op_code >> 3)]
        self.__set_target(self.__pop, dst=dst, mode=0)

    def SO(self, op_code):
        self.__set_target(self.__set_attribute, name='__seg_override', value=(op_code >> 3) & 0x3)

    def DAA(self, op_code):
        self.__set_target(self.__daa_das, mode=0)

    def DAS(self, op_code):
        self.__set_target(self.__daa_das, mode=1)

    def AAA(self, op_code):
        self.__set_target(self.__aaa_aas, mode=0)

    def AAS(self, op_code):
        self.__set_target(self.__aaa_aas, mode=1)

    def INC_R(self, op_code):
        reg = self.reg_table[True][op_code & 0x7]
        self.__set_target(self.__set_attribute, name=reg, value=self.__getattribute__(reg) + uint16(0x1))

    def DEC_R(self, op_code):
        reg = self.reg_table[True][op_code & 0x7]
        self.__set_target(self.__set_attribute, name=reg, value=self.__getattribute__(reg) - uint16(0x1))

    def PUSH_R(self, op_code):
        reg = self.reg_table[True][op_code & 0x7]
        self.__set_target(self.__push, data=self.__getattribute__(reg))

    def POP_R(self, op_code):
        dst = self.reg_table[True][op_code & 0x7]
        self.__set_target(self.__pop, dst=dst, mode=0)

    def JO(self, op_code):
        offset = int8(self.fetch())
        flag = self.get_flag('OF')
        self.__set_target(self.__jmp, flag=flag, offset=offset)

    def JNO(self, op_code):
        offset = int8(self.fetch())
        flag = not self.get_flag('OF')
        self.__set_target(self.__jmp, flag=flag, offset=offset)

    def JB(self, op_code):
        offset = int8(self.fetch())
        flag = self.get_flag('CF')
        self.__set_target(self.__jmp, flag=flag, offset=offset)

    def JNB(self, op_code):
        offset = int8(self.fetch())
        flag = not self.get_flag('CF')
        self.__set_target(self.__jmp, flag=flag, offset=offset)

    def JZ(self, op_code):
        offset = int8(self.fetch())
        flag = self.get_flag('ZF')
        self.__set_target(self.__jmp, flag=flag, offset=offset)

    def JNZ(self, op_code):
        offset = int8(self.fetch())
        flag = not self.get_flag('ZF')
        self.__set_target(self.__jmp, flag=flag, offset=offset)

    def JBE(self, op_code):
        offset = int8(self.fetch())
        flag = self.get_flag('CF') or self.get_flag('ZF')
        self.__set_target(self.__jmp, flag=flag, offset=offset)

    def JA(self, op_code):
        offset = int8(self.fetch())
        flag = not self.get_flag('CF') and not self.get_flag('ZF')
        self.__set_target(self.__jmp, flag=flag, offset=offset)

    def JS(self, op_code):
        offset = int8(self.fetch())
        flag = self.get_flag('SF')
        self.__set_target(self.__jmp, flag=flag, offset=offset)

    def JNS(self, op_code):
        offset = int8(self.fetch())
        flag = not self.get_flag('SF')
        self.__set_target(self.__jmp, flag=flag, offset=offset)

    def JPE(self, op_code):
        offset = int8(self.fetch())
        flag = self.get_flag('PF')
        self.__set_target(self.__jmp, flag=flag, offset=offset)

    def JPO(self, op_code):
        offset = int8(self.fetch())
        flag = not self.get_flag('PF')
        self.__set_target(self.__jmp, flag=flag, offset=offset)

    def JL(self, op_code):
        offset = int8(self.fetch())
        flag = self.get_flag('SF') != self.get_flag('OF')
        self.__set_target(self.__jmp, flag=flag, offset=offset)

    def JGE(self, op_code):
        offset = int8(self.fetch())
        flag = self.get_flag('SF') == self.get_flag('OF')
        self.__set_target(self.__jmp, flag=flag, offset=offset)

    def JLE(self, op_code):
        offset = int8(self.fetch())
        flag = self.get_flag('SF') != self.get_flag('OF') or self.get_flag('ZF')
        self.__set_target(self.__jmp, flag=flag, offset=offset)

    def JG(self, op_code):
        offset = int8(self.fetch())
        flag = self.get_flag('CF') == self.get_flag('OF') and self.get_flag('ZF')
        self.__set_target(self.__jmp, flag=flag, offset=offset)

    def GRP1(self, op_code, mod, reg, rm):
        s = bool(op_code & 0x2)
        w = bool(op_code & 0x1)

        _, dst, _, dst_type = self.__r_rm(mod, rm, rm, False)

        src = self.fetch()
        if w:
            if s:
                sign = bool(src & 0x80)
                imm_byte_2 = uint8(0xFF) if sign else uint8(0x00)
            else:
                imm_byte_2 = self.fetch()
            src = self.combine_bytes(imm_byte_2, src)
        src_type = 2

        self.__set_target(self.__arith_logic, src=src, dst=dst, src_type=src_type, dst_type=dst_type, w=w, mode=reg)

    def TEST_R_RM(self, op_code, mod, reg, rm):
        w = bool(op_code & 0x01)
        mode = 8
        src, dst, src_type, dst_type = self.__r_rm(mod, reg, rm)
        self.__set_target(self.__arith_logic, dst=dst, src=src, dst_type=dst_type, src_type=src_type, w=w, mode=mode)

    def XCHG_R_RM(self, op_code):  # TODO
        pass

    def MOV_R_RM(self, op_code, mod, reg, rm):
        d = bool(op_code & 0x02)
        w = bool(op_code & 0x01)

        src, dst, src_type, dst_type = self.__r_rm(mod, reg, rm)
        if not d:
            src, dst, src_type, dst_type = dst, src, dst_type, src_type
        self.__set_target(self.__mov, dst=dst, src=src, dst_type=dst_type, src_type=src_type, w=w)

    def MOV_RM_S(self, op_code, mod, reg, rm):
        d = bool(op_code & 0x2)

        src, _, src_type, _ = self.__r_rm(mod, rm, rm)
        dst = reg
        dst_type = 3
        if not d:
            src, dst, src_type, dst_type = dst, src, dst_type, src_type
        self.__set_target(self.__mov, dst=dst, src=src, dst_type=dst_type, src_type=src_type, w=True)

    def LEA(self, op_code):  # TODO
        pass

    def POP_RM(self, op_code):  # TODO
        pass

    def NOP(self, op_code):
        self.__set_target(self.__nop)

    def XCHG_R_A(self, op_code):  # TODO
        reg = self.reg_table[True][op_code & 0x7]
        temp = self.AX
        self.AX = self.__getattribute__(reg)
        self.__setattr__(reg, temp)

    def CBW(self, op_code):
        self.__set_target(self.__set_attribute, name='AX', value=self.__sign_extend(self.AL))

    def CWD(self, op_code):
        flag = bool(self.AX & 0x8000)
        self.__set_target(self.__set_attribute, name='DX', value=uint16(0xFF) if flag else uint16(0x0))

    def CALL_DI(self, op_code):  # TODO
        pass

    def WAIT(self, op_code):  # TODO
        pass

    def PUSHF(self, op_code):
        self.__set_target(self.__push, data=self.FLAG)

    def POPF(self, op_code):
        self.__set_target(self.__pop, dst='FLAG', dst_type=0)

    def SAHF(self, op_code):
        self.__set_target(self.__sahf)

    def LAHF(self, op_code):
        self.__set_target(self.__lahf)

    def MOV_M_A(self, op_code):
        d = bool(op_code & 0x02)
        w = bool(op_code & 0x01)

        src_type = 1
        low_byte = self.fetch()
        high_byte = self.fetch()
        src = self.combine_bytes(high_byte, low_byte)
        dst_type = 0
        dst = 0

        if d:
            src, dst, src_type, dst_type = dst, src, dst_type, src_type
        self.__set_target(self.__mov, dst=dst, src=src, dst_type=dst_type, src_type=src_type, w=w)

    def MOVS(self, op_code):
        w = bool(op_code & 0x1)
        self.__set_target(self.__movs, w=w)

    def CMPS(self, op_code):
        w = bool(op_code & 0x1)
        self.__set_target(self.__cmps, w=w)

    def TEST_I_A(self, op_code):  # TODO
        pass

    def STOS(self, op_code):
        w = bool(op_code & 0x1)
        self.__set_target(self.__stos, w=w)

    def LODS(self, op_code):
        w = bool(op_code & 0x1)
        self.__set_target(self.__ldos, w=w)

    def SCAS(self, op_code):
        w = bool(op_code & 0x1)
        self.__set_target(self.__scas, w=w)

    def MOV_I_R(self, op_code):
        w = bool(op_code & 0x8)
        reg = op_code & 0x7
        src = self.fetch()
        if w:
            src = self.combine_bytes(self.fetch(), src)
        self.__set_target(self.__mov, dst=reg, src=src, dst_type=0, src_type=2, w=w)

    def RET_S_I_SP(self, op_code):  # TODO
        pass

    def RET_S(self, op_code):  # TODO
        pass

    def LES(self, op_code):  # TODO
        pass

    def LDS(self, op_code):  # TODO
        pass

    def MOV_I_RM(self, op_code, mod, reg, rm):  # TESTED
        w = bool(op_code & 0x01)
        dst, _, dst_type, _ = self.__r_rm(mod, reg, rm)
        src = self.fetch()
        src_type = 2
        if w:
            src = self.combine_bytes(self.fetch(), src)
        self.__set_target(self.__mov, dst=dst, src=src, dst_type=dst_type, src_type=src_type, w=w)

    def RET_I_I_SP(self, op_code):  # TODO
        pass

    def RET_I(self, op_code):  # TODO
        pass

    def INT_3(self, op_code):  # TODO
        pass

    def INT_S(self, op_code):  # TODO
        pass

    def INTO(self, op_code):  # TODO
        pass

    def IRET(self, op_code):  # TODO
        pass

    def GRP2(self, op_code):  # TODO
        pass

    def AAM(self, op_code):  # TODO
        pass

    def AAD(self, op_code):  # TODO
        pass

    def XLAT(self, op_code):  # TODO
        pass

    def ESC(self, op_code):  # TODO
        pass

    def LOOPNZ(self, op_code):
        disp = self.__sign_extend(self.fetch())
        self.__set_target(self.__loop, mode=2, offset=disp)

    def LOOPZ(self, op_code):
        disp = self.__sign_extend(self.fetch())
        self.__set_target(self.__loop, mode=1, offset=disp)

    def LOOP(self, op_code):
        disp = self.__sign_extend(self.fetch())
        self.__set_target(self.__loop, mode=0, offset=disp)

    def JCXZ(self, op_code):  # TODO
        pass

    def IN_F(self, op_code):  # TODO
        pass

    def OUT_F(self, op_code):  # TODO
        pass

    def CALL_DS(self, op_code):  # TODO
        pass

    def JMP_DS(self, op_code):
        short = bool(op_code & 0x2)
        disp = self.fetch()
        if short:
            disp = self.__sign_extend(disp)
        else:
            disp_high = self.fetch()
            disp = self.combine_bytes(disp_high, disp)
        self.__set_target(self.__jmp, flag=True, offset=disp)

    def JMP_DI(self, op_code):  # TODO
        pass

    def IN_V(self, op_code):  # TODO
        pass

    def OUT_V(self, op_code):  # TODO
        pass

    def LOCK(self, op_code):  # TODO
        pass

    def REP(self, op_code):  # TODO
        pass

    def HLT(self, op_code):
        self.__halt = True

    def CMC(self, op_code):
        self.__set_target(self.set_flags, CF=not self.get_flag('CF'))

    def GRP3A(self, op_code):  # TODO
        pass

    def GRP3B(self, op_code):  # TODO
        pass

    def CLC(self, op_code):
        self.__set_target(self.set_flags, CF=False)

    def STC(self, op_code):
        self.__set_target(self.set_flags, CF=True)

    def CLI(self, op_code):
        self.__set_target(self.set_flags, IF=False)

    def STI(self, op_code):
        self.__set_target(self.set_flags, IF=True)

    def CLD(self, op_code):
        self.__set_target(self.set_flags, DF=False)

    def STD(self, op_code):
        self.__set_target(self.set_flags, DF=True)

    def GRP4(self, op_code):  # TODO
        pass

    def GRP5(self, op_code):  # TODO
        pass
