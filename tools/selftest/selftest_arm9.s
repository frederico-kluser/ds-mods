; ---------------------------------------------------------------------------
; Selftest ROM — ARM9 program (armips). Free, original code: no Nintendo data.
;
; Layout mimics a retail NitroSDK binary so the factory tools treat it like a game:
;   0x02000000  "decrypted secure area" id (E7FFDEFF E7FFDEFF) + padding
;   0x02000800  entry point (_start), like retail games
;   CONFIG      fixed-address data block (the "data mod" target)
;   SCORE_STEP  fixed-address instruction (the "code mod" target)
;   ModuleParams with the nitrocode magics (+ the 12-byte footer written by the builder)
;
; Behaviour (top screen, engine A, VRAM framebuffer):
;   * whole screen filled with CONFIG.base_color (blue); red while A is held
;   * a white bar sweeps down one line per frame (motion for freeze detection)
;   * a green bar at the bottom grows 8 px per point of score
;   * each new A press adds SCORE_STEP points to STATE.score
;   * pressing START freezes the program forever (simulated crash for the analyzer)
; STATE (main RAM, fixed): +0 frame counter, +4 score, +8 previous keys, +12 bar y, +16 magic
; ---------------------------------------------------------------------------
.nds
.create "arm9.bin", 0x02000000
.arm

STATE        equ 0x02100000
REG_DISPCNT  equ 0x04000000
REG_VCOUNT   equ 0x04000006
REG_KEYINPUT equ 0x04000130
REG_IME      equ 0x04000208
REG_VRAMCNTA equ 0x04000240
REG_POWCNT1  equ 0x04000304
VRAM_A       equ 0x06800000
OV0_ADDR     equ 0x02080000

secure_area_id:
    .word 0xE7FFDEFF, 0xE7FFDEFF

.org 0x02000100
config:                     ; data-mod target (stable address, documented in selftest.py)
config_base_color:
    .halfword 0x7C00        ; BGR555 blue
config_bar_color:
    .halfword 0x7FFF        ; white
config_score_color:
    .halfword 0x03E0        ; green
config_pad:
    .halfword 0
config_magic:
    .ascii "DSMODS-SELFTEST"
    .byte 0

.org 0x02000800
_start:
    ; rewrite the whole static image in place, word by word (same values), like a retail crt0 that
    ; decompresses arm9 in place: the emulator then treats these pages as written at runtime, so a
    ; savestate carries this code and a mod must be re-applied in RAM (what the hotpatcher tests).
    ldr   r0, =0x02000000
    ldr   r1, =autoload_start
@@touch:
    ldr   r2, [r0]
    str   r2, [r0], #4
    cmp   r0, r1
    blt   @@touch
    ldr   r0, =REG_IME
    mov   r1, #0
    str   r1, [r0]
    ldr   r0, =REG_POWCNT1
    ldr   r1, =0x8003       ; LCDs on, 2D engine A on, engine A on the top screen
    strh  r1, [r0]
    ldr   r0, =REG_VRAMCNTA
    mov   r1, #0x80         ; VRAM A enabled, LCDC mode
    strb  r1, [r0]
    ldr   r0, =REG_DISPCNT
    ldr   r1, =0x00020000   ; display mode 2: framebuffer from VRAM A
    str   r1, [r0]
    ldr   r0, =ov0_image        ; "load" overlay 0: copy its image to 0x02080000
    ldr   r1, =OV0_ADDR
    mov   r2, #(ov0_image_end - ov0_image)
@@ovcopy:
    ldr   r3, [r0], #4
    str   r3, [r1], #4
    subs  r2, r2, #4
    bne   @@ovcopy
    ldr   r4, =STATE
    mov   r1, #0
    str   r1, [r4, #0]
    str   r1, [r4, #4]
    str   r1, [r4, #8]
    str   r1, [r4, #12]
    ldr   r1, =0x54534D44   ; "DMST" — lets scripts check that STATE is live
    str   r1, [r4, #16]
    mvn   r1, #0
    str   r1, [r4, #20]     ; no background drawn yet
    mov   r1, #0
    str   r1, [r4, #24]

main_loop:
    bl    wait_vblank
    ldr   r0, =REG_KEYINPUT
    ldrh  r5, [r0]
    mvn   r5, r5
    ldr   r1, =0x3FF
    and   r5, r5, r1        ; r5 = keys held (active high)
    tst   r5, #8            ; START -> simulated crash
    bne   crash
    ldr   r6, [r4, #8]
    str   r5, [r4, #8]
    bic   r6, r5, r6        ; r6 = keys newly pressed
    tst   r6, #1
    beq   no_new_a
    ldr   r1, [r4, #4]
    bl    score_step
    str   r1, [r4, #4]
no_new_a:
    ldr   r1, [r4, #0]
    add   r1, r1, #1
    str   r1, [r4, #0]
    ldr   r1, [r4, #12]
    add   r1, r1, #1
    cmp   r1, #192
    movge r1, #0
    str   r1, [r4, #12]
    ldr   r2, =config
    ldrh  r7, [r2, #0]      ; base colour
    tst   r5, #1
    movne r7, #0x1F         ; A held -> red
    bl    draw
    b     main_loop

crash:
    b     crash

.org 0x02000A00
score_step:                 ; code-mod target: r1 += 1 (a mod can change the immediate)
    add   r1, r1, #1
    bx    lr

wait_vblank:
    ldr   r0, =REG_VCOUNT
@@out:
    ldrh  r1, [r0]
    cmp   r1, #192
    bge   @@out
@@in:
    ldrh  r1, [r0]
    cmp   r1, #192
    blt   @@in
    bx    lr

; r4 = STATE, r7 = background colour.
; Only what changed is redrawn, so all VRAM writes fit inside vblank (a full-screen fill every frame
; outlasts vblank and the scanout would race it — real DS behaviour that melonDS emulates).
; STATE +20 = background colour on screen (0xFFFFFFFF = none yet), +24 = row of the bar on screen.
draw:
    push  {r4-r11, lr}
    orr   r8, r7, r7, lsl #16
    ldr   r3, [r4, #20]
    cmp   r3, r7
    beq   @@erase_old_bar
    str   r7, [r4, #20]             ; background changed: full fill
    mov   r9, r8
    mov   r10, r8
    mov   r11, r8
    ldr   r0, =VRAM_A
    ldr   r1, =(256*192*2)/16
@@fill:
    stmia r0!, {r8-r11}
    subs  r1, r1, #1
    bne   @@fill
    b     @@draw_bar
@@erase_old_bar:
    ldr   r1, [r4, #24]             ; repaint the previous bar row with the background
    ldr   r0, =VRAM_A
    add   r0, r0, r1, lsl #9
    mov   r1, #128
@@erase:
    str   r8, [r0], #4
    subs  r1, r1, #1
    bne   @@erase
@@draw_bar:
    ; sweeping bar: one full row at y = STATE.bar_y
    ldr   r2, =config
    ldrh  r8, [r2, #2]
    orr   r8, r8, r8, lsl #16
    ldr   r1, [r4, #12]
    str   r1, [r4, #24]
    ldr   r0, =VRAM_A
    add   r0, r0, r1, lsl #9    ; y * 512 bytes
    mov   r1, #128
@@bar:
    str   r8, [r0], #4
    subs  r1, r1, #1
    bne   @@bar
    ; score bar: rows 176..183, width = min(score * 8, 256); colour comes from overlay 0
    ldr   r8, =OV0_ADDR
    ldrh  r8, [r8, #0x10]
    ldr   r3, [r4, #4]
    mov   r3, r3, lsl #3
    cmp   r3, #256
    movgt r3, #256
    cmp   r3, #0
    beq   @@done
    mov   r6, #176
@@row:
    ldr   r0, =VRAM_A
    add   r0, r0, r6, lsl #9
    mov   r1, r3
@@px:
    strh  r8, [r0], #2
    subs  r1, r1, #1
    bne   @@px
    add   r6, r6, #1
    cmp   r6, #184
    blt   @@row
@@done:
    pop   {r4-r11, pc}

autoload_done_callback:     ; header 0x070 points here (NitroSDK convention)
    bx    lr

.pool

.align 4
ov0_image:                  ; embedded copy of overlay 0 (assembled first, see selftest_ov0.s)
    .import "ov0.bin"
ov0_image_end:

.align 4
autoload_list:              ; NitroSDK autoload entries: dest address, size, bss size
    .word 0x01FF8000, itcm_end - itcm_start, 0
    .word 0x027E0000, dtcm_end - dtcm_start, 0x20
autoload_list_end:

.align 4
module_params:              ; NitroSDK _start_ModuleParams layout
    .word autoload_list
    .word autoload_list_end
    .word autoload_start
    .word bss_start
    .word bss_end
    .word 0                 ; compressed_static_end (0 = not compressed)
    .word 0x04020000        ; sdk_version (fake 4.2.0, for tooling only)
    .word 0xDEC00621        ; nitrocode (BE)
    .word 0x2106C0DE        ; nitrocode (LE)

.align 32
autoload_start:             ; autoload payloads (copied by a real crt0; unused by this program)
itcm_start:
    .word 0xE12FFF1E        ; bx lr (a tiny ITCM "routine")
    .fill 0x1C, 0
itcm_end:
dtcm_start:
    .ascii "DSMODS-DTCM-DATA"
    .fill 0x10, 0
dtcm_end:
bss_start:
bss_end:
.close
