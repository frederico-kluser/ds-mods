; Selftest ROM — ARM9 overlay 0 (data only). Lives at 0x02080000 like a retail overlay.
; The ARM9 program copies an embedded image of it to RAM at boot (stand-in for FS_LoadOverlay),
; so overlay patching and the overlay-guarded RAM hotpatch can be tested.
.nds
.create "ov0.bin", 0x02080000
.arm
ov0_signature:
    .ascii "OV00-DSMODS-SELF"   ; 16 bytes: used as the residency guard by the hotpatcher
ov0_score_color:
    .halfword 0x03E0            ; 0x02080010: score bar colour (green)  <- overlay mod target
ov0_reserved:
    .halfword 0
    .ascii "DSMODS-OVERLAY-0"
    .fill 0x0C, 0
.close
