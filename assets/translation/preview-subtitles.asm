; ARM946E-S / ARMv5TE. Entered by the ARM9 audio update's Thumb BL.
; All overwritten instructions and literal words are assembled from source.
.nds
.create "payload.bin", 0x023D0000
.thumb
entry:
    bx pc
    nop
.arm
    push {r0-r12,lr}
    bl 0x02107514             ; original audio service call
    sub sp, sp, 8
    ldr r4, =0x021C29EC      ; three existing ADX channel records
    mov r5, 3
find_voice:
    ldr r0, [r4, 12]
    cmp r0, 0
    beq next_voice
    ldr r6, [r4, 4]
    ldr r0, =330
    sub r0, r6, r0
    cmp r0, 3
    bls found_voice
next_voice:
    add r4, r4, 16
    subs r5, r5, 1
    bne find_voice
    b hide
found_voice:
    ldr r0, [r4]
    bl 0x02107BE4           ; wrapper -> ACSSBADX slot
    ldr r0, [r0]
    ldr r0, [r0, 8]        ; ACSSBADX -> sound object
    ldr r0, [r0, 4]        ; sound object -> ADXT handle
    cmp r0, 0
    beq hide
    mov r1, sp
    add r2, sp, 4
    bl 0x020F9160           ; ADXT_GetTime(handle, samples, frequency)
    ldr r9, [sp]
    ldr r10, [sp, 4]
    cmp r10, 0
    beq hide
    mov r0, 1000
    mul r9, r0, r9
    ldr r4, =cue_table
    ldr r5, =cue_count
scan_cue:
    ldr r0, [r4]
    cmp r0, r6
    bne next_cue
    ldr r0, [r4, 4]
    mul r1, r0, r10
    cmp r9, r1
    blo next_cue
    ldr r0, [r4, 8]
    mul r1, r0, r10
    cmp r9, r1
    blo show_cue
next_cue:
    add r4, r4, 16
    subs r5, r5, 1
    bne scan_cue
hide:
    ldr r4, =active
    ldr r0, [r4]
    cmp r0, 0
    beq finish
    bl restore_video
    mov r0, 0
    str r0, [r4]
    mvn r0, 0
    str r0, [r4, 4]
    b finish
show_cue:
    ldr r7, [r4, 12]
    ldr r4, =active
    ldr r0, [r4]
    cmp r0, 0
    bne already_active
    ; Preview renderer puts main/3D on the lower display, bank C on sub BG.
    ; Refuse other video layouts instead of taking another screen's VRAM.
    ldr r0, =0x04000304
    ldrh r0, [r0]
    tst r0, 0x8000
    bne finish
    ldr r0, =0x04000242
    ldrb r0, [r0]
    cmp r0, 0x84
    bne finish
    bl save_video
    mov r0, 1
    str r0, [r4]
already_active:
    ldr r0, [r4, 4]
    cmp r0, r7
    beq enable_video
    str r7, [r4, 4]
    ldr r0, =panel_data
    add r0, r0, r7, lsl 11
    ldr r1, =0x06200020
    mov r2, 2048
expand_byte:
    ldrb r3, [r0], 1
    mov r5, 0
    mov r6, 0
expand_pixel:
    and r8, r3, 3
    cmp r8, 0
    add r8, r8, r8, lsl 2
    moveq r8, 1
    orr r5, r5, r8, lsl r6
    mov r3, r3, lsr 2
    add r6, r6, 4
    cmp r6, 16
    bne expand_pixel
    strh r5, [r1], 2
    subs r2, r2, 1
    bne expand_byte
enable_video:
    ; Text panel is 256 x 32 pixels at y=144, lower-center on the upper display.
    ldr r0, =0x04001000
    ldr r1, =0x00010100
    str r1, [r0]
    ldr r1, =0x1F00
    strh r1, [r0, 8]
    mov r1, 0
    str r1, [r0, 16]
    str r1, [r0, 0x50]
    strh r1, [r0, 0x54]
    strh r1, [r0, 0x6C]
finish:
    add sp, sp, 8
    pop {r0-r12,lr}
    bx lr

save_video:
    push {r4-r7,lr}
    ldr r0, =0x04001000
    ldr r1, =saved_regs
    ldr r2, [r0]
    str r2, [r1]
    ldr r2, [r0, 8]
    str r2, [r1, 4]
    ldr r2, [r0, 16]
    str r2, [r1, 8]
    ldr r2, [r0, 0x50]
    str r2, [r1, 12]
    ldrh r2, [r0, 0x54]
    str r2, [r1, 16]
    ldrh r2, [r0, 0x6C]
    str r2, [r1, 20]
    ldr r0, =0x06200000
    ldr r1, =saved_tiles
    ldr r2, =4128
    bl copy_words
    ldr r0, =0x0620F800
    ldr r1, =saved_map
    mov r2, 2048
    bl copy_words
    ldr r0, =0x05000400
    ldr r1, =saved_palette
    mov r2, 32
    bl copy_words
    ldr r0, =0x06200000
    mov r1, 0
    mov r2, 32
    bl fill_words
    ldr r0, =0x0620F800
    mov r1, 0
    mov r2, 2048
    bl fill_words
    ldr r0, =0x0620FC80     ; row 18
    mov r1, 1
map_tile:
    strh r1, [r0], 2
    add r1, r1, 1
    cmp r1, 129
    bne map_tile
    ldr r0, =0x05000402
    mov r1, 0
    strh r1, [r0]
    ldr r0, =0x0500040A
    ldr r1, =0x2129
    strh r1, [r0]
    ldr r0, =0x05000414
    ldr r1, =0x4274
    strh r1, [r0]
    ldr r0, =0x0500041E
    ldr r1, =0x6BDF
    strh r1, [r0]
    ldr r0, =0x04001008
    ldr r1, =0x1F00
    strh r1, [r0]
    mov r1, 0
    str r1, [r0, 8]
    ldr r0, =0x04001050
    str r1, [r0]
    strh r1, [r0, 4]
    strh r1, [r0, 0x1C]
    pop {r4-r7,pc}
restore_video:
    push {r4-r7,lr}
    ; Disable the borrowed BG before restoring its data and registers.
    ldr r0, =0x04001000
    mov r1, 0x10000
    str r1, [r0]
    ldr r0, =saved_tiles
    ldr r1, =0x06200000
    ldr r2, =4128
    bl copy_words
    ldr r0, =saved_map
    ldr r1, =0x0620F800
    mov r2, 2048
    bl copy_words
    ldr r0, =saved_palette
    ldr r1, =0x05000400
    mov r2, 32
    bl copy_words
    ldr r0, =0x04001000
    ldr r1, =saved_regs
    ldr r2, [r1, 4]
    str r2, [r0, 8]
    ldr r2, [r1, 8]
    str r2, [r0, 16]
    ldr r2, [r1, 12]
    str r2, [r0, 0x50]
    ldr r2, [r1, 16]
    strh r2, [r0, 0x54]
    ldr r2, [r1, 20]
    strh r2, [r0, 0x6C]
    ldr r2, [r1]
    str r2, [r0]
    pop {r4-r7,pc}
copy_words:
    ldr r3, [r0], 4
    str r3, [r1], 4
    subs r2, r2, 4
    bne copy_words
    bx lr
fill_words:
    str r1, [r0], 4
    subs r2, r2, 4
    bne fill_words
    bx lr
code_end:
    .pool
    .align 4
active:
    .word 0, -1
saved_regs:
    .fill 24
saved_tiles:
    .fill 4128
saved_map:
    .fill 2048
saved_palette:
    .fill 32
cue_table:
    .incbin "cues.bin"
cue_table_end:
    .definelabel cue_count, (cue_table_end-cue_table)/16
panel_data:
    .incbin "panels.bin"
payload_end:
.close

.create "hook.bin", 0x0200B660
.thumb
    bl entry
.close

; OS_GetInitArenaHi(Main): reserve 64 KiB immediately below the original end.
.create "arena.bin", 0x0211ECEC
    .word 0x023D0000
.close
