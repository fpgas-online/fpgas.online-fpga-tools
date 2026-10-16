// SPDX-License-Identifier: Apache-2.0
//
// The /dev/gpiomem backend of the iCE40 SPI slave load (GpiomemIce40Spi,
// openFPGALoader patch "gpiomem: the iCE40 SPI slave load through a
// Raspberry Pi's GPIO registers") against a model of the Pi's GPIO
// registers and of what is wired to them: an iCE40 and the SPI flash that
// shares its bus. The real Ice40::program_cram_slave() drives the real
// GpiomemIce40Spi; only the register page is the model. No hardware, and no
// /dev/gpiomem, is touched.
//
// Linked to the objects of a built, patched openFPGALoader tree by
// packaging/tests/ice40-slave-model.sh. Each case states what must happen:
// the result, which bytes the iCE40 received, which flash commands the flash
// received, and that every pin function is put back as it was found. Exits 1
// on the first case that does not hold.

#include <fcntl.h>
#include <signal.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <iterator>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include "gpiomemIce40Spi.hpp"
#include "ice40.hpp"

/* the Fomu EVT's header: BCM GPIO numbers */
enum {SS = 8, SI = 9, SO = 10, SCK = 11, CDONE = 17, HOLDN = 25, CRESET = 27};

/* BCM2835 section 6.1 / BCM2711 section 5.2 */
enum {REG_GPFSEL0 = 0x00, REG_GPFSEL5 = 0x14, REG_GPSET0 = 0x1C,
	REG_GPCLR0 = 0x28, REG_GPLEV0 = 0x34};

enum Fault {
	NO_FAULT, CONFIG_FAILS, SIGTERM_IN_IMAGE, SIGINT_AT_END, SIGHUP_IN_FLASH,
	CDONE_LOW_BEFORE, HANDLER_RESTORE_IN_IMAGE
};

static std::string joined(const std::vector<std::string> &v)
{
	std::string s;
	for (const std::string &x : v)
		s += (s.empty() ? "" : ", ") + x;
	return s;
}

static void busy_wait_ns(double ns)
{
	if (ns <= 0)
		return;
	auto end = std::chrono::steady_clock::now() +
		std::chrono::nanoseconds(static_cast<long long>(ns));
	while (std::chrono::steady_clock::now() < end) {
	}
}

/* the registers of a Pi's GPIO block, an iCE40 on the pins, and its flash */
struct World {
	Fault fault;
	double cost_ns;  // what one register access takes
	uint32_t fsel[6] = {0, 0, 0, 0, 0, 0};
	uint32_t latch = 0;  // what GPSET0 / GPCLR0 left for each pin
	int accesses = 0, writes = 0;
	bool saw_alt_write = false;

	// the iCE40
	bool in_reset = false, slave = false, cdone = true;
	uint64_t image_bits = 0, want_bits = 0, post_clocks = 0, edges_in_image = 0;
	std::vector<uint8_t> received;
	// the flash on its bus
	std::vector<std::string> flash_ops;
	int fbits = 0, fop = 0;
	uint32_t last_levels = 0;
	std::vector<std::string> log;  // CRESET, SS and the end

	World(Fault f, double cost, uint64_t bits): fault(f), cost_ns(cost),
		want_bits(bits) {
		cdone = (f != CDONE_LOW_BEFORE);
		last_levels = levels();
	}

	uint32_t function(unsigned int pin) const {
		return (fsel[pin / 10] >> (3 * (pin % 10))) & 7;
	}
	void set_function(unsigned int pin, uint32_t fn) {
		fsel[pin / 10] = (fsel[pin / 10] & ~(7u << (3 * (pin % 10)))) |
			(fn << (3 * (pin % 10)));
	}
	bool ss_low() const {
		return function(SS) == 1 && !((latch >> SS) & 1);
	}
	/* the level of every pin: an output's latch, or what is wired to an
	 * input (the board's pull-ups, CDONE, the flash's DO)
	 */
	uint32_t levels() const {
		uint32_t lv = 0xFFFFFFFFu;  // inputs are pulled up
		for (unsigned int p : {SS, SI, SO, SCK, CDONE, HOLDN, CRESET}) {
			bool v;
			if (function(p) == 1)
				v = (latch >> p) & 1;
			else if (p == CDONE)
				v = cdone;
			else if (p == SI && in_reset && ss_low() && fbits >= 8 &&
					fop == 0x05)
				v = false;  // the flash's DO: status register 1 = 0 (idle)
			else
				v = true;
			lv = (lv & ~(1u << p)) | ((uint32_t)v << p);
		}
		return lv;
	}

	uint32_t read(uint32_t offset) {
		accesses++;
		busy_wait_ns(cost_ns);
		if (offset >= REG_GPFSEL0 && offset <= REG_GPFSEL5 && offset % 4 == 0)
			return fsel[offset / 4];
		if (offset == REG_GPLEV0)
			return levels();
		throw std::logic_error("read of a register the load must not read: " +
			std::to_string(offset));
	}

	void write(uint32_t offset, uint32_t value) {
		accesses++;
		writes++;
		/* a write of zero to GPSET0 / GPCLR0 changes nothing: with a free
		 * host it costs nothing here, which is what the load's own timing
		 * run (zero masks) then sees: a fast host, to be padded
		 */
		if (cost_ns == 0 && value == 0 &&
				(offset == REG_GPSET0 || offset == REG_GPCLR0))
			return;
		busy_wait_ns(cost_ns);
		if (offset >= REG_GPFSEL0 && offset <= REG_GPFSEL5 && offset % 4 == 0) {
			fsel[offset / 4] = value;
		} else if (offset == REG_GPSET0) {
			latch |= value;
		} else if (offset == REG_GPCLR0) {
			latch &= ~value;
		} else {
			throw std::logic_error("write to a register the load must not "
				"write: " + std::to_string(offset));
		}
		if (value || (offset >= REG_GPFSEL0 && offset <= REG_GPFSEL5))
			edges();
	}

	/* what the wires do after a register write */
	void edges() {
		uint32_t now = levels();
		uint32_t was = last_levels;
		last_levels = now;
		auto rose = [&](unsigned int p) { return !(was >> p & 1) && (now >> p & 1); };
		auto fell = [&](unsigned int p) { return (was >> p & 1) && !(now >> p & 1); };
		bool ss_low = this->ss_low();

		if (fell(CRESET)) {
			in_reset = true;
			slave = false;
			cdone = false;
			log.push_back("CRESET low");
			last_levels = levels();
		}
		if (rose(CRESET) && in_reset) {
			in_reset = false;
			log.push_back("CRESET high");
			if (ss_low) {
				slave = true;  // "enters SPI peripheral mode when CRESET_B
				cdone = false;  //  returns High while SPI_SS is Low"
				image_bits = 0;
				post_clocks = 0;
				received.assign((want_bits + 7) / 8, 0);
			} else {
				cdone = true;
				log.push_back("boots from flash");
			}
			last_levels = levels();
		}
		if (fell(SS) && in_reset) {
			fbits = 0;
			fop = 0;
			last_levels = levels();
		}
		if (rose(SCK)) {
			if (slave && ss_low) {
				if (image_bits < want_bits) {
					if (now >> SI & 1)
						received[image_bits >> 3] |= 0x80 >> (image_bits & 7);
					image_bits++;
					edges_in_image++;
					if (fault == SIGTERM_IN_IMAGE && edges_in_image == 300000)
						raise(SIGTERM);
					if (fault == HANDLER_RESTORE_IN_IMAGE &&
							edges_in_image == 300000)
						raise(SIGUSR1);
				} else {
					post_clocks++;
					if (post_clocks == 120 && fault == SIGINT_AT_END)
						raise(SIGINT);
					if (post_clocks == 100 && fault != CONFIG_FAILS) {
						cdone = true;
						last_levels = levels();
					}
				}
			} else if (in_reset && ss_low) {
				if (fbits < 8) {
					fop = (fop << 1) | (now >> SO & 1);
					fbits++;
					if (fbits == 8) {
						char op[16];
						snprintf(op, sizeof(op), "0x%02X", fop);
						flash_ops.push_back(op);
						if (fault == SIGHUP_IN_FLASH && fop == 0xAB)
							raise(SIGHUP);
					}
				}
			}
		}
	}
};


/* the registers the backend sees: the world's, by reference, so that the
 * world can be looked at after the backend is gone
 */
struct FakeRegs : Ice40GpioRegs {
	explicit FakeRegs(World *w): world(w) {}
	uint32_t read(uint32_t offset) override { return world->read(offset); }
	void write(uint32_t offset, uint32_t value) override {
		world->write(offset, value);
	}
	World *world;
};

static int failures = 0;

static void expect(bool ok, const std::string &what)
{
	if (!ok) {
		std::cout << "  FAIL: " << what << std::endl;
		failures++;
	}
}

static void handler_untouched(const char *name, int sig)
{
	struct sigaction now;
	sigaction(sig, NULL, &now);
	expect(now.sa_handler == SIG_DFL,
		std::string(name) + " handler put back after the load");
}

static ice40_gpio_pins_t fomu_pins()
{
	ice40_gpio_pins_t p;
	p.creset = CRESET; p.cdone = CDONE; p.si = SI; p.so = SO; p.sck = SCK;
	p.ss = SS; p.holdn = HOLDN; p.flash = true;
	return p;
}

/* signal-handler context for HANDLER_RESTORE_IN_IMAGE */
static GpiomemIce40Spi *handler_target = NULL;
static volatile sig_atomic_t handler_bad = -1;
static void on_usr1(int)
{
	handler_bad = handler_target->restore_functions();
}

/* GPIO25 (/HOLD) is an output at the start, as something earlier left it:
 * what is put back is the function that was saved, not "input"
 */
static void start_functions(World &w)
{
	w.set_function(HOLDN, 1);
	w.last_levels = w.levels();
}

struct Expect {
	std::string error;  // empty: loads; else words the error contains
	std::string flash;  // the flash commands the flash must have received
	bool image;  // the iCE40 received the whole image, in order
	bool cdone_after;
	bool pins_touched;  // false: no register was written at all
};

static std::vector<uint8_t> make_image()
{
	std::vector<uint8_t> img(104161);  // an iCE40UP5K image's size
	for (size_t i = 0; i < img.size(); i++)
		img[i] = static_cast<uint8_t>(i * 7 + 3);
	return img;
}

static void run(const std::string &name, Fault f, double cost_ns,
		const Expect &want)
{
	std::vector<uint8_t> img = make_image();
	World w(f, cost_ns, img.size() * 8ULL);
	start_functions(w);
	uint32_t before[6];
	memcpy(before, w.fsel, sizeof(before));
	std::string got;
	double hz = 0;
	{
		GpiomemIce40Spi spi(fomu_pins(),
			std::unique_ptr<Ice40GpioRegs>(new FakeRegs(&w)), -1);
		if (f == HANDLER_RESTORE_IN_IMAGE) {
			handler_target = &spi;
			handler_bad = -1;
			struct sigaction sa;
			memset(&sa, 0, sizeof(sa));
			sa.sa_handler = on_usr1;
			sigemptyset(&sa.sa_mask);
			sigaction(SIGUSR1, &sa, NULL);
		}
		Ice40 ice(&spi, "model.bin", "bin", -1);
		auto t0 = std::chrono::steady_clock::now();
		try {
			ice.program_cram_slave(img.data(), img.size());
		} catch (std::exception &e) {
			got = e.what();
		}
		hz = img.size() * 8.0 / std::chrono::duration<double>(
			std::chrono::steady_clock::now() - t0).count();
		if (f == HANDLER_RESTORE_IN_IMAGE) {
			struct sigaction sa;
			memset(&sa, 0, sizeof(sa));
			sa.sa_handler = SIG_DFL;
			sigaction(SIGUSR1, &sa, NULL);
			handler_target = NULL;
		}
	}  // the backend is gone: its destructor put the pins back
	std::cout << "=== " << name << ": " << (got.empty() ? "loaded" : got)
		<< std::endl << "  flash: " << joined(w.flash_ops) << "; iCE40: "
		<< joined(w.log) << "; " << w.writes << " register writes" << std::endl;
	(void)hz;
	if (want.error.empty())
		expect(got.empty(), "loads");
	else
		expect(got.find(want.error) != std::string::npos,
			"error says \"" + want.error + "\"");
	expect(joined(w.flash_ops) == want.flash,
		"the flash received: " + want.flash);
	if (want.image) {
		std::vector<uint8_t> rx(w.received.begin(),
			w.received.begin() + img.size());
		expect(w.image_bits == w.want_bits && rx == img,
			"the iCE40 received the whole image, msb first, in order");
	}
	for (int i = 0; i < 6; i++)
		expect(w.fsel[i] == before[i],
			"GPFSEL" + std::to_string(i) + " is as it was found");
	expect(w.cdone == want.cdone_after, std::string("CDONE ") +
		(want.cdone_after ? "high" : "low") + " at the end");
	expect(!w.in_reset, "CRESET_B let go at the end");
	if (!want.pins_touched)
		expect(w.writes == 0, "no register was written");
	if (f == HANDLER_RESTORE_IN_IMAGE)
		expect(handler_bad == 0, "restore_functions() from a signal handler "
			"put every changed pin back (and read back)");
	handler_untouched("SIGINT", SIGINT);
	handler_untouched("SIGTERM", SIGTERM);
	handler_untouched("SIGHUP", SIGHUP);
}

static void case_alt_function()
{
	World w(NO_FAULT, 0, 8);
	w.set_function(SI, 4);  // GPIO9 ALT0: SPI0 MISO
	std::string got;
	try {
		GpiomemIce40Spi spi(fomu_pins(),
			std::unique_ptr<Ice40GpioRegs>(new FakeRegs(&w)), -1);
	} catch (std::exception &e) {
		got = e.what();
	}
	std::cout << "=== a pin in an alternate function: " << got << std::endl;
	expect(got.find("GPIO9 is in alternate function 4") != std::string::npos,
		"refuses GPIO9 in ALT0");
	expect(w.writes == 0, "no register written");
}

static void case_pin_range()
{
	World w(NO_FAULT, 0, 8);
	ice40_gpio_pins_t p = fomu_pins();
	p.sck = 40;  // GPSET1 territory: not supported
	std::string got;
	try {
		GpiomemIce40Spi spi(p, std::unique_ptr<Ice40GpioRegs>(
			new FakeRegs(&w)), -1);
	} catch (std::exception &e) {
		got = e.what();
	}
	std::cout << "=== a pin beyond GPIO31: " << got << std::endl;
	expect(got.find("SPI_SCK: GPIO 40 is outside of the valid range") !=
		std::string::npos, "refuses GPIO40");
	got.clear();
	p = fomu_pins();
	p.sck = SI;
	try {
		GpiomemIce40Spi spi(p, std::unique_ptr<Ice40GpioRegs>(
			new FakeRegs(&w)), -1);
	} catch (std::exception &e) {
		got = e.what();
	}
	expect(got.find("is given to two signals") != std::string::npos,
		"refuses a pin given to two signals");
	expect(w.writes == 0, "no register written");
}

/* what the padding does for hosts of every speed */
static void case_padding()
{
	std::cout << "=== padding for a bit of 3 writes of w ns" << std::endl;
	// w = 5 ns (bit 15): high needs 20 more (25 - 5), period 3*5+2*20 = 55
	expect(GpiomemIce40Spi::pad_spins_for(15, 1) == 20, "w 5 ns, spin 1 ns: 20");
	// w = 10 ns: 15 ns each phase pad; period 30 + 30 = 60
	expect(GpiomemIce40Spi::pad_spins_for(30, 1) == 15, "w 10 ns, spin 1 ns: 15");
	// w = 12.5 ns: pad 12.5, period 37.5 + 25 = 62.5
	expect(GpiomemIce40Spi::pad_spins_for(37.5, 2.5) == 5,
		"w 12.5 ns, spin 2.5 ns: 5");
	// w = 25 ns: high 25 is met; low 50; period 75 > 50: no pad
	expect(GpiomemIce40Spi::pad_spins_for(75, 1) == 0, "w 25 ns: 0");
	// a slow host (w = 300 ns): no pad
	expect(GpiomemIce40Spi::pad_spins_for(900, 1) == 0, "w 300 ns: 0");
	// the padded bit never falls under 50 ns, whatever the spin cost
	for (double w = 1; w < 30; w += 0.7)
		for (double sp : {0.4, 1.0, 3.3}) {
			uint32_t pad = GpiomemIce40Spi::pad_spins_for(3 * w, sp);
			double padded = pad * sp;
			expect(3 * w + 2 * padded >= 50 - 1e-9 &&
				w + padded >= 25 - 1e-9 && 2 * w + padded >= 25 - 1e-9,
				"padded bit holds the 50 ns period and 25 ns phases");
		}
}

static void write_file(const std::string &path, const std::string &content)
{
	std::ofstream f(path, std::ios::binary | std::ios::trunc);
	f << content;
}

static void case_selection()
{
	std::cout << "=== backend selection" << std::endl;
	const std::string f = "ice40-gpiomem-model.compatible";
	struct {
		const char *name;
		std::string compat;
		bool gpiomem;
		const char *words;
	} cases[] = {
		{"Pi 3 Model B+", std::string("raspberrypi,3-model-b-plus\0brcm,bcm2837\0", 41),
			true, "brcm,bcm2837"},
		{"Pi 1", std::string("raspberrypi,model-b\0brcm,bcm2835\0", 33),
			true, "brcm,bcm2835"},
		{"Pi 2", std::string("raspberrypi,2-model-b\0brcm,bcm2836\0", 35),
			true, "brcm,bcm2836"},
		{"Pi 4", std::string("raspberrypi,4-model-b\0brcm,bcm2711\0", 35),
			true, "brcm,bcm2711"},
		{"Pi 5 (RP1)", std::string("raspberrypi,5-model-b\0brcm,bcm2712\0", 35),
			false, "no brcm,bcm2835/2836/2837/2711"},
		{"an x86 board", std::string("dell,optiplex\0", 14),
			false, "not a Raspberry Pi 1-4"},
		{"a name that only starts like one", std::string("brcm,bcm28370\0", 14),
			false, "not a Raspberry Pi 1-4"},
	};
	for (auto &c : cases) {
		write_file(f, c.compat);
		GpiomemIce40Spi::Choice ch = GpiomemIce40Spi::select(false, f);
		std::cout << "  " << c.name << ": " << (ch.gpiomem ? "gpiomem" :
			"libgpiod") << ": " << ch.why << std::endl;
		expect(ch.gpiomem == c.gpiomem, std::string(c.name) +
			(c.gpiomem ? " selects gpiomem" : " selects libgpiod"));
		expect(ch.why.find(c.words) != std::string::npos,
			std::string(c.name) + ": the reason names \"" + c.words + "\"");
	}
	write_file(f, std::string("brcm,bcm2837\0", 13));
	GpiomemIce40Spi::Choice ch = GpiomemIce40Spi::select(true, f);
	expect(!ch.gpiomem && ch.why.find("-d") != std::string::npos,
		"-d names a gpiochip: libgpiod, and says so");
	ch = GpiomemIce40Spi::select(false, "ice40-gpiomem-model.no-such-file");
	expect(!ch.gpiomem && ch.why.find("cannot read") != std::string::npos,
		"no device tree: libgpiod, and says why");
	remove(f.c_str());
}

static void case_open_gpiomem()
{
	std::cout << "=== /dev/gpiomem absent or closed on a BCM283x host" << std::endl;
	std::string got;
	try {
		GpiomemIce40Spi::open_gpiomem("ice40-gpiomem-model.absent");
	} catch (std::exception &e) {
		got = e.what();
	}
	std::cout << "  absent: " << got << std::endl;
	expect(got.find("is absent") != std::string::npos &&
		got.find("libgpiod is not used instead") != std::string::npos,
		"absent: stops, says so, no fallback");
	got.clear();
	// a directory cannot be opened read/write
	try {
		GpiomemIce40Spi::open_gpiomem(".");
	} catch (std::exception &e) {
		got = e.what();
	}
	std::cout << "  cannot be opened: " << got << std::endl;
	expect(got.find("is the user in the gpio group?") != std::string::npos &&
		got.find("groups: ") != std::string::npos &&
		got.find("libgpiod is not used instead") != std::string::npos,
		"cannot be opened: stops, gives the gpio group hint and the user's "
		"groups, no fallback");
}

/* GpiomemRegs on an ordinary 4 KiB file: the mapping, the volatile
 * accesses and the offsets (a file keeps what the page was given)
 */
static void case_regs_mapping()
{
	std::cout << "=== GpiomemRegs over a 4 KiB file" << std::endl;
	const std::string path = "ice40-gpiomem-model.page";
	write_file(path, std::string(4096, '\0'));
	{
		GpiomemRegs regs(path);
		regs.write(REG_GPSET0, 0x12345678);
		regs.write(0x0FFC, 0xCAFEF00D);
		expect(regs.read(REG_GPSET0) == 0x12345678, "read back GPSET0 offset");
		expect(regs.read(0x0FFC) == 0xCAFEF00D, "read back the last word");
	}
	std::ifstream f(path, std::ios::binary);
	std::string page((std::istreambuf_iterator<char>(f)),
		std::istreambuf_iterator<char>());
	expect(page.size() == 4096, "the page is 4096 bytes");
	uint32_t w;
	memcpy(&w, page.data() + REG_GPSET0, 4);
	expect(w == 0x12345678, "the word is at byte offset 0x1C");
	memcpy(&w, page.data() + 0x0FFC, 4);
	expect(w == 0xCAFEF00D, "the last word is at byte offset 0xFFC");
	remove(path.c_str());
}

int main()
{
	const Expect loaded = {"", "0xAB, 0x05, 0xB9", true, true, true};
	const std::string recover = "SPI lines released; the iCE40 was reset and "
		"booted from its flash again (CDONE high)";

	run("normal, host 3 register writes of 60 ns", NO_FAULT, 60, loaded);
	run("normal, host as fast as the model goes (padded)", NO_FAULT, 0, loaded);
	run("host too slow, 2 us a register access", NO_FAULT, 2000,
		{"below the iCE40's 1 MHz minimum", "0xAB, 0x05, 0xB9", false, true, true});
	run("configuration refused", CONFIG_FAILS, 60,
		{"CDONE stayed low after the image", "0xAB, 0x05, 0xB9", true, true, true});
	run("SIGTERM in the image", SIGTERM_IN_IMAGE, 0,
		{"interrupted by SIGTERM; " + recover, "0xAB, 0x05, 0xB9", false, true, true});
	run("SIGINT in the 49 clocks", SIGINT_AT_END, 0,
		{"interrupted by SIGINT", "0xAB, 0x05, 0xB9", true, true, true});
	run("SIGHUP during the flash power-down", SIGHUP_IN_FLASH, 0,
		{"interrupted by SIGHUP", "0xAB", false, true, true});
	run("CDONE low before", CDONE_LOW_BEFORE, 0,
		{"CDONE is low before the reset", "", false, false, false});
	/* the pins go to inputs under the load's feet: the image never arrives
	 * and CDONE stays low, the load stops and recovers as for any failure
	 */
	run("restore_functions() from a signal handler", HANDLER_RESTORE_IN_IMAGE,
		0, {"CDONE stayed low after the image", "0xAB, 0x05, 0xB9", false, true,
		true});
	case_alt_function();
	case_pin_range();
	case_padding();
	case_selection();
	case_open_gpiomem();
	case_regs_mapping();

	std::cout << (failures ? "FAILED: " : "all cases hold: ") << failures
		<< " failure(s)" << std::endl;
	return failures ? 1 : 0;
}
