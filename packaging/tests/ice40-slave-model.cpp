// SPDX-License-Identifier: Apache-2.0
//
// Ice40::program_cram_slave() (openFPGALoader patch "ice40: an SRAM load
// over any SPI slave interface") against a model of an iCE40 and its lines.
// Linked to the objects of a built, patched openFPGALoader tree by
// packaging/tests/ice40-slave-model.sh; no GPIO hardware is touched.
//
// Each case states what must happen: the result (loaded, or the error's
// words), the order of what was done to the lines, and the state the lines
// are left in. Exits 1 on the first case that does not hold.

#include <signal.h>
#include <string.h>

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "ice40.hpp"

enum Fault {
	NO_FAULT, NO_ICE40, CDONE_LOW_BEFORE, CDONE_READ_FAILS_IN_RESET,
	CONFIG_FAILS, STALL, RELEASE_FAILS, TAKE_FAILS, SIGNAL_IN_IMAGE,
	SIGNAL_IN_RESET, SIGNAL_AT_END
};

struct Model: Ice40SpiSlaveInterface {
	Fault fault;
	double hz;
	bool creset_low = false, ss_high = true, taken = false, cdone_v = true;
	bool slave = false;
	uint64_t image_bits = 0, want_bits;
	int writes = 0, cdone_reads = 0;
	std::vector<std::string> log;

	Model(Fault f, double rate, uint64_t bits): fault(f), hz(rate),
		want_bits(bits) { cdone_v = (f != CDONE_LOW_BEFORE); }

	void hold_reset(bool hold) override {
		log.push_back(hold ? "CRESET low" : "CRESET let go");
		if (fault == SIGNAL_IN_RESET && hold)
			raise(SIGTERM);
		if (hold) {
			creset_low = true;
			if (fault != NO_ICE40)
				cdone_v = false;
			slave = false;
			return;
		}
		creset_low = false;
		if (fault == NO_ICE40)
			return;
		if (taken && !ss_high) {
			slave = true;
			cdone_v = false;
			image_bits = 0;
		} else {
			cdone_v = true;
			log.push_back("boots from flash");
		}
	}
	bool cdone() override {
		cdone_reads++;
		if (fault == CDONE_READ_FAILS_IN_RESET && creset_low)
			throw std::runtime_error("unable to read GPIO17 (modelled)");
		return cdone_v;
	}
	void take_bus() override {
		if (!creset_low)
			throw std::logic_error("take_bus with CRESET not low");
		if (fault == TAKE_FAILS)
			throw std::runtime_error("take_bus failed (modelled)");
		taken = true;
		ss_high = true;
		log.push_back("take bus");
	}
	void release_bus() override {
		log.push_back("release bus");
		if (fault == RELEASE_FAILS)
			throw std::runtime_error("GPIO8 stuck (modelled)");
		taken = false;
		ss_high = true;
	}
	void set_ss(bool high) override {
		if (!taken)
			throw std::logic_error("set_ss without the bus");
		ss_high = high;
		log.push_back(high ? "SS high" : "SS low");
	}
	void write(const uint8_t *, uint32_t len) override {
		if (!taken || ss_high || !slave)
			throw std::logic_error("write while not selected in slave mode");
		writes++;
		if (log.back() != "write")
			log.push_back("write");
		double seconds = len * 8.0 / hz;
		if (fault == STALL && writes == 5)
			seconds += 0.05;
		for (uint32_t i = 0; i < len; i++) {  // the bit boundaries, by byte
			if (fault == SIGNAL_IN_IMAGE && writes == 3 && i == len / 2)
				raise(SIGTERM);
			abort_check();
			image_bits += 8;
		}
		std::this_thread::sleep_for(std::chrono::duration<double>(seconds));
	}
	void clocks(uint32_t n) override {
		if (!taken)
			throw std::logic_error("clocks without the bus");
		if (fault == SIGNAL_AT_END && n == 49)
			raise(SIGINT);
		for (uint32_t i = 0; i < n; i++)
			abort_check();
		log.push_back("clocks " + std::to_string(n) +
			(ss_high ? " SS high" : " SS low"));
		if (slave && image_bits == want_bits && fault != CONFIG_FAILS &&
				n >= 100)
			cdone_v = true;
	}
};

static int failures = 0;

static void expect(bool ok, const std::string &what)
{
	if (!ok) {
		std::cout << "  FAIL: " << what << std::endl;
		failures++;
	}
}

static std::string joined(const std::vector<std::string> &v)
{
	std::string s;
	for (const std::string &x : v)
		s += (s.empty() ? "" : ", ") + x;
	return s;
}

static void handler_untouched(const char *name, int sig)
{
	struct sigaction now;
	sigaction(sig, NULL, &now);
	expect(now.sa_handler == SIG_DFL,
		std::string(name) + " handler put back after the load");
}

/* error: empty for a load that must succeed, else words it must contain */
static void run(const std::string &name, Fault f, double hz,
		const std::string &error, const std::string &sequence,
		bool cdone_after)
{
	std::vector<uint8_t> img(104161, 0xa5);  // an iCE40UP5K image's size
	Model m(f, hz, img.size() * 8ULL);
	Ice40 ice(&m, "model.bin", "bin", -1);
	std::string got;
	try {
		ice.program_cram_slave(img.data(), img.size());
	} catch (std::exception &e) {
		got = e.what();
	}
	std::cout << "=== " << name << ": " << (got.empty() ? "loaded" : got)
		<< std::endl << "  " << joined(m.log) << std::endl;
	if (error.empty())
		expect(got.empty(), "loads");
	else
		expect(got.find(error) != std::string::npos,
			"error says \"" + error + "\"");
	expect(joined(m.log) == sequence, "sequence is: " + sequence);
	expect(!m.creset_low, "CRESET let go at the end");
	if (f != RELEASE_FAILS)
		expect(!m.taken, "bus released at the end");
	expect(m.cdone_v == cdone_after,
		std::string("CDONE ") + (cdone_after ? "high" : "low") + " at the end");
	handler_untouched("SIGINT", SIGINT);
	handler_untouched("SIGTERM", SIGTERM);
	handler_untouched("SIGHUP", SIGHUP);
}

int main()
{
	const std::string start = "CRESET low, take bus, SS low, CRESET let go, "
		"SS high, clocks 8 SS high, SS low, write";
	const std::string recover = "release bus, CRESET low, CRESET let go, "
		"boots from flash";

	run("normal, 2 MHz", NO_FAULT, 2e6, "",
		start + ", clocks 100 SS low, clocks 49 SS low, SS high, release bus",
		true);
	run("no iCE40 on that reset", NO_ICE40, 2e6,
		"CDONE stayed high with CRESET_B held low",
		"CRESET low, CRESET let go", true);
	run("CDONE low before", CDONE_LOW_BEFORE, 2e6,
		"CDONE is low before the reset", "", false);
	run("CDONE unreadable in reset", CDONE_READ_FAILS_IN_RESET, 2e6,
		"CRESET_B let go, no SPI line was driven",
		"CRESET low, CRESET let go, boots from flash", true);
	run("configuration refused", CONFIG_FAILS, 2e6,
		"CDONE stayed low after the image",
		start + ", clocks 100 SS low, " + recover, true);
	run("too slow, 0.5 MHz", NO_FAULT, 0.5e6, "below the iCE40's 1 MHz minimum",
		start + ", " + recover, true);
	run("too fast", NO_FAULT, 1e12, "above the iCE40's 25 MHz maximum",
		start + ", " + recover, true);
	run("one chunk stalled 50 ms", STALL, 2e6,
		"over bytes 16384-20479 of the image: below",
		start + ", " + recover, true);
	run("release fails at the end", RELEASE_FAILS, 2e6,
		"CRESET_B had already been let go",
		start + ", clocks 100 SS low, clocks 49 SS low, SS high, release bus, "
		"release bus", true);
	run("take_bus fails", TAKE_FAILS, 2e6, "take_bus failed",
		"CRESET low, release bus, CRESET let go, boots from flash", true);
	run("SIGTERM during the image", SIGNAL_IN_IMAGE, 2e6,
		"interrupted by SIGTERM; SPI lines released; the iCE40 was reset and "
		"booted from its flash again (CDONE high)",
		start + ", " + recover, true);
	run("SIGTERM as CRESET goes low", SIGNAL_IN_RESET, 2e6,
		"interrupted by SIGTERM",
		"CRESET low, release bus, CRESET let go, boots from flash", true);
	run("SIGINT during the last 49 clocks", SIGNAL_AT_END, 2e6,
		"interrupted by SIGINT",
		start + ", clocks 100 SS low, " + recover, true);

	std::cout << (failures ? "FAILED: " : "all cases hold: ") << failures
		<< " failure(s)" << std::endl;
	return failures ? 1 : 0;
}
