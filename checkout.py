
"""
    Company: Digilent RO
    Engineers: rb & bs
    Usage: Vitis projects
    
    @Description
    This checkout.py has the same behavior
    as the previous checkout.tcl. It creates
    from sw -> src multiple applications into
    sw -> ws.
    
    @Insights
    Vitis v2024.1 has Python v3.8.3.
    Vitis v2025.1 has Python v3.13.0.
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
import hsi
import xsdb
from misc import (LOG, stopDanglingVitisProcesses)

# Extensions _pruneStaleFiles treats as genuine (prunable) source files when
# scanning an app's own top-level "src" dir, where sources and Vitis-
# generated metadata (CMakeLists.txt, UserConfig.cmake, app.yaml, .clangd,
# compile_commands.json, ...) live side by side; anything else with no
# source counterpart is left untouched rather than guessed at.
PRUNABLE_SRC_FILE_EXTENSIONS = frozenset({
    ".c", ".cc", ".cpp", ".cxx", ".c++", ".C", ".h", ".hh", ".hpp", ".hxx", ".s", ".S", ".ld"
    })

# Captured once, at module load (before create_client() ever spawns a
# server), so stopDanglingVitisProcesses can tell "a leftover from some
# earlier, already-finished run" (safe to kill) apart from "a server THIS
# very invocation just started" (never safe to kill - confirmed to
# otherwise break the current gRPC channel mid-retry, see
# _setWorkspaceWithRetry).
_PROCESS_START_TIME = time.time()

def GetMetadata(**kwargs):
    """
    @Description
    Extract hardware family/target-processor metadata out of an XSA file
    using the HSI Python API, so checkOutSF can pick the right domain/BSP
    settings for a platform without any of it being hard-coded per project.

    @Parameters
    kwargs:
        xsa: path to the .xsa file to inspect.
        open_xsa: set to a truthy value to actually open/parse the xsa; if
                  falsy, an empty metadata dict is returned and no HSI call
                  is made.

    @Returns
    ret_metadata with "target_proc" (the first supported processor found,
    kept for callers/arch's that only ever use a single processor, e.g.
    microblaze) and "target_procs" (every supported processor found, in
    discovery order, so a multi-processor xsa - e.g. a ZynqMP exposing both
    an A-class and an R5 - can get one domain per processor instead of only
    ever the first one).
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
            LOG(f"Extracting hardware metadata from {xsa} using the HSI Python API...")
            #  xv_pycommontasks - py extension module (on win)
            #  xv_hsmpytasks - py extension module (on win)
            HwDesign = hsi.HwManager.open_hw_design(xsa)
            try:
                ret_metadata["arch"] = HwDesign.FAMILY
                for proc in HwDesign.get_cells(hierarchical="true", filter="IP_TYPE==PROCESSOR"):
                    if ((proc.IP_NAME == "psu_cortexa53" or
                         proc.IP_NAME == "psu_cortexa72" or
                         proc.IP_NAME == "psu_cortexr5") or
                        (proc.IP_NAME == "psv_cortexa72" or
                         proc.IP_NAME == "psv_cortexr5") or
                        proc.IP_NAME == "ps7_cortexa9"
                        ):
                        proc_name = proc.IP_NAME + "_0"
                        # Multi-processor XSAs (e.g. ZynqMP exposing both an
                        # A-class and an R5) must yield one domain per
                        # processor, not just the first found: keep scanning
                        # instead of breaking, and record every one.
                        if proc_name not in ret_metadata["target_procs"]:
                            ret_metadata["target_procs"].append(proc_name)
                        if ret_metadata["target_proc"] == "":
                            ret_metadata["target_proc"] = proc_name
            finally:
                # Release the HSI-side hardware design handle whether or
                # not metadata extraction above succeeded, so a scratch
                # xsa (see _extractXsaMetadataScratch) is never left open
                # when its temp dir is removed right after this returns.
                HwDesign.close()
        else:
            LOG("No XSA file was provided, hardware metadata cannot be extracted!")
    else:
        LOG("Skipping HW metadata extraction, not requested for this call.")
    
    return ret_metadata

class _BuildLogFilter:
    """
    @Description
    File-like stdout replacement used only while a component builds. Vitis
    itself prints its own, very verbose build log line-by-line (see
    vitis._build); this class keeps that complete log in a single file
    under the workspace, while only letting essential/error/warning lines
    still reach the terminal, so an unattended checkout.py run (see
    _vitis.ps1/.bat/.sh) stays readable.
    """
    ESSENTIAL_PATTERN = compile(r"error|warning|fail|build finished|build complete|\*\*\*",
                                RegexFlag.IGNORECASE)

    def __init__(self, logFile, realStream):
        self._logFile = logFile
        self._real = realStream
        self._pending = ""

    def write(self, data):
        self._logFile.write(data)
        self._pending += data
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if self.ESSENTIAL_PATTERN.search(line):
                self._real.write(line + "\n")

    def flush(self):
        self._logFile.flush()
        self._real.flush()

class Workspace:
    """
    @Description
    Resources to set app/domain configs, and create multiple applications.
    """
    SUCCESS = 0
    FAILURE = -1
    COMP_SETTINGS = "comp-settings.json"
    DEBUG = 0
    # Files the repo's top-level .gitignore whitelists under /ws/ (so git
    # still tracks the otherwise fully-ignored, empty workspace folder);
    # _prepareWorkspace must never delete these on wipe.
    PRESERVED_WS_ENTRIES = {".keep", "cleanup.cmd", "cleanup.sh"}

    def __init__(self):
        # Set for real in _prepareWorkspace; "" means quietBuild falls back
        # to plain, unfiltered building (e.g. if called before that point).
        self._buildLogPath = ""
        # Set for real in _prepareWorkspace; used by _extractPlatformMetadata
        # as a scratch area so HSI's xsa-unzip side effects never land next
        # to the checked-in xsa files under `src`.
        self._wsPath = ""
        # Set from checkOutSF's allow_process_cleanup arg. startedBefore
        # already prevents this run's own just-started server from being
        # killed, but a *pre-existing* vitis/vitis-server process from the
        # same install could still be an unrelated, still-active session
        # (another workspace, another user) rather than an actual leftover
        # from a crashed run - the OS process list alone cannot tell these
        # apart (the workspace is bound to a running server only via a
        # later gRPC call, never visible on its command line). So
        # force-killing pre-existing processes is opt-in, off by default.
        self._allowProcessCleanup = False

    def setConfigDomain(self,
                        domain,
                        option : str,
                        **kargs
                        ) -> int:
        """
        @Description
        Iterate over kargs : dict elements and set domain configs.

        @Insights
        domain.set_config can raise even though the param was actually
        applied: known vitis-py bug where params get stored dynamically
        into the domain's UserConfig(.cmake) before the (failing) return
        value is produced. So a raised exception here is not necessarily a
        real failure, it is logged for visibility, not treated as fatal.

        @Parameters
        domain: domain name of current workspace.
        option: for processor or OS.
        kargs: dict with {[params...] : [values...]}.
        """
        for key, value in kargs.items():
            try:
                domain.set_config(option=option, param=key, value=value)
                # return Workspace.SUCCESS
            except:
                LOG(f"domain.set_config raised for param \"{key}\" (known vitis-py "
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
        Iterate over kargs : dict elements and set app configs.

        @Insights
        app.set_app_config can raise even though the param was actually
        applied: known vitis-py bug where params get stored dynamically
        into the app's UserConfig.cmake before the (failing) return value
        is produced. So a raised exception here is not necessarily a real
        failure, it is logged for visibility, not treated as fatal.

        @Parameters
        app: current application obj.
        kargs: dict with {[params...] : [values...]}.
        """
        for key, value in kargs.items():
            try:
                app.set_app_config(key=key, value=value)
                # return Workspace.SUCCESS
            except:
                LOG(f"app.set_app_config raised for param \"{key}\" (known vitis-py "
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
        Set all User config from a custom file created at check in workflow.
        Only "USER_*" keys are applied as app config; any other key present
        (the platform/xsa correlation entry checkin.py stores alongside the
        settings) is intentionally skipped here, see getAppPlatformXsa.

        @Parameters
        app: obj returned by create_application_component function from vitis.cli_client.
        appname: name found in json file (last key) or it can be put manually.
        filepath: comp-setting.json path.
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
        Read the platform/xsa correlation entry checkin.py stores alongside
        the "USER_*" settings in comp-settings.json (the one key that is not
        "USER_*"-prefixed), so an application always gets rebuilt against the
        exact XSA variant it was checked in against (e.g. one hw variant vs
        another), instead of an arbitrary/first-found platform. The entry's
        value is either a bare xsa path string (older check-ins, before the
        processor/domain association below was tracked) or a dict with an
        "xsa" key (see getAppTargetProc); both are accepted here.

        @Parameters
        appname: app name, used only for logging context.
        filepath: comp-setting.json path.

        @Returns
        Relative xsa path as stored in the json (e.g.
        "src\\my_platform_hw_pf\\my_platform.xsa"), or "" if none or
        more than one candidate key is found.
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
        Read the exact processor/domain instance (e.g. "psu_cortexa53_0")
        this application was bound to at check-in time, stored inside the
        same platform/xsa correlation entry read by getAppPlatformXsa (see
        checkin.py's processGatherFiles). Needed on a multi-processor xsa
        (e.g. a ZynqMP exposing both an A-class and an R5): without it,
        every application checked in against such a platform would silently
        get rebuilt against whichever processor _extractPlatformMetadata
        happens to find first, rather than the one it actually used before
        check-in. Older comp-settings.json files (checked in before this was
        tracked) simply won't have it, and _resolveAppDomain falls back to
        the platform's default processor in that case.

        @Parameters
        appname: app name, used only for logging context.
        filepath: comp-setting.json path.

        @Returns
        The recorded cpu instance name (e.g. "psu_cortexa53_0"), or "" if
        none is recorded.
        """
        dJsonStruct = JSONDecoder().decode(open(filepath).read())
        for key, value in dJsonStruct.items():
            if key.startswith("USER_"):
                continue
            if isinstance(value, dict) and isinstance(value.get("cpu_instance"), str):
                return value["cpu_instance"]
        return ""

    def quietBuild(self, buildFn, desc="") -> None:
        """
        @Description
        Run `buildFn` (a component's bound .build method, e.g. platform.build
        or app.build) with Vitis's own build log redirected to
        self._buildLogPath instead of the terminal; only essential/error/
        warning lines are still echoed live, see _BuildLogFilter. Falls back
        to plain, unfiltered building if no log path has been set up yet
        (self._buildLogPath == "").

        Despite its own docstring claiming it either returns True or raises,
        vitis-py's build() actually returns a streamed status (SUCCESS=0/
        FAILURE=1/IN_PROGRESS=2) and does NOT raise on a genuine content/
        compile failure (only on gRPC/communication errors), so that return
        value must be checked here; otherwise a real BSP/app build failure
        silently continues into later, unrelated, harder to diagnose errors
        instead of aborting immediately at the real cause.

        Real bug found the hard way: a naive `status not in (None, True, 0)`
        check looks reasonable but is WRONG, because Python's bool is an int
        subclass, so `1 == True` - the genuine, integer FAILURE status (1)
        would compare equal to the accepted `True` entry and never raise at
        all, silently treating every failed app/platform build as success.
        `is not True` (identity, not equality) below avoids that trap.

        @Parameters
        buildFn: bound method to call, taking no arguments.
        desc: short human-readable description, used only for logging.
        """
        if not self._buildLogPath:
            status = buildFn()
        else:
            LOG(f"Building {desc}, full Vitis build log kept at: {self._buildLogPath}")
            realStdout = sys.stdout
            with open(self._buildLogPath, "a", encoding="utf-8") as logFile:
                sys.stdout = _BuildLogFilter(logFile, realStdout)
                try:
                    status = buildFn()
                finally:
                    sys.stdout = realStdout

        if status is not None and status is not True and status != 0:
            raise Exception(f"Build failed for {desc} (status: {status}), see {self._buildLogPath}")

    @staticmethod
    def _forceRemoveReadonly(func, path_, exc_info):
        """
        @Description
        onerror handler for shutil.rmtree: HSI/SDT generates some files
        (e.g. hw/sdt/include/dt-bindings/**/*.h) read-only, which makes
        os.remove/os.rmdir raise WinError 5 (Access is denied), completely
        unrelated to the WinError 32 dangling-process case _prepareWorkspace
        already recovers from. Clearing the read-only attribute and retrying
        the failed operation here lets rmtree finish; if `func` still fails
        (e.g. the file is genuinely locked by a process), the exception
        propagates out of rmtree as before and is handled by the retry loop.

        @Parameters
        func: the failed os function (os.remove/os.rmdir/os.unlink).
        path_: path that failed to be removed.
        exc_info: exception info tuple, unused (kept for shutil's callback signature).
        """
        chmod(path_, S_IWRITE)
        func(path_)

    def _clearWorkspaceContents(self, ws_path) -> None:
        """
        @Description
        Delete everything directly inside ws_path except
        PRESERVED_WS_ENTRIES, leaving ws_path itself (and those entries)
        in place. Used instead of shutil.rmtree(ws_path) so a wipe never
        destroys repo-tracked files living inside the otherwise fully
        git-ignored workspace folder (see PRESERVED_WS_ENTRIES).

        When ws_path does not exist yet (a genuinely fresh checkout, e.g. a
        parent repository that has never run checkout.py before), the
        ".keep" placeholder documented in README Note #3 (and negated by
        _ensureParentGitignore) is created here too, instead of only ever
        being preserved if it already happened to exist: otherwise a brand
        new setup would never get a trackable file under ws/ at all, since
        nothing else creates one.

        @Parameters
        ws_path: absolute path to the workspace directory to clear.
        """
        if not path.isdir(ws_path):
            makedirs(ws_path, exist_ok=True)
            keep_path = path.join(ws_path, ".keep")
            if not path.isfile(keep_path):
                open(keep_path, "a", encoding="utf-8").close()
            return
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

    def _ensureParentGitignore(self, ws_path) -> None:
        """
        @Description
        Make sure the parent repository (the one containing ws_path as a
        sibling of the `scripts` submodule, per README Note #2) has a
        .gitignore rule keeping this generated workspace out of `git
        status`, while still tracking PRESERVED_WS_ENTRIES. Nothing else in
        a fresh setup (following the README) creates this file: the old
        Tcl-era workflow used to install `sub/template.gitignore` here, but
        that mechanism was dropped when this repo moved to the Python
        checkin/checkout scripts, without a replacement - so without this,
        the entire generated workspace would be exposed to the parent
        repository on first run. A no-op if the rule is already present.

        @Parameters
        ws_path: absolute path to the workspace directory being (re)created.
        """
        repo_root = path.dirname(ws_path)
        gitignore_path = path.join(repo_root, ".gitignore")
        ws_name = path.basename(ws_path)
        ignore_rule = f"/{ws_name}/*"
        # Every line the block needs to be considered complete: the blanket
        # ignore rule plus one negation per PRESERVED_WS_ENTRIES entry. A
        # parent repository that already has the blanket rule (e.g. from an
        # older/partial version of this block, or added by hand) but is
        # missing one or more negations would otherwise never get them
        # added, silently keeping those preserved files out of `git
        # status` forever - so check each required line individually
        # instead of returning as soon as just the ignore rule is found.
        required_lines = [ignore_rule] + [f"!/{ws_name}/{entry}"
                                          for entry in sorted(self.PRESERVED_WS_ENTRIES)]
        existing_lines = set()
        if path.isfile(gitignore_path):
            with open(gitignore_path, "r", encoding="utf-8") as f:
                # Compare whole, active (non-comment) lines instead of a
                # substring search: a commented-out rule (e.g. "#/ws/*")
                # or an unrelated, differently-anchored path (e.g.
                # "/generated/ws/*") both contain ignore_rule as a
                # substring, which would wrongly be treated as "already
                # installed" and leave the generated workspace untracked.
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
        LOG(f"Added workspace ignore rules to {gitignore_path}")

    def _prepareWorkspace(self, client, ws_path) -> None:
        """
        @Description
        (Re)create the Vitis workspace at ws_path and point `client` at it.
        Only clears ws_path's contents, never the directory entry itself,
        and skips PRESERVED_WS_ENTRIES (files the repo's .gitignore
        whitelists under /ws/, e.g. ".keep" so git still tracks the
        otherwise-empty folder) so a wipe never destroys repo-tracked
        files. checkout.py is meant to run unattended through
        _vitis.ps1/.bat/.sh (bundled python, no interactive Vitis IDE
        watching over it), so a previous run's Vitis server can be left
        dangling and hold a lock on ws_path's files; stopDanglingVitisProcesses
        is used to clear that out between attempts, instead of blindly
        retrying the same wipe with no recovery action - but only if
        self._allowProcessCleanup was explicitly opted into (see
        checkOutSF's allow_process_cleanup), since a pre-existing process
        from the same install could just as easily be another still-active
        Vitis session rather than an actual leftover. Read-only files
        HSI/SDT leaves behind (see _forceRemoveReadonly) are handled within
        the same wipe, not counted as a failed attempt. Also sets
        self._buildLogPath, where every subsequent component build's full
        log is kept (see quietBuild).

        Ownership/lock availability is validated with a first
        set_workspace call (see _setWorkspaceWithRetry) BEFORE any content
        is cleared, and the wipe only proceeds once that succeeds: on
        Unix, deleting a file another process still has open does not fail
        (nor is it prevented on Windows for every file, only ones actually
        locked), so wiping first and only then discovering a lock via a
        failed set_workspace can already have destroyed an actively used
        workspace's unlocked project files by the time the one locked file
        is reached. set_workspace is called again after the wipe, since
        clearing ws_path's contents also removes the "_ide" workspace
        metadata the first call just created.

        @Parameters
        client: Vitis client obj returned by create_client().
        ws_path: absolute path to the workspace directory to (re)create.
        """
        makedirs(ws_path, exist_ok=True)
        self._setWorkspaceWithRetry(client, ws_path)

        max_try = 5
        for attempt in range(1, max_try + 1):
            try:
                self._clearWorkspaceContents(ws_path)
                LOG(f"Cleared workspace {ws_path} on attempt {attempt}.")
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

        self._setWorkspaceWithRetry(client, ws_path)
        makedirs(ws_path, exist_ok=True)
        self._ensureParentGitignore(ws_path)
        self._wsPath = ws_path
        self._buildLogPath = path.join(ws_path, "checkout_build.log")
        LOG(f"Successfully created Vitis client on workspace {client.get_workspace()}")

    def _setWorkspaceWithRetry(self, client, ws_path) -> None:
        """
        @Description
        Calls client.set_workspace(ws_path), recovering automatically from
        a previous run's Vitis server being left dangling (checkout.py runs
        unattended through _vitis.bat/.ps1/.sh, no interactive IDE watching
        over it) and still holding ws_path's own lock file
        ("_ide/.wsdata/.lock"), which makes set_workspace fail with
        "the workspace '...' is already in use" (FAILED_PRECONDITION) even
        though the process that created it is long gone. On failure, ONLY IF
        self._allowProcessCleanup was explicitly opted into (see checkOutSF's
        allow_process_cleanup), stops any dangling Vitis process
        (stopDanglingVitisProcesses) and removes the stale lock file itself
        (killing the process alone does not always delete it, since it may
        not get a chance to clean up on a forceful stop), then retries a
        bounded number of times. Without that opt-in, neither the process
        nor the lock file is touched - a "workspace already in use" failure
        can legitimately mean another active IDE session owns it, and
        deleting the lock blindly could let two sessions use the workspace
        concurrently. Shared by _prepareWorkspace and _openExistingWorkspace
        so neither codepath needs the user to manually stop dangling
        processes/delete the lock file before every run.

        @Parameters
        client: Vitis client obj returned by create_client().
        ws_path: absolute path to the workspace directory to select.
        """
        max_try = 5
        lock_path = path.join(ws_path, "_ide", ".wsdata", ".lock")
        for attempt in range(1, max_try + 1):
            try:
                client.set_workspace(ws_path)
                return
            except Exception as e:
                LOG(f"Attempt {attempt} to set workspace {ws_path} failed: {e}")
                if attempt < max_try:
                    if self._allowProcessCleanup:
                        stopped = stopDanglingVitisProcesses(environ.get("XILINX_VITIS", ""), _PROCESS_START_TIME)
                        if stopped:
                            LOG(f"Stopped dangling Vitis process(es) holding the workspace: {stopped}")
                        if path.isfile(lock_path):
                            try:
                                remove(lock_path)
                                LOG(f"Removed stale workspace lock file: {lock_path}")
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
        Point `client` at ws_path without touching its contents (used for a
        selective rebuild, see checkOutSF's `platforms`/`apps` parameters),
        as opposed to _prepareWorkspace's full wipe-and-recreate. Sets the
        same self._wsPath/self._buildLogPath _prepareWorkspace does, so
        quietBuild's log filtering behaves identically either way.

        @Parameters
        client: Vitis client obj returned by create_client().
        ws_path: absolute path to the (already existing) workspace directory.
        """
        makedirs(ws_path, exist_ok=True)
        self._ensureParentGitignore(ws_path)
        self._setWorkspaceWithRetry(client, ws_path)
        self._wsPath = ws_path
        self._buildLogPath = path.join(ws_path, "checkout_build.log")
        LOG(f"Reusing existing Vitis workspace {client.get_workspace()} (selective rebuild).")

    def _discoverAppsAndPlatforms(self, src_root) -> tuple:
        """
        @Description
        Walk `src_root` once to find every application folder (one with a
        "src" subdir) and every XSA file, then group XSA files by their
        containing "*_hw_pf" folder to derive one platform per XSA (not one
        per folder), so multiple HW variants sharing a folder (e.g.
        variant_a/variant_b) each get their own platform: named after the xsa's
        own stem (e.g. "my_platform_variant_a"), only prefixed with the
        hw_pf folder name in the rare case two different folders have xsa's
        sharing the same stem.

        @Parameters
        src_root: absolute path to the repository's `src` folder.

        @Returns
        (app_names, hw_platforms) where app_names is a list[str] and
        hw_platforms is a dict keyed by absolute xsa path, each value a dict
        with at least "name", "hw_pf_dir", "xsa_path".
        """
        app_names = []
        xsa_files = []

        # Only direct children of src_root are applications/platforms per
        # the documented layout (README "Note #2"): a fully recursive walk
        # would wrongly turn a nested "src/my_app/src/vendor/src/..." layout
        # into a bogus "vendor" application, and would pick up any .xsa
        # found anywhere under an app's own src tree (e.g. a test fixture)
        # as a spurious extra platform.
        for entry in listdir(src_root):
            entry_path = path.join(src_root, entry)
            if not path.isdir(entry_path):
                continue
            if path.isdir(path.join(entry_path, "src")):
                app_names.append(entry)
                LOG(f"Detected application: {entry}")
            for filename in listdir(entry_path):
                filepath = path.join(entry_path, filename)
                if path.isfile(filepath) and filename.endswith(".xsa"):
                    xsa_files.append(filepath)

        LOG(f"Detected {len(app_names)} application(s): {app_names}")

        xsa_by_dir = {}
        for xsa_path in xsa_files:
            xsa_by_dir.setdefault(path.dirname(xsa_path), []).append(xsa_path)

        hw_platforms = {}
        used_names = set()
        for hw_pf_dir, xsas_in_dir in xsa_by_dir.items():
            for xsa_path in xsas_in_dir:
                # Platform name is just the xsa's own stem (e.g.
                # "my_platform_variant_a"), not the containing hw_pf
                # folder, so it stays meaningful even when a folder is
                # renamed/shared. Only disambiguate with the hw_pf folder
                # name in the rare case two different folders have xsa's
                # sharing the same stem.
                platform_name = path.splitext(path.basename(xsa_path))[0]
                if platform_name in used_names:
                    hw_pf_name = path.basename(hw_pf_dir)
                    candidate_name = f"{hw_pf_name}_{platform_name}"
                    # Keep disambiguating until unique: the hw_pf-prefixed
                    # name can itself collide (e.g. "dir_foo.xsa" alongside
                    # "dir/foo.xsa" both resolve to "dir_foo"), and creating
                    # a second component with the same name would fail.
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
        Run GetMetadata/HSI against a throwaway COPY of xsa_path placed
        under the workspace, never the original: HSI unzips a handful of
        files (psu_init.*, a .bit, ...) right next to whatever xsa path it
        is given, so opening the checked-in xsa directly would pollute the
        `src` tree (and, on any crash between opening and cleanup, leave
        generated files behind for git to pick up). Split out of
        _extractPlatformMetadata to keep it focused on orchestration.

        @Parameters
        xsa_path: absolute path to the real, checked-in xsa file.
        plt_name: unique platform name, used only to namespace the scratch
                 dir when multiple xsa's are processed.

        @Returns
        The metadata dict returned by GetMetadata.
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
        Fill in "arch"/"target_proc" for every entry in hw_platforms using
        HSI (see GetMetadata), run against a scratch copy of each xsa kept
        under the workspace (see _extractXsaMetadataScratch) so the
        checked-in `src` tree is never touched. Runs for every discovered
        platform unconditionally, even during a selective/--incremental
        run that only rebuilds one of them: _setPlatformDomainInfo (needed
        to bind any requested app to whichever platform it resolves to,
        rebuilt or not) also depends on "arch"/"target_proc", and which
        platform(s) a selectively-rebuilt app may resolve to is not known
        until after this runs, so skipping it for "not explicitly
        requested" platforms would risk leaving an app unbindable. This
        does add HSI-extraction overhead proportional to the total
        platform count to every selective/--incremental run, not just the
        one(s) actually being rebuilt.

        @Parameters
        hw_platforms: dict produced by _discoverAppsAndPlatforms, mutated in
                     place with "arch"/"target_proc"/"target_procs" keys
                     added.
        """
        start_time = time.time()

        for xsa_path, plt in hw_platforms.items():
            metadata = self._extractXsaMetadataScratch(xsa_path, plt["name"])
            plt["arch"] = metadata["arch"]
            if plt["arch"] in ("spartan7", "artix7", "kintex7"):
                plt["target_proc"] = "microblaze_0"
                plt["target_procs"] = [plt["target_proc"]]
            else:
                plt["target_proc"] = metadata["target_proc"]
                plt["target_procs"] = metadata["target_procs"]
            LOG(f"Platform \"{plt['name']}\": detected arch \"{plt['arch']}\", "
               f"available target processor(s): {plt['target_procs']}")

        execution_time = time.time() - start_time
        LOG(f"Hardware metadata extraction took {execution_time:.4f} seconds")

    def _findDomainCmakeCache(self, platform_dir, domain_name):
        """
        @Description
        Locate the single CMakeCache.txt generated for domain_name's own BSP
        under platform_dir (the just-created platform's own workspace
        folder), used by _fixDomainCmakeFlags. A recursive search is used
        instead of hardcoding the "libsrc/build_configs/gen_bsp" path
        segment observed in practice, since that internal layout is
        Vitis-generated and not part of any documented/stable contract.

        @Parameters
        platform_dir: absolute path to the platform's own workspace folder
                     (client.get_workspace() + sep + platform name).
        domain_name: name of the domain whose BSP cache is being searched for.

        @Returns
        Absolute path to the found CMakeCache.txt, or None if not found.
        """
        needle = sep + domain_name + sep + "bsp"
        for root, _, files in walk(platform_dir):
            if "CMakeCache.txt" in files and needle in (root + sep):
                return path.join(root, "CMakeCache.txt")
        return None

    def _fixDomainCmakeFlags(self, ws_path, platform_name, domain_name) -> None:
        """
        @Description
        Locate domain_name's own CMakeCache.txt (see _findDomainCmakeCache)
        and apply the Vitis 2025.2 CMAKE_*_FLAGS workaround to it (see
        _fixCmakeFlags for the actual mechanics). A no-op (with a LOG
        warning) if the cache cannot be found, so an unexpected layout
        surfaces as the original build failure rather than a confusing
        partial edit.

        @Parameters
        ws_path: absolute path to the Vitis workspace (client.get_workspace()).
        platform_name: name of the just-created platform.
        domain_name: name of the domain whose BSP flags need fixing.
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
        Work around a Vitis 2025.2 code-generation bug (see _buildPlatform's
        call site for the full explanation): reconstruct and overwrite
        CMAKE_C_FLAGS/CMAKE_CXX_FLAGS/CMAKE_ASM_FLAGS in cache_path with the
        value its own toolchain file intended (built from that same cache's
        own, correctly-cached TOOLCHAIN_*_FLAGS/TOOLCHAIN_DEP_FLAGS/
        CMAKE_SPECS_FILE/CMAKE_INCLUDE_PATH entries), instead of whatever
        CMake's own first-configure CACHE-seeding left behind. The same bug
        affects both a platform domain's BSP cache (fixed proactively, right
        after add_domain(), see _fixDomainCmakeFlags) and an application's
        own build/CMakeCache.txt - but unlike a domain's BSP, an app's cache
        doesn't exist until its own build actually starts, so it can only be
        fixed reactively, after a first build failure (see _buildApplication).
        A no-op (with a LOG warning) if the expected building-block entries
        cannot be found, so an unexpected layout surfaces as the original
        build failure rather than a confusing partial edit.

        @Parameters
        cache_path: absolute path to the CMakeCache.txt to fix.
        label: short human-readable description of what cache_path belongs
              to, used only for logging context.

        @Returns
        True if at least one CMAKE_<LANG>_FLAGS entry's value actually
        changed, False otherwise (nothing needed fixing, or the expected
        building-block entries were not found) - used by _buildApplication
        to decide whether a rebuild is worth retrying.
        """
        with open(cache_path, "r", encoding="utf-8") as f:
            cache_text = f.read()

        def cacheVar(var_name):
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
            # Omit the include option entirely when there's no path: a bare
            # trailing "-I" with nothing after it makes the compiler
            # consume the next command-line token as the include dir (or
            # report a missing argument), turning this fix-up into another
            # build failure.
            if include_path:
                fixed_value += f" -I{include_path}"
            if cacheVar(f"CMAKE_{lang}_FLAGS") == fixed_value:
                continue
            # fixed_value can contain Windows paths with backslashes; a
            # plain string replacement would have re.sub interpret those
            # as escape/group-reference sequences (e.g. "\C..."), so use a
            # callable replacement to insert it as a literal value instead.
            cache_text, n = compile(rf"^(CMAKE_{lang}_FLAGS:\w+)=.*$", RegexFlag.MULTILINE).subn(
                lambda m: f"{m.group(1)}={fixed_value}", cache_text, count=1
                )
            if n:
                changed = True
                LOG(f"Fixed CMAKE_{lang}_FLAGS for {label}: {fixed_value}")

        if changed:
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(cache_text)
        return changed

    def _configureMicroblazeDomain(self, domain, domain_name) -> None:
        """
        @Description
        Apply the standard BSP proc/os settings this repo's MicroBlaze
        platforms (spartan7/artix7/kintex7) need, split out of
        _buildPlatform to keep that method focused on orchestration.

        @Parameters
        domain: domain obj returned by platform.add_domain.
        domain_name: name of `domain`, used only for logging context.
        """
        LOG(f"Configuring BSP settings for the domain \"{domain_name}\"...")
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
        Add the FSBL domain to a zynquplus platform, generate/build a custom
        FSBL application from the template, then rebuild the platform with
        the resulting elf as its boot bsp. Split out of _buildPlatform to
        keep that method focused on orchestration.

        @Parameters
        client: Vitis client obj returned by create_client().
        platform: platform component obj (already built once) to attach the
                 FSBL domain/elf to.
        plt: entry from the hw_platforms dict, needs "name"/"target_proc".
        repo_path: absolute path to the parent repository's `repo` folder
                  (embeddedsw).
        """
        name = plt["name"]
        target_proc = plt["target_proc"]

        embeddedsw = client.set_embedded_sw_repo(level="LOCAL", path=[repo_path])
        fsbl_domain_name = f"{target_proc}_domain_fsbl"
        LOG(f"Adding domain \"{fsbl_domain_name}\" for cpu \"{target_proc}\" and OS \"standalone\"...")
        zynqmp_fsbl_domain = platform.add_domain(
            cpu=target_proc,
            os="standalone",
            name=fsbl_domain_name,
            display_name=fsbl_domain_name,
            support_app="zynqmp_fsbl"
            )

        self.quietBuild(platform.build, f"platform \"{name}\" (fsbl domain)")
        # Generating custom fsbl application from template. Named after the
        # platform so multiple zynqmp platforms in the same workspace do not
        # collide on a single "ZynqMP_FSBL".
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
        platform.set_fsbl_elf(path=fsbl_app.component_location + sep + "build" + sep + f"{fsbl_app_name}.elf")
        self.quietBuild(platform.build, f"platform \"{name}\" (with fsbl elf)")

    def _buildPlatform(self, client, xsa_path, plt, repo_path) -> None:
        """
        @Description
        Create, configure and build ONE platform component from an already
        HSI-inspected entry (see _extractPlatformMetadata), including the
        ZynqMP FSBL domain/application when needed (see _buildZynqMPFsbl).
        Adds one domain per entry in plt["target_procs"] (e.g. a ZynqMP xsa
        exposing both an A-class and an R5 gets a domain for each, instead
        of only ever the first processor found), so an application checked
        in bound to a specific processor (see getAppTargetProc) can later be
        rebuilt against that same domain rather than an arbitrary one.
        Adds "domains"/"domain_name"/"xpfm" to `plt` for later use by
        _buildApplication (see _setPlatformDomainInfo).

        @Parameters
        client: Vitis client obj returned by create_client().
        xsa_path: absolute path to this platform's xsa file.
        plt: entry from the hw_platforms dict (see _discoverAppsAndPlatforms).
        repo_path: absolute path to the parent repository's `repo` folder
                  (embeddedsw), only used for zynqmp platforms.
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
        platform.update_desc(desc=name)
        self._writePlatformSourceDirManifest(platform, plt["hw_pf_dir"])

        for target_proc in target_procs:
            domain_name = "domain_microblaze_0" if is_microblaze else f"domain_{target_proc}"

            LOG(f"Adding domain \"{domain_name}\" for cpu \"{target_proc}\" and OS \"standalone\"...")
            # support_app must match the template _buildApplication actually uses
            # ("empty_application", see below) so the domain's BSP is generated
            # for the same app shape our real apps get built against; leaving
            # this as "hello_world" causes a mismatched BSP that can fail its own
            # CMake toolchain test when a second platform is built in the same
            # workspace/session.
            domain = platform.add_domain(
                cpu=target_proc,
                os="standalone",
                name=domain_name,
                display_name=domain_name,
                support_app="empty_application"
                )

            # Vitis 2025.2 bug: for a "regular" (non-FSBL) domain, the generated
            # <proc>_toolchain.cmake sets CMAKE_C_FLAGS/CXX_FLAGS/ASM_FLAGS via
            # plain, non-FORCE `set(... CACHE STRING ...)` calls, which CMake's
            # own first-configure CACHE-seeding silently wins against (leaving
            # them at whatever CMAKE_<LANG>_FLAGS_INIT/$ENV{CFLAGS,CXXFLAGS}
            # provided, or blank for ASM) instead of the toolchain file's
            # intended "${TOOLCHAIN_..._FLAGS} ... -specs=${CMAKE_SPECS_FILE}
            # ..." value - so every regular domain's BSP silently compiles with
            # the wrong flags/specs (missing -DSDT, dependency flags, and the
            # domain's own Xilinx.spec), causing "initializer element is not
            # computable at load time"/undeclared XPAR_* build failures further
            # down the pipeline. _fixDomainCmakeFlags reconstructs and
            # overwrites the correct values directly in the domain's
            # CMakeCache.txt afterwards.
            self._fixDomainCmakeFlags(client.get_workspace(), name, domain_name)

            if is_microblaze:
                self._configureMicroblazeDomain(domain, domain_name)

        self.quietBuild(platform.build, f"platform \"{name}\"")

        if arch == "zynquplus":
            self._buildZynqMPFsbl(client, platform, plt, repo_path)

        self._setPlatformDomainInfo(client, plt)

    def _setPlatformDomainInfo(self, client, plt) -> None:
        """
        @Description
        Set "domains"/"domain_name"/"xpfm" on `plt`, used by
        _buildApplication to resolve/bind an application to its platform.
        Split out of _buildPlatform so checkOutSF can also call it for a
        platform that is being intentionally left alone during a selective
        rebuild (see --platform/--app), whose "domains"/"domain_name"/
        "xpfm" would otherwise never get filled in without rebuilding it
        (all are pure functions of plt's own "name"/"arch"/"target_proc"/
        "target_procs", already known from _extractPlatformMetadata, not of
        anything the actual build produces).

        @Parameters
        client: Vitis client obj returned by create_client().
        plt: entry from the hw_platforms dict (see _discoverAppsAndPlatforms/
            _extractPlatformMetadata), needs "name"/"arch"/"target_proc"/
            "target_procs".
        """
        name = plt["name"]
        is_microblaze = plt["arch"] in ("spartan7", "artix7", "kintex7")
        plt["domains"] = {
            target_proc: ("domain_microblaze_0" if is_microblaze else f"domain_{target_proc}")
            for target_proc in plt["target_procs"]
            }
        # Default/fallback domain (e.g. for an app with no recorded
        # processor association, see _resolveAppDomain): the platform's
        # primary processor, i.e. the first one _extractPlatformMetadata
        # found - kept as its own key for backward compatibility with
        # anything still expecting a single "domain_name" per platform.
        plt["domain_name"] = plt["domains"].get(
            plt["target_proc"],
            next(iter(plt["domains"].values()), f"domain_{plt['target_proc']}")
            )
        plt["xpfm"] = (client.get_workspace() + sep + name + sep +
                       "export" + sep + name + sep + name + ".xpfm")

    def _rebuildPlatformInPlace(self, client, plt) -> None:
        """
        @Description
        Reuse an already-built platform component as-is (see checkOutSF's
        `incremental` parameter) instead of deleting and recreating it: just
        fetches the existing component and rebuilds it, without touching its
        domain/FSBL configuration. Meant for quickly recompiling after
        source-level BSP edits only; any HW/domain-level change (a different
        xsa, a changed processor/OS/support_app, ...) still needs a full
        rebuild (the default, non-incremental path), since add_domain()/
        _buildZynqMPFsbl() are not re-run here.

        @Parameters
        client: Vitis client obj returned by create_client().
        plt: entry from the hw_platforms dict (see _discoverAppsAndPlatforms).
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
        Recursively removes files/dirs present under dest_dir but no
        longer present (at the same relative path) under src_dir, so a
        component's on-disk copy stops silently retaining files that were
        deleted or renamed in the checked-in source since the last
        --incremental rebuild. import_files() only ever copies files IN
        (confirmed against vitis-py's own component.py); it never removes
        a destination file/dir that has no more matching source, so
        without this, a stale copy keeps being compiled/linked into an
        --incremental build even after being deleted/renamed at the
        source, silently diverging from the checked-in source tree.

        @Parameters
        dest_dir: component-side directory being mirrored (already
                  imported at least once).
        src_dir: checked-in source directory dest_dir is a copy of.
        top_level_skip: entry names (dest_dir's own direct children only,
                        not recursed into) to never touch, e.g. a
                        still-valid extra module dir handled by its own
                        separate call.
        strict_top_level: dest_dir's own direct children (not recursed
                        into ones, where source and Vitis-generated
                        metadata live mixed side by side - CMakeLists.txt,
                        UserConfig.cmake, app.yaml, .clangd,
                        compile_commands.json, .compile_commands, ... none
                        of these are a stable/enumerable set across Vitis
                        versions) are pruned conservatively: a FILE is only
                        removed if its extension is a known source
                        extension (see PRUNABLE_SRC_FILE_EXTENSIONS); a
                        DIRECTORY with no source counterpart is never
                        auto-deleted here at all (confirmed both app.yaml,
                        a file, and ".compile_commands", a directory, are
                        genuine Vitis-generated metadata living at this
                        exact level - deleting the former broke the next
                        build outright with "Error in retargeting the
                        Application"; guessing which directory names are
                        "safe" is not reliable enough to automate, so a
                        stale EXTRA MODULE directory - the one directory-
                        level case that must still be pruned - is instead
                        tracked/removed explicitly via a manifest, see
                        _extraModulesManifestPath/_rebuildApplicationInPlace).
                        Only ever True for the direct call on an app's own
                        top-level "src" dir; nested/recursive calls always
                        leave it False, since a subdirectory that made it
                        this deep is necessarily part of the mirrored
                        source tree, never Vitis metadata.
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
                LOG(f"Removed stale file no longer present in the checked-in source: {dest_entry}")
            elif is_dir:
                self._pruneStaleFiles(dest_entry, src_entry)

    def _extraModulesManifestPath(self, app) -> str:
        """
        @Description
        Path to the small manifest file recording which extra module
        directory names (see _importAppExtraModules) were imported into
        `app` the last time it was (re)built, so a later --incremental
        rebuild can tell "an extra module dir with no current source
        counterpart because it was genuinely renamed/removed" (safe/
        desired to delete) apart from any other unrelated top-level
        directory Vitis itself may have generated (never safe to delete
        by guesswork, see _pruneStaleFiles' strict_top_level).

        @Parameters
        app: application component obj.
        """
        return path.join(app.component_location, ".digilent_extra_modules")

    def _readExtraModulesManifest(self, app) -> set:
        manifest_path = self._extraModulesManifestPath(app)
        if not path.isfile(manifest_path):
            return set()
        with open(manifest_path, "r") as f:
            return {line.strip() for line in f if line.strip()}

    def _writeExtraModulesManifest(self, app, module_names) -> None:
        with open(self._extraModulesManifestPath(app), "w") as f:
            f.writelines(f"{name}\n" for name in sorted(module_names))

    def _writePlatformSourceDirManifest(self, platform, hw_pf_dir) -> None:
        """
        @Description
        Record the platform's original "src" folder name (e.g.
        "my_platform_hw_pf") into a small manifest file inside the platform's
        own workspace component dir, so checkin.py can check the xsa back
        into that same folder even when the workspace platform component
        name differs from it (see _discoverAppsAndPlatforms: the component
        is named after the xsa's own stem, disambiguated with the hw_pf
        folder name only on a stem collision). Without this, check-in would
        create a brand-new "src/<xsa-stem>" dir named after the component
        instead, orphaning the original folder's stale xsa for the next
        checkout to rediscover as a bogus duplicate platform.

        @Parameters
        platform: platform component obj, just created by
                 client.create_platform_component.
        hw_pf_dir: absolute path to the platform's original containing
                  folder under `src` (see _discoverAppsAndPlatforms).
        """
        manifest_path = path.join(platform.component_location, ".digilent_source_dir")
        with open(manifest_path, "w") as f:
            f.write(path.basename(hw_pf_dir) + "\n")

    def _rebuildApplicationInPlace(self, client, app_name, repo_root) -> None:
        """
        @Description
        Reuse an already-built application component as-is (see checkOutSF's
        `incremental` parameter) instead of deleting and recreating it:
        prunes any file/extra-module-dir deleted or renamed at the source
        since the last run (see _pruneStaleFiles - import_files alone would
        silently leave stale copies behind), re-syncs its sources (main
        "src" folder plus any extra module, see _importAppExtraModules)
        from `src` via import_files, then rebuilds (see
        _buildAppWithFlagsRetry), relying on cmake's own incremental
        compilation to only recompile what actually changed - much faster
        than _buildApplication's full delete+recreate+full-rebuild for a
        source-only edit.

        @Parameters
        client: Vitis client obj returned by create_client().
        app_name: application folder name under `src`.
        repo_root: absolute path to the parent repository (parent of `src`).
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
        Normalize an xsa path for cross-platform/host comparison: the
        correlation path stored in comp-settings.json is written with
        whatever separator the check-in host used, so a Windows-checked-in
        value (e.g. "src\\platform\\design.xsa") would never match a
        Linux-discovered path (which only uses "/") without first folding
        both to a common separator. Also folds case on Windows, where the
        filesystem (and thus path.normpath) is case-insensitive.

        @Parameters
        xsa_path: an absolute or comp-settings.json-relative xsa path,
                  possibly using either "/" or "\\" as separator.

        @Returns
        A normalized string suitable for direct "==" comparison against
        another path normalized the same way.
        """
        normalized = path.normpath(xsa_path.replace("\\", "/").replace("/", sep))
        if platform.system() == "Windows":
            normalized = normalized.lower()
        return normalized

    def _resolveAppPlatform(self, app_name, comp_settings_path, hw_platforms, repo_root):
        """
        @Description
        Determine which entry of hw_platforms `app_name` should bind to,
        based on the platform/xsa correlation entry in its own
        comp-settings.json (see getAppPlatformXsa). Falls back to the only
        detected platform if the app has no such entry, or returns None
        (with a clear log message) if that cannot be determined safely.
        Split out of _buildApplication to keep that method focused on
        orchestration.

        @Parameters
        app_name: application folder name under `src`.
        comp_settings_path: absolute path to this app's comp-settings.json.
        hw_platforms: dict produced by _discoverAppsAndPlatforms/_buildPlatform.
        repo_root: absolute path to the parent repository (parent of `src`),
                  the relative xsa path in comp-settings.json is anchored to.

        @Returns
        The resolved hw_platforms entry, or None if it could not be resolved.
        """
        requested_xsa = self.getAppPlatformXsa(app_name, filepath=comp_settings_path)
        if requested_xsa != "":
            requested_xsa_abs = self._normalizeXsaPathForCompare(
                path.join(repo_root, requested_xsa))
            for xsa_path, candidate in hw_platforms.items():
                if self._normalizeXsaPathForCompare(xsa_path) == requested_xsa_abs:
                    return candidate
            # The app explicitly named an XSA and it wasn't found: do NOT
            # fall back to "the only detected platform" below, since that
            # would silently bind the app to a platform it never asked
            # for (defeating the stored hardware association and possibly
            # building for the wrong target).
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

    def _resolveAppDomain(self, app_name, comp_settings_path, plt) -> str:
        """
        @Description
        Pick which of the platform's domains (see _setPlatformDomainInfo)
        `app_name` should be bound to, based on the exact processor/domain
        instance recorded for it at check-in time (see getAppTargetProc).
        Needed on a multi-processor xsa (e.g. a ZynqMP exposing both an
        A-class and an R5), where a platform now has more than one domain
        (see _buildPlatform) and blindly using the platform's default
        domain would rebuild every app on whichever processor happens to
        be first, losing its original CPU association. Falls back to the
        platform's default/first-detected processor's domain when no such
        entry is recorded (older comp-settings.json, or a brand-new app
        never checked in before) or it does not match any domain actually
        available on this platform.

        @Parameters
        app_name: application folder name under `src`, used only for logging.
        comp_settings_path: absolute path to this app's comp-settings.json.
        plt: entry from the hw_platforms dict (see _setPlatformDomainInfo),
            needs "domains"/"domain_name"/"name".

        @Returns
        The domain name to bind `app_name` to.
        """
        target_proc = self.getAppTargetProc(app_name, filepath=comp_settings_path)
        if target_proc != "" and target_proc in plt["domains"]:
            return plt["domains"][target_proc]
        if target_proc != "":
            LOG(f"Application \"{app_name}\" was checked in bound to processor "
               f"\"{target_proc}\", which is not available on platform "
               f"\"{plt['name']}\"; defaulting to \"{plt['domain_name']}\"")
        return plt["domain_name"]

    def _buildApplication(self, client, app_name, hw_platforms, repo_root) -> None:
        """
        @Description
        Resolve (see _resolveAppPlatform), create, configure and build ONE
        application component. Applications that cannot be resolved to a
        platform are skipped, with a clear log message explaining why.

        @Parameters
        client: Vitis client obj returned by create_client().
        app_name: application folder name under `src`.
        hw_platforms: dict produced by _discoverAppsAndPlatforms/
                     _buildPlatform, each value has "xpfm"/"domain_name".
        repo_root: absolute path to the parent repository (parent of `src`).
        """
        comp_settings_path = (repo_root + sep + "src" + sep + app_name +
                              sep + Workspace.COMP_SETTINGS)

        plt = self._resolveAppPlatform(app_name, comp_settings_path, hw_platforms, repo_root)
        if plt is None:
            return

        domain_name = self._resolveAppDomain(app_name, comp_settings_path, plt)
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
        LOG(f"Application component \"{app_name}\" created at: {app.component_location}")
        self._importAppExtraModules(app, app_name, repo_root)
        self._removeTemplateCruft(app)
        self._buildAppWithFlagsRetry(app, app_name)

    def _importAppExtraModules(self, app, app_name, repo_root) -> None:
        """
        @Description
        Import any extra module directory checked in alongside an
        application's own "src" folder under `src/<app_name>/` (e.g. a
        small shared driver module like "my_shared_module", a sibling of
        "src" rather than nested inside it) into the SAME-named
        subdirectory under the component's own "src", matching where this
        repo's comp-settings.json's USER_COMPILE_SOURCES/
        USER_INCLUDE_DIRECTORIES entries (see decJSON_Ws) expect to find
        them. USER_INCLUDE_DIRECTORIES does get consumed (via
        target_include_directories()), so headers resolve fine, but
        USER_COMPILE_SOURCES is a genuine Vitis 2025.2 CMakeLists.txt-
        generation gap: the generated CMakeLists.txt only ever populates
        its build sources via aux_source_directory(${CMAKE_SOURCE_DIR}
        _sources), which is NON-recursive (direct children of the
        component's own "src" only) and never references
        USER_COMPILE_SOURCES at all - so an extra module's .c file is
        silently left out of the build (compiles headers fine, but is
        never itself compiled/linked, "undefined reference" at link
        time), unless _wireExtraSourcesIntoCMakeLists patches it in.
        Shared by _buildApplication (fresh component) and
        _rebuildApplicationInPlace (--incremental, reused component) so
        both stay in sync with the same layout. Always (re)writes the
        extra-modules manifest (see _writeExtraModulesManifest), even to
        an empty set, so a later --incremental rebuild that finds this
        app has no extra modules anymore can still tell apart "never had
        one" from "used to have one, now removed" (needed by
        _rebuildApplicationInPlace's stale-directory cleanup).

        @Parameters
        app: application component obj (already created, "src" imported).
        app_name: application folder name under `src`.
        repo_root: absolute path to the parent repository (parent of `src`).
        """
        app_root = repo_root + sep + "src" + sep + app_name
        imported_modules = set()
        for entry in listdir(app_root):
            entry_path = app_root + sep + entry
            if entry == "src" or not path.isdir(entry_path):
                continue
            LOG(f"Importing extra module \"{entry}\" for application \"{app_name}\"...")
            app.import_files(from_loc=entry_path, dest_dir_in_cmp=path.join("src", entry))
            imported_modules.add(entry)
        self._writeExtraModulesManifest(app, imported_modules)
        if imported_modules:
            self._wireExtraSourcesIntoCMakeLists(app, app_name)

    def _wireExtraSourcesIntoCMakeLists(self, app, app_name) -> None:
        """
        @Description
        Work around the Vitis 2025.2 CMakeLists.txt-generation gap
        described in _importAppExtraModules: patches the generated
        CMakeLists.txt, once, to also append UserConfig.cmake's
        USER_COMPILE_SOURCES (already correctly populated by decJSON_Ws)
        into "_sources" right after aux_source_directory() populates it,
        with REMOVE_DUPLICATES afterwards since USER_COMPILE_SOURCES also
        happens to re-list the component's own direct sources (e.g.
        "main.c", already picked up by aux_source_directory() itself).
        A no-op if already patched (idempotent, safe to call again on an
        --incremental rebuild) or if CMakeLists.txt doesn't exist yet.

        @Parameters
        app: application component obj (already created, "src" imported).
        app_name: application folder name under `src`, used only for logging.
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
        LOG(f"Wired USER_COMPILE_SOURCES into the build for application "
           f"\"{app_name}\" (Vitis 2025.2 CMakeLists.txt does not consume "
           f"it on its own)...")

    def _buildAppWithFlagsRetry(self, app, app_name, desc_suffix="") -> None:
        """
        @Description
        Build an application component (see quietBuild), transparently
        working around the Vitis 2025.2 CMAKE_*_FLAGS bug (see
        _fixCmakeFlags). The generated cache is inspected/fixed after every
        first build attempt, not just a failed one - Vitis can seed
        incorrect flags yet still complete for an application that doesn't
        happen to exercise them, silently producing a binary built without
        the intended toolchain flags. Whenever the fix-up actually changes
        something the build is retried once, regardless of whether the
        first attempt succeeded or failed; if it changes nothing after a
        failed first attempt, that was a genuine content/compile error, so
        the original exception propagates as before. Shared by
        _buildApplication (fresh component) and _rebuildApplicationInPlace
        (--incremental, reused component), since an app's own
        build/CMakeCache.txt only exists once its build has actually
        started, so it can only be fixed reactively either way.

        @Parameters
        app: application component obj (already configured/populated).
        app_name: application folder name under `src`, used only for logging.
        desc_suffix: appended to the log description, e.g. " (incremental)".
        """
        desc = f"application \"{app_name}\"{desc_suffix}"
        build_error = None
        try:
            self.quietBuild(app.build, desc)
        except Exception as e:
            build_error = e

        # Inspect/fix the cache after every initial build, not just a
        # failed one: Vitis can seed incorrect CMAKE_*_FLAGS yet still
        # complete successfully for an application that happens not to
        # exercise the missing flags, silently accepting a binary built
        # without the intended toolchain flags. If the fix-up changed
        # anything, the build (successful or not) must be retried; if it
        # didn't and the first build had failed, that was a genuine
        # content/compile error, so the original exception propagates.
        cache_path = app.component_location + sep + "build" + sep + "CMakeCache.txt"
        if path.isfile(cache_path) and self._fixCmakeFlags(cache_path, desc):
            LOG(f"Retrying {desc} build after the CMAKE_*_FLAGS fix-up...")
            self.quietBuild(app.build, f"{desc} (retry)")
        elif build_error is not None:
            raise build_error

    def _removeTemplateCruft(self, app) -> None:
        """
        @Description
        Remove files left over by the "empty_application"/psinit template
        that this repo's checked-in sources always replace/don't need. Split
        out of _buildApplication to keep that method focused on
        orchestration.

        @Parameters
        app: app component obj, already populated via app.import_files.
        """
        for dirpath, dirnames, filenames in walk(app.component_location + sep + "src"):
            for filename in filenames:
                if (filename in ("Xilinx.spec", "README.txt")):
                    LOG(f"Removing template file \"{filename}\" from {path.join(dirpath, filename)}")
                    app.remove_files(files=[path.join(dirpath, filename)])

        for dirpath, dirnames, filenames in walk(app.component_location + sep + "_ide"+ sep + "psinit"):
            for filename in filenames:
                if (filename not in ("psu_init.tcl", "ps7_init.tcl")):
                    LOG(f"Removing template file \"{filename}\" from {path.join(dirpath, filename)}")
                    app.remove_files(files=[path.join(dirpath, filename)])

    def checkOutSF(self, platforms=None, apps=None, skip_unbound_platforms=False,
                   incremental=False, allow_process_cleanup=False) -> int:
        """
        @Description
        Recreate a Vitis workspace from the parent repository's `src`
        folder. High-level orchestration only, one call per concern:
        _prepareWorkspace, _discoverAppsAndPlatforms, _extractPlatformMetadata,
        _buildPlatform (once per detected platform) and _buildApplication
        (once per detected app), see each for the actual steps.

        When `platforms`/`apps` are both empty/None (the default,
        full-checkout behavior), the whole workspace is wiped (see
        _prepareWorkspace) and every detected platform/application is
        rebuilt from scratch, as before. When either is non-empty, an
        existing workspace is reused as-is (see _openExistingWorkspace) and
        only the requested components are deleted/rebuilt: requesting a
        platform also rebuilds every application bound to it (unless `apps`
        narrows that down explicitly), and requesting an application also
        (re)builds the platform it resolves to, if not already targeted.
        Every other already-built component is left completely untouched -
        e.g. `--platform my_platform_variant_a` updates that one platform
        and whatever application uses it, without rebuilding
        my_platform/my_platform_variant_b or re-wiping the workspace.

        @Parameters
        platforms: platform names (as derived in _discoverAppsAndPlatforms,
                  e.g. "my_platform_variant_a") to selectively rebuild, or
                  None/empty for a full checkout.
        apps: application folder names under `src` to selectively rebuild,
             or None/empty for a full checkout.
        skip_unbound_platforms: if True, don't build any platform that isn't
                  referenced by at least one application's comp-settings.json
                  (see _getBoundPlatformNames), regardless of `platforms`/
                  `apps`/full-checkout mode. A platform named explicitly via
                  `platforms` is still built even if unbound, since that's an
                  explicit request.
        incremental: if True, an already-existing --platform/--app target is
                  reused in place (see _rebuildPlatformInPlace/
                  _rebuildApplicationInPlace) - re-synced and rebuilt without
                  deleting/recreating its component directory first, so
                  cmake's own incremental build only recompiles what actually
                  changed. Meant for quick source-edit-and-rebuild cycles;
                  targets that don't exist yet are always created fresh
                  regardless of this flag, and any HW/domain-level platform
                  change still needs a full (non-incremental) rebuild.
        allow_process_cleanup: if True, _prepareWorkspace/_setWorkspaceWithRetry
                  are allowed to force-stop pre-existing vitis/vitis-server
                  processes from the same install when a workspace wipe/select
                  fails (see stopDanglingVitisProcesses). Off by default: the
                  OS process list cannot tell an actual leftover from a
                  crashed run apart from an unrelated, still-active Vitis
                  session (the workspace is only bound to a running server
                  through a later gRPC call, never visible on its command
                  line), so this is opt-in and should only be enabled on a
                  machine/CI runner where no other Vitis session runs
                  concurrently.
        """
        platforms = set(platforms or [])
        apps = set(apps or [])
        selective = bool(platforms or apps)
        self._allowProcessCleanup = allow_process_cleanup

        script_path = path.dirname(path.abspath(__file__))
        repo_root = script_path[:script_path.rfind(sep)]
        if Workspace.DEBUG:
            date = datetime.now().strftime("%Y%m%d%I%M%S")
            ws_path = repo_root + f"{sep}ws" + f"_{date}"
        else:
            ws_path = repo_root + f"{sep}ws"
        repo_path = repo_root + sep + "repo"

        # Domain/BSP generation resolves its CMake specs file from
        # $ENV{ESW_REPO} (see the generated cortexa53_toolchain.cmake); point
        # it at our embeddedsw submodule checkout before the Vitis server
        # subprocess is spawned, since it inherits our process env only at
        # spawn time.
        environ["ESW_REPO"] = repo_path

        # Workaround for a real Vitis 2025.2 bug: the toolchain file Vitis
        # generates for each domain only sets the non-"_INIT"
        # CMAKE_<LANG>_FLAGS (with the correct -specs=... needed for newlib
        # syscall stubs), but CMake's own internal CMakeTestCCompiler
        # sanity check (run once per fresh BSP build dir, before our flags
        # are even considered) only honors the "_INIT" variants, which
        # CMake seeds from $ENV{CFLAGS}/$ENV{LDFLAGS} if set. Without this,
        # that sanity check can fail with "undefined reference to
        # _exit/_read/..." depending on unrelated session state. Setting
        # these ourselves makes the check deterministic regardless.
        # Only CFLAGS/CXXFLAGS are needed (not LDFLAGS too): CMake's
        # compiler-test step links via a single combined gcc/g++
        # invocation, so also setting LDFLAGS to the same value would pass
        # "-specs=nosys.specs" twice and break gcc's own spec merging
        # ("attempt to rename spec ... to already defined spec"). CXXFLAGS
        # is seeded independently of CFLAGS by CMake's C++ compiler test.
        environ["CFLAGS"] = "-specs=nosys.specs"
        environ["CXXFLAGS"] = "-specs=nosys.specs"

        dispose()

        LOG("Checking out Vitis project into the workspace...")
        client = create_client()
        try:
            if selective:
                self._openExistingWorkspace(client, ws_path)
            else:
                self._prepareWorkspace(client, ws_path)

            app_names, hw_platforms = self._discoverAppsAndPlatforms(repo_root + f"{sep}src")
            self._extractPlatformMetadata(hw_platforms)

            if selective:
                known_platform_names = {plt["name"] for plt in hw_platforms.values()}
                unknown_platforms = platforms - known_platform_names
                if unknown_platforms:
                    # An unknown --platform value must fail loudly instead
                    # of silently matching nothing: otherwise the run
                    # "succeeds" having rebuilt nothing for a typo'd name.
                    LOG(f"Unknown --platform value(s) {sorted(unknown_platforms)}: "
                       f"no such platform among {sorted(known_platform_names)}.")
                    return Workspace.FAILURE
                unknown_apps = apps - set(app_names)
                if unknown_apps:
                    # Same reasoning as unknown_platforms above: an unknown
                    # --app value would otherwise reach
                    # _resolveSelectiveTargets, which opens
                    # src/<value>/comp-settings.json directly and raises an
                    # unhandled FileNotFoundError instead of failing cleanly.
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

            for xsa_path, plt in hw_platforms.items():
                if selective and plt["name"] not in platforms:
                    # Not being rebuilt, but _buildApplication still needs
                    # "domain_name"/"xpfm" to bind any requested app to it.
                    self._setPlatformDomainInfo(client, plt)
                    continue
                explicitly_requested = selective and plt["name"] in platforms
                if (bound_platforms is not None and not explicitly_requested
                        and plt["name"] not in bound_platforms):
                    LOG(f"Skipping platform \"{plt['name']}\": not referenced by any "
                       f"application's comp-settings.json (--skip-unbound-platforms)")
                    continue
                # _buildPlatform may also create a separate "<name>_FSBL" app
                # component (see _buildZynqMPFsbl, zynquplus only) - both must
                # be deleted before a rebuild, else create_app_component fails
                # with "project ...\<name>_FSBL already exists".
                if incremental and plt["name"] in existing:
                    self._rebuildPlatformInPlace(client, plt)
                else:
                    for stale_name in (plt["name"], f"{plt['name']}_FSBL"):
                        if stale_name in existing:
                            LOG(f"Deleting existing component \"{stale_name}\" for rebuild...")
                            client.delete_component(name=stale_name)
                    self._buildPlatform(client, xsa_path, plt, repo_path)

            for app_name in app_names:
                if selective and app_name not in apps:
                    continue
                if incremental and app_name in existing:
                    self._rebuildApplicationInPlace(client, app_name, repo_root)
                else:
                    if app_name in existing:
                        LOG(f"Deleting existing application component \"{app_name}\" for rebuild...")
                        client.delete_component(name=app_name)
                    self._buildApplication(client, app_name, hw_platforms, repo_root)

            return Workspace.SUCCESS
        finally:
            # Always dispose the client/server connection, even if an
            # exception was raised above - otherwise the server process
            # (and any lock it holds on ws_path) is left dangling for the
            # next invocation, undermining the retry/dangling-process
            # handling added elsewhere in this file.
            dispose()

    def _getBoundPlatformNames(self, app_names, hw_platforms, repo_root) -> set:
        """
        @Description
        Determine which platforms (by name) are actually referenced by at
        least one application's comp-settings.json (see
        _resolveAppPlatform/getAppPlatformXsa), so checkOutSF's
        skip_unbound_platforms can avoid building platforms no application
        currently uses. Considers every detected application regardless of
        `apps`/`platforms` selection, since "bound" is a property of the
        whole `src` tree, not of what's currently being selectively rebuilt.

        @Parameters
        app_names: every application folder name under `src` (see
                  _discoverAppsAndPlatforms).
        hw_platforms: dict produced by _discoverAppsAndPlatforms/
                     _extractPlatformMetadata.
        repo_root: absolute path to the parent repository (parent of `src`).

        @Returns
        set of platform names bound to at least one application.
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
        """
        @Description
        Expand an explicit --platform/--app selective-rebuild request into
        the full, consistent set of platform/app names that must actually
        be rebuilt together: requesting a platform pulls in every
        application bound to it (unless `apps` was also given explicitly,
        in which case only those are added), and requesting an application
        pulls in the platform it resolves to. Split out of checkOutSF to
        keep that method focused on orchestration.

        @Parameters
        platforms: set of explicitly-requested platform names (may be empty).
        apps: set of explicitly-requested app names (may be empty).
        app_names: every application folder name under `src` (see
                  _discoverAppsAndPlatforms).
        hw_platforms: dict produced by _discoverAppsAndPlatforms/
                     _extractPlatformMetadata.
        repo_root: absolute path to the parent repository (parent of `src`).

        @Returns
        (platforms, apps) tuple of the expanded sets, or None if an
        explicitly-requested app's platform/xsa correlation could not be
        resolved (checkOutSF must fail before mutating any component in
        that case: continuing would delete the existing app - since it's
        still a selective rebuild target - without anything to rebuild it
        with, yet still return success).
        """
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
    This ~file~ can be used as a module or standalone
    py program. From a cmd-line: `vitis -s [<relative-or-absolute-path>]checkout.py`,
    where is the current directory from terminal process does not influence behavior
    of the above functionalities.

    With no arguments, the whole workspace is wiped and every detected
    platform/application is rebuilt from scratch (unchanged, full-checkout
    behavior). --platform/--app (either may be repeated) instead reuse the
    existing workspace and only rebuild the requested component(s) - see
    checkOutSF's docstring for the exact expansion rules.
    --skip-unbound-platforms prunes any platform not referenced by an
    application's comp-settings.json, in either mode. --incremental, only
    meaningful together with --platform/--app, rebuilds an already-existing
    target in place (re-synced sources + cmake's own incremental compile)
    instead of deleting/recreating its component directory - much faster
    for a quick source-edit-and-rebuild cycle. Examples (through
    _vitis.bat/.ps1/.sh, which forward any extra args here):
        _vitis.bat -v 2025.2 -s .\\checkout.py
        _vitis.bat -v 2025.2 -s .\\checkout.py --platform my_platform
        _vitis.bat -v 2025.2 -s .\\checkout.py --app my_app
        _vitis.bat -v 2025.2 -s .\\checkout.py --app my_app --incremental
        _vitis.bat -v 2025.2 -s .\\checkout.py --skip-unbound-platforms
    """
    parser = argparse.ArgumentParser(
        description="Recreate (or selectively rebuild) the Vitis workspace from src/."
        )
    parser.add_argument(
        "--platform", action="append", default=[], metavar="NAME",
        help="Only rebuild this platform (e.g. my_platform), plus any "
            "application bound to it, instead of wiping/rebuilding the whole "
            "workspace. May be repeated. Default: rebuild everything."
        )
    parser.add_argument(
        "--app", action="append", default=[], metavar="NAME",
        help="Only rebuild this application, plus the platform it resolves to "
            "(unless already covered by --platform), instead of wiping/rebuilding "
            "the whole workspace. May be repeated. Default: rebuild everything."
        )
    parser.add_argument(
        "--skip-unbound-platforms", action="store_true",
        help="Don't build any platform that isn't referenced by at least one "
            "application's comp-settings.json (see getAppPlatformXsa). A "
            "platform named explicitly via --platform is still built even if "
            "unbound, since that's an explicit request."
        )
    parser.add_argument(
        "--incremental", action="store_true",
        help="When rebuilding an already-existing --platform/--app target, "
            "reuse its component in place (re-sync sources + rebuild only) "
            "instead of deleting and recreating it from scratch, so cmake's "
            "own incremental build only recompiles what actually changed. "
            "Targets that don't exist yet are always created fresh. Any "
            "HW/domain-level platform change still needs a full rebuild."
        )
    parser.add_argument(
        "--allow-process-cleanup", action="store_true",
        help="Allow force-stopping pre-existing vitis/vitis-server processes "
            "from the same install if a workspace wipe/select fails. Off by "
            "default, since a pre-existing process could be an unrelated, "
            "still-active Vitis session rather than an actual leftover; only "
            "enable this on a machine/CI runner where no other Vitis session "
            "runs concurrently."
        )
    args = parser.parse_args()

    lcWs = Workspace()
    iRet = lcWs.checkOutSF(
        platforms=args.platform,
        apps=args.app,
        skip_unbound_platforms=args.skip_unbound_platforms,
        incremental=args.incremental,
        allow_process_cleanup=args.allow_process_cleanup
        )
    LOG("Checkout finished with status: " + str(iRet))
    sys.exit(iRet)
