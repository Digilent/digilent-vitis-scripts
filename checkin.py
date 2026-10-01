
"""
Company: Digilent RO
Engineer: bs
Usage: Vitis projects
@Description Check in Vitis workspace configuration and source files.
"""
from os import (chdir, getcwd, listdir,
                path, sep, makedirs, mkdir,
                access, chmod, R_OK, W_OK,
                walk, remove, stat)
from stat import (S_IWUSR, S_IRUSR)
from vitis import (_build, _server)
from pathlib import Path
from shutil import copy, rmtree
from json import (JSONEncoder, JSONDecoder)
from re import (compile, escape, RegexFlag)
from sys import exit as sys_exit
from misc import (LOG, MapCmdLineOpts)

class UtilityWS:
    """
    @Description
    Manage workspace metadata helpers.
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
        Initialize workspace paths and the local Vitis server.

        @Parameters
        sIP: Optional server host override.
        sPort: Optional server port override.
        """
        # Working directory.
        self._wDir = path.dirname(path.realpath(__file__))
        self._pSubSw = self._wDir[:self._wDir.rfind(sep)]
        chdir(self._pSubSw)
        self._srcDir = "src"
        # Workspace apps live under "ws".
        self._appDir = "ws"
        self._lfConf = ["*.xpfm", "*.json", "*.cmake",
                        "*.yaml", "qemu_args.txt", "*.spfm",
                        "*.cfg"
                        ]
        self.wsJsonConf = "comp-settings.json"
        self.dConfWs = {}
        self._dPltAppCorr = {}
        self.enJsonObjFile = JSONEncoder(indent="\t", separators=(",", " : "))
        lcWsDir = path.join(self._pSubSw, self._appDir)
        self.kwCLO = {}
        MapCmdLineOpts(kwCLO=self.kwCLO)
        self.sIP = self.kwCLO["--ip"] if sIP == "" else sIP
        self.sPort = self.kwCLO["--port"] if sPort == "" else sPort
        if self.sIP != "" or self.sPort != "":
            UtilityWS.SET_IP_PORT = True
        # Reuse or start the workspace server.
        if path.isdir(self._srcDir) and path.isdir(self._appDir):
            if UtilityWS.srvCl is None:
                try:
                    LOG(msg="Local server, starting Vitis server...")
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
        Write comp-settings.json for each application.

        @Parameters
        bdRes: Build metadata returned by vitis-py.
        locations: Application directories to process.
        """
        idx = 0
        for location in locations:
            dirApp = location[location.rfind(sep) + 1:]
            sPrevWd = getcwd()
            chdir(dirApp)
            # Reset per-application state.
            self.dConfWs = {}
            iRet = self.prepDataStruct(bdRes[idx])
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
        Normalize vitis-py settings into self.dConfWs.

        @Parameters
        obj: Nested vitis-py settings object.
        """
        PTRN_EX = compile(escape("../"), RegexFlag.IGNORECASE)
        for item in obj.settings:
            if len(item.value) != UtilityWS.EMPTY_BUFFER:
                # Skip parent-relative entries.
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
    Wrap vitis build metadata collection.
    """
    def __init__(self,
                 lApps : list = None
                 ):
        """
        @Description
        Prepare vitis-py access for application configs.

        @Parameters
        lApps: Optional application directory list.
        """
        self._utilCfgWs = UtilityWS()
        self.bdComp = _build.Build(server=UtilityWS.srvCl)
        self._bdRes = []
        self._refLApps = []
        if UtilityWS.IS_DIRS and lApps is not None:
            self.setApps(lApps)

    def setApps(self, lApps : list) -> None:
        """Load Vitis app configs for the provided app list."""
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
    Collect and copy checked-in workspace files.
    """
    BUILD_FILE_IDX = 0
    COMP_FILE_IDX = 1
    # Standard config filenames.
    lsConfCpy = ["CMakeLists.txt", "vitis-comp.json", "*.cmake",
                 "*.ld", "Makefile", ".gitignore"
                 ]
    # Vitis-generated top-level entries under an app's src dir.
    VITIS_GENERATED_SRC_ENTRIES = frozenset({
        "vitis-comp.json", "UserConfig.cmake", "app.yaml",
        ".clangd", "compile_commands.json", ".compile_commands"
        })
    HOFF_HDL = "*.xsa"
    FAILURE = -1
    SUCCESS = 0
    APP_SRCCODE = "src"
    # checkout.py manifest for the checked-in platform src dir name.
    SRC_DIR_MANIFEST = ".digilent_source_dir"

    def __init__(self):
        """
        @Description
        Initialize file collections used during check-in.
        """
        self.utilSFWs = UtilityWS()
        self._lsBldFl = []
        self._lsArchFl = []
        # Workspace platform component names.
        self._lsArchPltDir = []
        # Checked-in platform dir names under src.
        self._lsArchSrcDir = []
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
        Copy build, source, and platform files into src.

        @Parameters
        lApps: Application directory list.
        """
        if lApps is None:
            return SrcFilesWS.FAILURE
        pTemp = path.join(self.utilSFWs.pSubSw, SrcFilesWS.APP_SRCCODE)
        dimLsBldFl = len(self.lsBldFl)
        dimLsArchFl = len(self.lsArchFl)
        # Allow platform-only workspaces.
        if (dimLsBldFl == UtilityWS.EMPTY_BUFFER and
            dimLsArchFl == UtilityWS.EMPTY_BUFFER
            ):
            return SrcFilesWS.FAILURE
        elif path.isdir(pTemp) is True:
            chdir(pTemp)
            for itemIdx in range(0, dimLsBldFl):
                strTempLoc = lApps[itemIdx][lApps[itemIdx].rfind(sep) + 1:]
                dTempLoc = path.join(strTempLoc, SrcFilesWS.APP_SRCCODE)
                if path.isdir(dTempLoc) is not True:
                    makedirs(dTempLoc)
                # Preserve existing mode bits when enabling owner rw.
                chmod(self.lsBldFl[itemIdx], stat(self.lsBldFl[itemIdx]).st_mode | S_IRUSR | S_IWUSR)
                if access(self.lsBldFl[itemIdx], R_OK | W_OK):
                    copy(self.lsBldFl[itemIdx], dTempLoc)
                else:
                    LOG(f"File {self.lsBldFl[itemIdx]} is not writable and readable")
                iRet = self.cpySrcFiles(itemIdx, lApps, (dTempLoc, strTempLoc))
            # Copy XSA files independently from application count.
            validXsaByDestDir = {}
            for pltIdx in range(0, dimLsArchFl):
                validXsaByDestDir.setdefault(self.lsArchSrcDir[pltIdx], set()).add(
                    path.basename(self.lsArchFl[pltIdx]))
            for pltIdx in range(0, dimLsArchFl):
                # Keep the original checked-in platform dir name.
                destPltDir = self.lsArchSrcDir[pltIdx]
                if path.isdir(destPltDir) is not True:
                    mkdir(destPltDir)
                else:
                    # Remove stale XSA files without deleting valid variants.
                    validNames = validXsaByDestDir[destPltDir]
                    for existing in listdir(destPltDir):
                        if (existing.lower().endswith(".xsa")
                                and existing not in validNames):
                            stalePath = path.join(destPltDir, existing)
                            remove(stalePath)
                            LOG(f"Removed stale checked-in XSA: {stalePath}")
                # Preserve existing mode bits when enabling owner rw.
                chmod(self.lsArchFl[pltIdx], stat(self.lsArchFl[pltIdx]).st_mode | S_IRUSR | S_IWUSR)
                if access(self.lsArchFl[pltIdx], R_OK | W_OK):
                    copy(self.lsArchFl[pltIdx], destPltDir)
                else:
                    LOG(f"File {self.lsArchFl[pltIdx]} is not writable and readable")
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
        @Description Copy one application's gathered source files.
        @Parameters
        appIdx: Application index.
        lApps: Application directory list.
        locDuo: App src dir and app root dir destinations.
        """
        loc, locFMisc = locDuo
        appSrcRoot = path.join(lApps[appIdx], SrcFilesWS.APP_SRCCODE)
        # Extra modules live beside the app src dir in the checked-in tree.
        extraModuleNames = set()
        manifestPath = path.join(lApps[appIdx], ".digilent_extra_modules")
        if path.isfile(manifestPath):
            with open(manifestPath, "r", encoding="utf-8") as f:
                extraModuleNames = {line.strip() for line in f if line.strip()}
        # Remove stale checked-in files on repeated check-ins.
        keepRelPaths = set()
        # Track stale-file cleanup separately for extra modules.
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
        # Preserve the build file copied by collectCpyFiles.
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
                miscFileName = subItem[subItem.rfind(sep) + 1:]
                destRoot = loc
                inAppSrcRoot = subItem.startswith(appSrcRoot + sep)
                if inAppSrcRoot:
                    relPath = path.relpath(subItem, appSrcRoot)
                    if relPath.split(sep, 1)[0] in extraModuleNames:
                        destRoot = locFMisc
                    relDirLoc = path.dirname(relPath)
                else:
                    relDirLoc = ""
                if relDirLoc not in ("", "."):
                    nLoc = path.join(destRoot, relDirLoc)
                    if path.isdir(nLoc) is not True:
                        makedirs(nLoc)
                    # Preserve existing mode bits when enabling owner rw.
                    chmod(subItem, stat(subItem).st_mode | S_IRUSR | S_IWUSR)
                    if access(subItem, R_OK | W_OK):
                        copy(subItem, nLoc)
                    else:
                        LOG(f"File {subItem} is not writeable and readable")
                    continue
                # Preserve existing mode bits when enabling owner rw.
                chmod(subItem, stat(subItem).st_mode | S_IRUSR | S_IWUSR)
                if access(subItem, R_OK | W_OK):
                    # Top-level app dotfiles stay beside src.
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
    Coordinate workspace discovery and check-in.
    """

    def __init__(self):
        """
        @Description
        Discover platforms and applications for the workspace.
        """
        self.idxApp = 0
        self.sfWs = SrcFilesWS()
        # Match only generated "<platform>_FSBL" components.
        self.lsExcludedApps = [compile(r"_fsbl$", RegexFlag.IGNORECASE)]
        # Fail the run if a platform/XSA correlation cannot be resolved.
        self.bPlatformResolutionFailed = False
        # Track unsupported apps that make the backup incomplete.
        self.bIncompleteCheckIn = False
        if UtilityWS.IS_DIRS:
            try:
                self.findPlatforms()
                self.cfgWs = ConfigWS()
                _lApps = self.findApplications()
                self.cfgWs.setApps(_lApps)
                LOG("All files have been collected!")
            except Exception:
                # Stop the server on construction-time failures.
                if UtilityWS.srvCl is not None and not UtilityWS.SET_IP_PORT:
                    LOG("Stopping Vitis server after a construction-time error...")
                    UtilityWS.srvCl.stop()
                raise

    def gatherAppSrcCd(self,
                       pSrcFl : str
                       ):
        """
        @Description
        Gather one app's source files under src.

        @Parameters
        pSrcFl: Application directory path.
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
            self.sfWs.lsTempSrcFl[self.idxApp].append(srcFiles)

    def gatherAppOtherConf(self,
                           pSrcFl : str,
                           repflOpt : bool = False
                           ):
        """
        @Description
        Gather top-level app files handled outside src.

        @Parameters
        pSrcFl: Application directory path.
        repflOpt: Include the app-level .gitignore when True.
        """
        if len(self.sfWs.lsTempSrcFl[self.idxApp]) != UtilityWS.EMPTY_BUFFER:
            if repflOpt:
                pGIgn = path.join(pSrcFl, SrcFilesWS.lsConfCpy[-1])
                if path.exists(pGIgn) is True:
                    self.sfWs.lsTempSrcFl[self.idxApp].append([pGIgn])

    def _removeStaleCheckedInApp(self, appName : str) -> None:
        """
        @Description
        Remove a stale checked-in app copy if it exists.

        @Parameters
        appName: Workspace component directory name.
        """
        staleDir = path.join(self.sfWs.pSubSw, SrcFilesWS.APP_SRCCODE, appName)
        if path.isdir(staleDir):
            rmtree(staleDir)
            LOG(f"Removed stale checked-in application \"{appName}\": it "
               f"can no longer be checked in from the current workspace.")

    def processGatherFiles(self,
                           cmpFile : list,
                           dJsonData : dict,
                           pItem : str,
                           lsDirApps : list
                           ) -> int:
        """
        @Description Filter one app and gather its checked-in files.
        @Parameters
        cmpFile: Matching vitis-comp.json locations.
        dJsonData: Decoded component metadata.
        pItem: Workspace component directory.
        lsDirApps: Collected application directories.
        """
        NOT_PATH = -1
        if dJsonData["type"] == "HLS":
            # HLS check-in needs a separate round-trip flow.
            appName = pItem[pItem.rfind(sep) + 1:]
            LOG(f"Skipping application \"{appName}\": HLS components are not "
               f"bare-metal applications and checkout.py has no dedicated HLS "
               f"check-in/checkout path; it must be checked in/managed separately.")
            self._removeStaleCheckedInApp(appName)
            self.bIncompleteCheckIn = True
            return
        if (len(cmpFile) != UtilityWS.EMPTY_BUFFER and
            (dJsonData["type"] == "HOST" or dJsonData["type"] == "UNKNOWN")
            ):
            # Associate application with its platform.
            appName = pItem[pItem.rfind(sep) + 1:]
            appOs = dJsonData.get("os", "standalone")
            if appOs != "standalone":
                # Non-standalone apps need dedicated checkout support.
                LOG(f"Skipping application \"{appName}\": checking in a "
                   f"\"{appOs}\" application is not supported (checkout.py "
                   f"only reconstructs \"standalone\" bare-metal domains); "
                   f"it must be checked in/managed separately.")
                self._removeStaleCheckedInApp(appName)
                self.bIncompleteCheckIn = True
                return
            lHwPlt = dJsonData["platform"]
            # Handle both "<path>.xpfm" and plain platform-name values.
            idxIfExXpfm = lHwPlt.rfind(".")
            idxPltName = lHwPlt.rfind(sep)
            if idxPltName == NOT_PATH and idxIfExXpfm == NOT_PATH:
                idxIfExXpfm = len(lHwPlt)
            pltName = lHwPlt[idxPltName + 1:idxIfExXpfm]
            # Use discovered XSA metadata instead of guessed filenames.
            relPathPlt = None
            for archIdx, archPltDir in enumerate(self.sfWs.lsArchPltDir):
                if archPltDir == pltName:
                    actualXsaName = path.basename(self.sfWs.lsArchFl[archIdx])
                    # Use the checked-in platform dir, not the workspace name.
                    destDir = self.sfWs.lsArchSrcDir[archIdx]
                    relPathPlt = SrcFilesWS.APP_SRCCODE + sep + destDir + sep + actualXsaName
                    break
            if relPathPlt is None:
                # Fail instead of writing a known-bad guessed XSA path.
                LOG(f"Application \"{appName}\" references platform \"{pltName}\" "
                   f"which was not found among the discovered platforms; "
                   f"cannot check in a valid xsa correlation for it.")
                self.bPlatformResolutionFailed = True
                return SrcFilesWS.FAILURE
            # Preserve app-to-platform binding details for checkout.py.
            cpuInstance = dJsonData.get("cpuInstance", "")
            self.cfgWs.utilCfgWs.dPltAppCorr[appName] = {
                "xsa": relPathPlt,
                "cpu_instance": cpuInstance,
                "os": appOs
                }
            # Use the app's canonical top-level CMakeLists.txt.
            canonicalBuildFile = path.join(pItem, SrcFilesWS.APP_SRCCODE,
                                           SrcFilesWS.lsConfCpy[SrcFilesWS.BUILD_FILE_IDX])
            if path.isfile(canonicalBuildFile):
                lsDirApps.append(pItem)
                self.sfWs.lsBldFl.append(canonicalBuildFile)
                self.sfWs.lsTempSrcFl.append([])
                self.gatherAppSrcCd(pItem)
                self.gatherAppOtherConf(pItem)
                self.idxApp = self.idxApp + 1

    def findApplications(self) -> list:
        """
        @Description
        Discover supported applications in ws.
        """
        IDX_VCOMP = 0
        self.sfWs.lsBldFl = []
        chdir(self.sfWs.appDir)
        lsDirApps = []
        pCwd = getcwd()
        for item in listdir(pCwd):
            if path.isdir(item) is False or item.startswith("."): continue
            pItem = path.join(pCwd, item)
            cmpFile = list(Path(pItem).rglob(
                            SrcFilesWS.lsConfCpy[SrcFilesWS.COMP_FILE_IDX]))
            if len(cmpFile) == UtilityWS.EMPTY_BUFFER: continue
            strCmpFile = str(cmpFile[IDX_VCOMP])
            dJsonData = JSONDecoder().decode(open(strCmpFile).read())
            mtName = self.lsExcludedApps[0].search(dJsonData["name"])
            if mtName is not None: continue
            iRet = self.processGatherFiles(cmpFile, dJsonData, pItem, lsDirApps)
        chdir(self.sfWs.pSubSw)
        LOG("Number of applications found: " + str(len(lsDirApps)))
        return lsDirApps

    def findPlatforms(self):
        """
        @Description
        Discover platform components and their XSA files.
        """
        IDX_FRENC = 0
        IDX_VCOMP = 0
        self.sfWs.lsArchFl, self.sfWs.lsArchPltDir, self.sfWs.lsArchSrcDir = [], [], []
        chdir(self.sfWs.appDir)
        pCwd = getcwd()
        for item in listdir(pCwd):
            if path.isdir(item) is False or item.startswith("."): continue
            pItem = path.join(pCwd, item)
            # Ignore non-platform components that happen to carry an XSA.
            cmpFile = list(Path(pItem).rglob(
                            SrcFilesWS.lsConfCpy[SrcFilesWS.COMP_FILE_IDX]))
            if len(cmpFile) == UtilityWS.EMPTY_BUFFER: continue
            dJsonData = JSONDecoder().decode(open(str(cmpFile[IDX_VCOMP])).read())
            if dJsonData.get("type") != "PLATFORM": continue
            archFile = list(Path(pItem).rglob(SrcFilesWS.HOFF_HDL))
            if len(archFile) == UtilityWS.EMPTY_BUFFER:
                # Missing XSA makes the platform unusable for check-in.
                LOG(f"Platform \"{item}\" has no discoverable xsa file; "
                   f"cannot check it in.")
                self.bPlatformResolutionFailed = True
                continue
            elif len(archFile) > 1:
                # Reject ambiguous platforms instead of guessing an XSA.
                LOG(f"Platform \"{item}\" contains {len(archFile)} xsa files; "
                   f"cannot unambiguously determine its authoritative "
                   f"handoff, skipping it.")
                self.bPlatformResolutionFailed = True
                continue
            else:
                self.sfWs.lsArchFl.append(str(archFile[IDX_FRENC]))
                self.sfWs.lsArchPltDir.append(item)
                srcDirManifest = path.join(pItem, SrcFilesWS.SRC_DIR_MANIFEST)
                srcDir = item
                if path.isfile(srcDirManifest):
                    with open(srcDirManifest, "r") as f:
                        manifestDir = f.read().strip()
                    # Accept only a safe single directory-name manifest.
                    isSafeSingleComponent = (
                        manifestDir != "" and
                        manifestDir not in (".", "..") and
                        sep not in manifestDir and
                        (path.altsep is None or path.altsep not in manifestDir) and
                        not path.isabs(manifestDir)
                        )
                    if manifestDir != "" and not isSafeSingleComponent:
                        LOG(f"Ignoring unsafe {SrcFilesWS.SRC_DIR_MANIFEST} "
                           f"content \"{manifestDir}\" for platform \"{item}\": "
                           f"expected a single directory name, falling back to "
                           f"\"{item}\".")
                    elif isSafeSingleComponent:
                        srcDir = manifestDir
                self.sfWs.lsArchSrcDir.append(srcDir)
        chdir(self.sfWs.pSubSw)
        LOG("Number of platforms found: " + str(len(self.sfWs.lsArchPltDir)))

    def checkInSF(self) -> int:
        """
        @Description
        Run the complete check-in flow.
        """
        if not UtilityWS.IS_DIRS:
            return UtilityWS.FAILURE
        try:
            if self.bPlatformResolutionFailed:
                # Avoid producing a known-unusable checked-in state.
                LOG("Aborting check-in: one or more applications' platform "
                   "xsa could not be resolved.")
                iRet = UtilityWS.FAILURE
            else:
                iRet = self.sfWs.collectCpyFiles(self.cfgWs.refLApps)
                if iRet == UtilityWS.SUCCESS:
                    iRet = self.cfgWs.utilCfgWs.encJSON_Ws(self.cfgWs.bdRes, locations=self.cfgWs.refLApps)
                else:
                    LOG("collectCpyFiles failed, skipping metadata encoding.")
                if iRet == UtilityWS.SUCCESS and self.bIncompleteCheckIn:
                    # Report partial backups as failures.
                    LOG("Check-in finished with at least one application skipped "
                       "(see prior log messages); checked-in backup is incomplete.")
                    iRet = UtilityWS.FAILURE
        finally:
            # Stop only the server started by this process.
            if not UtilityWS.SET_IP_PORT:
                UtilityWS.srvCl.stop()
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
