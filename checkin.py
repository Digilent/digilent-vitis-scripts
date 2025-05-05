
"""
    Company: Digilent RO
    Engineer: bs
    Usage: for vitis projects
    
    @Description
    This checkin.py has the same behavior
    as the previous checkin.tcl. It preserves
    workspace configuration & source files.
    
    @Insights
    Vitis v2024.1 has Python v3.8.3.
"""
from os import (chdir, getcwd, listdir,
                path, sep, makedirs, mkdir,
                access, F_OK, SEEK_END)
from vitis import (_build, _server)
from pathlib import Path
from shutil import copy
from json import (JSONEncoder, JSONDecoder)
from re import (compile, RegexFlag)
from misc import LOG

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

    def __init__(self):
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
        if path.isdir(self._srcDir) and path.isdir(self._appDir):
            if UtilityWS.srvCl is None:
                try:
                    LOG(msg="Local server, starting Vitis server...")
                    # Init server with pre-defined args.
                    UtilityWS.srvCl = _server.Server(
                        port=None,
                        host="localhost",
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
                del self.dConfWs[dirApp]
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
        # Extract from a protobuff class metadata.
        for item in obj.settings:
            if len(item.value) != UtilityWS.EMPTY_BUFFER:
                # Every value-obj has just one element in its list ? ... some maybe not.
                valLoc = item.value.__getitem__(0)
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
            # Find the largest vector.
            bigLs = dimLsBldFl if dimLsBldFl > dimLsArchFl else dimLsArchFl
            # Nr of platforms <= Nr of apps;
            for itemIdx in range(0, bigLs):
                if itemIdx < dimLsBldFl:
                    strTempLoc = lApps[itemIdx][lApps[itemIdx].rfind(sep) + 1:]
                else:
                    continue
                dTempLoc = path.join(strTempLoc, SrcFilesWS.APP_SRCCODE)
                # In py <= 3.8, certain modes for mkdir does not exist, so default one is used.
                if path.isdir(dTempLoc) is not True:
                    makedirs(dTempLoc)
                    if itemIdx < dimLsArchFl: 
                        mkdir(self.lsArchPltDir[itemIdx])
                # Should we use copy2 to preserve metadata instead of copy ?
                copy(self.lsBldFl[itemIdx], dTempLoc)
                if itemIdx < dimLsArchFl:
                    copy(self.lsArchFl[itemIdx], self.lsArchPltDir[itemIdx])
                # tuple(<app-dirname-src>, <app-dirname>)
                iRet = self.cpySrcFiles(itemIdx, lApps, (dTempLoc, strTempLoc))
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

        rfind + slices (idxLeft:idxRight) are used to get directories that need
        to be created for header files into <app-dirname-src>, if not they are
        overwritten anyway. In case of directories, if they have been already
        created, then calling `mkdir` or `makedirs` like functions are avoided.

        @Parameters
        appIdx: integer for App(s) files.
        lApps: matrix with paths of files to be copied.
        locDuo: tuple with <app-dirname-src> on left, <app-dirname> on right ~ pair like.
        """
        # item ~ list
        # e.g. appIdx=0 is for App1.
        loc, locFMisc = locDuo
        for item in self._lsTempSrcFl[appIdx]:
            for subItem in item:
                # Take all files from each sublist and copy them,
                # suppose only one dir is between <app-dir-src> and a header file.
                lastMarkerPos = subItem.rfind(sep)
                miscFileName = subItem[lastMarkerPos + 1:]
                trimSubItem = subItem[:lastMarkerPos]
                prevLastMarkerPos = trimSubItem.rfind(sep) + 1
                srcDirLoc = subItem[prevLastMarkerPos:lastMarkerPos]
                if srcDirLoc != SrcFilesWS.APP_SRCCODE and srcDirLoc != locFMisc:
                    nLoc = path.join(loc, srcDirLoc)
                    if path.isdir(nLoc) is not True:
                        mkdir(nLoc)
                    copy(subItem, nLoc)
                    continue
                # Prefix - other misc files can exist ... tp(".","")
                if miscFileName.startswith("."):
                    copy(subItem, locFMisc)
                else:
                    copy(subItem, loc)
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
        self.sfWs = SrcFilesWS()
        # pattern vector; /i -> case insensitive;
        self.lsExcludedApps = [compile("fsbl", RegexFlag.IGNORECASE)]
        if UtilityWS.IS_DIRS:
            self.findPlatforms()
            # Set multiple Utility ... ? 'fa(), ...'
            # ~ Set ConfigWS paths for platforms too ~
            self.cfgWs = ConfigWS()
            _lApps = self.findApplications()
            self.cfgWs.setApps(_lApps)
            LOG("All files have been collected!")

    def gatherAppSrcCd(self,
                       pSrcFl : str,
                       idxApp : int
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
            srcFiles = list(Path(pSrcFlLoc).rglob(item))
            if len(srcFiles) != UtilityWS.EMPTY_BUFFER:
                # [[]] - type
                self.sfWs.lsTempSrcFl[idxApp].append([str(itm) for itm in srcFiles])
        # [[App1],[App2],[App3], ...], where App1,App2,App3 are other lists with
        # paths of source files that need to be copied.
        chdir(pCwd)

    def gatherAppOtherConf(self,
                           pSrcFl : str,
                           idxApp : int,
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
        idxApp: Index for an application component, lsTempSrcFl from
                sfWs stores all of them from workspace.
        repflOpt: By default it is on '0', this avoids storing
                  files like .gitignore, but others can be added/ignored.
        """
        if len(self.sfWs.lsTempSrcFl[idxApp]) != UtilityWS.EMPTY_BUFFER:
            # Source files should have been stored by now.
            if repflOpt:
                pGIgn = path.join(pSrcFl, SrcFilesWS.lsConfCpy[-1])
                if path.exists(pGIgn) is True:
                    self.sfWs.lsTempSrcFl[idxApp].append([pGIgn])
            pSrcFlLoc = path.join(pSrcFl, SrcFilesWS.APP_SRCCODE)
            pCwd = getcwd()
            chdir(pSrcFlLoc)
            # Avoid build-sys file from first pos.
            for item in SrcFilesWS.lsConfCpy[SrcFilesWS.EXCLUDE_BSYSF_IDX + 1:-1]:
                srcFiles = list(Path(pSrcFlLoc).rglob(item))
                if len(srcFiles) != UtilityWS.EMPTY_BUFFER:
                    # [[]] - type
                    self.sfWs.lsTempSrcFl[idxApp].append([str(itm) for itm in srcFiles])
            chdir(pCwd)

    def processGatherFiles(self,
                           idxApp : int,
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
        if (len(cmpFile) != UtilityWS.EMPTY_BUFFER and
            ((dJsonData["type"] == "HOST" or
              dJsonData["type"] == "HLS") or
              dJsonData["type"] == "UNKNOWN")
            ):
            # Associate application with its platform.
            appName = pItem[pItem.rfind(sep) + 1:]
            lHwPlt = dJsonData["platform"]
            # Path to .xpfm file;
            idxPltName = lHwPlt.rfind(sep)
            # Pay attention which Utility object is used, bcs encJSON_Ws depends on it.
            relPathPlt = SrcFilesWS.APP_SRCCODE + sep + \
                            lHwPlt[idxPltName + 1:lHwPlt.rfind(".")] + sep + \
                            lHwPlt[idxPltName + 1:lHwPlt.rfind(".")] + ".xsa"
            self.cfgWs.utilCfgWs.dPltAppCorr[appName] = relPathPlt
            # Search for buid file.
            bFile = list(Path(pItem).rglob(
                            SrcFilesWS.lsConfCpy[SrcFilesWS.BUILD_FILE_IDX]))
            # Just one element should be in the list.
            if len(bFile) != UtilityWS.EMPTY_BUFFER:
                lsDirApps.append(pItem)
                # Populate in SrcFilesWS scope, SrcFilesWS.BUILD_FILE_IDX or simply 0.
                self.sfWs.lsBldFl.append(path.join(pItem, str(bFile[SrcFilesWS.BUILD_FILE_IDX])))
                self.sfWs.lsTempSrcFl.append([])
                # Collect source files from <app-dir>/src.
                self.gatherAppSrcCd(pItem, idxApp)
                self.gatherAppOtherConf(pItem, idxApp)
                idxApp = idxApp + 1

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
        
        Apps are counted with idxApp and passed to self.gatherAppOtherConf,
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
        idxApp = 0
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
            iRet = self.processGatherFiles(idxApp, cmpFile, dJsonData, pItem, lsDirApps)
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
        iRet = self.sfWs.collectCpyFiles(self.cfgWs.refLApps)
        iRet = self.cfgWs.utilCfgWs.encJSON_Ws(self.cfgWs.bdRes, locations=self.cfgWs.refLApps)
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
