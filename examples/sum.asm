; Adds up a table of numbers into "total" and
; prints the sum in decimal (Sum = 100).
; Try the debugger on it: add a watchpoint on
; total in the Watch dock, then Run (F5).
        .model small
        .stack 100h

        .data
numbers dw      12, 7, 30, 1, 50
COUNT   equ     ($ - numbers) / 2
total   dw      0
label1  db      'Sum = $'
crlf    db      13, 10, '$'

        .code
start:  mov     ax, @data
        mov     ds, ax

        mov     si, offset numbers
        mov     cx, COUNT
next:   mov     ax, [si]
        add     total, ax               ; writes total
        add     si, 2
        loop    next

        mov     dx, offset label1       ; DOS: print $-terminated string
        mov     ah, 09h
        int     21h
        mov     ax, total
        call    printdec
        mov     dx, offset crlf
        mov     ah, 09h
        int     21h

        mov     ax, 4C00h               ; DOS: exit with code 0
        int     21h

; Prints AX as an unsigned decimal number.
printdec proc
        mov     bx, 10
        xor     cx, cx
divide: xor     dx, dx
        div     bx                      ; AX = AX / 10, DX = last digit
        push    dx
        inc     cx
        or      ax, ax
        jnz     divide
print:  pop     dx
        add     dl, '0'
        mov     ah, 02h                 ; DOS: print the character in DL
        int     21h
        loop    print
        ret
printdec endp

        end     start
