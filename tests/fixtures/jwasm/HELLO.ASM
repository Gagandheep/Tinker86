	.model small
	.stack 100h
	.data
msg	db	'Hello, 8086!$'
	.code
start:	mov	ax, @data
	mov	ds, ax
	mov	dx, offset msg
	mov	ah, 9
	int	21h
	mov	ax, 4C07h
	int	21h
	end	start
