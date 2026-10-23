// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "ArmCpuTimer.h"
#include <cassert>

int main()
{
    uint32_t output = 99;
    assert(ArmCpuTimerIntid(nullptr, 0, &output) && output == 27);
    uint8_t cell[8] = {};
    for (uint32_t intid = 0; intid < 256; ++intid) {
        cell[0] = static_cast<uint8_t>(intid);
        output = 99;
        const bool valid = ArmCpuTimerIntid(cell, 4, &output);
        assert(valid == (intid >= 16 && intid <= 31));
        if (valid) {
            assert(output == intid);
            const uint32_t mask = ArmCpuSgiPpiEnable(output);
            assert((mask & 0xffffU) == 0xffffU);
            assert((mask & 0xffff0000U) == (uint32_t(1) << intid));
        } else {
            assert(output == 99);
        }
    }
    cell[0] = 19;
    for (size_t length = 0; length < 9; ++length) {
        output = 99;
        assert(ArmCpuTimerIntid(cell, length, &output) == (length == 4));
        assert(output == (length == 4 ? 19U : 99U));
    }
    const uint8_t bigEndian[] = {0, 0, 0, 19};
    output = 99;
    assert(!ArmCpuTimerIntid(bigEndian, 4, &output) && output == 99);
    assert(!ArmCpuTimerIntid(nullptr, 4, &output) && output == 99);
    assert(!ArmCpuTimerIntid(cell, 4, nullptr));
    // The real property can be unaligned in the packed ADT.
    const uint8_t unaligned[] = {0xff, 19, 0, 0, 0};
    assert(ArmCpuTimerIntid(unaligned + 1, 4, &output) && output == 19);
    return 0;
}
