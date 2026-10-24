; Selftest ROM — ARM7 program: idle forever (the test only needs the ARM9).
.nds
.create "arm7.bin", 0x02380000
.arm
_start7:
    b     _start7
.close
