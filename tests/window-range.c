/* SPDX-License-Identifier: BSD-2-Clause-Patent */
#include "WindowRange.h"
#include <assert.h>
#include <stdio.h>

int main(void)
{
    DARWIN_WINDOW_RANGE polaris = {0x100000000ULL, 0x7cc00000ULL};   /* 6 GiB variant bank */
    DARWIN_WINDOW_RANGE unrestricted = {0, 0};
    unsigned long long address = 0x1234;
    assert(DarwinWindowRangeValid(polaris.Base, polaris.Bytes));
    assert(!DarwinWindowRangeValid(1, 0x1000));
    assert(!DarwinWindowRangeValid(0x1000, 0));
    assert(!DarwinWindowRangeValid(0x1000, 1));
    assert(!DarwinWindowRangeValid(0xfffffffffffff000ULL, 0x2000));
    // A suitable low-bank run must still be refused by the polaris constraint.
    assert(!DarwinWindowCandidate(0xb0000000, 0xfcc00000, 0x7004000, 0x10000000, &polaris, &address));
    assert(address == 0x1234);
    assert(DarwinWindowCandidate(polaris.Base, polaris.Base + polaris.Bytes, 0x7004000, 0x40000000, &polaris, &address));
    assert(address == 0x100004000ULL);
    // Clip a run straddling the bank; the candidate never starts below it.
    assert(DarwinWindowCandidate(0xc0000000, 0x180000000, 0x7004000, 0x10000000, &polaris, &address));
    assert(address == 0x100004000ULL);
    address = 0x1234;
    assert(!DarwinWindowCandidate(0x1e0000000, 0x300000000, 0x7004000, 0x40000000, &polaris, &address));
    assert(address == 0x1234);
    assert(DarwinWindowCandidate(0xb0001000, 0xd0000000, 0x7004000, 0x10000000, &unrestricted, &address));
    assert(address == 0xb0204000);
    assert(!DarwinWindowCandidate(0, ~0ULL, 1, 0x1000, &unrestricted, &address));
    assert(!DarwinWindowCandidate(~0ULL - 0xfff, ~0ULL, 0, 0x1000, &unrestricted, &address));
    assert(!DarwinWindowCandidate(0, 0, 0, 0x1000, &unrestricted, &address));
    assert(!DarwinWindowCandidate(0, 0x200000, 0, 0, &unrestricted, &address));
    assert(!DarwinWindowCandidate(0, 0x200000, 0, 0x1000, NULL, &address));
    assert(!DarwinWindowCandidate(0, 0x200000, 0, 0x1000, &unrestricted, NULL));
    // Check the complete 2 MiB range of page-aligned kernel block offsets.
    for (unsigned offset = 0; offset < 0x200000; offset += 0x1000) {
        assert(DarwinWindowCandidate(0x100001000ULL, 0x200000000ULL,
               0x7000000 + offset, 0x10000000, &polaris, &address));
        assert(address >= polaris.Base && address + 0x10000000 <= polaris.Base + polaris.Bytes);
        assert((address & 0x1fffff) == offset);
    }
    puts("PASS: loader window range, low-bank refusal, clipping, overflow and all 512 block offsets");
    return 0;
}
