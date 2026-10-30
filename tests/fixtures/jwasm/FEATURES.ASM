; macro, INCLUDE, a false conditional block and multi-line data
        .model small
        .stack 100h
DEBUG = 0
putc    macro ch
        mov     dl, ch
        mov     ah, 2
        int     21h
        endm
        .data
big     db      "0123456789abcdefghijklmnopqrstuvwxyz", 0
count   dw      5
        .code
start:  mov     ax, @data
        mov     ds, ax
        putc    'A'
        call    helper
if DEBUG
        int     3
else
        nop
endif
        mov     ax, 4C00h
        int     21h
        include FEATURES.INC
        end     start
