// SPDX-License-Identifier: BSD-2-Clause-Patent
//
// Link stubs for the NativeMobileDisplay build on polaris.
//
// Two symbols are needed that neither the driver submodule nor the open XNU
// tree provides:
//
// 1. IOMobileFramebuffer's constructors and destructor.
//    OriginalMobileFramebufferABI.h declares the family base class with
//    OSDeclareAbstractStructors(IOMobileFramebuffer), which only *declares*
//    NativeMobileDisplay's base ctor/dtor. The real family kext supplies them
//    at runtime; in this prelinked kernel the definitions have to exist so the
//    weak references from the driver resolve. They forward to IOService, which
//    is the class IOMobileFramebuffer actually derives from.
//
// 2. native_mobile_display_provider_probe().
//    iokit/Kernel/IOUserClient.cpp calls this unconditionally when
//    XNU_NATIVE_MOBILE_DISPLAY is on. Upstream pairs the driver with an
//    instrument archive that implements it; that archive is not part of this
//    checkout, so the probe is a no-op here. It observes, it does not drive the
//    display, so an empty body keeps the display path intact.
//
// This file deliberately defines nothing else: every other entry the driver
// needs comes from its own object.

#include "OriginalMobileFramebufferABI.h"

// The header declares the base with OSDeclareAbstractStructors, so the
// definition has to be the matching abstract form: it supplies the ctor/dtor
// and the MetaClass the derived class links against.
OSDefineMetaClassAndAbstractStructors(IOMobileFramebuffer, IOService)

extern "C" void
native_mobile_display_provider_probe(void)
{
	// No-op: the qualified probe lives in the upstream instrument archive.
}
