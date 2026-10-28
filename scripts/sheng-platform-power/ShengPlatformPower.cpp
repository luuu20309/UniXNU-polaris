// SPDX-License-Identifier: BSD-2-Clause-Patent
//
// Publish a provider-less ApplePMGRNub so the unchanged target AMFI can finish
// its PMGR latch.
//
// Why this is needed
// ------------------
// AMFI's ConfigurationSettings.cpp:235 panics with "AMFI: No PMGR?" after
// resolving ApplePMGRNub through IOService::serviceMatching() and finding
// nothing. ApplePMGR publishes that nub from its AppleBringUpPMGR personality:
//
//     IOProviderClass = AppleARMIODevice
//     IONameMatch     = pmgr1,bring-up
//
// ArmDTPlatform publishes every arm-io child as an IOPlatformDevice, and
// IOKit gates personalities with service->metaCast(className), so an
// IOPlatformDevice can never satisfy IOProviderClass = AppleARMIODevice.
// Adding a /arm-io/pmgr node therefore does not help; the nub class itself has
// to come from ApplePMGR.
//
// The officially documented way out (UniXNU device records, sirius): AMFI reads
// the nub's provider at nub+0x88 and calls its readAon/writeAon slots. Those
// helpers return 0 / do nothing when the provider is NULL, so a real
// ApplePMGRNub instance with no provider satisfies the latch without any PMGR
// hardware. That is what this file publishes: it asks OSMetaClass for the real
// class by name (no compile-time dependency on ApplePMGR headers), allocates an
// instance, attaches it to the platform expert and registers it.
//
// Call site: bsd/kern/bsd_init.c calls sheng_pmgr_run() when
// XNU_SHENG_PMGR=1 and the boot argument sheng-pmgr=2 selects it.

#include <IOKit/IOPlatformExpert.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOService.h>
#include <libkern/c++/OSMetaClass.h>
#include <libkern/c++/OSSymbol.h>
#include <pexpert/pexpert.h>

extern "C" {

// Selected by sheng-pmgr=2; see the selection below.
static volatile int sheng_pmgr_selected = 0;
volatile uint32_t sheng_pmgr_published = 0;
volatile uint32_t sheng_pmgr_status = 0;

// Report one line so a serial or panic-screen probe can tell which branch ran.
static void
sheng_pmgr_report(const char *what, uint32_t value)
{
	IOLog("ShengPlatformPower: %s (%u)\n", what, value);
}

void
sheng_pmgr_run(void)
{
	const OSSymbol *name;
	const OSMetaClass *meta;
	OSObject *object;
	IOService *nub;
	IOService *platform;
	unsigned selection = 0;

	if (sheng_pmgr_selected) {
		return;
	}
	sheng_pmgr_selected = 1;

	// sheng-pmgr=2 is the documented value for the bring-up policy.
	if (!PE_parse_boot_argn("sheng-pmgr", &selection, sizeof(selection))) {
		sheng_pmgr_status = 1;
		return;
	}
	if (selection != 2) {
		sheng_pmgr_status = 2;
		return;
	}

	name = OSSymbol::withCStringNoCopy("ApplePMGRNub");
	if (name == nullptr) {
		sheng_pmgr_status = 3;
		return;
	}

	// The class is registered by the prelinked ApplePMGR kext, not compiled
	// into this archive, so resolve it at runtime.
	meta = OSMetaClass::getMetaClassWithName(name);
	if (meta == nullptr) {
		sheng_pmgr_status = 4;
		name->release();
		return;
	}

	object = meta->alloc();
	if (object == nullptr) {
		sheng_pmgr_status = 5;
		name->release();
		return;
	}

	nub = OSDynamicCast(IOService, object);
	if (nub == nullptr) {
		object->release();
		sheng_pmgr_status = 6;
		name->release();
		return;
	}

	// No provider is passed: the AIM's readAon/writeAon helpers behind
	// AMFI's latch return 0 when the provider slot is NULL, which is exactly
	// the state we want. init with no arguments keeps that slot clear.
	if (!nub->init()) {
		nub->release();
		sheng_pmgr_status = 7;
		name->release();
		return;
	}

	platform = IOService::getPlatform();
	if (platform == nullptr) {
		nub->release();
		sheng_pmgr_status = 8;
		name->release();
		return;
	}

	if (!nub->attach(platform)) {
		nub->release();
		sheng_pmgr_status = 9;
		name->release();
		return;
	}

	nub->registerService();
	sheng_pmgr_published = 1;
	sheng_pmgr_status = 10;
	sheng_pmgr_report("published a provider-less ApplePMGRNub", 10);

	nub->release();
	name->release();
}

} /* extern "C" */
