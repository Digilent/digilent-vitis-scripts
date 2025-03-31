
# digilent-vitis-scripts

This is a development branch for Vitis 2024.1,
it contains for now solutions for check in/out workflow.

**Special Notes:**
*Vitis creates a `.lock` file which after the IDE is closed or other*
*Vitis TCP/IP server is stopped, it will have its file descriptor still*
*hooked up to the previous process, so only after a few seconds should*
*one of the files be run. A workaround will be provided soon.*

----

## Quick Checkout Guide

Some Digilent Github repositories also require that you check out a specific demo branch.
Whenever checking out a demo branch, submodules should be reupdated and reinitialized:

`git submodule update --init`

When launching Vitis, whether through Vivado's *Tools* menu, or on its own,
the Vitis workspace should be set to the repository's sw -> ws folder.

The scripts present in this repository can be run through the use of the
Vitis Console 202x.y, for example 2024.1, which comes along with Vitis.
Upon launch, Vitis Console's current working directory is set to the
Vitis install directory: Xilinx -> Vitis -> 202x.y -> bin.
To recreate the workspace, enter the following command into the Vitis Console:

`<path-to-scripts-repo>checkout.py`

This process will populate the workspace with projects containing sources
from the parent repository's `src` folder, configure those projects, and fully build them.
This may take several minutes to fully complete. When the script is finished running,
the `Vitis [idx]:` prompt wil reappear in the command line, where `idx - 1` is the number of
inserted commands. From this point, the demo can be programmed onto a board,
sources can be viewed, and modified, as desired.

The above functionality can be reproduced from Vitis IDE launching the terminal from
Terminal -> New Terminal which uses the default command line executable from the OS. If
this is the choosen method, then it will be necessary to give absolute path to the `checkout.py`
file, not relative, or to change current working directory to the branch's sw submodule.

`vitis -s <path-to-scripts-repo>checkout.py`

**Note:**
*The current working directory is irrelevant to the functionality of the scripts in this submodule.*

----

## Quick Checkin Guide

**Important:** *The checkin.py can be used multiple times, it will overwrite the existent*
*files and if some new ones appear into an application they are going to be copied too.*

*Application source files (cmake and linker scripts) are soft-linked into the*
*workspace upon checkout, so existing files do not need to be manually copied back,*
*but the checkin.py saves them because can hold modifications, Vitis generates them*
*with respect to the application template and hardware platform.*

This section assumes that you have already created a Vitis workspace containing
one or more application projects.

The checkin script has only been tested with standalone application projects at time of writing.

To add this repository to a parent repository as a submodule, first open a terminal
with access to `git`. If your parent repository does not yet have a `sw` subdirectory (or submodule),
create one and `cd` into it. Adding the `scripts` path argument to the command is recommended
in order to keep file paths short.

`git submodule add https://github.com/Digilent/digilent-vitis-scripts scripts`
`git checkout new_vitis/2024.1`

The scripts present in this repository can be run through the use of the
Vitis Console 202x.y, for example 2024.1, which comes along with Vitis.
Upon launch, Vitis Console's current working directory is set to the
Vitis install directory: Xilinx -> Vitis -> 202x.y -> bin.
To backup the workspace, enter the following command into the Vitis Console:

`<path-to-scripts-repo>checkin.py`

The above functionality can be reproduced from Vitis IDE launching the terminal from
Terminal -> New Terminal which uses the default command line executable from the OS. If
this is the choosen method, then it will be necessary to give absolute path to the `checkin.py`
file, not relative, or to change current working directory to the branch's sw submodule.

`vitis -s <path-to-scripts-repo>checkin.py`

This script will create a `src` directory in the same folder as the scripts submodule, and populate it as below:

* One folder per hardware platform
  * The XSA file describing the hardware specification that the software targets, exported from Vivado.
* One folder per application project, containing the following:
  * A comp-settings.json file with user settings and relative path to XSA file used by a project.
  * A src directory with source files and cmake, linker scripts.

Note that the name of each folder is used in `checkout` to determine the name of the app/platform/domain.

The checkin/out scripts will handle all the neccessary tasks to ensure a smooth workflow
for development of `projects`.

----
