// SPDX-License-Identifier: Apache-2.0
//
// The /dev/gpiomem backend of the iCE40 SPI slave load (GpiomemIce40Spi)
// against a model of the Pi's GPIO registers and of what is wired to them:
// an iCE40 and the SPI flash that shares its bus. The real
// Ice40::program_cram_slave() drives the real GpiomemIce40Spi; only the
// register page, the clock, the spin and cpufreq are the model's. No
// hardware, and no /dev/gpiomem, is touched.
//
// The model keeps a virtual clock (ns): every register access and every
// spin advances it, by a cost the case gives, scaled by the modelled CPU
// clock. It time-stamps every SPI_SCK edge, so a case can assert the least
// SCK high time, low time and period the load produced. It also models the
// level a pin takes when its function becomes "output": the set/clear
// written first, or the latch's power-up 0, so a wrong order shows as a
// glitch on SPI_SS (counted).
//
// Mutants this test must kill (each was run by hand against the backend; the
// results are in the commit that added them): padding not applied; padding
// halved; padding not scaled to the maximum CPU clock; GPFSEL written before
// GPSET/GPCLR; the GPFSEL readback check deleted; the restore not run.
//
// Linked to the objects of a built, patched openFPGALoader tree by
// packaging/tests/ice40-slave-model.sh. Exits 1 on the first case that does
// not hold.

#include <fcntl.h>
#include <signal.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
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
	CDONE_LOW_BEFORE, HANDLER_RESTORE_IN_IMAGE, SIGQUIT_IN_IMAGE,
	SIGPIPE_IN_IMAGE
};

/* what the modelled host is like */
struct Host {
	double real_ns = 60;  // real time one access takes (Ice40 times the load in real time)
	double access_ns = 30;  // virtual time of one register access at ref_khz
	double spin_ns = 1;  // virtual time of one spin at ref_khz
	double ref_khz = 1000000;  // the clock the two costs above are for
	CpuFreq freq = {1000000, 1000000, 1000000};
	long ramp_at = -1;  // image rising edges after which the CPU is at max
	bool freq_throws = false;  // cpufreq cannot be read
	bool freq_flaps = false;  // the clock reads different every time
	int stick_pin = -1;  // a pin whose GPFSEL bits do not take...
	bool stick_stays_output = false;  // ...once it is an output (else: never becomes one)
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

/* the five signals the load handles are blocked in this process now */
static bool handled_signals_blocked()
{
	sigset_t cur;
	sigprocmask(SIG_BLOCK, NULL, &cur);
	for (int sig : {SIGINT, SIGTERM, SIGHUP, SIGQUIT, SIGPIPE})
		if (!sigismember(&cur, sig))
			return false;
	return true;
}

/* the registers of a Pi's GPIO block, an iCE40 on the pins, and its flash */
struct World {
	Fault fault;
	Host host;
	uint32_t fsel[6] = {0, 0, 0, 0, 0, 0};
	uint32_t latch = 0;  // what GPSET0 / GPCLR0 left for each pin
	int writes = 0;  // every write, the zero-mask timing writes included
	int pin_writes = 0;  // GPFSEL writes and GPSET/GPCLR writes of a mask
	int unblocked_fsel_writes = 0;  // GPFSEL writes with a handled signal open
	double vt = 0;  // the virtual clock, ns

	// the iCE40
	bool in_reset = false, slave = false, cdone = true;
	uint64_t image_bits = 0, want_bits = 0, post_clocks = 0, edges_in_image = 0;
	std::vector<uint8_t> received;
	// the flash on its bus
	std::vector<std::string> flash_ops;
	int fbits = 0, fop = 0;
	uint32_t last_levels = 0;
	std::vector<std::string> log;
	// the wires
	int ss_changes = 0;
	std::vector<double> sck_rise, sck_fall;  // virtual times, slave mode

	World(Fault f, const Host &h, uint64_t bits): fault(f), host(h),
		want_bits(bits) {
		cdone = (f != CDONE_LOW_BEFORE);
		last_levels = levels();
	}

	double scale() const { return host.ref_khz / host.freq.cur_khz; }

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

	void tick() {
		vt += host.access_ns * scale();
		busy_wait_ns(host.real_ns);
	}

	uint32_t read(uint32_t offset) {
		tick();
		if (offset >= REG_GPFSEL0 && offset <= REG_GPFSEL5 && offset % 4 == 0)
			return fsel[offset / 4];
		if (offset == REG_GPLEV0)
			return levels();
		throw std::logic_error("read of a register the load must not read: " +
			std::to_string(offset));
	}

	void write(uint32_t offset, uint32_t value) {
		writes++;
		tick();
		bool is_fsel = offset >= REG_GPFSEL0 && offset <= REG_GPFSEL5 &&
			offset % 4 == 0;
		if (is_fsel || value)
			pin_writes++;
		if (is_fsel && !handled_signals_blocked())
			unblocked_fsel_writes++;
		if (is_fsel) {
			uint32_t old = fsel[offset / 4];
			fsel[offset / 4] = value;
			int sp = host.stick_pin;
			if (sp >= 0 && offset / 4 == (uint32_t)sp / 10) {
				uint32_t was = (old >> (3 * (sp % 10))) & 7;
				uint32_t want = (value >> (3 * (sp % 10))) & 7;
				bool refuse = host.stick_stays_output ?
					(was == 1 && want != 1) : (want == 1);
				if (refuse)
					set_function(sp, was);
			}
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

	void spin(uint32_t n) { vt += n * host.spin_ns * scale(); }

	CpuFreq cpu_freq() {
		if (host.freq_throws)
			throw std::runtime_error("cannot read cpufreq (modelled)");
		if (host.freq_flaps) {
			host.freq.cur_khz += 1;
			host.freq.max_khz = std::max(host.freq.max_khz, host.freq.cur_khz);
		}
		return host.freq;
	}

	/* what the wires do after a register write */
	void edges() {
		uint32_t now = levels();
		uint32_t was = last_levels;
		last_levels = now;
		auto rose = [&](unsigned int p) { return !(was >> p & 1) && (now >> p & 1); };
		auto fell = [&](unsigned int p) { return (was >> p & 1) && !(now >> p & 1); };
		bool sel = ss_low();

		if (rose(SS) || fell(SS))
			ss_changes++;
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
			if (sel) {
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
		if (slave && rose(SCK))
			sck_rise.push_back(vt);
		if (slave && fell(SCK))
			sck_fall.push_back(vt);
		if (rose(SCK)) {
			if (slave && sel) {
				if (image_bits < want_bits) {
					if (now >> SI & 1)
						received[image_bits >> 3] |= 0x80 >> (image_bits & 7);
					image_bits++;
					edges_in_image++;
					if ((long)edges_in_image == host.ramp_at)
						host.freq.cur_khz = host.freq.max_khz;
					if (edges_in_image == 300000) {
						if (fault == SIGTERM_IN_IMAGE)
							raise(SIGTERM);
						if (fault == SIGQUIT_IN_IMAGE)
							raise(SIGQUIT);
						if (fault == SIGPIPE_IN_IMAGE)
							raise(SIGPIPE);
						if (fault == HANDLER_RESTORE_IN_IMAGE)
							raise(SIGUSR1);
					}
				} else {
					post_clocks++;
					if (post_clocks == 120 && fault == SIGINT_AT_END)
						raise(SIGINT);
					if (post_clocks == 100 && fault != CONFIG_FAILS) {
						cdone = true;
						last_levels = levels();
					}
				}
			} else if (in_reset && sel) {
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
	double now_ns() override { return world->vt; }
	void spin(uint32_t n) override { world->spin(n); }
	CpuFreq cpu_freq() override { return world->cpu_freq(); }
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

/* stderr into a file for the length of a scope: the destructor's words */
struct CaptureStderr {
	CaptureStderr(): saved(dup(2)) {
		fflush(stderr);
		std::cerr.flush();
		int fd = open("ice40-gpiomem-model.stderr",
			O_WRONLY | O_CREAT | O_TRUNC, 0644);
		dup2(fd, 2);
		close(fd);
	}
	std::string finish() {
		fflush(stderr);
		std::cerr.flush();
		dup2(saved, 2);
		close(saved);
		std::ifstream f("ice40-gpiomem-model.stderr");
		return std::string((std::istreambuf_iterator<char>(f)),
			std::istreambuf_iterator<char>());
	}
	int saved;
};

struct Expect {
	Expect(const std::string &e, const std::string &f, bool img, bool cd,
		bool touched): error(e), flash(f), image(img), cdone_after(cd),
		pins_touched(touched) {}
	std::string error;  // empty: loads; else words the error contains
	std::string flash;  // the flash commands the flash must have received
	bool image;  // the iCE40 received the whole image, in order
	bool cdone_after;
	bool pins_touched;  // false: nothing that moves a pin was written
	bool reset_held = false;  // CRESET_B still held low (a line is stuck)
	bool timing = false;  // assert the SCK phases and period
	bool pad = false;  // the load must have applied padding
	int ss_changes = -1;  // the level changes SPI_SS must have had
	bool fsel_back = true;  // every GPFSEL as it was found
	std::string stderr_has;  // words the destructor must have said
};

static std::vector<uint8_t> make_image()
{
	std::vector<uint8_t> img(104161);  // an iCE40UP5K image's size
	for (size_t i = 0; i < img.size(); i++)
		img[i] = static_cast<uint8_t>(i * 7 + 3);
	return img;
}

static void run(const std::string &name, Fault f, const Host &host,
		const Expect &want)
{
	std::vector<uint8_t> img = make_image();
	World w(f, host, img.size() * 8ULL);
	uint32_t before[6];
	memcpy(before, w.fsel, sizeof(before));
	std::string got, err_out;
	uint32_t pad = 0;
	{
		CaptureStderr cap;
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
			try {
				ice.program_cram_slave(img.data(), img.size());
			} catch (std::exception &e) {
				got = e.what();
			}
			pad = spi.pad_spins();
			if (f == HANDLER_RESTORE_IN_IMAGE) {
				struct sigaction sa;
				memset(&sa, 0, sizeof(sa));
				sa.sa_handler = SIG_DFL;
				sigaction(SIGUSR1, &sa, NULL);
				handler_target = NULL;
			}
		}  // the backend is gone: its destructor put the pins back
		err_out = cap.finish();
	}
	std::cout << "=== " << name << ": " << (got.empty() ? "loaded" : got)
		<< std::endl << "  flash: " << joined(w.flash_ops) << "; iCE40: "
		<< joined(w.log) << "; " << w.writes << " register writes, pad "
		<< pad << " spins" << std::endl;
	if (!err_out.empty())
		std::cout << "  stderr: " << err_out;
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
	if (want.fsel_back)
		for (int i = 0; i < 6; i++)
			expect(w.fsel[i] == before[i],
				"GPFSEL" + std::to_string(i) + " is as it was found");
	expect(w.cdone == want.cdone_after, std::string("CDONE ") +
		(want.cdone_after ? "high" : "low") + " at the end");
	expect(w.in_reset == want.reset_held, want.reset_held ?
		"CRESET_B still held low at the end (a line is stuck)" :
		"CRESET_B let go at the end");
	if (!want.pins_touched) {
		expect(w.pin_writes == 0, "no register that moves a pin was written, "
			"CRESET_B never touched");
		expect(got.find("SPI lines released") == std::string::npos,
			"nothing was driven, so nothing is said to be released");
	}
	expect(w.unblocked_fsel_writes == 0, "every GPFSEL write had the handled "
		"signals blocked");
	{
		sigset_t cur;
		sigprocmask(SIG_BLOCK, NULL, &cur);
		expect(!sigismember(&cur, SIGINT) && !sigismember(&cur, SIGTERM),
			"the signal mask is put back");
	}
	if (!want.stderr_has.empty())
		expect(err_out.find(want.stderr_has) != std::string::npos,
			"the destructor says \"" + want.stderr_has + "\"");
	if (want.pad)
		expect(pad > 0, "padding was applied");
	if (want.ss_changes >= 0)
		expect(w.ss_changes == want.ss_changes, "SPI_SS changed level " +
			std::to_string(want.ss_changes) + " times (got " +
			std::to_string(w.ss_changes) + "): no glitch when it becomes "
			"an output");
	if (want.timing) {
		double hi = 1e18, lo = 1e18, per = 1e18;
		size_t n = std::min(w.sck_rise.size(), w.sck_fall.size());
		expect(w.sck_rise.size() >= w.want_bits, "SPI_SCK edges were timed");
		for (size_t i = 0; i < n; i++) {
			hi = std::min(hi, w.sck_fall[i] - w.sck_rise[i]);
			if (i + 1 < w.sck_rise.size()) {
				lo = std::min(lo, w.sck_rise[i + 1] - w.sck_fall[i]);
				per = std::min(per, w.sck_rise[i + 1] - w.sck_rise[i]);
			}
		}
		std::cout << "  SPI_SCK (virtual clock): least high " << hi
			<< " ns, least low " << lo << " ns, least period " << per
			<< " ns" << std::endl;
		expect(hi >= 25 - 1e-6, "least SCK high time is 25 ns or more");
		expect(lo >= 25 - 1e-6, "least SCK low time is 25 ns or more");
		expect(per >= 50 - 1e-6, "least SCK period is 50 ns or more");
	}
	if (f == HANDLER_RESTORE_IN_IMAGE)
		expect(handler_bad == 0, "restore_functions() from a signal handler "
			"put every changed pin back (and read back)");
	handler_untouched("SIGINT", SIGINT);
	handler_untouched("SIGTERM", SIGTERM);
	handler_untouched("SIGHUP", SIGHUP);
	handler_untouched("SIGQUIT", SIGQUIT);
	handler_untouched("SIGPIPE", SIGPIPE);
}

/* the start state: which pins are in which function, and what the
 * constructor says; nothing may be written
 */
static void case_start(const std::string &name,
		const std::vector<std::pair<int, int>> &functions,
		const std::vector<std::string> &words)
{
	Host h;
	World w(NO_FAULT, h, 8);
	for (auto &p : functions)
		w.set_function(p.first, p.second);
	w.last_levels = w.levels();
	std::string got;
	try {
		GpiomemIce40Spi spi(fomu_pins(),
			std::unique_ptr<Ice40GpioRegs>(new FakeRegs(&w)), -1);
	} catch (std::exception &e) {
		got = e.what();
	}
	std::cout << "=== " << name << ": " << got << std::endl;
	for (const std::string &s : words)
		expect(got.find(s) != std::string::npos, name + ": says \"" + s + "\"");
	expect(w.writes == 0, name + ": no register written");
}

static void case_pin_range()
{
	Host h;
	World w(NO_FAULT, h, 8);
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

/* the pad for hosts of every speed (bit_ns and spin_ns as at max clock) */
static void case_padding()
{
	std::cout << "=== padding for a bit of 3 writes of w ns" << std::endl;
	expect(GpiomemIce40Spi::pad_spins_for(15, 1) == 20, "w 5 ns, spin 1 ns: 20");
	expect(GpiomemIce40Spi::pad_spins_for(30, 1) == 15, "w 10 ns, spin 1 ns: 15");
	expect(GpiomemIce40Spi::pad_spins_for(37.5, 2.5) == 5,
		"w 12.5 ns, spin 2.5 ns: 5");
	expect(GpiomemIce40Spi::pad_spins_for(75, 1) == 0, "w 25 ns: 0");
	expect(GpiomemIce40Spi::pad_spins_for(900, 1) == 0, "w 300 ns: 0");
	// the padded bit holds the 25 ns phases and the 50 ns period
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
	remove(f.c_str());
	// absent: not a device-tree host: libgpiod
	ch = GpiomemIce40Spi::select(false, "ice40-gpiomem-model.no-such-file");
	expect(!ch.gpiomem && ch.why.find("does not exist") != std::string::npos,
		"no device tree: libgpiod, and says why");
	// present but unreadable (a directory): an error, not a guess
	std::string got;
	try {
		GpiomemIce40Spi::select(false, ".");
	} catch (std::exception &e) {
		got = e.what();
	}
	std::cout << "  present but unreadable: " << got << std::endl;
	expect(got.find("cannot read .") != std::string::npos &&
		got.find("the load stops") != std::string::npos,
		"compatible present but unreadable: stops loud");
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

/* cpufreq as read from sysfs-shaped files */
static void case_cpufreq_files()
{
	std::cout << "=== cpufreq files" << std::endl;
	const std::string d = "ice40-gpiomem-model.cpufreq";
	mkdir(d.c_str(), 0755);
	write_file(d + "/scaling_cur_freq", "600000\n");
	write_file(d + "/cpuinfo_min_freq", "600000\n");
	write_file(d + "/cpuinfo_max_freq", "1400000\n");
	CpuFreq f = Ice40GpioRegs::read_cpufreq(d);
	expect(f.cur_khz == 600000 && f.min_khz == 600000 && f.max_khz == 1400000,
		"cur, min and max are read in kHz");
	std::string got;
	remove((d + "/cpuinfo_max_freq").c_str());
	try {
		Ice40GpioRegs::read_cpufreq(d);
	} catch (std::exception &e) {
		got = e.what();
	}
	std::cout << "  missing max: " << got << std::endl;
	expect(got.find("cannot read " + d + "/cpuinfo_max_freq") !=
		std::string::npos && got.find("nothing is driven") != std::string::npos &&
		got.find("cpufreq driver loaded") != std::string::npos,
		"a missing cpufreq file stops loud, naming the file and the remedy");
	write_file(d + "/cpuinfo_max_freq", "not a number\n");
	got.clear();
	try {
		Ice40GpioRegs::read_cpufreq(d);
	} catch (std::exception &e) {
		got = e.what();
	}
	expect(got.find("does not hold a positive frequency") != std::string::npos,
		"a file that is not a number stops loud");
	write_file(d + "/cpuinfo_max_freq", "500000\n");  // below min
	got.clear();
	try {
		Ice40GpioRegs::read_cpufreq(d);
	} catch (std::exception &e) {
		got = e.what();
	}
	expect(got.find("not in order") != std::string::npos,
		"cur/min/max out of order stops loud");
	remove((d + "/scaling_cur_freq").c_str());
	remove((d + "/cpuinfo_min_freq").c_str());
	remove((d + "/cpuinfo_max_freq").c_str());
	rmdir(d.c_str());
}

int main()
{
	const Expect loaded = {"", "0xAB, 0x05, 0xB9", true, true, true};
	const std::string recover = "SPI lines released; the iCE40 was reset and "
		"booted from its flash again (CDONE high)";
	Host steady;  // 1 GHz, nothing moves

	Expect normal = loaded;
	normal.timing = true;
	normal.ss_changes = 10;
	run("normal, writes of 30 ns", NO_FAULT, steady, normal);

	// a fast CPU: writes of 5 ns need padding to reach 25 ns phases
	Host fast = steady;
	fast.access_ns = 5;
	Expect padded = normal;
	padded.pad = true;
	run("fast CPU, writes of 5 ns (padded)", NO_FAULT, fast, padded);

	// the CPU starts at 600 MHz and ramps to 1400 MHz inside the image:
	// the pad, sized at 600 MHz, must hold at 1400 MHz
	Host ramp = steady;
	ramp.ref_khz = 600000;
	ramp.freq = {600000, 600000, 1400000};
	ramp.access_ns = 12;
	ramp.ramp_at = 1000;
	run("CPU at 600 MHz ramps to its 1400 MHz maximum in the image", NO_FAULT,
		ramp, padded);

	// the real time of the load under 1 MHz: Ice40's check stops it
	Host slow_real = steady;
	slow_real.real_ns = 2000;
	Expect slow = {"below the iCE40's 1 MHz minimum", "0xAB, 0x05, 0xB9",
		false, true, true};
	run("host too slow in real time, 2 us a register access", NO_FAULT,
		slow_real, slow);

	// a CPU too slow at its minimum clock for 1 MHz: stopped before the image
	Host slow_min = steady;
	slow_min.access_ns = 400;
	run("a bit takes over 1 us at the minimum clock", NO_FAULT, slow_min,
		{"would fall under the iCE40's 1 MHz minimum", "", false, true, false});

	Host nofreq = steady;
	nofreq.freq_throws = true;
	run("cpufreq cannot be read", NO_FAULT, nofreq,
		{"cannot read cpufreq (modelled)", "", false, true, false});
	Host flap = steady;
	flap.freq_flaps = true;
	run("the CPU clock never holds still", NO_FAULT, flap,
		{"the CPU clock kept changing", "", false, true, false});

	run("configuration refused", CONFIG_FAILS, steady,
		{"CDONE stayed low after the image", "0xAB, 0x05, 0xB9", true, true, true});
	for (auto sig : {SIGTERM_IN_IMAGE, SIGQUIT_IN_IMAGE, SIGPIPE_IN_IMAGE}) {
		const char *n = sig == SIGTERM_IN_IMAGE ? "SIGTERM" :
			sig == SIGQUIT_IN_IMAGE ? "SIGQUIT" : "SIGPIPE";
		run(std::string(n) + " in the image", sig, steady,
			{std::string("interrupted by ") + n + "; " + recover,
			"0xAB, 0x05, 0xB9", false, true, true});
	}
	run("SIGINT in the 49 clocks", SIGINT_AT_END, steady,
		{"interrupted by SIGINT", "0xAB, 0x05, 0xB9", true, true, true});
	run("SIGHUP during the flash power-down", SIGHUP_IN_FLASH, steady,
		{"interrupted by SIGHUP", "0xAB", false, true, true});
	run("CDONE low before", CDONE_LOW_BEFORE, steady,
		{"CDONE is low before the reset", "", false, false, false});
	/* the pins go to inputs under the load's feet: the image never arrives
	 * and CDONE stays low, the load stops and recovers as for any failure
	 */
	run("restore_functions() from a signal handler", HANDLER_RESTORE_IN_IMAGE,
		steady, {"CDONE stayed low after the image", "0xAB, 0x05, 0xB9", false,
		true, true});

	// a GPFSEL write that does not take, SPI_SCK never becomes an output:
	// the readback in fsel() must see it
	Host nosck = steady;
	nosck.stick_pin = SCK;
	Expect nosck_e = {"unable to change the function of GPIO11: wrote 1, "
		"read back 0", "", false, true, true};
	run("GPFSEL of SPI_SCK does not take (fsel readback)", NO_FAULT, nosck,
		nosck_e);
	// SPI_SO becomes an output and stays one: the load stops, and the
	// restore's readback disagrees (bad > 0) and says so
	Host stuck = steady;
	stuck.stick_pin = SO;
	stuck.stick_stays_output = true;
	Expect stuck_e = {"unable to change the function of GPIO10: wrote 0, "
		"read back 1", "0xAB, 0x05, 0xB9", false, false, true};
	stuck_e.reset_held = true;
	stuck_e.fsel_back = false;
	stuck_e.stderr_has = "pinctrl set 8,10,9,11,25 ip; then pinctrl set 27 ip; then pinctrl get 17";
	run("GPFSEL of SPI_SO stays an output (restore readback)", NO_FAULT,
		stuck, stuck_e);

	// the start state
	case_start("a pin in an alternate function", {{SI, 4}},
		{"GPIO9 (SPI_SI) is in alternate function 4", "not taken over"});
	case_start("CDONE already an output", {{CDONE, 1}},
		{"GPIO17 (CDONE)", "already an output", "pinctrl set 17 ip"});
	case_start("other driven pins already outputs", {{HOLDN, 1}, {SS, 1}},
		{"GPIO8 (SPI_SS)", "GPIO25 (/HOLD)", "pinctrl set 8 ip",
		"pinctrl set 25 ip", "already an output"});
	case_pin_range();
	case_padding();
	case_selection();
	case_open_gpiomem();
	case_regs_mapping();
	case_cpufreq_files();

	std::cout << (failures ? "FAILED: " : "all cases hold: ") << failures
		<< " failure(s)" << std::endl;
	return failures ? 1 : 0;
}
