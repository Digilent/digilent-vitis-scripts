
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
from shutil import rmtree
from re import search
from json import JSONDecoder
from os import (path, getcwd, walk,
                sep
                )

class Workspace:
    """
    @Description
    Resources to set app/domain configs, and create multiple applications.
    """
    SUCCESS = 0
    FAILURE = -1
    COMP_SETTINGS = "comp-settings.json"

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
        """
        @Description
        ...

        TODO:
        Find and extract xsa metadata with py-vitis/hsi resources for
        cpu, os to configure domain. Use more pystd lib functions to
        some parts of this file more efficient.
        """
        client = create_client()
        script_path = path.dirname(path.abspath(__file__))
        # Strip out cwd which is ~scripts~.
        ws_path = script_path[:script_path.rfind(sep)] + f"{sep}ws"
        # Delete the workspace if already exists.
        if (path.isdir(ws_path)):
            rmtree(ws_path)
            print(f"Deleted workspace {ws_path}")
        client.set_workspace(ws_path)
        print("Successfully created Vitis client on workspace {}".format(client.get_workspace()))
        app_names = []
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
                    print(f"xsa dirname = {path.basename(dirpath)}")
                    xsa_dirpath_name = path.basename(dirpath)
        print(f"\nDetected one or more applications present: {app_names}")
        mcu = client.get_processor_os_list(xsa=file)
        print(f"\nProcessor device family is: {mcu.deviceFamily}")
        if mcu.deviceFamily == "fpga":
            print("Creating platform component " + xsa_dirpath_name + ".")
            platform = client.create_platform_component(
                name=xsa_dirpath_name,
                hw_design=file,
                no_boot_bsp=True
            )
            platform.update_desc(desc=xsa_dirpath_name)
            print("Adding new domain \"domain_microblaze_0\" for cpu: \"microblaze_0\" and OS: \"standalone\"")
            domain = platform.add_domain(
                cpu="microblaze_0",
                os="standalone",
                name="domain_microblaze_0",
                display_name="domain_microblaze_0"
            )
            print("Configuring the domain \"domain_microblaze_0\"...")
            print("BSP settings for the domain \"domain_microblaze_0\"")
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
            print("Adding new domain \"domain_ps7_cortexa9_0\" for cpu: \"ps7_cortexa9_0\" and OS: \"standalone\"")
            domain = platform.add_domain(
                cpu="ps7_cortexa9_0",
                os="standalone",
                name="domain_ps7_cortexa9_0",
                display_name="domain_ps7_cortexa9_0"
            )
            print("Configuring the domain \"domain_ps7_cortexa9_0\"...")
            print("BSP settings for the domain \"domain_ps7_cortexa9_0\"")
        platform.build()
        print("\n")
        for driver in domain.get_drivers():
            print(driver["name"])
        for app_name in app_names:
            print(f"Creating application component {app_name}")
            app = client.create_app_component(
                name=app_name,
                platform=client.get_workspace() + sep +
                         xsa_dirpath_name + sep +
                         "export" + sep +
                         xsa_dirpath_name + sep +
                         xsa_dirpath_name + ".xpfm",
                domain="domain_ps7_cortexa9_0",
                template="hello_world"
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
                    if (filename == "helloworld.c" or
                        filename == "Xilinx.spec" or
                        filename == "README.txt"
                        ):
                        print(f"\nRemoving {filename} from {path.join(dirpath, filename)}")
                        app.remove_files(files=[app.component_location + sep + "src" + sep + filename])
            # Removing tcl files that should not be there.
            for dirpath, dirnames, filenames in walk(app.component_location + sep + "_ide"+ sep + "psinit"):
                print("")
                print(filenames)
                for filename in filenames:
                    if filename != "ps7_init.tcl":
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
