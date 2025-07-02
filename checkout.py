
"""
    Company: Digilent RO
    Engineers: rb & bs
    Usage: for vitis projects (>= v2023.2)
    
    @Description
    This checkout.py has the same behavior
    as the previous checkout.tcl. It creates
    from sw -> src multiple applications into
    sw -> ws.

    @Insights
    Vitis v2024.1 has Python v3.8.3.
"""
from vitis import create_client, dispose
import time
from datetime import datetime
import shutil
from re import search
from json import JSONDecoder
from os import (path, getcwd, walk,
                sep, listdir, unlink
                )
import hsi
import xsdb
import sys

class Workspace:
    """
    @Description
    Resources to set app/domain configs, and create multiple applications.
    """
    SUCCESS = 0
    FAILURE = -1
    COMP_SETTINGS = "comp-settings.json"
    DEBUG = 0

    def __init__(self):
        pass

    def setConfigDomain(self,
                        domain,
                        option : str,
                        **kargs
                        ) -> int:
        """
        @Description
        Iterate over kargs : dict elements and set domain configs.

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
                print(f"ERROR: Failed to set config param {key} for processor!")
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

        @Parameters
        app: current application obj.
        kargs: dict with {[params...] : [values...]}.
        """
        for key, value in kargs.items():
            try:
                app.set_app_config(key=key, value=value)
                # return Workspace.SUCCESS
            except:
                print(f"ERROR: Failed to set config param {key} for processor!")
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

        @Parameters
        app: obj returned by create_application_component function from vitis.cli_client.
        appname: name found in json file (last key) or it can be put manually.
        filepath: comp-setting.json path.
        """
        dJsonStruct = JSONDecoder().decode(open(filepath).read())
        for key, value in dJsonStruct.items():
            if (appname != key and
                len(value) != 0 and
                key != "USER_UNDEFINED_SYMBOLS"
                ):
                app.set_app_config(key, value)
        return Workspace.SUCCESS
    
    def checkOutSF(self) -> int:
        def get_metadata(**kwargs):
            xsa = ""
            open_xsa = 0
            ret_metadata = {'arch' : '', 'target_proc' : ''}
            for key, value in kwargs.items():
                if key == "xsa":
                    xsa = value
                if key == "open_xsa":
                    open_xsa = 1
            
            if open_xsa == 1:
                if xsa != "":
                    print("Info: Using XSA file: " + xsa + " to extract HW metadata using HSI Python API")
                    HwDesign = hsi.HwManager.open_hw_design(xsa)
                    ret_metadata['arch'] = HwDesign.FAMILY
                    for proc in HwDesign.get_cells(hierarchical='true',filter='IP_TYPE==PROCESSOR'):
                        if proc.IP_NAME == "psu_cortexa53" or proc.IP_NAME == "psu_cortexa72" or proc.IP_NAME == "ps7_cortexa9":
                            ret_metadata['target_proc'] = proc.IP_NAME+"_0"
                            break
                    #HwDesign.close()
                else:
                    print("Error: No XSA passed. HW metadata will not be extracted.")
            else:
                print("Info: no need to open XSA")
            
            return ret_metadata
        """
        @Description
        ...

        TODO:
        Find and extract xsa metadata with py-vitis/hsi resources for
        cpu, os to configure domain. Use more pystd lib functions to
        some parts of this file more efficient.
        """
        dispose()

        print("\n---------------------------------------------------------")
        print("  Checking out Vitis project to Vitis Unified IDE  ")
        print("---------------------------------------------------------")
        client = create_client()
        script_path = path.dirname(path.abspath(__file__))
        if Workspace.DEBUG:
            date = datetime.now().strftime("%Y%m%d%I%M%S")
            # Strip out cwd which is ~scripts~.
            ws_path = script_path[:script_path.rfind(sep)] + f"{sep}ws" + f"_{date}"
        else:
            # Strip out cwd which is ~scripts~.
            ws_path = script_path[:script_path.rfind(sep)] + f"{sep}ws"
        repo_path = script_path[:script_path.rfind(sep)] + sep + 'repo'
        # Delete the workspace if it already exists.
        max_try = 10
        for attempt in range(max_try):
            try:
                shutil.rmtree(ws_path)
                print(f"Deleted workspace {ws_path} on attempt: {attempt+1}.")
                break
            except FileNotFoundError:
                print("Old workspace folder was already deleted or does not exist. The checkout script will continue to run unimpeded.")
                break
            except Exception as e:
                print(f"Attempt {attempt+1} failed: {e}")
        else:
            raise Exception(f"Failed to delete old workspace. Please delete it manually before running the checkout script one more time.")
        client.set_workspace(ws_path)
        print("Successfully created Vitis client on workspace {}".format(client.get_workspace()))
        app_names = []
        hw_platforms = []
        hw_pf_paths = []
        for dirpath, dirnames, filenames in walk(script_path[:script_path.rfind(sep)] + f"{sep}src"):
            print(filenames)
            for dirname in dirnames:
                if search(r"src", dirname):
                    print(dirpath)
                    application_name = path.basename(dirpath)
                    print(application_name)
                    print(f"this is the name of the project: {application_name}")
                    app_names.append(application_name)
                    print(app_names)
            match = search(r"src\.+", dirpath)
            print(match)
            for filename in filenames:
                if filename.endswith(".xsa"):
                    file = path.join(dirpath, filename)
                    print(f"Found - {file}")
                    print(f"xsa dirpath = {dirpath}")
                    xsa_dirpath = dirpath
                    hw_pf_paths.append(xsa_dirpath)
                    print(f"xsa dirname = {path.basename(xsa_dirpath)}")
                    xsa_dirpath_name = path.basename(xsa_dirpath)
                    hw_platforms.append(xsa_dirpath_name)
        print(f"\nDetected one or more applications present: {app_names}")
        print(f"Detected one or more hardware platforms present: {hw_platforms}")
        print(hw_pf_paths)
        
        ret_metadata = {"arch" : "", "target_proc" : ""}

        start_time = time.time()

        arch_and_cpu_metadata = get_metadata(xsa=file, open_xsa="1")

        arch = arch_and_cpu_metadata['arch']
        
        if arch in ('spartan7', 'artix7', 'kintex7'):
            target_proc = 'microblaze_0'
        else:
            target_proc = arch_and_cpu_metadata['target_proc']
        
        print("Info: Detected arch: " + arch)
        print("Info: Using target processor: " + target_proc)
        
        # List all files and directories in the given path
        # Remove all files that result from get_metadata() unzipping the xsa
        for xsa_path in hw_pf_paths:
            print(listdir(xsa_path))
            for filename in listdir(xsa_path):
                if not filename.endswith(".xsa"):
                    file_path = path.join(xsa_path, filename)
                    try:
                        if path.isfile(file_path) or path.islink(file_path):
                            unlink(file_path)           # Remove file or symbolic link
                        elif path.isdir(file_path):
                            shutil.rmtree(file_path)       # Remove directory and its contents
                    except Exception as e:
                        print(f'Failed to delete {file_path}. Reason: {e}')

        end_time = time.time()
        # Measure execution time
        execution_time = end_time - start_time
        print(f"Execution time: {execution_time:.4f} seconds")
        print(f"\nProcessor device family is: {arch}")
        
        if arch in ('spartan7', 'artix7', 'kintex7'):
            print("Creating platform component " + xsa_dirpath_name + ".")
            platform = client.create_platform_component(
                name=xsa_dirpath_name,
                hw_design=file,
                no_boot_bsp=True
            )
            platform.update_desc(desc=xsa_dirpath_name)
            print(f"Adding new domain \"domain_{target_proc}\" for cpu: \"{target_proc}\" and OS: \"standalone\"")
            domain = platform.add_domain(
                cpu=target_proc,
                os="standalone",
                name=f"domain_{target_proc}",
                display_name=f"domain_{target_proc}",
                support_app = "hello_world"
            )
            print(f"Configuring the domain \"domain_{target_proc}\"...")
            print(f"BSP settings for the domain \"domain_{target_proc}\"")
            # Setting config params for processor and os.
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
        else:
            print("Creating platform component " + xsa_dirpath_name + ".")
            platform = client.create_platform_component(
                name=xsa_dirpath_name,
                hw_design=file
            )
            platform.update_desc(desc=xsa_dirpath_name)
            print(f"Adding new domain \"domain_{target_proc}\" for cpu: \"{target_proc}\" and OS: \"standalone\"")
            domain = platform.add_domain(
                cpu=target_proc,
                os="standalone",
                name=f"domain_{target_proc}",
                display_name=f"domain_{target_proc}",
                support_app = "hello_world"
            )
            print(f"Configuring the domain \"domain_{target_proc}\"...")
            print(f"BSP settings for the domain \"domain_{target_proc}\"")
        platform.build()
        print("\n")
        
        if arch == "zynquplus":
            embeddedsw = client.set_embedded_sw_repo(level = 'LOCAL', path = [repo_path])
            print(f"Adding new domain '{target_proc}_domain_fsbl' for cpu: '{target_proc}' and OS: 'standalone'")
            zynqmp_fsbl_domain = platform.add_domain(cpu = target_proc, os = 'standalone', name = f'{target_proc}_domain_fsbl', display_name = f'{target_proc}_domain_fsbl', support_app = "zynqmp_fsbl")
            platform.build()
            #Generating custom fsbl application from template
            fsbl_app = client.create_app_component(
                    name='ZynqMP_FSBL',
                    platform=client.get_workspace() + sep +
                             xsa_dirpath_name + sep +
                             "export" + sep +
                             xsa_dirpath_name + sep +
                             xsa_dirpath_name + ".xpfm",
                    domain=f"{target_proc}_domain_fsbl",
                    template="zynqmp_fsbl"
                    )
            #platforms = client.list_platform_components()
            print(f'Platform is located at: {script_path[:script_path.rfind(sep)] + sep + xsa_dirpath_name}')
            fsbl_app.import_files(
                    from_loc=client.get_workspace() + sep + xsa_dirpath_name + sep + 'hw' + sep + 'sdt',
                    files= ['psu_init.c', 'psu_init.h'],
                    dest_dir_in_cmp="src"
                )
            fsbl_app.build()
            platform.remove_boot_bsp()
            platform.set_fsbl_elf(path = fsbl_app.component_location + sep + 'build' + sep + 'ZynqMP_FSBL.elf')
            platform.build()
        for app_name in app_names:
            print(f"Creating application component {app_name}")
            app = client.create_app_component(
                name=app_name,
                platform=client.get_workspace() + sep +
                         xsa_dirpath_name + sep +
                         "export" + sep +
                         xsa_dirpath_name + sep +
                         xsa_dirpath_name + ".xpfm",
                domain=f"domain_{target_proc}" if arch not in ('spartan7', 'artix7', 'kintex7') else "domain_microblaze_0",
                template='empty_application'
            )
            # Extract saved settings.
            iRet = self.decJSON_Ws(
                app,
                appname=app_name,
                filepath=script_path[:script_path.rfind(sep)] + sep +
                         "src" + sep +
                         app_name + sep +
                         Workspace.COMP_SETTINGS
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
                from_loc=script_path[:script_path.rfind(sep)] + sep + "src" + sep + app.component_name + sep + "src",
                dest_dir_in_cmp="src"
            )
            print(f"\nApp component location: {app.component_location}\n")
            # Removing files generated by the previous template.
            for dirpath, dirnames, filenames in walk(app.component_location + sep + "src"):
                print(filenames)
                for filename in filenames:
                    if (filename in ('Xilinx.spec', 'README.txt')):
                        print(f"\nRemoving {filename} from {path.join(dirpath, filename)}")
                        app.remove_files(files=[app.component_location + sep + "src" + sep + filename])
            # Removing tcl files that should not be there.
            for dirpath, dirnames, filenames in walk(app.component_location + sep + "_ide"+ sep + "psinit"):
                print("")
                print(filenames)
                for filename in filenames:
                    if (filename not in ('psu_init.tcl', 'ps7_init.tcl')):
                        print(f"\nRemoving {filename} from {path.join(dirpath, filename)}")
                        app.remove_files(files=[app.component_location + sep + "_ide" + sep + "psinit" + sep + filename])
            app.build()
            dispose()
            return Workspace.SUCCESS

if __name__ == "__main__":
    """
    @Description
    This ~file~ can be used as a module or standalone
    py program. From a cmd-line: `vitis -s [<relative-or-absolute-path>]checkout.py`,
    where is the current directory from terminal process does not influence behavior
    of the above functionalities.
    """
    lcWs = Workspace()
    iRet = lcWs.checkOutSF()