
"""
    Company: Digilent RO
    Engineer: bs
    Usage: Vitis projects
    
    @Description
    Different utility functions to manage some file or
    data in a certain way that facilitates a vitis
    workspace automation.
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
from argparse import (ArgumentParser, FileType)

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
    Extract options passed to checkout or checkin utilities. kwCLO
    modified an attribute from current class that calls this function,
    this way an item can be transfered to Vitis server like port.
    
    Form-factor:
    --port=<port-number>, this can be left blank to get automatically the port
    --ip=<ip-address>, this can be left blank to use localhost
    --fastsave=<speed-level>, levels: 1, 2, 3, these influence nr. of threads,
                               if left blank no other thread is created
    --outputfile=<save-log-messages-into-a-file>, path to a file or name of file
    
    The above options order is not important, so they can be passed
    in any possible order.
    """
    lsEntries = ["--port", "--ip", "--fastsave", "--outputfile"]
    for itm in lsEntries:
        kwCLO[itm] = ""

    if len(argv) > 1:
        parser = ArgumentParser(
                    description="Checkin options" if opt == OPT_CHECKOUT else
                                "Checkout options")
        parser.add_argument(f"{lsEntries[0]}", type=int, help="Server's port number")
        parser.add_argument(f"{lsEntries[1]}", type=str, help="Server's ip or localhost")
        parser.add_argument(f"{lsEntries[2]}", type=int, help="Influences no. of threads")
        parser.add_argument(f"{lsEntries[3]}", type=FileType("w"),
                            help="Redirect output of used utility")
        args = parser.parse_args()
        for idx in range(0, len(lsEntries)):
            # Trim the first two `--` dashes.
            sTmp = lsEntries[idx][2:]
            attr = getattr(args, sTmp)
            if attr is not None:
                kwCLO[lsEntries[idx]] = attr

def LOG(msg="",
        format="%(asctime)s %(levelname)s : %(message)s",
        nameOtp=LOG_CHECKIN
        ):
    """
    @Description
    Custom logging mechanism for displaying informations during
    the execution of check in workflow.

    @Parameters
    msg: message to display to stdout
    format: default format: time - level name - actual message
    nameOpt: what utility uses this logger
    """
    name = "Info Checkin" if nameOtp == LOG_CHECKIN else "Info Checkout"
    
    class _LOG(Logger):
        """
        Simple layout class for logging
        """
        def __init__(self, msg, fmt, name="", stream=stdout):
            super().__init__(name=name, level=INFO)
            self.sHnd = StreamHandler(stream)
            self.message = msg
            self._formater = Formatter(fmt)
            self.sHnd.setFormatter(self._formater)
            self.addHandler(self.sHnd)
            self.log(level=INFO, msg=self.message)
    # Nested log
    _locLog = _LOG(msg, format, name)

def CkFileOpenBlock(filename : str,
                    osName=""
                    ) -> int:
    """
    @Description
    Options for file management on win are found at
    win32 -> fileio -> file-management-functions on win website. For lnx,
    on man-pages -> man0 -> fcnt.h. The `.lock` file is assumed to be in
    workspace root dir.
    """
    # Check if file exits.
    if not os.access(filename, os.F_OK):
        # File doesn't exist.
        return FNEXIST
    
    if osName == "Windows":
        _close = cdll.msvcrt._close
        _locking = cdll.msvcrt._locking
        _filelength = cdll.msvcrt._filelength
        # Win CRT in general returns 0 if any error did not occur.
        _LK_UNLCK = 0x0
        _LK_NBLCK = 0x2
        with open(filename, "r") as lcFile:
            fHnd = lcFile.fileno()
            # Lock at least 1 byte (locking 0 bytes never contends) in
            # non-blocking mode: this only succeeds if no other process
            # currently holds a lock on this file.
            nBytes = max(_filelength(fHnd), 1)
            try:
                _locking(fHnd, _LK_NBLCK, nBytes)
            except OSError:
                # Another process holds a lock on this file.
                return FOPEN
            else:
                # We got the lock ourselves - release it right away and
                # report that nobody else had this file open.
                _locking(fHnd, _LK_UNLCK, nBytes)
                return FNOPEN
    elif osName == "Linux" or osName == "Darwin":
        # This module comes only on Unix like platforms.
        from fcntl import (lockf, LOCK_EX, LOCK_NB, LOCK_UN)
        # Get file descriptor.
        with open(filename, "r") as lcFile:
            lcFd = lcFile.fileno()
            # Attempt a non-blocking exclusive lock to test whether some
            # other process currently holds a lock on this file.
            try:
                lockf(lcFd, LOCK_EX | LOCK_NB)
            except BlockingIOError:
                # Another process holds a lock on this file.
                return FOPEN
            except OSError as err:
                # Interpret error from lockf in some way.
                LOG(msg=f"{err.__cause__}" + f"{err.__context__}")
                return FLOCKERR
            else:
                # We got the lock ourselves - release it right away and
                # report that nobody else had this file open.
                lockf(lcFd, LOCK_UN)
                return FNOPEN
    else:
        LOG("This OS: " + osName + " is not supported!")

# ---------------------------------------------------------------------------
# Vitis install discovery / bundled-python launch / dangling process cleanup
# ---------------------------------------------------------------------------
# This section is the "base engine" for locating a Vitis install (any
# version) and running checkin.py/checkout.py (or any other script) with the
# *exact* python interpreter that ships inside that install, so nothing here
# depends on system PATH/env vars (XILINX_VITIS, PATH, ...) or on the
# interactive `vitis -s <script>` launcher. Only python (+ tcl, indirectly,
# through vitis-py itself) is required.

# AMD renamed the install root dir from "Xilinx" to "AMDDesignTools" starting
# with the 2025.1 release family (same rename affects Vivado, see
# digilent-vivado-scripts/clean_build.py). Both are searched so older and
# newer installs are found on the same machine.
VITIS_ROOT_DIR_NAMES = ["AMDDesignTools", "Xilinx"]

# Process names that can be left dangling by a crashed/closed Vitis IDE and
# keep a workspace's files/locks open (root cause of checkout.py's rmtree
# WinError 32 / stale ".wsdata/.lock" failures).
VITIS_PROC_NAMES_WIN = ["vitis.exe", "vitis-server.exe", "eclipse.exe", "java.exe"]
VITIS_PROC_NAMES_LNX = ["vitis", "vitis-server", "eclipse", "java"]


def list_windows_drives() -> list:
    """
    @Description
    Returns available drive roots on Windows, e.g. ['C:\\\\', 'D:\\\\', 'E:\\\\'].
    Same approach as digilent-vivado-scripts/clean_build.py, ported here so
    Vitis tooling does not need to depend on that repo.
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
    Both known on-disk Vitis install layouts under an install root, e.g. for
    rootBase="E:\\AMDDesignTools" and version="2025.2":
      - E:\\AMDDesignTools\\2025.2\\Vitis\\bin\\vitis.bat  (2025.1+ layout, confirmed)
      - E:\\AMDDesignTools\\Vitis\\2025.2\\bin\\vitis.bat  (older/legacy layout)
    """
    return [
        os.path.join(rootBase, version, "Vitis", "bin", exeName),
        os.path.join(rootBase, "Vitis", version, "bin", exeName),
    ]


def findVitisRoot(version : str, configuredInstallPath : str = "") -> str:
    """
    @Description
    Locates the Vitis install root (the "...\\Vitis" dir, parent of "bin",
    "cli", "tps", ...) for `version`, with no dependency on env vars.

    Search order:
      1. Both known layouts under configuredInstallPath (equivalent to
         config.ini's VivadoInstallPath on the Vivado side), and under its
         parent, in case it was already given as a "...\\Vitis" path.
      2. Both known layouts under "<root>/<root_dir_name>" for every root
         name in VITIS_ROOT_DIR_NAMES, where <root> is every drive letter on
         Windows, or a common install base on Linux.

    @Parameters
    version: Vitis version string, e.g. "2025.2".
    configuredInstallPath: optional user-provided install path to try first.
    """
    isWin = platform.system() == "Windows"
    exeName = "vitis.bat" if isWin else "vitis"
    candidates = []

    if configuredInstallPath:
        configuredInstallPath = os.path.abspath(configuredInstallPath)
        # configuredInstallPath may already be the "...\Vitis" root itself
        # (as documented for -InstallPath/-i), so check that directly too.
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
            # candidate = <vitis_root>/bin/<exeName>, so <vitis_root> is
            # two path components up.
            return os.path.dirname(os.path.dirname(candidate))
    return ""


def findVitisPython(vitisRoot : str) -> str:
    """
    @Description
    Locate the python executable bundled inside a Vitis install, e.g.
    <vitisRoot>/tps/win64/python-3.13.0/python.exe (Windows) or
    <vitisRoot>/tps/lnx64/python-3.*/bin/python3 (Linux). This is the same
    interpreter `vitis -s <script>` uses internally.

    @Parameters
    vitisRoot: path returned by findVitisRoot.
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
    Dirs that must be prepended to PYTHONPATH so the bundled interpreter can
    `import vitis` (the same module checkin.py/checkout.py use) without going
    through the interactive `vitis -s` launcher: the `vitis` package itself,
    plus its vendored grpc/protobuf runtime deps and generated proto stubs.

    @Parameters
    vitisRoot: path returned by findVitisRoot.
    """
    arch = "win64" if platform.system() == "Windows" else "lnx64"
    return [
        os.path.join(vitisRoot, "cli"),
        os.path.join(vitisRoot, "cli", "python-packages", arch),
        os.path.join(vitisRoot, "cli", "proto"),
        # Platform-independent 3rd-party deps (pyelftools, psutil, ...) that
        # `import xsdb` (transitively, via xsdb._elf) needs, vendored
        # separately from the per-arch dir above.
        os.path.join(vitisRoot, "cli", "python-packages", "site-packages"),
        # `import hsi` (used by GetMetadata to read arch/target_proc out of
        # an XSA) is not under `cli` at all, it is HSI's own self-contained
        # package (native libs resolved relative to itself, see its
        # __init__.py), one level up under `scripts/python_pkg`.
        os.path.join(vitisRoot, "scripts", "python_pkg"),
    ]


def runWithVitisPython(vitisRoot : str,
                       scriptPath : str,
                       scriptArgs : list = None,
                       cwd : str = None
                       ) -> int:
    """
    @Description
    Invoke `scriptPath` (checkin.py, checkout.py, ...) with the python
    interpreter bundled inside `vitisRoot`, injecting the PYTHONPATH entries
    from vitisPythonPathEntries so `import vitis`/`hsi`/`xsdb` work, and
    XILINX_VITIS/RDI_DATADIR so vitis-py's create_client()/startServer finds
    the real "<vitisRoot>/bin/vitis-server(.bat)" instead of falling back to
    a stale dev-build layout that does not exist in a real install, and
    hsi's native libs (xv_pycommontasks/xv_hsmpytasks) can initialize. All
    are set only on the child process's env, not the caller's, so this
    still does not depend on any pre-existing system env var/PATH.

    @Parameters
    vitisRoot: path returned by findVitisRoot.
    scriptPath: absolute/relative path to the python script to run.
    scriptArgs: optional list of extra cmd-line args for scriptPath.
    cwd: optional working dir to run the script from.

    @Returns
    Process return code (0 on success), or -1 if the bundled python could
    not be located.
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


# "vitis.exe"/"vitis-server.exe" are unambiguous, but "eclipse"/"java" are
# generic image names shared with unrelated, unrelated-to-Vitis apps (any
# other Java program, another Eclipse-based IDE, ...). Killing those by
# bare name alone would risk taking down something that has nothing to do
# with the workspace being cleared, so they are only ever matched once
# their on-disk executable path is confirmed to live under the Vitis
# install actually in use (see listVitisProcesses).
_VITIS_GENERIC_PROC_NAMES_WIN = {"eclipse.exe", "java.exe"}
_VITIS_GENERIC_PROC_NAMES_LNX = {"eclipse", "java"}


def listVitisProcesses(vitisRoot : str = "", startedBefore : float = None) -> list:
    """
    @Description
    List running Vitis-related processes (vitis/vitis-server/eclipse/java)
    using only OS-native tools (`Get-CimInstance`/`ps`), no 3rd-party deps
    like psutil, so this stays usable with just the bundled python's
    stdlib. A single query is used per platform (not one call per pid) to
    also recover each process's own start time, needed to never treat the
    CURRENT run's own just-started Vitis server as "dangling" (see
    stopDanglingVitisProcesses).

    @Parameters
    vitisRoot: if given, processes with a generic image name ("java"/
               "eclipse", see _VITIS_GENERIC_PROC_NAMES_*) are only
               included when their actual executable path is located
               under this Vitis install; if not given, generic names are
               skipped entirely (fail safe) and only the unambiguous
               "vitis"/"vitis-server" names are matched.
    startedBefore: if given (a time.time()-style epoch), processes that
               started at or after this timestamp are excluded - meant to
               be the calling script's own start time, so a server it
               just spawned itself is never mistaken for a leftover from
               some earlier, already-finished run.

    @Returns
    list of (pid : int, name : str) tuples.
    """
    procs = []
    isWin = platform.system() == "Windows"
    if isWin:
        genericNames = _VITIS_GENERIC_PROC_NAMES_WIN
        nameList = ",".join(f"'{n}'" for n in VITIS_PROC_NAMES_WIN)
        # One shot: ProcessId, Name, ExecutablePath (for scoping "eclipse"/
        # "java" to this Vitis install) and CreationDate (for startedBefore),
        # pipe-delimited since none of those fields can contain a "|" on
        # Windows.
        script = (
            f"Get-CimInstance Win32_Process | Where-Object {{ @({nameList}) -contains $_.Name }} | "
            "ForEach-Object { "
            "$created = ''; "
            "if ($_.CreationDate) { $created = [Management.ManagementDateTimeConverter]::ToDateTime($_.CreationDate).ToString('o') }; "
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
                # An install root is known: scope every matched name
                # (including the otherwise-unambiguous "vitis"/
                # "vitis-server") to it, so a different Vitis version's
                # processes are never touched.
                if not exePath or not os.path.normcase(exePath).startswith(os.path.normcase(os.path.abspath(vitisRoot))):
                    continue
            elif name in genericNames:
                # No install root given: generic names are inherently
                # ambiguous, so skip them entirely rather than risk
                # matching an unrelated app.
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
                # An install root is known: scope every matched name
                # (including the otherwise-unambiguous "vitis"/
                # "vitis-server") to it, so a different Vitis version's
                # processes are never touched.
                exePath = os.path.realpath(f"/proc/{pid}/exe")
                if not exePath.startswith(os.path.abspath(vitisRoot)):
                    continue
            elif name in genericNames:
                # No install root given: generic names are inherently
                # ambiguous, so skip them entirely rather than risk
                # matching an unrelated app.
                continue
            if startedBefore is not None and len(parts) == 3:
                try:
                    from datetime import datetime
                    createdEpoch = datetime.strptime(parts[2].strip(), "%a %b %d %H:%M:%S %Y").timestamp()
                    # "ps ... lstart" only has whole-second resolution, while
                    # startedBefore comes from a fractional time.time(); a
                    # process started later in the same second as
                    # startedBefore would otherwise floor-round below the
                    # cutoff and be mistaken for an older, dangling one. A
                    # 1s tolerance keeps the just-started current server out
                    # of the "dangling" list.
                    if createdEpoch >= startedBefore - 1:
                        continue
                except ValueError:
                    pass
            procs.append((pid, name))
    return procs


def stopDanglingVitisProcesses(vitisRoot : str = "", startedBefore : float = None,
                               force : bool = True) -> list:
    """
    @Description
    Find and terminate leftover vitis/vitis-server/eclipse/java processes
    that hold file/lock handles on a workspace. Meant to be called before a
    checkout that needs to delete/recreate the ws dir, to avoid the
    WinError 5 (read-only)/WinError 32 (file locked by a stale process)
    failures currently hit blindly (10x retry, no recovery) by checkout.py's
    shutil.rmtree call.

    @Parameters
    vitisRoot: forwarded to listVitisProcesses; pass the Vitis install
               actually in use (e.g. environ["XILINX_VITIS"]) so the
               generic "java"/"eclipse" names are scoped to processes that
               truly belong to it, instead of matching (and killing) any
               unrelated Java/Eclipse app on the machine.
    startedBefore: forwarded to listVitisProcesses; pass the calling
               script's own start time (e.g. captured once at import time)
               so a server IT just spawned this very run is never killed
               mid-RPC - confirmed to otherwise happen: killing "any
               matching vitis-server.exe" can hit the current session's
               own server, breaking its already-open gRPC channel
               ("Connection reset"/"Connection refused" on every retry
               afterwards, since nothing is listening anymore).
    force: True uses `taskkill /F` / SIGKILL, False asks nicely first
           (`taskkill` without /F / SIGTERM).

    @Returns
    list of pids that were successfully signaled to stop.
    """
    stopped = []
    isWin = platform.system() == "Windows"
    for pid, name in listVitisProcesses(vitisRoot, startedBefore):
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
    @Description
    Thin wrapper around vitis-py's platform_component.update_hw, to swap the
    hardware specification (.xsa) an existing platform/app is built against,
    e.g. moving a tac5142 app onto a freshly rebuilt tac5112 *_hw_pf xsa
    without recreating the whole workspace.

    @Parameters
    platformComp: object returned by client.get_platform_component(<name>).
    xsaPath: path to the new .xsa file to bind the platform to.

    @Returns
    True on success, False if vitis-py raised while updating.
    """
    try:
        return platformComp.update_hw(hw_design=os.path.abspath(xsaPath))
    except Exception as err:
        LOG(f"Failed to update platform hw spec to {xsaPath}: {err}")
        return False
