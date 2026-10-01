
"""
    Company: Digilent RO
    Engineer: bs
    Usage: Vitis projects
    @Description Provide shared helpers for Vitis workspace automation.
"""
import os
import platform
import subprocess
import string
import ctypes
from ctypes import cdll
from logging import (Logger, INFO, StreamHandler,
                     Formatter)
from sys import (stdout, argv)
from argparse import ArgumentParser

# File constants
FNEXIST = 1
FNOPEN = 2
FOPEN = 3
FLOCKERR = 4

# Log constants
LOG_CHECKIN = 1
LOG_CHECKOUT = LOG_CHECKIN << 1

# Opt constants
OPT_CHECKIN = 2
OPT_CHECKOUT = OPT_CHECKIN << 1

def MapCmdLineOpts(opt=OPT_CHECKIN, kwCLO={}):
    """
    @Description
    Parse shared check-in/check-out CLI options.

    @Parameters
    opt: Utility mode used to label argparse help.
    kwCLO: Output dictionary for parsed option values.
    """
    lsEntries = ["--port", "--ip"]
    for itm in lsEntries:
        kwCLO[itm] = ""

    if len(argv) > 1:
        parser = ArgumentParser(
                    description="Checkout options" if opt == OPT_CHECKOUT else
                                "Checkin options")
        parser.add_argument(f"{lsEntries[0]}", type=int, help="Server's port number")
        parser.add_argument(f"{lsEntries[1]}", type=str, help="Server's ip or localhost")
        args = parser.parse_args()
        for idx in range(0, len(lsEntries)):
            sTmp = lsEntries[idx][2:]
            attr = getattr(args, sTmp)
            if attr is not None:
                kwCLO[lsEntries[idx]] = attr

def LOG(msg="",
        format="%(asctime)s %(levelname)s : %(message)s",
        nameOtp=LOG_CHECKIN
        ):
    """
    @Description Log a message for check-in or check-out output.
    @Parameters
    msg: Message to display.
    format: Logging formatter string.
    nameOtp: Selects the logger label.
    """
    name = "Info Checkin" if nameOtp == LOG_CHECKIN else "Info Checkout"
    
    class _LOG(Logger):
        """Emit a one-shot formatted log record."""
        def __init__(self, msg, fmt, name="", stream=stdout):
            """Initialize and emit the configured log message."""
            super().__init__(name=name, level=INFO)
            self.sHnd = StreamHandler(stream)
            self.message = msg
            self._formater = Formatter(fmt)
            self.sHnd.setFormatter(self._formater)
            self.addHandler(self.sHnd)
            self.log(level=INFO, msg=self.message)
    _locLog = _LOG(msg, format, name)

def CkFileOpenBlock(filename : str,
                    osName=""
                    ) -> int:
    """
    @Description
    Check whether a lock file exists or is held open.

    @Parameters
    filename: Lock-file path to inspect.
    osName: Platform name override.
    """
    if not os.access(filename, os.F_OK):
        return FNEXIST
    
    if osName == "Windows":
        _close = cdll.msvcrt._close
        _locking = cdll.msvcrt._locking
        _filelength = cdll.msvcrt._filelength
        _LK_UNLCK = 0x0
        _LK_NBLCK = 0x2
        with open(filename, "r") as lcFile:
            fHnd = lcFile.fileno()
            # Lock at least one byte for a real contention check.
            nBytes = max(_filelength(fHnd), 1)
            try:
                _locking(fHnd, _LK_NBLCK, nBytes)
            except OSError:
                return FOPEN
            else:
                _locking(fHnd, _LK_UNLCK, nBytes)
                return FNOPEN
    elif osName == "Linux" or osName == "Darwin":
        from fcntl import (lockf, LOCK_EX, LOCK_NB, LOCK_UN)
        with open(filename, "r") as lcFile:
            lcFd = lcFile.fileno()
            # Try a non-blocking exclusive lock.
            try:
                lockf(lcFd, LOCK_EX | LOCK_NB)
            except BlockingIOError:
                return FOPEN
            except OSError as err:
                LOG(msg=f"{err.__cause__}" + f"{err.__context__}")
                return FLOCKERR
            else:
                lockf(lcFd, LOCK_UN)
                return FNOPEN
    else:
        LOG("This OS: " + osName + " is not supported!")

# Vitis install discovery and process-management helpers.
VITIS_ROOT_DIR_NAMES = ["AMDDesignTools", "Xilinx"]

# Vitis processes that can keep workspaces locked.
VITIS_PROC_NAMES_WIN = ["vitis.exe", "vitis-server.exe", "eclipse.exe", "java.exe"]
VITIS_PROC_NAMES_LNX = ["vitis", "vitis-server", "eclipse", "java"]


def list_windows_drives() -> list:
    """
    @Description
    Return available Windows drive roots.
    """
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for idx, letter in enumerate(string.ascii_uppercase):
        if bitmask & (1 << idx):
            drives.append(f"{letter}:{os.sep}")
    return drives


def _vitisLayoutCandidates(rootBase : str, version : str, exeName : str) -> list:
    """
    @Description
    Build candidate Vitis executable paths for a version.
    """
    return [
        os.path.join(rootBase, version, "Vitis", "bin", exeName),
        os.path.join(rootBase, "Vitis", version, "bin", exeName),
    ]


def findVitisRoot(version : str, configuredInstallPath : str = "") -> str:
    """
    @Description
    Locate the Vitis install root for a version.

    @Parameters
    version: Vitis version string, e.g. "2025.2".
    configuredInstallPath: Optional install path to try first.
    """
    isWin = platform.system() == "Windows"
    exeName = "vitis.bat" if isWin else "vitis"
    candidates = []

    if configuredInstallPath:
        configuredInstallPath = os.path.abspath(configuredInstallPath)
        # configuredInstallPath may already be the "...\\Vitis" root.
        candidates.append(os.path.join(configuredInstallPath, "bin", exeName))
        candidates += _vitisLayoutCandidates(configuredInstallPath, version, exeName)
        candidates += _vitisLayoutCandidates(os.path.dirname(configuredInstallPath), version, exeName)

    if isWin:
        rootBases = [os.path.join(drive, name)
                     for drive in list_windows_drives()
                     for name in VITIS_ROOT_DIR_NAMES]
    else:
        rootBases = [os.path.join(base, name)
                     for base in ("/opt", "/tools", os.path.expanduser("~"))
                     for name in VITIS_ROOT_DIR_NAMES]

    for rootBase in rootBases:
        candidates += _vitisLayoutCandidates(rootBase, version, exeName)

    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.dirname(os.path.dirname(candidate))
    return ""


def findVitisPython(vitisRoot : str) -> str:
    """
    @Description
    Locate the Python executable bundled with Vitis.

    @Parameters
    vitisRoot: Path returned by findVitisRoot.
    """
    if not vitisRoot:
        return ""
    isWin = platform.system() == "Windows"
    arch = "win64" if isWin else "lnx64"
    tpsDir = os.path.join(vitisRoot, "tps", arch)
    if not os.path.isdir(tpsDir):
        return ""
    for entry in sorted(os.listdir(tpsDir)):
        if entry.startswith("python-"):
            exe = os.path.join(tpsDir, entry,
                               "python.exe" if isWin else os.path.join("bin", "python3"))
            if os.path.isfile(exe):
                return exe
    return ""


def vitisPythonPathEntries(vitisRoot : str) -> list:
    """
    @Description
    Return PYTHONPATH entries needed by bundled Vitis Python.

    @Parameters
    vitisRoot: Path returned by findVitisRoot.
    """
    arch = "win64" if platform.system() == "Windows" else "lnx64"
    return [
        os.path.join(vitisRoot, "cli"),
        os.path.join(vitisRoot, "cli", "python-packages", arch),
        os.path.join(vitisRoot, "cli", "proto"),
        # Shared 3rd-party deps for xsdb and related imports.
        os.path.join(vitisRoot, "cli", "python-packages", "site-packages"),
        # HSI lives outside cli under scripts/python_pkg.
        os.path.join(vitisRoot, "scripts", "python_pkg"),
    ]


def runWithVitisPython(vitisRoot : str,
                       scriptPath : str,
                       scriptArgs : list = None,
                       cwd : str = None
                       ) -> int:
    """
    @Description Run a script with Vitis's bundled Python environment.
    @Parameters
    vitisRoot: Path returned by findVitisRoot.
    scriptPath: Script path to execute.
    scriptArgs: Optional extra CLI arguments. cwd: Optional working directory.
    @Returns Process return code, or -1 if bundled Python was not found.
    """
    pyExe = findVitisPython(vitisRoot)
    if not pyExe:
        LOG(f"Could not locate a bundled python under {vitisRoot}")
        return -1
    env = os.environ.copy()
    extraPaths = vitisPythonPathEntries(vitisRoot)
    prevPyPath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(extraPaths + ([prevPyPath] if prevPyPath else []))
    env["XILINX_VITIS"] = vitisRoot
    env["RDI_DATADIR"] = os.path.join(vitisRoot, "data")
    cmd = [pyExe, scriptPath] + (scriptArgs or [])
    result = subprocess.run(cmd, cwd=cwd, env=env)
    return result.returncode


# Generic names are scoped to a confirmed Vitis install path.
_VITIS_GENERIC_PROC_NAMES_WIN = {"eclipse.exe", "java.exe"}
_VITIS_GENERIC_PROC_NAMES_LNX = {"eclipse", "java"}


def _isUnderInstallRoot(candidate : str, root : str) -> bool:
    """
    @Description Test whether a path is the install root or below it.
    @Parameters
    candidate: Path to test.
    root: Install root path.
    @Returns True if candidate == root or nested under root.
    """
    normCandidate = os.path.normcase(os.path.normpath(os.path.abspath(candidate)))
    normRoot = os.path.normcase(os.path.normpath(os.path.abspath(root)))
    return normCandidate == normRoot or normCandidate.startswith(normRoot + os.sep)


def listVitisProcesses(vitisRoot : str = "", startedBefore : float = None) -> list:
    """
    @Description List running Vitis-related processes.
    @Parameters
    vitisRoot: Optional Vitis install root used to scope generic names.
    startedBefore: Optional cutoff used to ignore newly started processes.
    @Returns list of (pid : int, name : str) tuples.
    """
    procs = []
    isWin = platform.system() == "Windows"
    if isWin:
        genericNames = _VITIS_GENERIC_PROC_NAMES_WIN
        nameList = ",".join(f"'{n}'" for n in VITIS_PROC_NAMES_WIN)
        # Query pid, name, path, and creation time in one call.
        script = (
            f"Get-CimInstance Win32_Process | Where-Object {{ @({nameList}) -contains $_.Name }} | "
            "ForEach-Object { "
            "$created = ''; "
            # Emit 6 fractional digits for Python 3.8 compatibility.
            "if ($_.CreationDate) { $created = $_.CreationDate.ToString('yyyy-MM-ddTHH:mm:ss.ffffffK') }; "
            "\"$($_.ProcessId)|$($_.Name)|$($_.ExecutablePath)|$created\" }"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True).stdout
        for line in out.splitlines():
            parts = line.strip().split("|")
            if len(parts) < 4:
                continue
            pidStr, name, exePath, createdStr = parts[0], parts[1], parts[2], parts[3]
            if not pidStr.isdigit():
                continue
            pid = int(pidStr)
            if vitisRoot:
                # Scope matches to the selected install.
                if not exePath or not _isUnderInstallRoot(exePath, vitisRoot):
                    continue
            elif name in genericNames:
                # Skip ambiguous generic names without a known install root.
                continue
            if startedBefore is not None and createdStr:
                try:
                    from datetime import datetime
                    createdEpoch = datetime.fromisoformat(createdStr).timestamp()
                    if createdEpoch >= startedBefore:
                        continue
                except ValueError:
                    pass
            procs.append((pid, name))
    else:
        genericNames = _VITIS_GENERIC_PROC_NAMES_LNX
        out = subprocess.run(["ps", "-eo", "pid,comm,lstart"],
                             capture_output=True, text=True).stdout
        for line in out.splitlines()[1:]:
            parts = line.split(None, 2)
            if len(parts) < 2:
                continue
            pidStr, name = parts[0], parts[1]
            if name not in VITIS_PROC_NAMES_LNX:
                continue
            pid = int(pidStr)
            if vitisRoot:
                # Scope matches to the selected install.
                exePath = os.path.realpath(f"/proc/{pid}/exe")
                if not _isUnderInstallRoot(exePath, vitisRoot):
                    continue
            elif name in genericNames:
                # Skip ambiguous generic names without a known install root.
                continue
            if startedBefore is not None and len(parts) == 3:
                try:
                    from datetime import datetime
                    createdEpoch = datetime.strptime(parts[2].strip(), "%a %b %d %H:%M:%S %Y").timestamp()
                    # Allow a 1s tolerance for whole-second ps timestamps.
                    if createdEpoch >= startedBefore - 1:
                        continue
                except ValueError:
                    pass
            procs.append((pid, name))
    return procs


def stopDanglingVitisProcesses(vitisRoot : str = "", startedBefore : float = None,
                               force : bool = True) -> list:
    """
    @Description Stop leftover Vitis processes from earlier runs.
    @Parameters
    vitisRoot: Optional Vitis install root for scoping.
    startedBefore: Optional cutoff for excluding current-run processes.
    force: Use forceful termination when True.
    @Returns list of pids that were successfully signaled to stop.
    """
    return stopVitisProcessesByPid(listVitisProcesses(vitisRoot, startedBefore), force)


def stopVitisProcessesByPid(procs : list, force : bool = True) -> list:
    """
    @Description Stop the supplied Vitis process list.
    @Parameters
    procs: List of ``(pid, name)`` tuples.
    force: Use forceful termination when True.
    @Returns list of pids that were successfully signaled to stop.
    """
    stopped = []
    isWin = platform.system() == "Windows"
    for pid, name in procs:
        try:
            if isWin:
                cmd = ["taskkill", "/PID", str(pid)]
                if force:
                    cmd.append("/F")
                result = subprocess.run(cmd, capture_output=True)
                if result.returncode != 0:
                    LOG(f"Failed to stop process {name} (pid={pid}): "
                        f"{result.stderr.decode(errors='replace').strip()}")
                    continue
            else:
                from signal import SIGKILL, SIGTERM
                os.kill(pid, SIGKILL if force else SIGTERM)
            stopped.append(pid)
        except Exception as err:
            LOG(f"Failed to stop process {name} (pid={pid}): {err}")
    return stopped


def updatePlatformXsa(platformComp, xsaPath : str) -> bool:
    """
    @Description Update a platform component to use a new XSA.
    @Parameters
    platformComp: Platform component returned by vitis-py.
    xsaPath: Path to the new XSA file.
    @Returns True on success, False if vitis-py raised while updating.
    """
    try:
        return platformComp.update_hw(hw_design=os.path.abspath(xsaPath))
    except Exception as err:
        LOG(f"Failed to update platform hw spec to {xsaPath}: {err}")
        return False
