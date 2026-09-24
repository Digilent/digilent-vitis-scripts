
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
                walk, remove)
from stat import (S_IWUSR, S_IRUSR)
from vitis import (_build, _server)
from pathlib import Path
from shutil import copy
from json import (JSONEncoder, JSONDecoder)
from re import (compile, escape, RegexFlag)
from sys import version_info, exit as sys_exit
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
    lsSrcCpy = ["*.h", "*.hpp", "*.c",
                "*.cpp", "*.cc", "*.S",
                "*.scat", "*.mk", "*.C",
                "*.cxx", "*.c++", "*.s"
                ]
    HOFF_HDL = "*.xsa"
    FAILURE = -1
    SUCCESS = 0
    APP_SRCCODE = "src"
    EXCLUDE_BSYSF_IDX = 1

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
        # Dir(s) for platforms;
        self._lsArchPltDir = []
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
        if lApps is None or len(lApps) == UtilityWS.EMPTY_BUFFER:
            return SrcFilesWS.FAILURE
        pTemp = path.join(self.utilSFWs.pSubSw, SrcFilesWS.APP_SRCCODE)
        dimLsBldFl = len(self.lsBldFl)
        dimLsArchFl = len(self.lsArchFl)
        # These lists should have been populated by now.
        if (dimLsBldFl == UtilityWS.EMPTY_BUFFER or
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
                # Impose read and write access for current files.
                chmod(self.lsBldFl[itemIdx], S_IRUSR | S_IWUSR)
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
            for pltIdx in range(0, dimLsArchFl):
                if path.isdir(self.lsArchPltDir[pltIdx]) is not True:
                    mkdir(self.lsArchPltDir[pltIdx])
                else:
                    # Remove any previously checked-in XSA(s) for this
                    # platform dir before copying the current one: if the
                    # export was renamed since the last check-in, the old
                    # file would otherwise remain alongside the new one,
                    # and checkout discovers every ".xsa" it finds, turning
                    # the stale leftover into a bogus extra platform.
                    newXsaName = path.basename(self.lsArchFl[pltIdx])
                    for existing in listdir(self.lsArchPltDir[pltIdx]):
                        if (existing.lower().endswith(".xsa")
                                and existing != newXsaName):
                            stalePath = path.join(self.lsArchPltDir[pltIdx], existing)
                            remove(stalePath)
                            LOG(f"Removed stale checked-in XSA: {stalePath}")
                # Impose read and write access for current files.
                chmod(self.lsArchFl[pltIdx], S_IRUSR | S_IWUSR)
                # Check for write protected file in self.lsArchFl.
                if access(self.lsArchFl[pltIdx], R_OK | W_OK):
                    copy(self.lsArchFl[pltIdx], self.lsArchPltDir[pltIdx])
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
        (e.g. src/tac5x1x_tac5142/<any-further-nesting>/tac5x1x_tac5142.c),
        not just the immediate parent dir. makedirs (instead of mkdir) is used
        since more than one intermediate directory level may need to be created
        at once. If directories have been already created, then calling
        `makedirs` is avoided.

        @Parameters
        appIdx: integer for App(s) files.
        lApps: matrix with paths of files to be copied.
        locDuo: tuple with <app-dirname-src> on left, <app-dirname> on right ~ pair like.
        """
        # item ~ list
        # e.g. appIdx=0 is for App1.
        loc, locFMisc = locDuo
        appSrcRoot = path.join(lApps[appIdx], SrcFilesWS.APP_SRCCODE)
        # Reconcile <app-dirname-src> (loc) against what will actually be
        # (re)copied this run: a repeated check-in must also remove any
        # previously checked-in source file whose workspace counterpart was
        # deleted/renamed since the last check-in, otherwise a later
        # checkout keeps restoring (and compiling) the stale copy forever.
        keepRelPaths = set()
        for item in self._lsTempSrcFl[appIdx]:
            for subItem in item:
                if subItem.startswith(appSrcRoot + sep):
                    keepRelPaths.add(path.relpath(subItem, appSrcRoot))
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
        for item in self._lsTempSrcFl[appIdx]:
            for subItem in item:
                # Take all files from each sublist and copy them, preserving
                # any nr of nested dirs found between <app-dir-src> and a file.
                miscFileName = subItem[subItem.rfind(sep) + 1:]
                if subItem.startswith(appSrcRoot + sep):
                    relDirLoc = path.dirname(path.relpath(subItem, appSrcRoot))
                else:
                    # File is not under <app-dir>/src (e.g. .gitignore sitting
                    # directly in <app-dirname>), handled by the misc case below.
                    relDirLoc = ""
                if relDirLoc not in ("", "."):
                    nLoc = path.join(loc, relDirLoc)
                    if path.isdir(nLoc) is not True:
                        makedirs(nLoc)
                    # Impose read and write access for current files.
                    chmod(subItem, S_IRUSR | S_IWUSR)
                    # Check for write protected file
                    if access(subItem, R_OK | W_OK):
                        copy(subItem, nLoc)
                    else:
                        LOG(f"File {subItem} is not writeable and readable")
                    continue
                # Impose read and write access for current files.
                chmod(subItem, S_IRUSR | S_IWUSR)
                # Check for write protected file
                if access(subItem, R_OK | W_OK):
                    # Prefix - other misc files can exist ... tp(".","")
                    if miscFileName.startswith("."):
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
        that need to be copied to <sw-src>. srcFiles holds all the encountered
        header files for example.

        @Parameters
        pSrcFl: Path to src dir from <app-dir>.
        """
        pSrcFlLoc = path.join(pSrcFl, SrcFilesWS.APP_SRCCODE)
        pCwd = getcwd()
        chdir(pSrcFlLoc)
        # Can store not just SrcFilesWS.lsSrcCpy '*.<some-extension>'.
        for item in SrcFilesWS.lsSrcCpy:
            # rglob only accepts case_sensitive= from py v3.12; Vitis 2024.1
            # ships Python 3.8.3, so gate the keyword by version instead of
            # always passing it (which raises TypeError on older pythons).
            if version_info >= (3, 12):
                srcFiles = list(Path(pSrcFlLoc).rglob(item, case_sensitive=True))
            else:
                srcFiles = list(Path(pSrcFlLoc).rglob(item))
            if len(srcFiles) != UtilityWS.EMPTY_BUFFER:
                # [[]] - type
                self.sfWs.lsTempSrcFl[self.idxApp].append([str(itm) for itm in srcFiles])
        # [[App1],[App2],[App3], ...], where App1,App2,App3 are other lists with
        # paths of source files that need to be copied.
        chdir(pCwd)

    def gatherAppOtherConf(self,
                           pSrcFl : str,
                           repflOpt : bool = False
                           ):
        """
        @Description
        Files from SrcFilesWS.lsConfCpy are added to self.sfWs.lsTempSrcFl if
        they are found. Some are excluded with "slices" <idx-prefix> : <idx-suffix>.
        In some cases prefix/suffix are put directly as values, SrcFilesWS.lsConfCpy
        should be edited accordingly.

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
            pSrcFlLoc = path.join(pSrcFl, SrcFilesWS.APP_SRCCODE)
            pCwd = getcwd()
            chdir(pSrcFlLoc)
            # Avoid build-sys file from first pos.
            for item in SrcFilesWS.lsConfCpy[SrcFilesWS.EXCLUDE_BSYSF_IDX + 1:-1]:
                srcFiles = list(Path(pSrcFlLoc).rglob(item))
                if len(srcFiles) != UtilityWS.EMPTY_BUFFER:
                    # [[]] - type
                    self.sfWs.lsTempSrcFl[self.idxApp].append([str(itm) for itm in srcFiles])
            chdir(pCwd)

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
        """
        NOT_PATH = -1
        if (len(cmpFile) != UtilityWS.EMPTY_BUFFER and
            ((dJsonData["type"] == "HOST" or
              dJsonData["type"] == "HLS") or
              dJsonData["type"] == "UNKNOWN")
            ):
            # Associate application with its platform.
            appName = pItem[pItem.rfind(sep) + 1:]
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
                    relPathPlt = SrcFilesWS.APP_SRCCODE + sep + pltName + sep + actualXsaName
                    break
            if relPathPlt is None:
                LOG(f"Application \"{appName}\" references platform \"{pltName}\" "
                   f"which was not found among the discovered platforms; its "
                   f"comp-settings.json xsa correlation entry may be wrong!")
                relPathPlt = SrcFilesWS.APP_SRCCODE + sep + pltName + sep + pltName + ".xsa"
            self.cfgWs.utilCfgWs.dPltAppCorr[appName] = relPathPlt
            # Search for build file.
            bFile = list(Path(pItem).rglob(
                            SrcFilesWS.lsConfCpy[SrcFilesWS.BUILD_FILE_IDX]))
            # Just one element should be in the list.
            if len(bFile) != UtilityWS.EMPTY_BUFFER:
                lsDirApps.append(pItem)
                # Populate in SrcFilesWS scope, SrcFilesWS.BUILD_FILE_IDX or simply 0.
                self.sfWs.lsBldFl.append(path.join(pItem, str(bFile[SrcFilesWS.BUILD_FILE_IDX])))
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
        Iterate over all content of <ws> to find *.xsa file(s). Only the first
        file found is stored in self.sfWs.lsArchFl, <platform-dirname> into
        self.sfWs.lsArchPltDir respectively.
        """
        IDX_FRENC = 0
        self.sfWs.lsArchFl, self.sfWs.lsArchPltDir = [], []
        chdir(self.sfWs.appDir)
        pCwd = getcwd()
        for item in listdir(pCwd):
            if path.isdir(item) is False or item.startswith("."): continue
            # Get handoff ~ can be more;
            pItem = path.join(pCwd, item)
            archFile = list(Path(pItem).rglob(SrcFilesWS.HOFF_HDL))
            # Just one element should be in the list.
            # Maybe check for vitis-comp.json or other files specific to a platform ?
            if len(archFile) != UtilityWS.EMPTY_BUFFER:
                # Populate with xsa files path.
                self.sfWs.lsArchFl.append(str(archFile[IDX_FRENC]))
                # Preserve platforms dirs.
                self.sfWs.lsArchPltDir.append(item)
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
