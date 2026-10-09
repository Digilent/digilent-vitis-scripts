#!/usr/bin/env python3
"""
@Description
Program a Zynq-7000 or Zynq UltraScale+ board from the launch configuration of
a Vitis app, without opening the Vitis IDE. The steps are the ones the IDE
runs: system reset, bitstream, PS init (psu_init.tcl / ps7_init.tcl or the
FSBL), ELF download and run. The device family is taken from the launch
configuration (core names, init Tcl file) or from --family.
Use the Vitis bundled Python so its xsdb module is available:
    _vitis.bat -v 2025.2 -s .\\program.py [options]
Examples:
    _vitis.bat -v 2025.2 -s .\\program.py
    _vitis.bat -v 2025.2 -s .\\program.py --elf-only
    _vitis.bat -v 2025.2 -s .\\program.py --build --elf-only --watch
    _vitis.bat -v 2025.2 -s .\\program.py --dry-run
"""
import argparse
import glob
import json
import re
import sys
import tkinter
from os import path, stat, environ, pathsep, _exit
from time import sleep

import xsdb
from misc import LOG, setLogFile

# Per family settings: the IDE uses the same values in its launch scripts
FAMILIES = {
    "zynqmp": {
        "pl_filter": 'name == "PL"',
        "pl_ranges": [[0x80000000, 0xBFFFFFFF], [0x400000000, 0x5FFFFFFFF],
                      [0x1000000000, 0x7FFFFFFFFF]],
        "core": "psu_cortexa53_0",
        "init_proc": "psu_init",
        "post_procs": ["psu_ps_pl_isolation_removal", "psu_ps_pl_reset_config"],
        "post_if_pl_powerup": True,
        "protect_proc": "psu_protection",
        "fsbl_exit": "XFsbl_Exit",
        "pmu_gate": True,
    },
    "zynq": {
        "pl_filter": 'name =~ "xc7z*"',
        "pl_ranges": [[0x40000000, 0xBFFFFFFF]],
        "core": "ps7_cortexa9_0",
        "init_proc": "ps7_init",
        "post_procs": ["ps7_post_config"],
        "post_if_pl_powerup": False,
        "protect_proc": None,
        "fsbl_exit": "FsblHandoffJtagExit",
        "pmu_gate": False,
    },
}
PMU_GATE_REG = 0xFFCA0038
PMU_GATE_MASK = 0x1C0
APU_FILTER = 'name =~ "APU*"'
PSU_FILTER = 'name =~ "PSU*"'
POLL_TRIES = 1000
POLL_STEP_S = 0.005
SYSTEM_RESET_SETTLE_S = 3
PSU_INIT_SETTLE_S = 1


def _num(text):
    """
    @Description Convert a Tcl number (hex, or decimal with leading zeros).
    @Parameters
    text: Number as text.
    """
    text = str(text).strip()
    if text.lower().startswith("0x"):
        return int(text, 16)
    return int(text, 10)


class PsInit:
    """
    @Description
    Embedded Tcl interpreter that runs psu_init.tcl unchanged. The XSDB
    commands the script relies on are backed by the Python xsdb session.
    """

    def __init__(self, session):
        """
        @Parameters
        session: Connected xsdb session with the target already selected.
        """
        self.session = session
        self.tcl = tkinter.Tcl()
        for name, func in (("mrd", self._mrd), ("mwr", self._mwr),
                           ("mask_write", self._mask_write),
                           ("mask_delay", self._mask_delay),
                           ("configparams", self._configparams)):
            self.tcl.createcommand(name, func)
        self.tcl.eval("proc init_ps {script} {uplevel #0 $script}")

    def _mrd(self, *args):
        """Tcl mrd [-force] [-value] addr: one 32-bit word."""
        flags = [a for a in args if a.startswith("-")]
        addr = _num([a for a in args if not a.startswith("-")][0])
        opts = ["-f"] if "-force" in flags else []
        val = self.session.mrd(addr, "-v", *opts)
        if "-value" in flags:
            return str(val)
        # Same text layout as the Tcl command, scripts slice it by position
        return "%08X:   %08X\n" % (addr, val)

    def _mwr(self, *args):
        """Tcl mwr [-force] addr value: one 32-bit word."""
        flags = [a for a in args if a.startswith("-")]
        nums = [_num(a) for a in args if not a.startswith("-")]
        opts = ["-f"] if "-force" in flags else []
        self.session.mwr(nums[0], *opts, words=nums[1])
        return ""

    def _mask_write(self, addr, mask, value):
        """Tcl mask_write addr mask value: read-modify-write."""
        addr, mask, value = _num(addr), _num(mask), _num(value)
        cur = self.session.mrd(addr, "-v", "-f")
        new = ((cur & ~mask) | (value & mask)) & 0xFFFFFFFF
        self.session.mwr(addr, "-f", words=new)
        return ""

    def _mask_delay(self, addr, delay_ms):
        """Tcl mask_delay addr ms: plain delay."""
        sleep(_num(delay_ms) / 1000.0)
        return ""

    def _mask_poll(self, addr, mask, value=None, timeout=None):
        """Tcl mask_poll addr mask [value]: wait for (reg & mask) == value."""
        addr, mask = _num(addr), _num(mask)
        value = mask if value is None else _num(value)
        for _ in range(POLL_TRIES):
            if self.session.mrd(addr, "-v", "-f") & mask == value:
                return ""
            sleep(POLL_STEP_S)
        LOG(f"WARNING: mask_poll timeout at 0x{addr:08X} mask 0x{mask:08X} value 0x{value:08X}")
        return ""

    def _configparams(self, *args):
        """Tcl configparams name [value]: get or set a debugger parameter."""
        if len(args) == 1:
            if args[0] == "force-mem-accesses":
                return "1" if self.session._force_mem_accesses else "0"
            return ""
        value = "1" if args[1] in ("1", "True", "true") else args[1]
        if value in ("False", "false"):
            value = "0"
        self.session.configparams(args[0], value)
        return ""

    def source(self, file):
        """
        @Description Load a Tcl file.
        @Parameters
        file: Path of the Tcl file.
        """
        self.tcl.evalfile(file)
        # The init files define their own mask_poll (2 arguments) and mask_delay (global
        # timer based, slow over JTAG), the init scripts need the XSDB builtin behavior
        self.tcl.createcommand("mask_poll", self._mask_poll)
        self.tcl.createcommand("mask_delay", self._mask_delay)

    def run(self, command):
        """
        @Description Run a Tcl command line.
        @Parameters
        command: Tcl command text.
        """
        self.tcl.eval(command)


def _subst(obj, ws):
    """
    @Description Expand ${workspaceFolder} in every string of a JSON object.
    @Parameters
    obj: Parsed JSON value.
    ws: Workspace folder.
    """
    if isinstance(obj, str):
        return obj.replace("${workspaceFolder}", ws)
    if isinstance(obj, list):
        return [_subst(i, ws) for i in obj]
    if isinstance(obj, dict):
        return {k: _subst(v, ws) for k, v in obj.items()}
    return obj


def core_filter(core):
    """
    @Description Map a launch.json core name to an xsdb target filter.
    @Parameters
    core: Core name such as psu_cortexa53_0, psu_cortexr5_1 or ps7_cortexa9_0.
    """
    match = re.match(r"(?:psu_cortex(a53|r5)|ps7_cortex(a9))_(\d+)$", core)
    if not match:
        raise Exception(f"Unsupported core '{core}' (expected psu_cortexa53_N, "
                        "psu_cortexr5_N or ps7_cortexa9_N)")
    cpu = (match.group(1) or match.group(2)).upper()
    return f'name =~ "*{cpu}*#{match.group(3)}"'


def detect_family(args, cores, init_tcl):
    """
    @Description Pick the device family from --family, the cores or the init file.
    @Parameters
    args: Parsed command line options.
    cores: Core names of the ELFs to download.
    init_tcl: PS init Tcl file of the launch configuration, or None.
    """
    if args.family:
        return args.family
    for core in cores:
        if core.startswith("ps7_"):
            return "zynq"
        if core.startswith("psu_"):
            return "zynqmp"
    if init_tcl and path.basename(init_tcl).startswith("ps7_"):
        return "zynq"
    return "zynqmp"


def find_app(ws, app):
    """
    @Description Find the app whose launch configuration is used.
    @Parameters
    ws: Workspace folder.
    app: App name or None to auto-detect.
    """
    if app:
        return app
    found = [path.basename(path.dirname(path.dirname(p))) for p in
             glob.glob(path.join(ws, "*", "_ide", "launch.json"))]
    # Platform FSBL apps have launch files too but are not what gets programmed
    found = [n for n in found if not n.endswith("_FSBL")]
    if len(found) != 1:
        raise Exception(f"Use --app, launch configurations found in {ws}: {found or 'none'}")
    return found[0]


def find_xsa(ws, fsbl_file):
    """
    @Description Find the exported XSA of the platform the app is built on.
    @Parameters
    ws: Workspace folder.
    fsbl_file: FSBL path from the launch configuration, used to name the platform.
    """
    found = glob.glob(path.join(ws, "*", "export", "*", "hw", "*.xsa"))
    if fsbl_file and fsbl_file.startswith(ws):
        platform = path.relpath(fsbl_file, ws).split("\\")[0]
        found = [p for p in found if path.relpath(p, ws).split("\\")[0] == platform] or found
    return found[0] if len(found) == 1 else None


def load_plan(ws, args):
    """
    @Description Build the programming plan from launch.json and the options.
    @Parameters
    ws: Workspace folder.
    args: Parsed command line options.
    """
    app = find_app(ws, args.app)
    launch = path.join(ws, app, "_ide", "launch.json")
    if not path.isfile(launch):
        raise Exception(f"{launch} not found, run checkout.py for {app} first")
    with open(launch, encoding="utf-8") as fh:
        configs = _subst(json.load(fh)["configurations"], ws)
    if args.config:
        configs = [c for c in configs if c.get("name") == args.config]
    if not configs:
        raise Exception(f"No launch configuration {args.config or ''} in {launch}")
    setup = configs[0]["targetSetup"]
    # The init section is named per family (zuInitialization on ZynqMP), find it by content
    init = next((v for v in setup.values() if isinstance(v, dict) and "usingFSBL" in v), {})
    fsbl = init.get("usingFSBL", {})
    script = next((v for k, v in init.items() if k != "usingFSBL" and isinstance(v, dict)), {})
    init_tcl = next((v for k, v in script.items() if k.endswith("InitTclFile")), None)
    if not init_tcl:
        found = glob.glob(path.join(ws, app, "_ide", "psinit", "p*_init.tcl"))
        init_tcl = found[0] if found else None
    use_fsbl = bool(init.get("isFsbl", False))
    if args.fsbl:
        use_fsbl = True
    elif args.psu_init:
        use_fsbl = False
    elfs = [{"core": e["core"], "file": e["elfFile"],
             "reset": bool(e.get("resetProcessor", True)),
             "stop": bool(e.get("stopAtEntry", False))}
            for e in setup.get("downloadElf", [])]
    family = detect_family(args, [e["core"] for e in elfs], args.init_tcl or init_tcl)
    fam = FAMILIES[family]
    if args.elf:
        elfs = [{"core": args.core or fam["core"], "file": path.abspath(args.elf),
                 "reset": True, "stop": False}]
    if not elfs:
        raise Exception(f"No ELF to download in {launch}, use --elf")
    for elf in elfs:
        if args.stop_at_entry:
            elf["stop"] = True
    return {
        "app": app,
        "family": family,
        "bit": args.bit or setup.get("bitstreamFile"),
        "xsa": args.xsa or find_xsa(ws, fsbl.get("fsblFile")),
        "reset_system": bool(setup.get("resetSystem", True)),
        "program_device": bool(setup.get("programDevice", True)),
        "use_fsbl": use_fsbl,
        "fsbl_elf": args.fsbl_elf or fsbl.get("fsblFile"),
        "fsbl_exit": args.fsbl_exit or fsbl.get("fsblExitSymbol") or fam["fsbl_exit"],
        "init_tcl": args.init_tcl or init_tcl,
        "pl_powerup": bool(script.get("plPowerup", True)),
        "elfs": elfs,
    }


def check_files(plan, args):
    """
    @Description Fail early if a file the selected steps need is missing.
    @Parameters
    plan: Programming plan.
    args: Parsed command line options.
    """
    need = [e["file"] for e in plan["elfs"]]
    if not args.elf_only:
        if plan["program_device"] and not args.no_bitstream:
            need.append(plan["bit"])
        if not args.no_init:
            need.append(plan["fsbl_elf"] if plan["use_fsbl"] else plan["init_tcl"])
            if not plan["use_fsbl"] and not plan["xsa"]:
                raise Exception("No platform XSA found, use --xsa")
            if plan["xsa"]:
                need.append(plan["xsa"])
    for file in need:
        if not file or not path.isfile(file):
            raise Exception(f"File not found: {file}")


class Programmer:
    """
    @Description Runs the programming steps through an xsdb session.
    """

    def __init__(self, plan, args):
        """
        @Parameters
        plan: Programming plan.
        args: Parsed command line options.
        """
        self.plan = plan
        self.args = args
        self.fam = FAMILIES[plan["family"]]
        self.session = None

    def step(self, text):
        """
        @Description Log a step; return False when only a dry run.
        @Parameters
        text: Equivalent XSDB command line.
        """
        LOG(("[dry-run] " if self.args.dry_run else "") + text)
        return not self.args.dry_run

    def connect(self):
        """@Description Open the xsdb session and connect to hw_server."""
        if not self.step(f"connect -url tcp:{self.args.host}:{self.args.port}"):
            return
        self.session = xsdb.start_debug_session()
        self.session.connect(url=f"tcp:{self.args.host}:{self.args.port}")

    def select(self, expr):
        """
        @Description Make the target matching a filter the current one.
        @Parameters
        expr: xsdb filter expression.
        """
        # The xsdb Python filter parser has no quoted strings, so spaces become wildcards
        expr = expr.replace('"', "")
        if self.args.cable:
            cable = self.args.cable.replace(" ", "*")
            expr = f"({expr}) and jtag_cable_name =~ *{cable}*"
        if self.step(f"targets -set -nocase -filter {{{expr}}}"):
            self.session.targets("--set", "--nocase", filter=expr)

    def system_reset(self):
        """@Description Reset the whole system through the APU target."""
        self.select(APU_FILTER)
        if self.step("rst -system"):
            self.session.rst(type="system")
            LOG(f"Waiting {SYSTEM_RESET_SETTLE_S}s for the PMU to come back")
            sleep(SYSTEM_RESET_SETTLE_S)

    def program_bitstream(self):
        """@Description Configure the PL."""
        self.select(self.fam["pl_filter"])
        if self.step(f"fpga -file {self.plan['bit']}"):
            self.session.fpga(file=self.plan["bit"])

    def load_hw(self):
        """@Description Load the hardware design to set the memory map."""
        if not self.plan["xsa"]:
            LOG("No platform XSA found, skipping loadhw")
            return
        ranges = self.fam["pl_ranges"]
        if self.step(f"loadhw -hw {self.plan['xsa']} -mem-ranges {ranges}"):
            self.session.loadhw(hw=self.plan["xsa"], mem_ranges=ranges)

    def ps_init(self):
        """@Description Initialize the PS by running psu_init.tcl or ps7_init.tcl."""
        fam = self.fam
        if fam["pmu_gate"]:
            self.select(PSU_FILTER)
            if self.step(f"disable_pmu_gate ({PMU_GATE_REG:#x} |= {PMU_GATE_MASK:#x})"):
                cur = self.session.mrd(PMU_GATE_REG, "-v", "-f")
                self.session.mwr(PMU_GATE_REG, "-f", words=cur | PMU_GATE_MASK)
        self.select(APU_FILTER)
        self.load_hw()
        if self.step("configparams force-mem-accesses 1"):
            self.session.configparams("force-mem-accesses", "1")
        if not self.step(f"source {self.plan['init_tcl']}"):
            return
        tcl = PsInit(self.session)
        tcl.source(self.plan["init_tcl"])
        calls = [fam["init_proc"]]
        if self.plan["pl_powerup"] or not fam["post_if_pl_powerup"]:
            calls += fam["post_procs"]
        for call in calls:
            LOG(call)
            tcl.run(call)
            sleep(PSU_INIT_SETTLE_S)
        if fam["protect_proc"]:
            try:
                tcl.run(fam["protect_proc"])
            except tkinter.TclError as err:
                LOG(f"{fam['protect_proc']} skipped: {err}")

    def fsbl_init(self):
        """@Description Initialize the PS by running the FSBL up to its exit."""
        self.select(core_filter(self.fam["core"]))
        if self.step("rst -processor"):
            self.session.rst(type="processor")
        if self.step(f"dow {self.plan['fsbl_elf']}"):
            self.session.dow(self.plan["fsbl_elf"])
        if self.step(f"bpadd -addr {self.plan['fsbl_exit']}"):
            self.session.bpadd(addr=self.plan["fsbl_exit"])
        if self.step(f"con -block -timeout {self.args.timeout}"):
            self.session.con("-b", timeout=self.args.timeout)
        if self.step("bpremove -all"):
            self.session.bpremove("--all")
        self.load_hw()

    def download_elfs(self):
        """@Description Download the ELFs to their cores and start them."""
        for elf in self.plan["elfs"]:
            self.select(core_filter(elf["core"]))
            if elf["reset"] and self.step("rst -processor"):
                self.session.rst(type="processor")
            if self.step(f"dow {elf['file']}"):
                self.session.dow(elf["file"])
            if elf["stop"] or self.args.no_run:
                LOG("Left stopped, not running")
            elif self.step("con"):
                self.session.con()

    def program(self, elf_only):
        """
        @Description Run the programming sequence.
        @Parameters
        elf_only: Only reset the core, download the ELF and run it.
        """
        args = self.args
        if self.session is None and not args.dry_run:
            self.connect()
        if self.step("bpremove -all"):
            self.session.bpremove("--all")
        if not elf_only:
            if self.plan["reset_system"] and not args.no_reset:
                self.system_reset()
            if self.plan["program_device"] and not args.no_bitstream:
                self.program_bitstream()
            if not args.no_init:
                if self.plan["use_fsbl"]:
                    self.fsbl_init()
                else:
                    self.ps_init()
        self.download_elfs()
        if not elf_only and self.step("configparams force-mem-accesses 0"):
            self.session.configparams("force-mem-accesses", "0")
        LOG("Done")

    def watch(self):
        """@Description Reprogram the ELF each time the file changes."""
        elf = self.plan["elfs"][0]["file"]
        last = stat(elf).st_mtime
        LOG(f"Watching {elf}, Ctrl-C to stop")
        while True:
            sleep(1)
            try:
                mtime = stat(elf).st_mtime
            except OSError:
                continue
            if mtime != last:
                sleep(1)
                last = stat(elf).st_mtime
                self.program(elf_only=True)


def build_app(ws, app):
    """
    @Description Rebuild the app with the Vitis client before programming.
    @Parameters
    ws: Workspace folder.
    app: App component name.
    """
    from vitis import create_client, dispose
    client = create_client()
    try:
        client.set_workspace(ws)
        LOG(f"Building {app}")
        client.get_component(name=app).build()
    finally:
        dispose()


def main():
    """@Description Parse the options and run the programming flow."""
    scripts = path.dirname(path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description="Program the board from a Vitis launch configuration without the IDE.")
    parser.add_argument("--ws", default=path.join(path.dirname(scripts), "ws"),
                        help="Workspace folder (default: ..\\ws).")
    parser.add_argument("--app", help="App whose launch.json is used (default: the only one).")
    parser.add_argument("--config", help="Launch configuration name (default: first).")
    parser.add_argument("--elf-only", action="store_true",
                        help="Only reset the core, download the ELF and run it.")
    parser.add_argument("--no-reset", action="store_true", help="Skip the system reset.")
    parser.add_argument("--no-bitstream", action="store_true", help="Skip the bitstream.")
    parser.add_argument("--no-init", action="store_true", help="Skip the PS initialization.")
    parser.add_argument("--no-run", action="store_true", help="Download the ELF but do not run it.")
    parser.add_argument("--stop-at-entry", action="store_true", help="Leave the core stopped.")
    parser.add_argument("--psu-init", "--ps7-init", "--ps-init", dest="psu_init", action="store_true",
                        help="Initialize the PS with psu_init.tcl / ps7_init.tcl.")
    parser.add_argument("--fsbl", action="store_true", help="Initialize the PS by running the FSBL.")
    parser.add_argument("--family", choices=sorted(FAMILIES),
                        help="Device family (default: from the launch configuration).")
    parser.add_argument("--elf", help="ELF to download instead of the launch.json one.")
    parser.add_argument("--core", help="Core for --elf (default: first core of the family).")
    parser.add_argument("--bit", help="Bitstream to program instead of the launch.json one.")
    parser.add_argument("--xsa", help="XSA used for loadhw (default: the platform export).")
    parser.add_argument("--init-tcl", help="PS init Tcl to use instead of the launch.json one.")
    parser.add_argument("--fsbl-elf", help="FSBL ELF to use instead of the launch.json one.")
    parser.add_argument("--fsbl-exit", help="FSBL symbol to stop at (default: per family).")
    parser.add_argument("--timeout", type=int, default=60, help="FSBL run timeout in seconds.")
    parser.add_argument("--cable", help="JTAG cable name substring, when several are connected.")
    parser.add_argument("--host", default="127.0.0.1", help="hw_server host.")
    parser.add_argument("--port", type=int, default=3121, help="hw_server port.")
    parser.add_argument("--build", action="store_true", help="Rebuild the app first.")
    parser.add_argument("--watch", action="store_true",
                        help="After programming, reprogram the ELF whenever it changes.")
    parser.add_argument("--list-targets", action="store_true", help="List the targets and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Print the steps without running them.")
    parser.add_argument("--log", help="Log file (default: <ws>\\program.log, appended).")
    args = parser.parse_args()
    if args.fsbl and args.psu_init:
        parser.error("--fsbl and --psu-init are exclusive")

    # connect() auto-starts hw_server by name, so the Vitis bin dir must be on PATH
    vitis_bin = path.join(environ.get("XILINX_VITIS", ""), "bin")
    if path.isdir(vitis_bin):
        environ["PATH"] = vitis_bin + pathsep + environ.get("PATH", "")

    ws = path.abspath(args.ws)
    if not args.dry_run and path.isdir(ws):
        setLogFile(args.log or path.join(ws, "program.log"))
    try:
        plan = load_plan(ws, args)
        if args.build and not args.dry_run:
            build_app(ws, plan["app"])
        if not args.list_targets:
            check_files(plan, args)
        prog = Programmer(plan, args)
        prog.connect()
        if args.list_targets:
            if prog.session:
                prog.session.targets()
            return 0
        prog.program(elf_only=args.elf_only)
        if args.watch and not args.dry_run:
            prog.watch()
    except KeyboardInterrupt:
        LOG("Interrupted")
    except Exception as err:
        LOG(f"ERROR: {err}")
        return 1
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    # Leave without waiting for the xsdb helper threads
    _exit(code)
