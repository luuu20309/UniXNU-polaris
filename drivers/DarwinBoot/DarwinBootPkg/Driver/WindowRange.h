// SPDX-License-Identifier: BSD-2-Clause-Patent
#ifndef DARWINBOOT_WINDOW_RANGE_H
#define DARWINBOOT_WINDOW_RANGE_H

// An optional allocation constraint, never a claim that RAM is available.
// The actual UEFI conventional-memory run and AllocatePages still decide.
typedef struct {
  unsigned long long Base;
  unsigned long long Bytes;
} DARWIN_WINDOW_RANGE;

static inline int DarwinWindowRangeValid(unsigned long long base, unsigned long long bytes)
{
  return bytes && !(base & 0xfffULL) && !(bytes & 0xfffULL) && bytes <= ~0ULL - base;
}

// RunEnd and RangeEnd are exclusive. Keep the kernel VA/PA block offset
// congruent, including overflow checks before rounding or adding offsets.
static inline int DarwinWindowCandidate(unsigned long long runStart, unsigned long long runEnd,
    unsigned long long kernelBase, unsigned long long windowBytes,
    const DARWIN_WINDOW_RANGE *range, unsigned long long *result)
{
  const unsigned long long mask = 0x1fffffULL;
  if (!range || !result || !windowBytes || runEnd <= runStart || (kernelBase & 0xfffULL)) { return 0; }
  if (range->Bytes) {
    if (!DarwinWindowRangeValid(range->Base, range->Bytes)) { return 0; }
    if (runStart < range->Base) { runStart = range->Base; }
    if (runEnd > range->Base + range->Bytes) { runEnd = range->Base + range->Bytes; }
  } else if (range->Base) { return 0; }
  if (runEnd <= runStart || runStart > ~0ULL - mask) { return 0; }
  unsigned long long aligned = (runStart + mask) & ~mask;
  unsigned long long offset = kernelBase & mask;
  if (aligned > ~0ULL - offset) { return 0; }
  unsigned long long candidate = aligned + offset;
  if (candidate < runStart || candidate >= runEnd || windowBytes > runEnd - candidate) { return 0; }
  *result = candidate;
  return 1;
}
#endif
