// SPDX-License-Identifier: BSD-2-Clause-Patent
// QEMU virt only. Never include this synthetic serial fixture in a phone image.
.section .text, "ax"
.global _start
_start:
    mrs x1, sctlr_el1
    mov x2, #0x1005
    tst x1, x2
    b.ne failed
    ldr w1, [x0]
    mov w2, #0x20002
    cmp w1, w2
    b.ne failed
    ldr x1, [x0, #8]
    ldr x2, =0xfffffff007004000
    cmp x1, x2
    b.ne failed
    ldr x3, [x0, #16]
    mov x4, #0x40000000
    cmp x3, x4
    b.lo failed
    mov x4, #0x80000000
    mov x5, #0x10000000
    add x5, x3, x5
    cmp x5, x4
    b.hi failed
    eor x4, x3, x1
    tst x4, #0x1fffff
    b.ne failed
    ldr x4, [x0, #24]
    mov x5, #0x10000000
    cmp x4, x5
    b.ne failed
    // Darwin 23's larger boot_args, beyond the Darwin 22 structure.
    ldr x4, [x0, #0x478]
    cmp x4, x5
    b.ne failed
    adr x4, _start
    sub x4, x4, x3
    cmp x4, #0x1000
    b.ne failed
    adr x4, command_line
    add x5, x0, #0x6c
1:
    ldrb w6, [x4], #1
    ldrb w7, [x5], #1
    cmp w6, w7
    b.ne failed
    cbnz w6, 1b
    adr x0, passed
    b puts
failed:
    adr x0, failure
puts:
    mov x1, #0x09000000 // QEMU PL011, not a polaris resource
2:
    ldrb w2, [x0], #1
    cbz w2, done
3:
    ldr w3, [x1, #0x18]
    tbnz w3, #5, 3b
    str w2, [x1]
    b 2b
done:
    wfe
    b done
command_line:
    .asciz "polaris.fixture=1"
passed:
    .asciz "POLARIS-LOADER-FIXTURE: constrained range and Darwin23 boot_args PASS\n"
failure:
    .asciz "POLARIS-LOADER-FIXTURE: FAIL\n"
