
"""
    Company: Digilent RO
    Engineer: bs
    Usage: Vitis projects
    
    @Description
    This checkin.py has the same behavior
    as the previous checkin.tcl. It preserves
    workspace configuration & source files.
    
    @Insights
    Vitis v2024.1 has Python v3.8.3.
    Vitis v2025.1 has Python v3.13.0.
"""
from os import (chdir, getcwd, listdir,
                path, sep, makedirs, mkdir,
                access, chmod, R_OK, W_OK,
                walk, remove, stat)
from stat import (S_IWUSR, S_IRUSR)
from vitis import (_build, _server)
from pathlib import Path
from shutil import copy
from json import (JSONEncoder, JSONDecoder)
from re import (compile, escape, RegexFlag)
from sys import exit as sys_exit
from misc import (LOG, MapCmdLineOpts)

class UtilityWS:
    """
    @Description
    Extract data / Generate data from
    specific workspace files like *.spfm.

    @Insights
    comp-settings.json structure (example):
    {
        CFLAGS : ["", "", "", ...]
        TEMPLATE : ["..."],
        LFLAGS : ["", "", "", ...],
        PREV_BUILDSTATUS : ["Valid"],
        PART : ["<part-number>"]
    }
    """
    srvCl = None
    EMPTY_BUFFER = 0
    FAILURE = -1
    SUCCESS = 0
    COMP_TYPES = ["UNKNOWN", "AI_ENGINE", "PL_KERNEL", "HOST",
                  "HLS", "USER_MANAGED_KERNEL", "PLATFORM"
                  ]
    # configuration -> {} -> "componentType", "hostToolchainConfigurations"
    COMP_MISC = ["name", "type", "domain", "cpuInstance",
                 "cpuType", "os", "configuration", "domainRealName",
                 "applicationFlow"
                 ]
    IS_DIRS = False
    SET_IP_PORT = False

    def __init__(self, sIP="", sPort=""):
        """
        @Description
        This file has its location directory as the starting point,
        then `src`, `ws` need to exist before any checkin flow to
        happen. If these have not been created by default on a branch,
        all the checkin flow would be bypassed, then logging some Warning
        Message into default <stream-buff>, default is cmd-line.

        Other files are stored in self._lfConf that have valuable data for
        platform/project. Through vitis-py resources or custom logic can be
        extracted, this depends on what other features checkin flow needs
        to have. Class dependent variables are set, like COMP_TYPES, srvCl,
        these are `utilities` for vitis-workspaces.
        """
        # Always have a reference wd.
        self._wDir = path.dirname(path.realpath(__file__))
        # os.sep ~ platform dependent;
        self._pSubSw = self._wDir[:self._wDir.rfind(sep)]
        chdir(self._pSubSw)
        # Can be changed to other dir (new or existent);
        self._srcDir = "src"
        # App or prj ?
        # Set path for win/lnx "\\" or "/"; Use py-stdlib
        # functions to avoid manually use/change these OS differences.
        self._appDir = "ws"
        # '*' -> Substitute with platform name (example only), <vitis-comp>.json;
        self._lfConf = ["*.xpfm", "*.json", "*.cmake",
                        "*.yaml", "qemu_args.txt", "*.spfm",
                        "*.cfg"
                        ]
        self.wsJsonConf = "comp-settings.json"
        self.dConfWs = {}
        self._dPltAppCorr = {}
        self.enJsonObjFile = JSONEncoder(indent="\t", separators=(",", " : "))
        # Check for src and ws dirs.
        lcWsDir = path.join(self._pSubSw, self._appDir)
        self.kwCLO = {}
        # Extract cmd line parameters into kwCLO.
        MapCmdLineOpts(kwCLO=self.kwCLO)
        self.sIP = self.kwCLO["--ip"] if sIP == "" else sIP
        self.sPort = self.kwCLO["--port"] if sPort == "" else sPort
        if self.sIP != "" or self.sPort != "":
            UtilityWS.SET_IP_PORT = True
        # Server attributes can be used to attach to an existing server made
        # with Server class from vitis._server.
        if path.isdir(self._srcDir) and path.isdir(self._appDir):
            if UtilityWS.srvCl is None:
                try:
                    LOG(msg="Local server, starting Vitis server...")
                    # Init server with pre-defined args.
                    UtilityWS.srvCl = _server.Server(
                        port=None if self.sPort == "" else self.sPort,
                        host="localhost" if self.sIP == "" else self.sIP,
                        workspace=lcWsDir
                        )
                    UtilityWS.IS_DIRS = True
                except Exception as err:
                    LOG(msg=f"Error Local server: {err.__class__} {err.__context__}")

    def encJSON_Ws(self,
                   bdRes : list,
                   locations : list
                   ) -> int:
        """
        @Description
        Prepare build/ws metadata to be saved into a json file for
        each existent application. Get <app-dirname> with rfind +
        sep tweaks to create json file under it after data is
        prepared with self.prepDataStruct(). Module json has encoder +
        decoder classes to get/set json data structure, this format has
        been choosen because vitis generates similar files.

        @Parameters
        bdRes: List with build chace representative structure.
        location: Where the self.wsJsonConf will be saved.
        """
        idx = 0
        for location in locations:
            # Save apps/platforms into a json dt at checkin, with
            # flags, template !!, processor, target, ... etc.
            dirApp = location[location.rfind(sep) + 1:]
            sPrevWd = getcwd()
            chdir(dirApp)
            # Reset per-application: self.dConfWs is reused across every
            # location in this loop, so a setting present for a previous
            # app but absent from this one's obj.settings must not survive
            # (previously only the dirApp correlation key was cleared
            # below, leaking every other stale setting into this app's
            # generated comp-settings.json).
            self.dConfWs = {}
            iRet = self.prepDataStruct(bdRes[idx])
            # Add relative path to json settings.
            self.dConfWs[dirApp] = self._dPltAppCorr[dirApp]
            if iRet != UtilityWS.FAILURE:
                sFConfWs = self.enJsonObjFile.encode(self.dConfWs)
                with open(self.wsJsonConf, "w+", encoding="utf-8") as fd:
                    fd.write("\n" + sFConfWs + "\n")
                LOG(f"File sw{sep}src{sep}{dirApp}{sep}{self.wsJsonConf} has been created!")
            else:
                LOG("Data structure from Vitis modules has NOT been parsed correctly!")
                return UtilityWS.FAILURE
            chdir(sPrevWd)
            if idx < len(bdRes):
                idx = idx + 1
        return UtilityWS.SUCCESS

    def prepDataStruct(self,
                       obj
                       ) -> int:
        """
        @Description
        Only with a debugger it's easy to profile/study the nested data
        structure the obj parameter will hold. The below logic is depenent on
        obj, obj.settings is a subclass of type(obj), item.(key or value) are
        string, list respectively. len() or item.value.__len__() function can be
        used, item.value.__getitem__(idx) is class dependent, but can have an
        equivalent. Some item.value are of dim=0, so empty list is stored.

        @Parameters
        obj: The type of this item is dependent on vitis-py resources,
             it consists of a nested data structure.
        """
        # The separator used is similar to lnx platforms for item.value items.
        PTRN_EX = compile(escape("../"), RegexFlag.IGNORECASE)
        # Extract from a protobuff class metadata.
        for item in obj.settings:
            if len(item.value) != UtilityWS.EMPTY_BUFFER:
                # Drop any parent-relative ("../") entries wherever they
                # occur, regardless of how many values this setting has -
                # a single such entry is just as wrong to keep as one
                # among several.
                valLoc = [v for v in item.value if PTRN_EX.search(v) is None]
                if len(valLoc) == 1:
                    valLoc = valLoc[0]
                self.dConfWs[item.key] = valLoc if type(valLoc) is list else [valLoc]
            else:
                self.dConfWs[item.key] = []
        return UtilityWS.SUCCESS

    @property
    def dPltAppCorr(self) -> dict:
        """ Get reference for self._dPltAppCorr """
        return self._dPltAppCorr

    @property
    def appDir(self) -> str:
        """ Get reference for self._appDir """
        return self._appDir

    @property
    def lfConf(self) -> list:
        """ Get reference for self._lfConf """
        return self._lfConf

    @property
    def wDir(self) -> str:
        """ Get reference for self._wDir """
        return self._wDir

    @property
    def srcDir(self) -> str:
        """ Get reference for self._srcDir """
        return self._srcDir

    @property
    def pSubSw(self) -> str:
        """ Get reference for self._pSubSw """
        return self._pSubSw

class ConfigWS:
    """
    @Description
    Extract metadata with vitis tools,
    process them here and use them where it's needed.
    """
    def __init__(self,
                 lApps : list = None
                 ):
        """
        @Description
        This unit is mainly used to interact with vitis-py resources to
        extract different data from auto generated files. In some cases,
        it's easier to implement an indepentend logic for a certain feature.

        @Insights
        Apps are more important to have them stored.
        Not all vitis-py resources are easily to be used.
        From vitis :: component.py: get_app_config, set_sysroot, get_config_info,
                      get_ld_script, ... .
        Check out more resources from vitis-tools to encode medata into config_ws.json.
        """
        self._utilCfgWs = UtilityWS()
        self.bdComp = _build.Build(server=UtilityWS.srvCl)
        # Store all configs from apps.
        self._bdRes = []
        # Py makes here a reference automatically, _ref<...> just for intuitive distinction.
        self._refLApps = []
        if UtilityWS.IS_DIRS and lApps is not None:
            self.setApps(lApps)

    def setApps(self, lApps : list) -> None:
        self._refLApps = lApps
        for item in lApps:
            self._bdRes.append(
                self.bdComp.getAppConfig(
                    component_location=item
                    )
                )

    @property
    def dPltAppCorr(self) -> dict:
        """ Get reference for self._utilSFWs.dPltAppCorr """
        return self._utilCfgWs.dPltAppCorr

    @property
    def bdRes(self) -> list:
        """ Get reference for self._bdRes """
        return self._bdRes

    @property
    def refLApps(self) -> list:
        """ Get reference for self._refLApps """
        return self._refLApps

    @property
    def utilCfgWs(self):
        """ Get reference for self._utilCfgWs """
        return self._utilCfgWs

class SrcFilesWS:
    """
    @Description
    Copy apps source files into local dir 'src'
    that is present in all projects branches.
    """
    # Consts
    BUILD_FILE_IDX = 0
    COMP_FILE_IDX = 1
    # Predef vectors - some files need to have a standard name.
    # Note :: First and last item are always excluded.
    lsConfCpy = ["CMakeLists.txt", "vitis-comp.json", "*.cmake",
                 "*.ld", "Makefile", ".gitignore"
                 ]
    # Entries that can appear directly under an app's workspace <app>/src
    # dir that are Vitis-generated metadata, never actual application
    # source (mirrors checkout.py's _pruneStaleFiles rationale/set for the
    # same directory level - CMakeLists.txt is excluded separately below,
    # since it's already tracked as this app's own build file). Files not
    # listed here are gathered unconditionally (see gatherAppSrcCd): a
    # fixed extension allowlist previously stood in for this set, silently
    # dropping any valid file whose extension it did not anticipate (e.g.
    # ".hh"/".hxx" headers, ".inc" fragments, or binary assets), and - since
    # that same list drove cpySrcFiles' stale-file pruning - deleting any
    # such file's already checked-in copy on a repeated check-in.
    VITIS_GENERATED_SRC_ENTRIES = frozenset({
        "vitis-comp.json", "UserConfig.cmake", "app.yaml",
        ".clangd", "compile_commands.json", ".compile_commands"
        })
    HOFF_HDL = "*.xsa"
    FAILURE = -1
    SUCCESS = 0
    APP_SRCCODE = "src"
    # Manifest written by checkout.py (_buildPlatform) into a platform's
    # workspace component dir, recording the original "src" folder name the
    # xsa was checked out from (see checkout.py's ".digilent_source_dir").
    SRC_DIR_MANIFEST = ".digilent_source_dir"

    def __init__(self):
        """
        @Description
        Init base vectors that will hold paths to build, handoff, platform-dir,
        and app-src paths, these are important for preserving workspace for
        Vitis >= 2023.2. Getter are provided to take references to below lists,
        making them usable in other contexts.
        """
        self.utilSFWs = UtilityWS()
        # There are other extensions, check for a pattern if one is not in the below list.
        # Path(s) of build-system script;
        self._lsBldFl = []
        # Path(s) of xsa files;
        self._lsArchFl = []
        # Dir(s) for platforms (workspace component/xsa-stem names, used to
        # correlate an app's recorded ".xpfm" reference back to its xsa);
        self._lsArchPltDir = []
        # Dir(s) to check the platform's xsa file INTO under src (the
        # original source folder name, when checkout recorded one via
        # ".digilent_source_dir"; falls back to the workspace component
        # name otherwise). May differ from lsArchPltDir when the workspace
        # component name and the original checked-in folder name diverge
        # (e.g. platform disambiguation renamed the xsa stem).
        self._lsArchSrcDir = []
        # Buffer to store Path(s) of src files;
        self._lsTempSrcFl = []

    @property
    def dPltAppCorr(self) -> dict:
        """ Get reference for self.utilSFWs.dPltAppCorr """
        return self.utilSFWs.dPltAppCorr

    @property
    def appDir(self) -> str:
        """ Get reference for self.utilSFWs.appDir """
        return self.utilSFWs.appDir

    @property
    def wDir(self) -> str:
        """ Get reference for self.utilSFWs.wDir """
        return self.utilSFWs.wDir

    @property
    def pSubSw(self) -> str:
        """ Get reference for self.utilSFWs.pSubSw """
        return self.utilSFWs.pSubSw

    def collectCpyFiles(self,
                        lApps : list
                        ) -> int:
        """
        @Description
        Only in this function (+ self.cpySrcFiles, or others if the code is extended)
        are files copied through copy or copy2 stdlib functions. Keep things
        well structured to not make mistakes. Before copying files, some conditions
        need to be respected otherwise stdlib functions may raise exceptions.

        path.join function is mainly used to create paths that are system
        dependent, in some cases os.sep (separator) is used. Nr of platforms
        should be lower than that of applications of equal, but this case is treated
        anyway. Different tweaks with rfind and itemIdx are implemented to extract from
        lApps a certain path, in this function it is <app-dirname>.

        self.lsBldFl and self.lsArchFl contain paths to build, xsa files, these are copied
        directly within this function, but others are in self.cpySrcFiles to not have
        ultimately a large function. In case this function has copied everything we need
        it returns 0, otherwise -1 if some error occured. The error must not exit the program
        randomly, but close it without any undefined behavor.

        @Parameters
        lApps: ref to matrix with paths of files to be copied.
        """
        if lApps is None:
            return SrcFilesWS.FAILURE
        pTemp = path.join(self.utilSFWs.pSubSw, SrcFilesWS.APP_SRCCODE)
        dimLsBldFl = len(self.lsBldFl)
        dimLsArchFl = len(self.lsArchFl)
        # Fail only when there is truly nothing to check in: an unbound
        # platform with no application (dimLsBldFl == 0) is an explicitly
        # supported, valid workspace (see the independent platform-copy
        # loop below), so it must not be rejected here before that loop
        # ever gets to run.
        if (dimLsBldFl == UtilityWS.EMPTY_BUFFER and
            dimLsArchFl == UtilityWS.EMPTY_BUFFER
            ):
            return SrcFilesWS.FAILURE
        elif path.isdir(pTemp) is True:
            # Copy files into 'src' dir.
            chdir(pTemp)
            # Applications drive lApps/self.lsBldFl (1:1 by construction).
            for itemIdx in range(0, dimLsBldFl):
                strTempLoc = lApps[itemIdx][lApps[itemIdx].rfind(sep) + 1:]
                dTempLoc = path.join(strTempLoc, SrcFilesWS.APP_SRCCODE)
                # In py <= 3.8, certain modes for mkdir does not exist, so default one is used.
                if path.isdir(dTempLoc) is not True:
                    makedirs(dTempLoc)
                # Add owner read/write without dropping any existing
                # exec/group/other bits (copy() propagates this mode).
                chmod(self.lsBldFl[itemIdx], stat(self.lsBldFl[itemIdx]).st_mode | S_IRUSR | S_IWUSR)
                # Check for write protected file in self.lsBldFl.
                if access(self.lsBldFl[itemIdx], R_OK | W_OK):
                    # Should we use copy2 to preserve metadata instead of copy ?
                    copy(self.lsBldFl[itemIdx], dTempLoc)
                else:
                    LOG(f"File {self.lsBldFl[itemIdx]} is not writable and readable")
                # tuple(<app-dirname-src>, <app-dirname>)
                iRet = self.cpySrcFiles(itemIdx, lApps, (dTempLoc, strTempLoc))
            # Platforms (XSA handoff files): copied independently of the
            # application count/loop above, since a workspace can contain
            # more platforms than applications (e.g. an intentionally
            # unbound platform kept via --skip-unbound-platforms).
            # Multiple platforms can share one destination "src" dir
            # (valid XSA variants); precompute the still-current basenames
            # per dir so stale-cleanup below never deletes a variant.
            validXsaByDestDir = {}
            for pltIdx in range(0, dimLsArchFl):
                validXsaByDestDir.setdefault(self.lsArchSrcDir[pltIdx], set()).add(
                    path.basename(self.lsArchFl[pltIdx]))
            for pltIdx in range(0, dimLsArchFl):
                # Destination dir preserves the original checked-in "src"
                # folder name across renames of the workspace platform
                # component (see findPlatforms/lsArchSrcDir); using the
                # workspace name directly here would create a brand-new
                # dir and orphan the original one whenever they diverge.
                destPltDir = self.lsArchSrcDir[pltIdx]
                if path.isdir(destPltDir) is not True:
                    mkdir(destPltDir)
                else:
                    # Remove checked-in XSA(s) no longer current for this
                    # dir (e.g. renamed export), skipping any name still
                    # valid for another platform sharing this dir, so
                    # variants aren't deleted by each other's pass.
                    validNames = validXsaByDestDir[destPltDir]
                    for existing in listdir(destPltDir):
                        if (existing.lower().endswith(".xsa")
                                and existing not in validNames):
                            stalePath = path.join(destPltDir, existing)
                            remove(stalePath)
                            LOG(f"Removed stale checked-in XSA: {stalePath}")
                # Add owner read/write without dropping any existing
                # exec/group/other bits (copy() propagates this mode).
                chmod(self.lsArchFl[pltIdx], stat(self.lsArchFl[pltIdx]).st_mode | S_IRUSR | S_IWUSR)
                # Check for write protected file in self.lsArchFl.
                if access(self.lsArchFl[pltIdx], R_OK | W_OK):
                    copy(self.lsArchFl[pltIdx], destPltDir)
                else:
                    LOG(f"File {self.lsArchFl[pltIdx]} is not writable and readable")
            # Handoff (xsa) and build script files have been copied, src files too.
            LOG(f"Matching directories have been created in sw{sep}src")
            return SrcFilesWS.SUCCESS
        else:
            return SrcFilesWS.FAILURE

    def cpySrcFiles(self,
                    appIdx : int,
                    lApps : list,
                    locDuo : tuple
                    ) -> int:
        """
        @Description
        Current function is called in self.collectCpyFiles to divide
        some steps and limit function code. In this one, files corresponding
        to an application type are copied to 'src-<app-dirname>', <app-dirname>
        and <default-src> come in tuple form `locDuo`, because there can be some
        files which should be put in <app-dirname>, not <src>.

        srcDirLoc and LocFMisc are equal only when a file needs to be copied
        into <app-dirname>, like `.gitignore`, but these type of files are excluded
        by default (check out gatherAppOtherConf from Workspace which has repflOpt
        on False implicitly). Each file type is stored into a list, which will be
        iterated over with below 2x for structure.

        path.relpath against <app-dir-src> (lApps[appIdx] + APP_SRCCODE) is used to
        recover the *full* subdirectory chain that needs to be recreated under
        <app-dirname-src>, no matter how many levels deep a file is nested
        (e.g. src/utils/<any-further-nesting>/foo.c), not just the immediate
        parent dir. The one exception is an extra module directory (e.g.
        "my_shared_module", a small shared driver module) - the workspace
        nests it one level inside the component's own "src"
        (<app-dir-src>/<module>/...), matching where checkout.py's
        _importAppExtraModules put it, but it's checked in as a SIBLING of
        <app-dirname-src> instead, at <app-dirname>/<module>/..., per the
        checkout-side manifest (.digilent_extra_modules) recorded at the
        component root. makedirs (instead of mkdir) is used since more than
        one intermediate directory level may need to be created at once. If
        directories have been already created, then calling `makedirs` is
        avoided.

        @Parameters
        appIdx: integer for App(s) files.
        lApps: matrix with paths of files to be copied.
        locDuo: tuple with <app-dirname-src> on left, <app-dirname> on right ~ pair like.
        """
        # item ~ list
        # e.g. appIdx=0 is for App1.
        loc, locFMisc = locDuo
        appSrcRoot = path.join(lApps[appIdx], SrcFilesWS.APP_SRCCODE)
        # Extra module directories (see checkout.py's _importAppExtraModules)
        # are imported into the workspace nested one level inside the
        # component's own "src" (<component>/src/<module>/...), but are
        # checked in as a SIBLING of the app's own "src" folder, at
        # src/<app-dirname>/<module>/... - not inside it. Read back the
        # manifest checkout.py writes at the component root
        # (.digilent_extra_modules, see _writeExtraModulesManifest) so
        # those top-level directory names can be redirected to locFMisc
        # (the app root) instead of being nested under loc like ordinary
        # source files.
        extraModuleNames = set()
        manifestPath = path.join(lApps[appIdx], ".digilent_extra_modules")
        if path.isfile(manifestPath):
            with open(manifestPath, "r", encoding="utf-8") as f:
                extraModuleNames = {line.strip() for line in f if line.strip()}
        # Reconcile <app-dirname-src> (loc) against what will actually be
        # (re)copied this run: a repeated check-in must also remove any
        # previously checked-in source file whose workspace counterpart was
        # deleted/renamed since the last check-in, otherwise a later
        # checkout keeps restoring (and compiling) the stale copy forever.
        keepRelPaths = set()
        # Same reconciliation, but per extra module (see the docstring
        # above): each module's checked-in destination is
        # <app-dirname>/<module>, a SEPARATE directory from loc, so it
        # needs its own keep-set/prune pass below - otherwise a file
        # deleted/renamed inside a workspace extra module was never
        # pruned here, and the next checkout kept restoring/compiling the
        # stale sibling-module copy forever.
        moduleKeepRelPaths = {}
        for item in self._lsTempSrcFl[appIdx]:
            for subItem in item:
                if subItem.startswith(appSrcRoot + sep):
                    relPath = path.relpath(subItem, appSrcRoot)
                    moduleName, moduleSep, moduleRelPath = relPath.partition(sep)
                    if moduleName in extraModuleNames and moduleSep != "":
                        moduleKeepRelPaths.setdefault(moduleName, set()).add(moduleRelPath)
                    else:
                        keepRelPaths.add(relPath)
        # collectCpyFiles already (re)copied this app's build file (e.g.
        # CMakeLists.txt) directly into loc, right before calling this
        # function - it lives outside _lsTempSrcFl, so without this it
        # would immediately be treated as "no longer present" and deleted
        # by the pruning loop below on every check-in.
        keepRelPaths.add(path.basename(self.lsBldFl[appIdx]))
        if path.isdir(loc):
            for dirpath, _, filenames in walk(loc):
                for filename in filenames:
                    destAbs = path.join(dirpath, filename)
                    destRel = path.relpath(destAbs, loc)
                    if destRel not in keepRelPaths:
                        remove(destAbs)
                        LOG(f"Removed stale checked-in source no longer "
                           f"present in the workspace: {destAbs}")
        for moduleName in extraModuleNames:
            moduleDestDir = path.join(locFMisc, moduleName)
            if not path.isdir(moduleDestDir):
                continue
            keepModulePaths = moduleKeepRelPaths.get(moduleName, set())
            for dirpath, _, filenames in walk(moduleDestDir):
                for filename in filenames:
                    destAbs = path.join(dirpath, filename)
                    destRel = path.relpath(destAbs, moduleDestDir)
                    if destRel not in keepModulePaths:
                        remove(destAbs)
                        LOG(f"Removed stale checked-in extra module source "
                           f"no longer present in the workspace: {destAbs}")
        for item in self._lsTempSrcFl[appIdx]:
            for subItem in item:
                # Take all files from each sublist and copy them, preserving
                # any nr of nested dirs found between <app-dir-src> and a file.
                miscFileName = subItem[subItem.rfind(sep) + 1:]
                destRoot = loc
                inAppSrcRoot = subItem.startswith(appSrcRoot + sep)
                if inAppSrcRoot:
                    relPath = path.relpath(subItem, appSrcRoot)
                    if relPath.split(sep, 1)[0] in extraModuleNames:
                        destRoot = locFMisc
                    relDirLoc = path.dirname(relPath)
                else:
                    # File is not under <app-dir>/src (e.g. .gitignore sitting
                    # directly in <app-dirname>), handled by the misc case below.
                    relDirLoc = ""
                if relDirLoc not in ("", "."):
                    nLoc = path.join(destRoot, relDirLoc)
                    if path.isdir(nLoc) is not True:
                        makedirs(nLoc)
                    # Add owner read/write without dropping any existing
                    # exec/group/other bits (copy() propagates this mode).
                    chmod(subItem, stat(subItem).st_mode | S_IRUSR | S_IWUSR)
                    # Check for write protected file
                    if access(subItem, R_OK | W_OK):
                        copy(subItem, nLoc)
                    else:
                        LOG(f"File {subItem} is not writeable and readable")
                    continue
                # Add owner read/write without dropping any existing
                # exec/group/other bits (copy() propagates this mode).
                chmod(subItem, stat(subItem).st_mode | S_IRUSR | S_IWUSR)
                # Check for write protected file
                if access(subItem, R_OK | W_OK):
                    # Only a file gathered from OUTSIDE appSrcRoot (e.g. a
                    # dotfile sitting directly in <app-dirname>) uses the
                    # misc destination: a dotfile at the top of the app's
                    # own "src" folder is a real source file and must stay
                    # in loc, the only dir checkout.py re-imports.
                    if not inAppSrcRoot and miscFileName.startswith("."):
                        copy(subItem, locFMisc)
                    else:
                        copy(subItem, loc)
                else:
                    LOG(f"File {subItem} is not writeable and readable")
        return SrcFilesWS.SUCCESS

    @property
    def lsTempSrcFl(self) -> list:
        """ Get reference for self._lsTempSrcFl """
        return self._lsTempSrcFl

    @lsTempSrcFl.setter
    def lsTempSrcFl(self, val):
        """ This setter so far is used to initialize self._lsTempSrcFl with a list """
        self._lsTempSrcFl = val

    @property
    def lsBldFl(self) -> list:
        """ Get reference for self._lsBldFl """
        return self._lsBldFl

    @lsBldFl.setter
    def lsBldFl(self, val):
        """ This setter so far is used only to reset self._lsBldFl """
        self._lsBldFl = val

    @property
    def lsArchFl(self) -> list:
        """ Get reference for self._lsArchFl """
        return self._lsArchFl

    @lsArchFl.setter
    def lsArchFl(self, val):
        """ This setter so far is used only to reset self._lsArchFl """
        self._lsArchFl = val

    @property
    def lsArchPltDir(self) -> list:
        """ Get reference for self._lsArchPltDir """
        return self._lsArchPltDir

    @lsArchPltDir.setter
    def lsArchPltDir(self, val):
        """ This setter so far is used only to reset self._lsArchPltDir """
        self._lsArchPltDir = val

    @property
    def lsArchSrcDir(self) -> list:
        """ Get reference for self._lsArchSrcDir """
        return self._lsArchSrcDir

    @lsArchSrcDir.setter
    def lsArchSrcDir(self, val):
        """ This setter so far is used only to reset self._lsArchSrcDir """
        self._lsArchSrcDir = val

class Workspace:
    """
    @Description
    Extract/Store different files that
    are in every WS made with vitis >= v2023.2.
    """

    def __init__(self):
        """
        @Description
        Search and Store platforms, then applications, these functions should
        be linked with an attribute that will be added to <comp-settings>.json
        later, Applications[attr] <-> Platforms[attr]. #...

        Attributes self.sfWs and self.cfgWs hold data that needs to be copied/processed,
        from self.cfgWs different settings are preserved + references to <apps-dirname>.
        Both have setters/getters methods that are used to access various class dependent
        attributes (SrcFilesWS or ConfigWS) by reference, not redundant copies.
        """
        self.idxApp = 0
        self.sfWs = SrcFilesWS()
        # pattern vector; /i -> case insensitive; matches the generated
        # boot component's exact "<platform>_FSBL" suffix (see checkout.py's
        # _buildZynqMPFsbl), not merely any name containing "fsbl" anywhere
        # (which would wrongly exclude e.g. a user app named "fsblinky_test").
        self.lsExcludedApps = [compile(r"_fsbl$", RegexFlag.IGNORECASE)]
        # Set by processGatherFiles when an app's platform xsa could not be
        # resolved: checkInSF checks this and fails instead of writing a
        # comp-settings.json with a synthesized, known-nonexistent xsa path.
        self.bPlatformResolutionFailed = False
        if UtilityWS.IS_DIRS:
            self.findPlatforms()
            # Set multiple Utility ... ? 'fa(), ...'
            # ~ Set ConfigWS paths for platforms too ~
            self.cfgWs = ConfigWS()
            _lApps = self.findApplications()
            self.cfgWs.setApps(_lApps)
            LOG("All files have been collected!")

    def gatherAppSrcCd(self,
                       pSrcFl : str
                       ):
        """
        @Description
        Store in a list which is associated with an app, all the files
        that need to be copied to <sw-src>. Every regular file found under
        <app-dir>/src is gathered recursively, except this app's own build
        file (already tracked separately, see collectCpyFiles/lsBldFl) and
        a defined set of Vitis-generated metadata entries that can appear
        directly at this level alongside real source (see
        VITIS_GENERATED_SRC_ENTRIES).

        Walking the whole tree instead of matching only a fixed extension
        allowlist (the previous per-extension rglob loop, SrcFilesWS.
        lsSrcCpy) means a file whose extension that allowlist never
        anticipated (e.g. ".hh"/".hxx" headers, ".inc" fragments, binary
        assets) is no longer silently skipped - which, since this same
        file set also drives cpySrcFiles' stale-file pruning, previously
        deleted such a file's already checked-in copy on a repeated
        check-in.

        @Parameters
        pSrcFl: Path to src dir from <app-dir>.
        """
        pSrcFlLoc = path.join(pSrcFl, SrcFilesWS.APP_SRCCODE)
        buildFilePath = path.normpath(self.sfWs.lsBldFl[self.idxApp])
        srcFiles = []
        for dirpath, dirnames, filenames in walk(pSrcFlLoc):
            isTopLevel = dirpath == pSrcFlLoc
            if isTopLevel:
                dirnames[:] = [d for d in dirnames
                              if d not in SrcFilesWS.VITIS_GENERATED_SRC_ENTRIES]
            for filename in filenames:
                if isTopLevel and filename in SrcFilesWS.VITIS_GENERATED_SRC_ENTRIES:
                    continue
                fullPath = path.join(dirpath, filename)
                if path.normpath(fullPath) == buildFilePath:
                    continue
                srcFiles.append(fullPath)
        if len(srcFiles) != UtilityWS.EMPTY_BUFFER:
            # [[]] - type
            self.sfWs.lsTempSrcFl[self.idxApp].append(srcFiles)
        # [[App1],[App2],[App3], ...], where App1,App2,App3 are other lists with
        # paths of source files that need to be copied.

    def gatherAppOtherConf(self,
                           pSrcFl : str,
                           repflOpt : bool = False
                           ):
        """
        @Description
        Adds pSrcFl's own top-level ".gitignore" (SrcFilesWS.lsConfCpy[-1])
        to self.sfWs.lsTempSrcFl when repflOpt is set: it lives directly
        under <app-dirname>, not under <app-dirname>/src, so gatherAppSrcCd
        never encounters it. Everything under <app-dirname>/src itself
        (e.g. "*.cmake"/"*.ld"/Makefile files previously matched here by
        name) is now gathered unconditionally by gatherAppSrcCd instead of
        matched against a fixed allowlist in this function.

        @Parameters
        pSrcFl: Path to src dir from <app-dir>.
        repflOpt: By default it is on '0', this avoids storing
                  files like .gitignore, but others can be added/ignored.
        """
        if len(self.sfWs.lsTempSrcFl[self.idxApp]) != UtilityWS.EMPTY_BUFFER:
            # Source files should have been stored by now.
            if repflOpt:
                pGIgn = path.join(pSrcFl, SrcFilesWS.lsConfCpy[-1])
                if path.exists(pGIgn) is True:
                    self.sfWs.lsTempSrcFl[self.idxApp].append([pGIgn])

    def processGatherFiles(self,
                           cmpFile : list,
                           dJsonData : dict,
                           pItem : str,
                           lsDirApps : list
                           ) -> int:
        """
        @Description
        Filter Vitis applications files, gather source files + other configs,
        findApplications uses it, but functions should have a limited no. or lines.
        Check description from it.

        Only "standalone" (bare-metal) applications are checked in: checkout.py
        only ever reconstructs "standalone" domains (see _buildPlatform), and
        an actual Linux application needs a "linux" domain/template plus a
        sysroot, none of which these scripts set up. Silently checking such
        an app in anyway would let a later checkout rebuild it against a
        standalone BSP instead, replacing its real target without any
        indication something went wrong - so it is rejected here instead
        (see dJsonData["os"], vitis-comp.json's own recorded OS). HLS
        components ("type" == "HLS") are excluded outright, not just
        redirected to the "standalone" path: they are not application
        components, may lack the "platform"/"os" fields read below, and
        checkout.py always recreates any accepted component with
        create_app_component(..., template="empty_application"), so an
        HLS component could never round-trip correctly anyway.
        """
        NOT_PATH = -1
        if dJsonData["type"] == "HLS":
            # TODO: add a dedicated HLS check-in/checkout round-trip path
            # (HLS components use their own kernel template/build flow,
            # not "empty_application") instead of excluding them outright.
            appName = pItem[pItem.rfind(sep) + 1:]
            LOG(f"Skipping application \"{appName}\": HLS components are not "
               f"bare-metal applications and checkout.py has no dedicated HLS "
               f"check-in/checkout path; it must be checked in/managed separately.")
            return
        if (len(cmpFile) != UtilityWS.EMPTY_BUFFER and
            (dJsonData["type"] == "HOST" or dJsonData["type"] == "UNKNOWN")
            ):
            # Associate application with its platform.
            appName = pItem[pItem.rfind(sep) + 1:]
            appOs = dJsonData.get("os", "standalone")
            if appOs != "standalone":
                # TODO: add real non-"standalone" (e.g. "linux") app
                # support - a "linux" domain/template plus sysroot setup
                # on the checkout.py side - instead of rejecting here.
                LOG(f"Skipping application \"{appName}\": checking in a "
                   f"\"{appOs}\" application is not supported (checkout.py "
                   f"only reconstructs \"standalone\" bare-metal domains); "
                   f"it must be checked in/managed separately.")
                return
            lHwPlt = dJsonData["platform"]
            # This idx has two uses, one for path like values in "platform"
            # and the second one if there is directly the name of platform.
            idxIfExXpfm = lHwPlt.rfind(".")
            # Path to .xpfm file only if it exists, can be used if it's -1 though
            idxPltName = lHwPlt.rfind(sep)
            if idxPltName == NOT_PATH and idxIfExXpfm == NOT_PATH:
                # Overwrite if necessary
                idxIfExXpfm = len(lHwPlt)
            pltName = lHwPlt[idxPltName + 1:idxIfExXpfm]
            # Pay attention which Utility object is used, bcs encJSON_Ws depends on it.
            # Look up the actual XSA discovered for this platform (see
            # findPlatforms) instead of assuming its filename matches the
            # platform name: checkout disambiguates duplicate XSA stems by
            # renaming the *platform*, so the real XSA basename can differ,
            # and a synthesized "<platform>.xsa" guess can point nowhere.
            relPathPlt = None
            for archIdx, archPltDir in enumerate(self.sfWs.lsArchPltDir):
                if archPltDir == pltName:
                    actualXsaName = path.basename(self.sfWs.lsArchFl[archIdx])
                    # Use the actual checked-in destination directory (see
                    # findPlatforms/lsArchSrcDir), not the workspace
                    # platform name: when they diverge (e.g. checkout
                    # disambiguated the platform's own name from its
                    # original "src" folder), pltName points nowhere under
                    # the checked-in src tree the next checkout will see.
                    destDir = self.sfWs.lsArchSrcDir[archIdx]
                    relPathPlt = SrcFilesWS.APP_SRCCODE + sep + destDir + sep + actualXsaName
                    break
            if relPathPlt is None:
                # Do not synthesize a guessed path here: it is already
                # known not to exist (findPlatforms did not discover this
                # platform), and writing it into comp-settings.json would
                # let check-in report success while producing a backup
                # checkout.py can never resolve. Fail check-in instead.
                LOG(f"Application \"{appName}\" references platform \"{pltName}\" "
                   f"which was not found among the discovered platforms; "
                   f"cannot check in a valid xsa correlation for it.")
                self.bPlatformResolutionFailed = True
                return SrcFilesWS.FAILURE
            # Preserve the exact processor/domain this app was bound to
            # (e.g. "psu_cortexa53_0" vs "psu_cortexr5_0" on a
            # multi-processor xsa) alongside the xsa correlation, so
            # checkout.py rebuilds it against the same domain instead of
            # whichever processor its own HW metadata extraction happens
            # to expose first (see checkout.py's getAppTargetProc). "os" is
            # also recorded (even though only "standalone" ever reaches
            # this point today, see the appOs check above) so checkout.py
            # can independently refuse to silently rebuild an app as
            # "standalone" if a comp-settings.json ever reaches it with a
            # different recorded OS (e.g. checked in by an older/patched
            # checkin.py, or a legacy check-in predating this guard),
            # instead of relying solely on this check-in-time rejection.
            cpuInstance = dJsonData.get("cpuInstance", "")
            self.cfgWs.utilCfgWs.dPltAppCorr[appName] = {
                "xsa": relPathPlt,
                "cpu_instance": cpuInstance,
                "os": appOs
                }
            # Resolve the canonical build file directly at
            # <app-dir>/src/CMakeLists.txt instead of an app-wide recursive
            # search: an extra module (a supported, separate top-level dir
            # under the app, see checkout.py's _importAppExtraModules) can
            # contain its own CMakeLists.txt, and filesystem traversal
            # order is not guaranteed, so picking rglob's first result
            # could silently select the wrong file instead of the app's own.
            canonicalBuildFile = path.join(pItem, SrcFilesWS.APP_SRCCODE,
                                           SrcFilesWS.lsConfCpy[SrcFilesWS.BUILD_FILE_IDX])
            if path.isfile(canonicalBuildFile):
                lsDirApps.append(pItem)
                self.sfWs.lsBldFl.append(canonicalBuildFile)
                self.sfWs.lsTempSrcFl.append([])
                # Collect source files from <app-dir>/src.
                self.gatherAppSrcCd(pItem)
                self.gatherAppOtherConf(pItem)
                self.idxApp = self.idxApp + 1

    def findApplications(self) -> list:
        """
        @Description
        Applications have a vitis-comp.json file that is checked to
        validate if it's platform or app. SrcFilesWS.lsConfCpy vector
        on idx=1 has predefined this file. It appears in <platform-dirname>
        too when an application is created by vitis.
        
        Path("<path>").rglob("<file-pattern>") searches in "<path>" all
        occurences of "<file-patter>". It returns a generator that can be
        casted to a vector/list obj. Therefore, indexing the wanted element,
        such as CMakeLists.txt or *.json.
        
        Apps are counted with self.idxApp and passed to self.gatherAppOtherConf,
        Some sort of correlation can be done to know which app has a certain
        platform. With a JSONDecoder, file-buffer is read then passed to
        <jsondecoder-obj>.decode func to get {[keys...] : [values...]} struct.
        """
        IDX_VCOMP = 0
        self.sfWs.lsBldFl = []
        # No intermediate dirs, levelDepth ~ 1;
        chdir(self.sfWs.appDir)
        # Store apps build files.
        lsDirApps = []
        pCwd = getcwd()
        for item in listdir(pCwd):
            if path.isdir(item) is False or item.startswith("."): continue
            # Get build file ~ maybe check if it exists ?
            # Check for vitis-comp.json or other files specific to an application.
            pItem = path.join(pCwd, item)
            cmpFile = list(Path(pItem).rglob(
                            SrcFilesWS.lsConfCpy[SrcFilesWS.COMP_FILE_IDX]))
            if len(cmpFile) == UtilityWS.EMPTY_BUFFER: continue
            strCmpFile = str(cmpFile[IDX_VCOMP])
            # Extract type of component; this can be moved to UtilityWS.
            # Extras: An app can have multiple templates though, but vitis-py-tools bugs
            # prevent it from having them set, except <hello-world>.
            dJsonData = JSONDecoder().decode(open(strCmpFile).read())
            # For now just one type of application is needed to not be saved.
            mtName = self.lsExcludedApps[0].search(dJsonData["name"])
            if mtName is not None: continue
            # Pass parameters by ref with the same names.
            iRet = self.processGatherFiles(cmpFile, dJsonData, pItem, lsDirApps)
        # Get back to 'sw submodule'.
        chdir(self.sfWs.pSubSw)
        LOG("Number of applications found: " + str(len(lsDirApps)))
        return lsDirApps

    def findPlatforms(self):
        """
        @Description
        Working dir when this func is called must be sw submodule. Anyway,
        sw structure had been verified long before calling findPlatforms.
        Only components whose vitis-comp.json declares type "PLATFORM" are
        considered (an application can legitimately carry its own xsa
        asset). Iterate over its content to find *.xsa file(s): exactly one
        is required, its path is stored in self.sfWs.lsArchFl,
        <platform-dirname> into self.sfWs.lsArchPltDir respectively;
        ambiguous (more than one) or missing xsa's are rejected/skipped
        instead of guessing. self.sfWs.lsArchSrcDir gets the directory the
        xsa should be checked in UNDER src: the original source folder name
        recorded by checkout.py in SRC_DIR_MANIFEST when available (so a
        rename/disambiguation of the workspace platform name, e.g. two
        xsa's sharing a stem, does not make check-in create a brand-new
        "src" directory and orphan the original one, which would leave a
        stale checked-in xsa for the next checkout to rediscover as a bogus
        duplicate platform), otherwise the workspace platform dirname
        itself (first-time check-in of a new platform).
        """
        IDX_FRENC = 0
        IDX_VCOMP = 0
        self.sfWs.lsArchFl, self.sfWs.lsArchPltDir, self.sfWs.lsArchSrcDir = [], [], []
        chdir(self.sfWs.appDir)
        pCwd = getcwd()
        for item in listdir(pCwd):
            if path.isdir(item) is False or item.startswith("."): continue
            pItem = path.join(pCwd, item)
            # Only a vitis-comp.json-declared "PLATFORM" component is a
            # platform: an application can legitimately carry its own xsa
            # asset (e.g. a reference/test copy), and without this check
            # it would be miscorrelated as a platform below.
            cmpFile = list(Path(pItem).rglob(
                            SrcFilesWS.lsConfCpy[SrcFilesWS.COMP_FILE_IDX]))
            if len(cmpFile) == UtilityWS.EMPTY_BUFFER: continue
            dJsonData = JSONDecoder().decode(open(str(cmpFile[IDX_VCOMP])).read())
            if dJsonData.get("type") != "PLATFORM": continue
            # Get handoff ~ can be more;
            archFile = list(Path(pItem).rglob(SrcFilesWS.HOFF_HDL))
            if len(archFile) == UtilityWS.EMPTY_BUFFER:
                # A validated "PLATFORM" component with no discoverable xsa
                # cannot be checked in at all, bound to an app or not: an
                # unbound platform never goes through processGatherFiles'
                # own resolution failure path, so without this check-in
                # would silently drop it and still report success. Record
                # the failure and abort instead of skipping quietly.
                LOG(f"Platform \"{item}\" has no discoverable xsa file; "
                   f"cannot check it in.")
                self.bPlatformResolutionFailed = True
                continue
            elif len(archFile) > 1:
                # A real platform can legitimately contain more than one
                # xsa (e.g. the originally imported design alongside a
                # regenerated export copy); picking an arbitrary one (rglob
                # order is not guaranteed) risks copying/correlating the
                # wrong handoff. Reject rather than guess. Same as the
                # zero-xsa branch above, an unbound platform never reaches
                # processGatherFiles' own resolution failure path, so mark
                # discovery as failed here too instead of silently
                # dropping the platform while check-in still succeeds.
                LOG(f"Platform \"{item}\" contains {len(archFile)} xsa files; "
                   f"cannot unambiguously determine its authoritative "
                   f"handoff, skipping it.")
                self.bPlatformResolutionFailed = True
                continue
            else:
                # Populate with xsa files path.
                self.sfWs.lsArchFl.append(str(archFile[IDX_FRENC]))
                # Preserve platforms dirs.
                self.sfWs.lsArchPltDir.append(item)
                # Original source folder, if checkout.py recorded one.
                srcDirManifest = path.join(pItem, SrcFilesWS.SRC_DIR_MANIFEST)
                srcDir = item
                if path.isfile(srcDirManifest):
                    with open(srcDirManifest, "r") as f:
                        manifestDir = f.read().strip()
                    if manifestDir != "":
                        srcDir = manifestDir
                self.sfWs.lsArchSrcDir.append(srcDir)
        # Get back to 'sw submodule'.
        chdir(self.sfWs.pSubSw)
        LOG("Number of platforms found: " + str(len(self.sfWs.lsArchPltDir)))

    def checkInSF(self) -> int:
        """
        @Description
        Just one obj to Workspace class is needed to collect/prepare
        source/config files that will be copied with collectCpyFiles func.

        Ideal structure of sw submodule:
        ++++++++++++++++++++++++++++++++++++++++++++
        + sw                                       +
        +  |- src                                  +
        +      |- <checked-in-platforms-dirs>      +
        +      |- <checked-in-applications-dirs>   +
        +  |- scripts                              +
        +      |- __pychace__                      +
        +            |- <precompiled-py-files>     +
        +      |- checkin.py                       +
        +      |- checkout.py                      +
        +      |- <other-files>                    +
        +  |- ws                                   +
        +      |- <platform-dirs>                  +
        +           |- ... <specific-files>        +
        +      |- <application-dirs>               +
        +           |- ... <specific-files>        +
        +  |- <other-files>                        +
        ++++++++++++++++++++++++++++++++++++++++++++
        """
        if not UtilityWS.IS_DIRS:
            return UtilityWS.FAILURE
        try:
            if self.bPlatformResolutionFailed:
                # An app's platform xsa could not be resolved during
                # discovery (see processGatherFiles); do not copy/encode
                # anything that would produce an unusable checked-in state.
                LOG("Aborting check-in: one or more applications' platform "
                   "xsa could not be resolved.")
                iRet = UtilityWS.FAILURE
            else:
                iRet = self.sfWs.collectCpyFiles(self.cfgWs.refLApps)
                if iRet == UtilityWS.SUCCESS:
                    iRet = self.cfgWs.utilCfgWs.encJSON_Ws(self.cfgWs.bdRes, locations=self.cfgWs.refLApps)
                else:
                    LOG("collectCpyFiles failed, skipping metadata encoding.")
        finally:
            # Stop the locally started Vitis server on every exit path,
            # including an exception raised above: a copy/JSON/Vitis error
            # must not leave the server process and workspace lock dangling.
            # Check if port or ip have been assigned manually.
            if not UtilityWS.SET_IP_PORT:
                UtilityWS.srvCl.stop()
        # Clean up .wsdata after vitis-server shutdown if it exists.
        return iRet

if __name__ == "__main__":
    """
    @Description
    This ~file~ can be used as a module or standalone
    py program. From a cmd-line: `vitis -s [<relative-or-absolute-path>]checkin.py`,
    where is the current directory from terminal process does not influence behavior
    of the above functionalities.
    """
    lcWs = Workspace()
    iRet = lcWs.checkInSF()
    LOG("Check in file finished with status: " + str(iRet))
    sys_exit(iRet)
