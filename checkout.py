
"""
Company: Digilent RO
Engineers: rb & bs
Usage: Vitis projects

@Description
Recreate Vitis projects from sw -> src into sw -> ws.
"""
from vitis import create_client, dispose
import argparse
import time
import sys
import platform
from datetime import datetime
import shutil
from re import (search, compile, RegexFlag)
from json import JSONDecoder
from os import (path, walk, listdir, remove,
                sep, makedirs, chmod, environ)
from stat import S_IWRITE
from tempfile import mkdtemp
from hashlib import sha256
import hsi
import xsdb
import threading
import zipfile
from xml.etree import ElementTree
from misc import (LOG, LOGD, setLogFile, stopDanglingVitisProcesses, listVitisProcesses,
                  stopVitisProcessesByPid)
from contextlib import (contextmanager, ExitStack)

# Source extensions _pruneStaleFiles may prune at an app src top level.
PRUNABLE_SRC_FILE_EXTENSIONS = frozenset({
    ".c", ".cc", ".cpp", ".cxx", ".c++", ".C", ".h", ".hh", ".hpp", ".hxx", ".s", ".S", ".ld"
    })

# Captured before create_client() can spawn this run's backend process.
_PROCESS_START_TIME = time.time()

def GetMetadata(**kwargs):
    """
    @Description
    Extract hardware metadata from an XSA.

    @Parameters
    kwargs: accepts xsa and open_xsa.

    @Returns
    dict with arch, target_proc, and target_procs.
    """
    xsa = ""
    open_xsa = 0
    ret_metadata = {"arch" : "", "target_proc" : "", "target_procs" : []}
    for key, value in kwargs.items():
        if key == "xsa":
            xsa = value
        if key == "open_xsa" and value:
            open_xsa = 1
    
    if open_xsa == 1:
        if xsa != "":
            LOGD(f"Extracting hardware metadata from {xsa} using the HSI Python API...")
            #  xv_pycommontasks - py extension module (on win)
            #  xv_hsmpytasks - py extension module (on win)
            HwDesign = hsi.HwManager.open_hw_design(xsa)
            SUPPORTED_PROCESSOR_IP_NAMES = (
                "psu_cortexa53", "psu_cortexa72", "psu_cortexr5",
                "psv_cortexa72", "psv_cortexr5", "ps7_cortexa9",
                "microblaze"
                )
            try:
                ret_metadata["arch"] = HwDesign.FAMILY
                for proc in HwDesign.get_cells(hierarchical="true", filter="IP_TYPE==PROCESSOR"):
                    if proc.IP_NAME in SUPPORTED_PROCESSOR_IP_NAMES:
                        proc_name = proc.NAME
                        if proc_name not in ret_metadata["target_procs"]:
                            ret_metadata["target_procs"].append(proc_name)
                        if ret_metadata["target_proc"] == "":
                            ret_metadata["target_proc"] = proc_name
            finally:
                HwDesign.close()
        else:
            LOG("No XSA file was provided, hardware metadata cannot be extracted!")
    else:
        LOGD("Skipping HW metadata extraction, not requested for this call.")
    
    return ret_metadata

class _BuildLogFilter:
    """
    @Description
    Filter verbose Vitis build output. Everything goes to the log file;
    only errors, failures and build results reach the terminal. Compiler
    and CMake warnings are counted but kept in the log only.
    """
    ESSENTIAL_PATTERN = compile(r"\berror\b|fail|build finished|build complete|\*\*\*",
                                RegexFlag.IGNORECASE)
    WARNING_PATTERN = compile(r"warning", RegexFlag.IGNORECASE)

    def __init__(self, logFile, realStream):
        """Store the wrapped log and terminal streams."""
        self._logFile = logFile
        self._real = realStream
        self._pending = ""
        self.warningCount = 0

    def write(self, data):
        """Mirror data to the log and selected terminal lines."""
        self._logFile.write(data)
        self._pending += data
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if self.ESSENTIAL_PATTERN.search(line):
                self._real.write(line + "\n")
            elif self.WARNING_PATTERN.search(line):
                self.warningCount += 1

    def flush(self):
        """Flush both wrapped streams."""
        self._logFile.flush()
        self._real.flush()

class _BuildWatchdog:
    """
    @Description
    Monitor quiet builds and log hang diagnostics.
    """
    WARN_INTERVAL_SEC = 180

    def __init__(self, desc, ws_path="", auto_recover=False):
        """Initialize the watchdog state."""
        self._desc = desc
        self._wsPath = ws_path
        self._autoRecover = auto_recover
        self.killedForRecovery = False
        self._stopEvent = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        """Start the watchdog thread."""
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop the watchdog thread."""
        self._stopEvent.set()
        self._thread.join(timeout=5)
        return False

    @staticmethod
    def _mostRecentMtime(ws_path):
        """
        @Description
        Return the newest file mtime under a workspace tree, ignoring the run
        log the watchdog itself writes to.
        """
        latest = None
        runLog = path.normcase(path.join(ws_path, "checkout.log"))
        try:
            for root, _dirs, files in walk(ws_path):
                for f in files:
                    fullPath = path.join(root, f)
                    if path.normcase(fullPath) == runLog:
                        continue
                    try:
                        mtime = path.getmtime(fullPath)
                    except OSError:
                        continue
                    if latest is None or mtime > latest:
                        latest = mtime
        except OSError:
            pass
        return latest

    def _run(self) -> None:
        """Poll for silent hangs and optionally trigger recovery."""
        elapsedSec = 0
        while not self._stopEvent.wait(self.WARN_INTERVAL_SEC):
            elapsedSec += self.WARN_INTERVAL_SEC
            now = time.time()
            latestMtime = self._mostRecentMtime(self._wsPath) if self._wsPath else None
            if latestMtime is not None and (now - latestMtime) < self.WARN_INTERVAL_SEC:
                LOG(f"Still building {self._desc}: no console output for over "
                   f"{elapsedSec // 60} minute(s), but files under the workspace "
                   f"were last modified {int(now - latestMtime)}s ago - Vitis's own "
                   "progress output is mostly filtered out (see quietBuild), so this "
                   "is expected for a big/slow build step, not a hang.")
                continue
            try:
                vitisRoot = environ.get("XILINX_VITIS", "")
                allProcs = listVitisProcesses(vitisRoot)
                staleProcs = listVitisProcesses(vitisRoot, _PROCESS_START_TIME)
                staleDesc = ", ".join(f"{name} (pid {pid})" for pid, name in staleProcs) or "none"
                ownProcs = [p for p in allProcs if p not in staleProcs]
                ownDesc = ", ".join(f"{name} (pid {pid})" for pid, name in ownProcs) or "none found (may have crashed)"
            except Exception as e:
                ownProcs = []
                staleDesc = ownDesc = f"unavailable ({e})"
            if self._autoRecover and ownProcs:
                LOG(f"WARNING: building {self._desc} has shown no console output AND no "
                   f"workspace file activity for over {elapsedSec // 60} minute(s) - this "
                   "looks like a genuine hang, not just a slow build. Force-stopping this "
                   f"run's own stuck Vitis backend ({ownDesc}) to attempt automatic "
                   "recovery (a single retry in a fresh Vitis session)...")
                stopped = stopVitisProcessesByPid(ownProcs)
                self.killedForRecovery = True
                LOG(f"Stopped process(es) {stopped} for recovery; waiting for the "
                   "interrupted build call to return control...")
                return
            LOG(f"WARNING: building {self._desc} has shown no console output AND no "
               f"workspace file activity for over {elapsedSec // 60} minute(s) - this "
               "looks like a genuine hang, not just a slow build. Leftover process(es) "
               f"from an EARLIER run (a likely cause): {staleDesc}. This run's own "
               f"Vitis backend, still alive: {ownDesc}.")

class Workspace:
    """
    @Description
    Manage workspace checkout, rebuild, and build helpers.
    """
    SUCCESS = 0
    FAILURE = -1
    COMP_SETTINGS = "comp-settings.json"
    DEBUG = 0
    # Repo-tracked entries that must survive a workspace wipe.
    PRESERVED_WS_ENTRIES = {".keep", "cleanup.cmd", "cleanup.sh"}

    def __init__(self):
        """Initialize workspace state used across one checkout run."""
        self._buildLogPath = ""
        # General (non-build) log kept next to the build log.
        self._runLogPath = ""
        # Scratch area base for HSI side effects.
        self._wsPath = ""
        # Opt-in cleanup for pre-existing Vitis processes.
        self._allowProcessCleanup = False
        # Skip the wipe confirmation prompt on full checkout.
        self._skipConfirmation = False
        # Launch configs initialize the PS with psu_init.tcl instead of the FSBL.
        self._psuInitLaunch = False
        # XSA paths skipped for a confirmed Vitis-version mismatch.
        self._versionSkippedXsaPaths = set()

    def setConfigDomain(self,
                        domain,
                        option : str,
                        **kargs
                        ) -> int:
        """
        @Description
        Apply domain configuration keys.

        @Parameters
        domain: current domain object.
        option: config namespace, such as proc or os.
        kargs: param/value pairs to apply.
        """
        for key, value in kargs.items():
            try:
                domain.set_config(option=option, param=key, value=value)
                # return Workspace.SUCCESS
            except:
                LOGD(f"domain.set_config raised for param \"{key}\" (known vitis-py "
                   f"UserConfig bug, value may still have been applied)")
                # Cannot set all params...
                # return Workspace.FAILURE
        # Even though set_config fails.
        return Workspace.SUCCESS

    def setConfigApp(self,
                     app,
                     **kargs
                     ) -> int:
        """
        @Description
        Apply application configuration keys.

        @Parameters
        app: current application object.
        kargs: param/value pairs to apply.
        """
        for key, value in kargs.items():
            try:
                app.set_app_config(key=key, value=value)
                # return Workspace.SUCCESS
            except:
                LOGD(f"app.set_app_config raised for param \"{key}\" (known vitis-py "
                   f"UserConfig bug, value may still have been applied)")
                # Cannot set all params...
                # return Workspace.FAILURE
        return Workspace.SUCCESS

    def decJSON_Ws(self,
                   app,
                   appname : str,
                   filepath=""
                   ) -> int:
        """
        @Description
        Apply saved USER_* settings from comp-settings.json.

        @Parameters
        app: application component object.
        appname: application name.
        filepath: comp-settings.json path.
        """
        dJsonStruct = JSONDecoder().decode(open(filepath).read())
        for key, value in dJsonStruct.items():
            if (key.startswith("USER_") and
                key != "USER_UNDEFINED_SYMBOLS" and
                len(value) != 0
                ):
                app.set_app_config(key, value)
        return Workspace.SUCCESS

    def getAppPlatformXsa(self,
                         appname : str,
                         filepath=""
                         ) -> str:
        """
        @Description
        Read the recorded XSA path for an application.

        @Parameters
        appname: app name, used for logging.
        filepath: comp-settings.json path.

        @Returns
        Recorded relative xsa path, or "".
        """
        dJsonStruct = JSONDecoder().decode(open(filepath).read())
        candidates = []
        for key, value in dJsonStruct.items():
            if key.startswith("USER_"):
                continue
            if isinstance(value, str) and value != "":
                candidates.append(value)
            elif isinstance(value, dict) and isinstance(value.get("xsa"), str) and value["xsa"] != "":
                candidates.append(value["xsa"])
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            LOG(f"Application \"{appname}\" has more than one platform/xsa correlation "
               f"entry in {filepath}, cannot determine which one to use: {candidates}")
        return ""

    def getAppTargetProc(self,
                        appname : str,
                        filepath=""
                        ) -> str:
        """
        @Description
        Read the recorded processor binding for an application.

        @Parameters
        appname: app name, used for logging.
        filepath: comp-settings.json path.

        @Returns
        Recorded cpu instance name, or "".
        """
        dJsonStruct = JSONDecoder().decode(open(filepath).read())
        for key, value in dJsonStruct.items():
            if key.startswith("USER_"):
                continue
            if isinstance(value, dict) and isinstance(value.get("cpu_instance"), str):
                return value["cpu_instance"]
        return ""

    def getAppOs(self,
               appname : str,
               filepath=""
               ) -> str:
        """
        @Description
        Read the recorded OS for an application.

        @Parameters
        appname: app name, used for logging.
        filepath: comp-settings.json path.

        @Returns
        Recorded OS name, defaulting to "standalone".
        """
        dJsonStruct = JSONDecoder().decode(open(filepath).read())
        for key, value in dJsonStruct.items():
            if key.startswith("USER_"):
                continue
            if isinstance(value, dict) and isinstance(value.get("os"), str) and value["os"] != "":
                return value["os"]
        return "standalone"

    @contextmanager
    def _quietOutput(self):
        """
        @Description
        Send general Vitis/HSI console output to checkout.log and keep only
        essential lines (errors, failures, build results) on the terminal.
        LOG() status messages are unaffected and also mirrored to the log.
        """
        if not self._runLogPath:
            yield
            return
        realStdout = sys.stdout
        with open(self._runLogPath, "a", encoding="utf-8") as logFile:
            sys.stdout = _BuildLogFilter(logFile, realStdout)
            try:
                yield
            finally:
                sys.stdout = realStdout

    def quietBuild(self, buildFn, desc="") -> None:
        """
        @Description
        Run a build and surface only essential console output.

        @Parameters
        buildFn: bound build method to call.
        desc: short log description.
        """
        with _BuildWatchdog(desc, self._wsPath):
            if not self._buildLogPath:
                status = buildFn()
            else:
                LOGD(f"Building {desc}, full Vitis build log kept at: {self._buildLogPath}")
                realStdout = sys.stdout
                warnings = 0
                with open(self._buildLogPath, "a", encoding="utf-8") as logFile:
                    buildFilter = _BuildLogFilter(logFile, realStdout)
                    sys.stdout = buildFilter
                    try:
                        status = buildFn()
                    finally:
                        sys.stdout = realStdout
                        warnings = buildFilter.warningCount
                if warnings:
                    LOG(f"Built {desc}: {warnings} warning line(s) not shown "
                        f"(see {self._buildLogPath}).")

        if status is not None and status is not True and status != 0:
            raise Exception(f"Build failed for {desc} (status: {status}), see {self._buildLogPath}")

    @staticmethod
    def _forceRemoveReadonly(func, path_, exc_info):
        """
        @Description
        Retry a failed delete after clearing read-only bits.

        @Parameters
        func: failing remove function.
        path_: failing path.
        exc_info: shutil callback metadata, unused.
        """
        chmod(path_, S_IWRITE)
        func(path_)

    def _clearWorkspaceContents(self, ws_path) -> None:
        """
        @Description
        Clear a workspace directory while preserving tracked entries.

        @Parameters
        ws_path: absolute workspace path to clear.
        """
        if not path.isdir(ws_path):
            makedirs(ws_path, exist_ok=True)
        else:
            for entry in listdir(ws_path):
                if entry in self.PRESERVED_WS_ENTRIES:
                    continue
                entry_path = path.join(ws_path, entry)
                if path.isdir(entry_path) and not path.islink(entry_path):
                    shutil.rmtree(entry_path, onerror=self._forceRemoveReadonly)
                else:
                    try:
                        remove(entry_path)
                    except PermissionError:
                        self._forceRemoveReadonly(remove, entry_path, None)
        keep_path = path.join(ws_path, ".keep")
        if not path.isfile(keep_path):
            open(keep_path, "a", encoding="utf-8").close()
        scripts_dir = path.dirname(path.abspath(__file__))
        for cleanupScript in ("cleanup.cmd", "cleanup.sh"):
            srcScript = path.join(scripts_dir, cleanupScript)
            dstScript = path.join(ws_path, cleanupScript)
            if path.isfile(srcScript) and not path.isfile(dstScript):
                shutil.copy2(srcScript, dstScript)

    def _ensureParentGitignore(self, ws_path) -> None:
        """
        @Description
        Ensure the parent repository ignores the generated workspace.

        @Parameters
        ws_path: absolute workspace path being prepared.
        """
        repo_root = path.dirname(ws_path)
        gitignore_path = path.join(repo_root, ".gitignore")
        ws_name = path.basename(ws_path)
        ignore_rule = f"/{ws_name}/*"
        required_lines = [ignore_rule, f"!/{ws_name}/.keep"]
        existing_lines = set()
        if path.isfile(gitignore_path):
            with open(gitignore_path, "r", encoding="utf-8") as f:
                existing_lines = {line.strip() for line in f
                                  if line.strip() and not line.strip().startswith("#")}
        missing_lines = [line for line in required_lines if line not in existing_lines]
        if not missing_lines:
            return
        lines = [f"# ignore everything in the generated {ws_name} workspace",
                f"# (added automatically by checkout.py, see README Note #2)"
                ] + missing_lines
        needs_leading_blank = path.isfile(gitignore_path) and path.getsize(gitignore_path) > 0
        with open(gitignore_path, "a", encoding="utf-8") as f:
            if needs_leading_blank:
                f.write("\n")
            f.write("\n".join(lines) + "\n")
        LOGD(f"Added workspace ignore rules to {gitignore_path}")

    def _confirmWorkspaceWipe(self, ws_path) -> None:
        """
        @Description
        Confirm before wiping a non-empty workspace.

        @Parameters
        ws_path: absolute workspace path about to be wiped.
        """
        if not path.isdir(ws_path):
            return
        existing_entries = [entry for entry in listdir(ws_path)
                            if entry not in self.PRESERVED_WS_ENTRIES]
        if not existing_entries:
            return
        if self._skipConfirmation:
            LOG(f"Workspace {ws_path} has existing content {existing_entries}; "
               "proceeding without confirmation (assume_yes/-y).")
            return
        answer = input(
            f"Workspace \"{ws_path}\" already has content ({existing_entries}) that "
            "will be PERMANENTLY DELETED by this checkout. If you meant to check "
            "in those changes first, answer \"n\" and run checkin.py instead. "
            "Continue and wipe the workspace? [y/N]: "
            )
        if answer.strip().lower() not in ("y", "yes"):
            raise Exception("Checkout aborted by user before wiping a non-empty workspace "
                            "(pass assume_yes/-y to skip this prompt for unattended/CI runs).")

    def _prepareWorkspace(self, client, ws_path) -> None:
        """
        @Description
        Recreate the workspace and point the client at it.

        @Parameters
        client: Vitis client object.
        ws_path: absolute workspace path to prepare.
        """
        self._confirmWorkspaceWipe(ws_path)
        makedirs(ws_path, exist_ok=True)
        self._setWorkspaceWithRetry(client, ws_path)

        # Switch away first so this run releases ws_path's lock.
        scratch_ws = mkdtemp(prefix="vitis_scratch_")
        try:
            self._setWorkspaceWithRetry(client, scratch_ws)

            # Give Vitis a moment to release its old workspace handles.
            time.sleep(1)

            max_try = 5
            for attempt in range(1, max_try + 1):
                try:
                    self._clearWorkspaceContents(ws_path)
                    LOGD(f"Cleared workspace {ws_path} on attempt {attempt}.")
                    break
                except Exception as e:
                    LOG(f"Attempt {attempt} to clear the old workspace failed: {e}")
                    if attempt < max_try:
                        if self._allowProcessCleanup:
                            stopped = stopDanglingVitisProcesses(environ.get("XILINX_VITIS", ""), _PROCESS_START_TIME)
                            if stopped:
                                LOG(f"Stopped dangling Vitis process(es) holding the workspace: {stopped}")
                        else:
                            LOG("Not stopping any Vitis process automatically (pass "
                                "allow_process_cleanup/--allow-process-cleanup to enable this "
                                "if you know no other Vitis session is active on this machine).")
                        time.sleep(1)
            else:
                raise Exception("Failed to delete old workspace even after stopping dangling Vitis "
                                "processes. Please delete it manually before running checkout again.")
        finally:
            shutil.rmtree(scratch_ws, ignore_errors=True)

        self._setWorkspaceWithRetry(client, ws_path)
        makedirs(ws_path, exist_ok=True)
        self._ensureParentGitignore(ws_path)
        self._wsPath = ws_path
        self._buildLogPath = path.join(ws_path, "checkout_build.log")
        self._runLogPath = path.join(ws_path, "checkout.log")
        setLogFile(self._runLogPath)
        LOG(f"Successfully created Vitis client on workspace {client.get_workspace()}")

    @staticmethod
    def _isWorkspaceVersionMismatch(errMsg) -> bool:
        """
        @Description
        Detect a workspace-version mismatch error message.

        @Parameters
        errMsg: str(exception) from client.set_workspace.

        @Returns
        True when the message looks like this mismatch.
        """
        msg = errMsg.lower()
        return "workspace" in msg and "version" in msg and "update" in msg

    def _setWorkspaceWithRetry(self, client, ws_path) -> None:
        """
        @Description
        Set a workspace with retry and lock cleanup logic.

        @Parameters
        client: Vitis client object.
        ws_path: absolute workspace path to select.
        """
        max_try = 5
        lock_path = path.join(ws_path, "_ide", ".wsdata", ".lock")
        for attempt in range(1, max_try + 1):
            try:
                client.set_workspace(ws_path)
                return
            except Exception as e:
                if self._isWorkspaceVersionMismatch(str(e)):
                    LOG(f"Workspace {ws_path} needs its metadata initialized/"
                       "migrated; retrying via update_workspace...")
                    try:
                        client.update_workspace(ws_path)
                        return
                    except Exception as update_err:
                        LOG(f"update_workspace also failed for {ws_path}: {update_err}")
                LOG(f"Attempt {attempt} to set workspace {ws_path} failed: {e}")
                if attempt < max_try:
                    if self._allowProcessCleanup:
                        stopped = stopDanglingVitisProcesses(environ.get("XILINX_VITIS", ""), _PROCESS_START_TIME)
                        if stopped:
                            LOG(f"Stopped dangling Vitis process(es) holding the workspace: {stopped}")
                        if path.isfile(lock_path):
                            try:
                                remove(lock_path)
                                LOGD(f"Removed stale workspace lock file: {lock_path}")
                            except OSError as lock_err:
                                LOG(f"Failed to remove stale lock file {lock_path}: {lock_err}")
                    else:
                        LOG("Not stopping any Vitis process nor removing the workspace lock "
                            "file automatically (pass allow_process_cleanup/"
                            "--allow-process-cleanup to enable this if you know no other "
                            "Vitis session is active on this machine) - a normal 'workspace "
                            "already in use' failure can mean another active IDE genuinely "
                            "owns this lock.")
                    time.sleep(1)
        raise Exception(f"Failed to set workspace {ws_path} even after stopping dangling Vitis "
                        "processes and removing any stale lock file. Please investigate manually.")

    def _openExistingWorkspace(self, client, ws_path) -> None:
        """
        @Description
        Open an existing workspace without clearing it.

        @Parameters
        client: Vitis client object.
        ws_path: absolute existing workspace path.
        """
        makedirs(ws_path, exist_ok=True)
        self._ensureParentGitignore(ws_path)
        self._setWorkspaceWithRetry(client, ws_path)
        self._wsPath = ws_path
        self._buildLogPath = path.join(ws_path, "checkout_build.log")
        self._runLogPath = path.join(ws_path, "checkout.log")
        setLogFile(self._runLogPath)
        LOG(f"Reusing existing Vitis workspace {client.get_workspace()} (selective rebuild).")

    def _hashFileContents(self, filepath) -> str:
        """
        @Description
        Hash a file's contents with sha256.

        @Parameters
        filepath: absolute file path.

        @Returns
        Hex digest string.
        """
        digest = sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _dedupeXsaFilesByContent(self, xsa_files) -> list:
        """
        @Description
        Deduplicate byte-identical XSA files.

        @Parameters
        xsa_files: absolute xsa paths.

        @Returns
        One canonical path per distinct content hash.
        """
        by_hash = {}
        for xsa_path in xsa_files:
            by_hash.setdefault(self._hashFileContents(xsa_path), []).append(xsa_path)

        deduped = []
        for content_hash, paths in by_hash.items():
            if len(paths) == 1:
                deduped.append(paths[0])
                continue
            canonical = None
            for candidate in sorted(paths):
                stem = path.splitext(path.basename(candidate))[0]
                if path.basename(path.dirname(candidate)) == stem:
                    canonical = candidate
                    break
            if canonical is None:
                canonical = sorted(paths)[0]
            dropped = [p for p in sorted(paths) if p != canonical]
            LOG(f"Ignoring {len(dropped)} duplicate xsa file(s) with content "
               f"identical to \"{canonical}\": {dropped}")
            deduped.append(canonical)

        return deduped

    def _getRunningVitisVersion(self) -> str:
        """
        @Description
        Read the active Vitis version from XILINX_VITIS.

        @Returns
        Version string, or "".
        """
        match = search(r"(\d{4}\.\d)", environ.get("XILINX_VITIS", ""))
        return match.group(1) if match else ""

    def _getXsaGeneratorVersion(self, xsa_path) -> str:
        """
        @Description
        Read the Vivado version recorded inside an XSA.

        @Parameters
        xsa_path: absolute xsa path.

        @Returns
        Version string, or "".
        """
        try:
            with zipfile.ZipFile(xsa_path) as zf:
                if "xsa.xml" in zf.namelist():
                    root = ElementTree.fromstring(zf.read("xsa.xml"))
                    genAppInfo = root.find(".//GenAppInfo")
                    if genAppInfo is not None and genAppInfo.get("Version"):
                        return genAppInfo.get("Version")
                if "sysdef.xml" in zf.namelist():
                    root = ElementTree.fromstring(zf.read("sysdef.xml"))
                    toolVersion = root.find(".//TOOL_VERSION")
                    if toolVersion is not None and toolVersion.get("Version"):
                        return toolVersion.get("Version")
        except (zipfile.BadZipFile, ElementTree.ParseError, OSError, KeyError):
            pass
        return ""

    def _filterXsaFilesByVitisVersion(self, xsa_files) -> list:
        """
        @Description
        Filter out XSAs known to mismatch the active Vitis version.

        @Parameters
        xsa_files: discovered xsa paths.

        @Returns
        Compatible xsa paths.
        """
        runningVersion = self._getRunningVitisVersion()
        if not runningVersion:
            LOG("Could not determine the running Vitis version from "
               "XILINX_VITIS; skipping xsa/Vitis version compatibility check.")
            return xsa_files

        compatible = []
        for xsa_path in xsa_files:
            xsaVersion = self._getXsaGeneratorVersion(xsa_path)
            if xsaVersion and xsaVersion != runningVersion:
                LOG(f"Skipping \"{xsa_path}\": generated by Vivado {xsaVersion}, "
                   f"incompatible with the running Vitis {runningVersion} (a "
                   "version mismatch here has been observed to hang, not "
                   "cleanly fail, during platform export/DTS generation).")
                self._versionSkippedXsaPaths.add(
                    self._normalizeXsaPathForCompare(xsa_path))
                continue
            compatible.append(xsa_path)
        return compatible

    def _discoverAppsAndPlatforms(self, src_root) -> tuple:
        """
        @Description
        Discover applications and XSA-backed platforms under src.

        @Parameters
        src_root: absolute path to the src directory.

        @Returns
        Tuple of app names and platform metadata.
        """
        app_names = []
        xsa_files = []

        for entry in listdir(src_root):
            entry_path = path.join(src_root, entry)
            if not path.isdir(entry_path):
                continue
            if path.isdir(path.join(entry_path, "src")):
                app_names.append(entry)
                LOGD(f"Detected application: {entry}")
            for filename in listdir(entry_path):
                filepath = path.join(entry_path, filename)
                if path.isfile(filepath) and filename.endswith(".xsa"):
                    xsa_files.append(filepath)

        LOG(f"Detected {len(app_names)} application(s): {app_names}")

        xsa_files = self._dedupeXsaFilesByContent(xsa_files)
        xsa_files = self._filterXsaFilesByVitisVersion(xsa_files)

        xsa_by_dir = {}
        for xsa_path in xsa_files:
            xsa_by_dir.setdefault(path.dirname(xsa_path), []).append(xsa_path)

        hw_platforms = {}
        used_names = set()
        for hw_pf_dir, xsas_in_dir in xsa_by_dir.items():
            for xsa_path in xsas_in_dir:
                platform_name = path.splitext(path.basename(xsa_path))[0]
                if platform_name in used_names:
                    hw_pf_name = path.basename(hw_pf_dir)
                    candidate_name = f"{hw_pf_name}_{platform_name}"
                    suffix = 2
                    while candidate_name in used_names:
                        candidate_name = f"{hw_pf_name}_{platform_name}_{suffix}"
                        suffix += 1
                    platform_name = candidate_name
                    LOG(f"Platform name collision on xsa stem for {xsa_path}, "
                       f"disambiguating as \"{platform_name}\"")
                used_names.add(platform_name)
                hw_platforms[xsa_path] = {
                    "name": platform_name,
                    "hw_pf_dir": hw_pf_dir,
                    "xsa_path": xsa_path
                }

        LOG(f"Detected {len(hw_platforms)} hardware platform(s): "
           f"{[plt['name'] for plt in hw_platforms.values()]}")
        return app_names, hw_platforms

    def _extractXsaMetadataScratch(self, xsa_path, plt_name):
        """
        @Description
        Run HSI metadata extraction on a scratch XSA copy.

        @Parameters
        xsa_path: absolute checked-in xsa path.
        plt_name: unique platform name for scratch namespacing.

        @Returns
        Metadata dict from GetMetadata.
        """
        scratch_dir = path.join(self._wsPath, "_hsi_scratch", plt_name)
        makedirs(scratch_dir, exist_ok=True)
        scratch_xsa = path.join(scratch_dir, path.basename(xsa_path))
        shutil.copy2(xsa_path, scratch_xsa)
        try:
            return GetMetadata(xsa=scratch_xsa, open_xsa="1")
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)

    def _extractPlatformMetadata(self, hw_platforms) -> None:
        """
        @Description
        Populate per-platform hardware metadata with HSI.

        @Parameters
        hw_platforms: platform dict updated in place.
        """
        start_time = time.time()

        for xsa_path, plt in hw_platforms.items():
            metadata = self._extractXsaMetadataScratch(xsa_path, plt["name"])
            plt["arch"] = metadata["arch"]
            plt["target_proc"] = metadata["target_proc"]
            plt["target_procs"] = metadata["target_procs"]
            LOGD(f"Platform \"{plt['name']}\": detected arch \"{plt['arch']}\", "
               f"available target processor(s): {plt['target_procs']}")

        execution_time = time.time() - start_time
        LOGD(f"Hardware metadata extraction took {execution_time:.4f} seconds")

    def _findDomainCmakeCache(self, platform_dir, domain_name):
        """
        @Description
        Find a domain BSP CMakeCache.txt under a platform directory.

        @Parameters
        platform_dir: absolute platform workspace directory.
        domain_name: domain name to search for.

        @Returns
        Absolute cache path, or None.
        """
        needle = sep + domain_name + sep + "bsp"
        for root, _, files in walk(platform_dir):
            if "CMakeCache.txt" in files and needle in (root + sep):
                return path.join(root, "CMakeCache.txt")
        return None

    def _fixDomainCmakeFlags(self, ws_path, platform_name, domain_name) -> None:
        """
        @Description
        Apply the CMake flags workaround to a domain BSP cache.

        @Parameters
        ws_path: absolute Vitis workspace path.
        platform_name: platform name.
        domain_name: target domain name.
        """
        platform_dir = path.join(ws_path, platform_name)
        cache_path = self._findDomainCmakeCache(platform_dir, domain_name)
        if not cache_path:
            LOG(f"Could not find a CMakeCache.txt for domain \"{domain_name}\"; "
                "skipping the CMAKE_*_FLAGS fix-up.")
            return
        self._fixCmakeFlags(cache_path, f"domain \"{domain_name}\"")

    def _fixCmakeFlags(self, cache_path, label) -> bool:
        """
        @Description
        Rebuild CMAKE_*_FLAGS values from toolchain cache entries.

        @Parameters
        cache_path: absolute CMakeCache.txt path.
        label: short logging label.

        @Returns
        True if any cached flag changed.
        """
        with open(cache_path, "r", encoding="utf-8") as f:
            cache_text = f.read()

        def cacheVar(var_name):
            """Return one cached CMake variable value."""
            m = search(rf"^{var_name}:\w+=(.*)$", cache_text, RegexFlag.MULTILINE)
            return m.group(1) if m else None

        dep_flags = cacheVar("TOOLCHAIN_DEP_FLAGS") or ""
        specs_file = cacheVar("CMAKE_SPECS_FILE")
        include_path = cacheVar("CMAKE_INCLUDE_PATH") or ""
        if specs_file is None:
            LOG(f"CMakeCache.txt for {label} is missing an expected "
                "TOOLCHAIN_*/CMAKE_SPECS_FILE entry; skipping the CMAKE_*_FLAGS fix-up.")
            return False

        changed = False
        for lang, toolchain_var in (("C", "TOOLCHAIN_C_FLAGS"),
                                     ("CXX", "TOOLCHAIN_CXX_FLAGS"),
                                     ("ASM", "TOOLCHAIN_ASM_FLAGS")):
            lang_flags = cacheVar(toolchain_var)
            if lang_flags is None:
                continue
            fixed_value = f"{lang_flags} {dep_flags} -specs={specs_file}"
            if include_path:
                fixed_value += f" -I{include_path}"
            if cacheVar(f"CMAKE_{lang}_FLAGS") == fixed_value:
                continue
            cache_text, n = compile(rf"^(CMAKE_{lang}_FLAGS:\w+)=.*$", RegexFlag.MULTILINE).subn(
                lambda m: f"{m.group(1)}={fixed_value}", cache_text, count=1
                )
            if n:
                changed = True
                LOGD(f"Fixed CMAKE_{lang}_FLAGS for {label}: {fixed_value}")

        if changed:
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(cache_text)
        return changed

    def _configureMicroblazeDomain(self, domain, domain_name) -> None:
        """
        @Description
        Apply standard MicroBlaze BSP settings.

        @Parameters
        domain: domain object.
        domain_name: domain name for logging.
        """
        LOGD(f"Configuring BSP settings for the domain \"{domain_name}\"...")
        self.setConfigDomain(
            domain,
            "proc",
            proc_extra_compiler_flags="-g -ffunction-sections -fdata-sections -Wall -Wextra",
            proc_xmdstub_peripheral="none",
            proc_archiver="mb-ar",
            proc_dependency_flags="-MMD -MP",
            proc_assembler="mb-as",
            proc_compiler_flags="-O2 -c",
            proc_compiler="mb-gcc"
            )

        self.setConfigDomain(
            domain,
            "os",
            standalone_microblaze_exceptions="false",
            standalone_stdin="axi_uartlite_0",
            standalone_ttc_select_cntr="2",
            standalone_stdout="axi_uartlite_0",
            standalone_predecode_fpu_exceptions="false",
            standalone_sleep_timer="none",
            standalone_enable_sw_intrusive_profiling="false",
            standalone_hypervisor_guest="false",
            standalone_clocking="false",
            standalone_zynqmp_fsbl_bsp="false",
            standalone_lockstep_mode_debug="false",
            standalone_profile_timer="none"
            )

    def _buildZynqMPFsbl(self, client, platform, plt, repo_path) -> None:
        """
        @Description
        Build and attach the ZynqMP FSBL application.

        @Parameters
        client: Vitis client object.
        platform: built platform component.
        plt: platform metadata entry.
        repo_path: optional embeddedsw override path.
        """
        name = plt["name"]
        target_proc = plt["target_proc"]

        if repo_path:
            client.set_embedded_sw_repo(level="LOCAL", path=[repo_path])
        else:
            LOG(f"No --esw-repo provided: FSBL build for platform \"{name}\" will "
               "use Vitis's own bundled embeddedsw repo.")
        fsbl_domain_name = f"{target_proc}_domain_fsbl"
        LOGD(f"Adding domain \"{fsbl_domain_name}\" for cpu \"{target_proc}\" and OS \"standalone\"...")
        zynqmp_fsbl_domain = platform.add_domain(
            cpu=target_proc,
            os="standalone",
            name=fsbl_domain_name,
            display_name=fsbl_domain_name,
            support_app="zynqmp_fsbl"
            )

        self.quietBuild(platform.build, f"platform \"{name}\" (fsbl domain)")
        fsbl_app_name = f"{name}_FSBL"
        LOG(f"Creating FSBL application \"{fsbl_app_name}\" for platform \"{name}\"...")
        fsbl_app = client.create_app_component(
                name=fsbl_app_name,
                platform=client.get_workspace() + sep +
                         name + sep +
                         "export" + sep +
                         name + sep +
                         name + ".xpfm",
                domain=fsbl_domain_name,
                template="zynqmp_fsbl"
                )

        fsbl_app.import_files(
                from_loc=client.get_workspace() + sep + name + sep + "hw" + sep + "sdt",
                files= ["psu_init.c", "psu_init.h"],
                dest_dir_in_cmp="src"
            )

        self.quietBuild(fsbl_app.build, f"FSBL application \"{fsbl_app_name}\"")
        platform.remove_boot_bsp()
        # Vitis launch configurations expect the platform boot elf to be named fsbl.elf
        fsbl_build_dir = fsbl_app.component_location + sep + "build"
        fsbl_default_elf = fsbl_build_dir + sep + "fsbl.elf"
        shutil.copy2(fsbl_build_dir + sep + f"{fsbl_app_name}.elf", fsbl_default_elf)
        platform.set_fsbl_elf(path=fsbl_default_elf)
        self.quietBuild(platform.build, f"platform \"{name}\" (with fsbl elf)")

    def _buildPlatform(self, client, xsa_path, plt, repo_path):
        """Build one platform and return the client to keep using."""
        watchdog = _BuildWatchdog(f"platform \"{plt['name']}\"", self._wsPath, auto_recover=True)
        LOG(f"Building platform \"{plt['name']}\": the Vitis gRPC server may block here; if there "
           f"is no output and no workspace activity for ~{_BuildWatchdog.WARN_INTERVAL_SEC // 60} "
           "minute(s), Vitis will be reset automatically and the platform retried once.")
        try:
            with watchdog:
                self._buildPlatformImpl(client, xsa_path, plt, repo_path)
            return client
        except Exception as e:
            if not watchdog.killedForRecovery:
                raise
            LOG(f"Platform \"{plt['name']}\" build failed ({e}) after this run's "
               "own Vitis backend was force-stopped for a detected genuine "
               "hang; starting a fresh Vitis session and retrying this "
               "platform once (AUTOMATIC RESET, no action needed)...")

        # Recovery left a stale lock behind; clear it before retrying.
        lock_path = path.join(self._wsPath, "_ide", ".wsdata", ".lock")
        if path.isfile(lock_path):
            try:
                remove(lock_path)
            except OSError as lock_err:
                LOG(f"Failed to remove stale workspace lock file {lock_path} "
                   f"after recovery: {lock_err}")

        dispose()
        client = create_client()
        self._setWorkspaceWithRetry(client, self._wsPath)
        # Remove partial components from the failed attempt.
        existing = {c["name"] for c in client.list_components()}
        for stale_name in (plt["name"], f"{plt['name']}_FSBL"):
            if stale_name in existing:
                LOG(f"Deleting partially-built component \"{stale_name}\" before retry...")
                client.delete_component(name=stale_name)

        # Do not auto-recover again on the retry.
        with _BuildWatchdog(f"platform \"{plt['name']}\" (retry)", self._wsPath):
            self._buildPlatformImpl(client, xsa_path, plt, repo_path)
        return client

    def _buildPlatformImpl(self, client, xsa_path, plt, repo_path) -> None:
        """
        @Description
        Create, configure, and build one platform component.

        @Parameters
        client: Vitis client object.
        xsa_path: absolute xsa path.
        plt: platform metadata entry.
        repo_path: optional embeddedsw override path.
        """
        name = plt["name"]
        arch = plt["arch"]
        target_procs = plt["target_procs"]
        is_microblaze = arch in ("spartan7", "artix7", "kintex7")

        LOG(f"Creating platform component \"{name}\" from {xsa_path}...")
        platform_kwargs = {"name": name, "hw_design": xsa_path}
        if is_microblaze:
            platform_kwargs["no_boot_bsp"] = True
        platform = client.create_platform_component(**platform_kwargs)
        if platform is None:
            raise Exception(f"Platform \"{name}\" creation returned no result - "
                             "likely interrupted (e.g. Ctrl+C) or failed silently "
                             f"during the build; see {self._buildLogPath or 'the console output above'}.")
        platform.update_desc(desc=name)
        self._writePlatformSourceDirManifest(platform, plt["hw_pf_dir"])

        for target_proc in target_procs:
            proc_is_microblaze = target_proc.startswith("microblaze")
            domain_name = f"domain_{target_proc}"

            LOGD(f"Adding domain \"{domain_name}\" for cpu \"{target_proc}\" and OS \"standalone\"...")
            domain = platform.add_domain(
                cpu=target_proc,
                os="standalone",
                name=domain_name,
                display_name=domain_name,
                support_app="empty_application"
                )

            self._fixDomainCmakeFlags(client.get_workspace(), name, domain_name)

            if proc_is_microblaze:
                self._configureMicroblazeDomain(domain, domain_name)

        self.quietBuild(platform.build, f"platform \"{name}\"")

        if arch == "zynquplus":
            self._buildZynqMPFsbl(client, platform, plt, repo_path)

        self._setPlatformDomainInfo(client, plt)

    def _setPlatformDomainInfo(self, client, plt) -> None:
        """
        @Description
        Fill in derived domain and xpfm metadata on a platform entry.

        @Parameters
        client: Vitis client object.
        plt: platform metadata entry.
        """
        name = plt["name"]
        plt["domains"] = {
            target_proc: f"domain_{target_proc}"
            for target_proc in plt["target_procs"]
            }
        plt["domain_name"] = plt["domains"].get(
            plt["target_proc"],
            next(iter(plt["domains"].values()), f"domain_{plt['target_proc']}")
            )
        plt["xpfm"] = (client.get_workspace() + sep + name + sep +
                       "export" + sep + name + sep + name + ".xpfm")

    def _rebuildPlatformInPlace(self, client, plt) -> None:
        """
        @Description
        Rebuild an existing platform component in place.

        @Parameters
        client: Vitis client object.
        plt: platform metadata entry.
        """
        name = plt["name"]
        LOG(f"Reusing existing platform component \"{name}\" in place (--incremental)...")
        platform = client.get_component(name=name)
        self.quietBuild(platform.build, f"platform \"{name}\" (incremental)")
        self._setPlatformDomainInfo(client, plt)

    def _pruneStaleFiles(self, dest_dir, src_dir, top_level_skip=frozenset(),
                        strict_top_level=False) -> None:
        """
        @Description
        Remove files missing from the checked-in source tree.

        @Parameters
        dest_dir: mirrored component directory.
        src_dir: source directory to mirror.
        top_level_skip: direct children to preserve.
        strict_top_level: prune only known source files at dest_dir root.
        """
        if not path.isdir(dest_dir):
            return
        for entry in listdir(dest_dir):
            if entry in top_level_skip:
                continue
            dest_entry = path.join(dest_dir, entry)
            src_entry = path.join(src_dir, entry)
            is_dir = path.isdir(dest_entry)
            if not path.exists(src_entry):
                if strict_top_level:
                    if is_dir or path.splitext(entry)[1] not in PRUNABLE_SRC_FILE_EXTENSIONS:
                        continue
                    remove(dest_entry)
                elif is_dir:
                    shutil.rmtree(dest_entry, onerror=self._forceRemoveReadonly)
                else:
                    remove(dest_entry)
                LOGD(f"Removed stale file no longer present in the checked-in source: {dest_entry}")
            elif is_dir:
                self._pruneStaleFiles(dest_entry, src_entry)

    def _extraModulesManifestPath(self, app) -> str:
        """
        @Description
        Return the extra-module manifest path for an app.

        @Parameters
        app: application component object.
        """
        return path.join(app.component_location, ".digilent_extra_modules")

    def _readExtraModulesManifest(self, app) -> set:
        """Read the recorded extra-module names for an app."""
        manifest_path = self._extraModulesManifestPath(app)
        if not path.isfile(manifest_path):
            return set()
        with open(manifest_path, "r") as f:
            return {line.strip() for line in f if line.strip()}

    def _writeExtraModulesManifest(self, app, module_names) -> None:
        """Write the recorded extra-module names for an app."""
        with open(self._extraModulesManifestPath(app), "w") as f:
            f.writelines(f"{name}\n" for name in sorted(module_names))

    def _writePlatformSourceDirManifest(self, platform, hw_pf_dir) -> None:
        """
        @Description
        Record the original source directory name for a platform.

        @Parameters
        platform: newly created platform component.
        hw_pf_dir: original containing directory under src.
        """
        manifest_path = path.join(platform.project_location, ".digilent_source_dir")
        with open(manifest_path, "w") as f:
            f.write(path.basename(hw_pf_dir) + "\n")

    def _rebuildApplicationInPlace(self, client, app_name, repo_root) -> None:
        """
        @Description
        Re-sync and rebuild an existing application in place.

        @Parameters
        client: Vitis client object.
        app_name: application folder name under src.
        repo_root: absolute repository root.
        """
        LOG(f"Reusing existing application component \"{app_name}\" in place (--incremental)...")
        app = client.get_component(name=app_name)
        app_root = repo_root + sep + "src" + sep + app_name
        valid_extra_modules = {entry for entry in listdir(app_root)
                               if entry != "src" and path.isdir(path.join(app_root, entry))}
        previous_extra_modules = self._readExtraModulesManifest(app)
        for stale_module in previous_extra_modules - valid_extra_modules:
            stale_dir = path.join(app.component_location, "src", stale_module)
            if path.isdir(stale_dir):
                shutil.rmtree(stale_dir, onerror=self._forceRemoveReadonly)
                LOG(f"Removed stale extra module directory (renamed/removed at "
                   f"the source since the last run): {stale_dir}")
        self._pruneStaleFiles(
            path.join(app.component_location, "src"),
            path.join(app_root, "src"),
            top_level_skip=valid_extra_modules,
            strict_top_level=True
            )
        for entry in valid_extra_modules:
            self._pruneStaleFiles(
                path.join(app.component_location, "src", entry),
                path.join(app_root, entry)
                )
        app.import_files(
            from_loc=repo_root + sep + "src" + sep + app_name + sep + "src",
            dest_dir_in_cmp="src"
            )
        self._importAppExtraModules(app, app_name, repo_root)
        self._buildAppWithFlagsRetry(app, app_name, " (incremental)")

    @staticmethod
    def _normalizeXsaPathForCompare(xsa_path) -> str:
        """
        @Description
        Normalize an XSA path for direct comparison.

        @Parameters
        xsa_path: absolute or relative xsa path.

        @Returns
        Normalized path string.
        """
        normalized = path.normpath(xsa_path.replace("\\", "/").replace("/", sep))
        if platform.system() == "Windows":
            normalized = normalized.lower()
        return normalized

    def _resolveAppPlatform(self, app_name, comp_settings_path, hw_platforms, repo_root):
        """Resolve an application to its platform metadata entry."""
        requested_xsa = self.getAppPlatformXsa(app_name, filepath=comp_settings_path)
        if requested_xsa != "":
            requested_xsa_abs = self._normalizeXsaPathForCompare(
                path.join(repo_root, requested_xsa))
            for xsa_path, candidate in hw_platforms.items():
                if self._normalizeXsaPathForCompare(xsa_path) == requested_xsa_abs:
                    return candidate
            if requested_xsa_abs in self._versionSkippedXsaPaths:
                LOG(f"Application \"{app_name}\" references XSA \"{requested_xsa}\" "
                   "which was skipped for a Vitis version mismatch (see "
                   "the earlier \"Skipping\" message): resolve that "
                   "mismatch (regenerate/replace the xsa, or run with a "
                   "matching Vitis version) before this application can "
                   "be built.")
            else:
                LOG(f"Application \"{app_name}\" references XSA \"{requested_xsa}\" "
                   f"which was not found among the detected platforms!")
            return None

        if len(hw_platforms) == 1:
            plt = next(iter(hw_platforms.values()))
            LOG(f"Application \"{app_name}\" has no platform/xsa correlation entry, "
               f"defaulting to the only detected platform \"{plt['name']}\"")
            return plt

        LOG(f"Skipping application \"{app_name}\": could not determine which "
           f"of the {len(hw_platforms)} detected platforms to use!")
        return None

    def _resolveAppDomain(self, app_name, comp_settings_path, plt):
        """
        @Description
        Resolve the domain name an application should use.

        @Parameters
        app_name: application folder name under src.
        comp_settings_path: absolute comp-settings.json path.
        plt: platform metadata entry with domain info.

        @Returns
        Domain name, or None.
        """
        app_os = self.getAppOs(app_name, filepath=comp_settings_path)
        if app_os != "standalone":
            LOG(f"Application \"{app_name}\" was checked in with OS "
               f"\"{app_os}\", which checkout.py cannot rebuild (only "
               f"\"standalone\" bare-metal domains are reconstructed); "
               f"refusing to silently rebuild it against a \"standalone\" "
               f"domain instead.")
            return None
        target_proc = self.getAppTargetProc(app_name, filepath=comp_settings_path)
        if target_proc == "":
            return plt["domain_name"]
        if target_proc in plt["domains"]:
            return plt["domains"][target_proc]
        LOG(f"Application \"{app_name}\" was checked in bound to processor "
           f"\"{target_proc}\", which is not available on platform "
           f"\"{plt['name']}\"; refusing to rebuild it against a different CPU.")
        return None

    def _isAppBoundToVersionSkippedXsa(self, app_name, comp_settings_path, repo_root) -> bool:
        """
        @Description
        Check whether an app references a version-skipped XSA.

        @Parameters
        app_name: application folder name under src.
        comp_settings_path: absolute comp-settings.json path.
        repo_root: absolute repository root.

        @Returns
        True if the exact recorded xsa was skipped this run.
        """
        requested_xsa = self.getAppPlatformXsa(app_name, filepath=comp_settings_path)
        if requested_xsa == "":
            return False
        requested_xsa_abs = self._normalizeXsaPathForCompare(
            path.join(repo_root, requested_xsa))
        return requested_xsa_abs in self._versionSkippedXsaPaths

    def _appHasValidMapping(self, app_name, hw_platforms, repo_root) -> bool:
        """
        @Description
        Check whether an app resolves to a valid platform and domain.

        @Parameters
        app_name: application folder name under src.
        hw_platforms: platform metadata with domain info.
        repo_root: absolute repository root.

        @Returns
        True if both mappings resolve.
        """
        comp_settings_path = (repo_root + sep + "src" + sep + app_name +
                              sep + Workspace.COMP_SETTINGS)
        plt = self._resolveAppPlatform(app_name, comp_settings_path, hw_platforms, repo_root)
        if plt is None:
            return False
        return self._resolveAppDomain(app_name, comp_settings_path, plt) is not None

    def _buildApplication(self, client, app_name, hw_platforms, repo_root) -> bool:
        """Resolve, create, configure, and build one application."""
        comp_settings_path = (repo_root + sep + "src" + sep + app_name +
                              sep + Workspace.COMP_SETTINGS)

        plt = self._resolveAppPlatform(app_name, comp_settings_path, hw_platforms, repo_root)
        if plt is None:
            return False

        domain_name = self._resolveAppDomain(app_name, comp_settings_path, plt)
        if domain_name is None:
            return False
        LOG(f"Creating application component \"{app_name}\" for platform \"{plt['name']}\" "
           f"(domain \"{domain_name}\")...")
        app = client.create_app_component(
            name=app_name,
            platform=plt["xpfm"],
            domain=domain_name,
            template="empty_application"
        )
        # Extract saved settings.
        iRet = self.decJSON_Ws(
            app,
            appname=app_name,
            filepath=comp_settings_path
        )
        """
        iRet = self.setConfigApp(
            app,
            USER_COMPILE_DEFINITIONS=["DEBUG"],
            USER_COMPILE_DEBUG_LEVEL=["-g3"],
            USER_LINK_LIBRARIES=["m"]
        )
        if(mcu.deviceFamily == "zynq"):
            iRet = self.setConfigApp(
                app,
                USER_COMPILE_OTHER_FLAGS="-fmessage-length=0 -MT\"$$@\" -mcpu=cortex-a9 -mfpu=vfpv3 -mfloat-abi=hard",
                USER_LINK_OTHER_FLAGS="-mcpu=cortex-a9 -mfpu=vfpv3 -mfloat-abi=hard -Wl,-build-id=none"
            )
        """
        app.import_files(
            from_loc=repo_root + sep + "src" + sep + app.component_name + sep + "src",
            dest_dir_in_cmp="src"
        )
        LOGD(f"Application component \"{app_name}\" created at: {app.component_location}")
        self._importAppExtraModules(app, app_name, repo_root)
        self._removeTemplateCruft(app)
        self._buildAppWithFlagsRetry(app, app_name)
        self._applyLaunchInit(app, app_name, comp_settings_path)
        return True

    def _applyLaunchInit(self, app, app_name, comp_settings_path) -> None:
        """
        @Description
        Switch the app launch config from FSBL to psu_init.tcl when requested
        by --psu-init or by "psu_init": true in the app's comp-settings.json.

        @Parameters
        app: built application component.
        app_name: application folder name, used for logging.
        comp_settings_path: absolute comp-settings.json path.
        """
        enabled = self._psuInitLaunch
        if not enabled and path.isfile(comp_settings_path):
            with open(comp_settings_path) as f:
                for key, value in JSONDecoder().decode(f.read()).items():
                    if (not key.startswith("USER_") and isinstance(value, dict)
                            and value.get("psu_init") is True):
                        enabled = True
        launch_path = path.join(app.component_location, "_ide", "launch.json")
        if not enabled or not path.isfile(launch_path):
            return
        with open(launch_path, "r") as f:
            content = f.read()
        patched = (content.replace('"isFsbl": true', '"isFsbl": false')
                          .replace('"initWithFSBL": true', '"initWithFSBL": false'))
        with open(launch_path, "w") as f:
            f.write(patched)
        LOG(f"Launch config of \"{app_name}\" initializes the PS with psu_init.tcl.")

    def _importAppExtraModules(self, app, app_name, repo_root) -> None:
        """
        @Description
        Import sibling source modules for an application.

        @Parameters
        app: created application component.
        app_name: application folder name under src.
        repo_root: absolute repository root.
        """
        app_root = repo_root + sep + "src" + sep + app_name
        imported_modules = set()
        for entry in listdir(app_root):
            entry_path = app_root + sep + entry
            if entry == "src" or not path.isdir(entry_path):
                continue
            LOGD(f"Importing extra module \"{entry}\" for application \"{app_name}\"...")
            app.import_files(from_loc=entry_path, dest_dir_in_cmp=path.join("src", entry))
            imported_modules.add(entry)
        self._writeExtraModulesManifest(app, imported_modules)
        if imported_modules:
            self._wireExtraSourcesIntoCMakeLists(app, app_name)

    def _wireExtraSourcesIntoCMakeLists(self, app, app_name) -> None:
        """
        @Description
        Patch CMakeLists.txt to include USER_COMPILE_SOURCES.

        @Parameters
        app: created application component.
        app_name: application folder name for logging.
        """
        cmakelists_path = path.join(app.component_location, "src", "CMakeLists.txt")
        if not path.isfile(cmakelists_path):
            return
        with open(cmakelists_path, "r") as f:
            content = f.read()
        marker = "list(APPEND _sources ${USER_COMPILE_SOURCES})"
        if marker in content:
            return
        anchor = "aux_source_directory(${CMAKE_SOURCE_DIR} _sources)"
        if anchor not in content:
            return
        content = content.replace(
            anchor,
            anchor + "\n" + marker + "\nlist(REMOVE_DUPLICATES _sources)"
            )
        with open(cmakelists_path, "w") as f:
            f.write(content)
        LOGD(f"Wired USER_COMPILE_SOURCES into the build for application "
           f"\"{app_name}\" (Vitis 2025.2 CMakeLists.txt does not consume "
           f"it on its own)...")

    def _buildAppWithFlagsRetry(self, app, app_name, desc_suffix="") -> None:
        """
        @Description
        Build an app and retry once after a CMake flags fix-up.

        @Parameters
        app: configured application component.
        app_name: application folder name for logging.
        desc_suffix: suffix appended to the build description.
        """
        desc = f"application \"{app_name}\"{desc_suffix}"
        build_error = None
        try:
            self.quietBuild(app.build, desc)
        except Exception as e:
            build_error = e

        cache_path = app.component_location + sep + "build" + sep + "CMakeCache.txt"
        if path.isfile(cache_path) and self._fixCmakeFlags(cache_path, desc):
            LOG(f"Retrying {desc} build after the CMAKE_*_FLAGS fix-up...")
            self.quietBuild(app.build, f"{desc} (retry)")
        elif build_error is not None:
            raise build_error

    def _removeTemplateCruft(self, app) -> None:
        """
        @Description
        Remove unwanted files left by generated templates.

        @Parameters
        app: populated application component.
        """
        for dirpath, dirnames, filenames in walk(app.component_location + sep + "src"):
            for filename in filenames:
                if (filename in ("Xilinx.spec", "README.txt")):
                    LOGD(f"Removing template file \"{filename}\" from {path.join(dirpath, filename)}")
                    app.remove_files(files=[path.join(dirpath, filename)])

        for dirpath, dirnames, filenames in walk(app.component_location + sep + "_ide"+ sep + "psinit"):
            for filename in filenames:
                if (filename not in ("psu_init.tcl", "ps7_init.tcl")):
                    LOGD(f"Removing template file \"{filename}\" from {path.join(dirpath, filename)}")
                    app.remove_files(files=[path.join(dirpath, filename)])

    def checkOutSF(self, platforms=None, apps=None, skip_unbound_platforms=False,
                   incremental=False, allow_process_cleanup=False, assume_yes=False,
                   esw_repo=None, psu_init=False) -> int:
        """Recreate or selectively rebuild the Vitis workspace."""
        platforms = set(platforms or [])
        apps = set(apps or [])
        selective = bool(platforms or apps)
        self._psuInitLaunch = psu_init
        self._allowProcessCleanup = allow_process_cleanup
        self._skipConfirmation = assume_yes

        script_path = path.dirname(path.abspath(__file__))
        repo_root = script_path[:script_path.rfind(sep)]
        if Workspace.DEBUG:
            date = datetime.now().strftime("%Y%m%d%I%M%S")
            ws_path = repo_root + f"{sep}ws" + f"_{date}"
        else:
            ws_path = repo_root + f"{sep}ws"
        repo_path = esw_repo

        if repo_path:
            environ["ESW_REPO"] = repo_path

        # Seed compiler-test flags for the Vitis 2025.2 toolchain issue.
        environ["CFLAGS"] = "-specs=nosys.specs"
        environ["CXXFLAGS"] = "-specs=nosys.specs"

        dispose()

        LOG("Checking out Vitis project into the workspace...")
        LOG("Console shows essential messages only. Full details: "
            f"{path.join(ws_path, 'checkout.log')} (all steps) and "
            f"{path.join(ws_path, 'checkout_build.log')} (Vitis builds, incl. warnings).")
        outputStack = ExitStack()
        try:
            client = create_client()
            if selective:
                self._openExistingWorkspace(client, ws_path)
            else:
                self._prepareWorkspace(client, ws_path)
            outputStack.enter_context(self._quietOutput())
            app_names, hw_platforms = self._discoverAppsAndPlatforms(repo_root + f"{sep}src")
            self._extractPlatformMetadata(hw_platforms)

            if selective:
                known_platform_names = {plt["name"] for plt in hw_platforms.values()}
                unknown_platforms = platforms - known_platform_names
                if unknown_platforms:
                    LOG(f"Unknown --platform value(s) {sorted(unknown_platforms)}: "
                       f"no such platform among {sorted(known_platform_names)}.")
                    return Workspace.FAILURE
                unknown_apps = apps - set(app_names)
                if unknown_apps:
                    LOG(f"Unknown --app value(s) {sorted(unknown_apps)}: "
                       f"no such application among {sorted(app_names)}.")
                    return Workspace.FAILURE
                resolved = self._resolveSelectiveTargets(
                    platforms, apps, app_names, hw_platforms, repo_root
                    )
                if resolved is None:
                    return Workspace.FAILURE
                platforms, apps = resolved
                existing = {c["name"] for c in client.list_components()}
            else:
                existing = set()

            bound_platforms = None
            if skip_unbound_platforms:
                bound_platforms = self._getBoundPlatformNames(app_names, hw_platforms, repo_root)

            for plt in hw_platforms.values():
                self._setPlatformDomainInfo(client, plt)

            rebuild_apps = [
                app_name for app_name in app_names
                if not (selective and app_name not in apps)
                and not (incremental and app_name in existing)
                ]
            unresolved_apps = [
                app_name for app_name in rebuild_apps
                if not self._appHasValidMapping(app_name, hw_platforms, repo_root)
                ]
            version_skipped_app_names = set()
            if unresolved_apps:
                comp_settings_paths = {
                    app_name: (repo_root + sep + "src" + sep + app_name +
                              sep + Workspace.COMP_SETTINGS)
                    for app_name in unresolved_apps
                    }
                non_version_skip_apps = [
                    app_name for app_name in unresolved_apps
                    if not self._isAppBoundToVersionSkippedXsa(
                        app_name, comp_settings_paths[app_name], repo_root)
                    ]
                if non_version_skip_apps:
                    LOG(f"Aborting before deleting any component: application(s) "
                       f"{non_version_skip_apps} could not be resolved to a platform/domain "
                       f"(see prior log messages).")
                    return Workspace.FAILURE
                version_skipped_app_names = set(unresolved_apps)
                LOG(f"Continuing checkout without application(s) {unresolved_apps}: "
                   f"each is bound to an xsa skipped for a Vitis version mismatch.")

            for xsa_path, plt in hw_platforms.items():
                if selective and plt["name"] not in platforms:
                    self._setPlatformDomainInfo(client, plt)
                    continue
                explicitly_requested = selective and plt["name"] in platforms
                if (bound_platforms is not None and not explicitly_requested
                        and plt["name"] not in bound_platforms):
                    LOG(f"Skipping platform \"{plt['name']}\": not referenced by any "
                       f"application's comp-settings.json (--skip-unbound-platforms)")
                    continue
                if incremental and plt["name"] in existing:
                    self._rebuildPlatformInPlace(client, plt)
                else:
                    for stale_name in (plt["name"], f"{plt['name']}_FSBL"):
                        if stale_name in existing:
                            LOGD(f"Deleting existing component \"{stale_name}\" for rebuild...")
                            client.delete_component(name=stale_name)
                    client = self._buildPlatform(client, xsa_path, plt, repo_path)

            all_apps_resolved = True
            for app_name in app_names:
                if selective and app_name not in apps:
                    continue
                if app_name in version_skipped_app_names:
                    LOG(f"Skipping application \"{app_name}\": bound to an "
                       "xsa skipped for a Vitis version mismatch (see "
                       "prior log messages); leaving any existing "
                       "component untouched.")
                    all_apps_resolved = False
                    continue
                if incremental and app_name in existing:
                    self._rebuildApplicationInPlace(client, app_name, repo_root)
                else:
                    if app_name in existing:
                        LOGD(f"Deleting existing application component \"{app_name}\" for rebuild...")
                        client.delete_component(name=app_name)
                    if not self._buildApplication(client, app_name, hw_platforms, repo_root):
                        all_apps_resolved = False

            if not all_apps_resolved:
                LOG("Checkout finished with at least one application skipped "
                   "(see prior log messages); workspace is incomplete.")
                return Workspace.FAILURE

            return Workspace.SUCCESS
        finally:
            outputStack.close()
            dispose()

    def _getBoundPlatformNames(self, app_names, hw_platforms, repo_root) -> set:
        """
        @Description
        Return platform names referenced by at least one app.

        @Parameters
        app_names: detected application names.
        hw_platforms: discovered platform metadata.
        repo_root: absolute repository root.

        @Returns
        Set of referenced platform names.
        """
        bound = set()
        for app_name in app_names:
            comp_settings_path = (repo_root + sep + "src" + sep + app_name +
                                  sep + Workspace.COMP_SETTINGS)
            plt = self._resolveAppPlatform(app_name, comp_settings_path, hw_platforms, repo_root)
            if plt:
                bound.add(plt["name"])
        return bound

    def _resolveSelectiveTargets(self, platforms, apps, app_names, hw_platforms, repo_root):
        """Expand selective rebuild requests into consistent targets."""
        platforms = set(platforms)
        apps = set(apps)
        apps_explicitly_requested = bool(apps)

        for app_name in apps:
            comp_settings_path = (repo_root + sep + "src" + sep + app_name +
                                  sep + Workspace.COMP_SETTINGS)
            plt = self._resolveAppPlatform(app_name, comp_settings_path, hw_platforms, repo_root)
            if plt is None:
                LOG(f"Cannot selectively rebuild application \"{app_name}\": its "
                    f"platform/xsa correlation could not be resolved (see above).")
                return None
            platforms.add(plt["name"])

        if not apps_explicitly_requested:
            for app_name in app_names:
                comp_settings_path = (repo_root + sep + "src" + sep + app_name +
                                      sep + Workspace.COMP_SETTINGS)
                plt = self._resolveAppPlatform(app_name, comp_settings_path, hw_platforms, repo_root)
                if plt and plt["name"] in platforms:
                    apps.add(app_name)

        return platforms, apps

if __name__ == "__main__":
    """
    @Description
    Run checkout.py as a standalone workspace rebuild script.
    Use: `vitis -s [<relative-or-absolute-path>]checkout.py [options]`
    Examples:
        _vitis.bat -v 2025.2 -s .\\checkout.py
        _vitis.bat -v 2025.2 -s .\\checkout.py --platform my_platform
        _vitis.bat -v 2025.2 -s .\\checkout.py --app my_app
        _vitis.bat -v 2025.2 -s .\\checkout.py --app my_app --incremental
    """
    parser = argparse.ArgumentParser(
        description="Recreate or selectively rebuild the Vitis workspace from src."
        )
    parser.add_argument(
        "--platform", action="append", default=[], metavar="NAME",
        help="Rebuild this platform and its bound apps only."
        )
    parser.add_argument(
        "--app", action="append", default=[], metavar="NAME",
        help="Rebuild this app and its platform only."
        )
    parser.add_argument(
        "--skip-unbound-platforms", action="store_true",
        help="Skip platforms not referenced by any app."
        )
    parser.add_argument(
        "--incremental", action="store_true",
        help="Reuse existing targets in place when possible."
        )
    parser.add_argument(
        "--allow-process-cleanup", action="store_true",
        help="Allow cleanup of stale Vitis processes on failure."
        )
    parser.add_argument(
        "-y", "--assume-yes", action="store_true",
        help="Skip the full-checkout wipe confirmation prompt."
        )
    parser.add_argument(
        "--esw-repo", default=None, metavar="PATH",
        help="Use this embeddedsw checkout for ZynqMP FSBL work."
        )
    parser.add_argument(
        "--psu-init", action="store_true",
        help="Launch configs initialize the PS with psu_init.tcl instead of the FSBL."
        )
    args = parser.parse_args()

    lcWs = Workspace()
    iRet = lcWs.checkOutSF(
        platforms=args.platform,
        apps=args.app,
        skip_unbound_platforms=args.skip_unbound_platforms,
        incremental=args.incremental,
        allow_process_cleanup=args.allow_process_cleanup,
        assume_yes=args.assume_yes,
        esw_repo=args.esw_repo,
        psu_init=args.psu_init
        )
    LOG("Checkout finished with status: " + str(iRet))
    if lcWs._runLogPath:
        LOG(f"Full details: {lcWs._runLogPath} and {lcWs._buildLogPath}")
    sys.exit(iRet)
