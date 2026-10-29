; Prints a greeting and its length, then exits with code 0.
; Build with F7 in the emulator (TASM + TLINK in DOSBox), debug with F8;
; the output appears in the Screen view.
        .model small
        .stack 100h

        .data
message db      'Hello from the 8086!', 13, 10, '$'

        .code
start:  mov     ax, @data
        mov     ds, ax

        mov     dx, offset message      ; DOS: print $-terminated string
        mov     ah, 09h
        int     21h

        mov     si, offset message      ; count characters up to '$'
        xor     cx, cx
count:  lodsb
        cmp     al, '$'
        je      done
        inc     cx
        jmp     count

done:   mov     ax, 4C00h               ; DOS: exit with code 0
        int     21h
        end     start
