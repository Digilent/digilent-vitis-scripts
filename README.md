
# digilent-vitis-scripts

This is a repo for the new Vitis Unified IDE; it contains solutions for check in/out workflow.
The checkin/out scripts will handle all the neccessary tasks to ensure a smooth workflow
for development of `projects`.

**Note #1:**
*To add this repository to a parent repository as a submodule, first open a terminal*
*with access to `git`. Adding the `scripts` path argument to the command is recommended*
*in order to keep file paths short.*

`git submodule add https://github.com/Digilent/digilent-vitis-scripts scripts`

`git checkout new_vitis/master`

**Note #2:**
*The checkout/checkin scripts need to have `src` directory in the same folder as the*
*scripts submodule, populating it as below:*

* One folder per hardware platform
  * The XSA file describing the hardware specification that the software targets, exported from Vivado.
* One folder per application project, containing the following:
  * A comp-settings.json file with user settings and relative path to XSA file used by a project.
  * A src directory with source files and cmake, linker scripts.

*Note that the name of each folder is used in `checkout` to determine the name of the app/platform/domain.*

**Special Note:**
*Vitis Unified IDE creates a `.lock` file which after the IDE is closed or another*
*Vitis TCP/IP server is stopped, it will have its file descriptor still*
*hooked up to the previous process. Therefore, between consecutive*
*`checkout.py` runs/`checkin.py` runs/Vitis Unified IDE closing leave at least a few seconds.*

----

## Quick Checkout Guide

1. Some Digilent Github repositories also require that you check out a specific demo branch.
Whenever checking out a demo branch, submodules should be updated and initialized:

`git submodule update --init [--recursive]`

2. Close Vitis Unified IDE, if you have it open.

3. If you ran the `checkout.py` script before for the same project, please make sure you
delete the `ws` folder from `sw`.

4. The scripts present in this repository can be run through the use of the
Vitis Commandline Tool 202x.y, which comes along with Vitis.
To recreate the workspace, enter the following command into the Vitis Commandline Tool,
specifying the absolute path to the checkout script:

`run <path-to-scripts-repo>checkout.py`

This process will populate the workspace with projects containing sources
from the parent repository's `src` folder, configure those projects, and fully build them.
This may take several minutes to fully complete. When the script is finished running,
the "Build Finished successfully" message will appear in the command line, followed by the
`Vitis [idx]:` prompt, where `idx - 1` is the number of inserted commands.

The above functionality can be reproduced from Vitis Unified IDE launching the terminal
from Terminal -> New Terminal which uses the default command line executable from the OS.
If this is the choosen method, then it will be necessary to give absolute path to the
`checkout.py` file, not relative:

`vitis -s <path-to-scripts-repo>checkout.py`

Alternatively, you can change the Vitis Unified IDE current working directory to the
branch's `sw` submodule and then specify the path to the checkout script as
`scripts\checkout.py`.

**Note:**
*The current working directory is irrelevant to the functionality of the scripts in this submodule.*

5. Close the Vitis Commandline Tool/Vitis Unified IDE window which you used to recreate
the workspace.

6. Open Vitis Unified IDE either through Vivado's *Tools* menu, either on its own, and
set the Vitis workspace to the repository's `sw` -> `ws` folder.
From this point, the demo can be programmed onto a board, sources can be viewed and
modified as desired.
----

## Quick Checkin Guide

**Important:** *The `checkin.py` can be used multiple times, it will overwrite the existent*
*files and if some new ones appear into an application they are going to be copied too.*

**Note:**
*This section assumes that you have already created a Vitis workspace containing*
*one or more application projects.*

**Note:**
*The checkin script has only been tested with standalone application projects at time of writing.*

1. Close Vitis Unified IDE, if you have it open.

2. The scripts presented in this repository can be run through the use of the
Vitis Commandline Tool 202x.y, for example 2025.1, which comes along with Vitis.
To backup the workspace, enter the following command into the Vitis Console, giving it the
absolute path to the checkin script:

`run <path-to-scripts-repo>checkin.py`

The above functionality can be reproduced from Vitis Unified IDE by launching the terminal from
Terminal -> New Terminal which uses the default command line executable from the OS. If
this is the choosen method, then it will be necessary to give absolute path to the `checkin.py`
file, not relative:

`vitis -s <path-to-scripts-repo>checkin.py`

Alternatively, you can change the Vitis Unified IDE current working directory to the
branch's `sw` submodule and then specify the path to the checkin script as
`scripts\checkin.py`.

3. Close the Vitis Commandline Tool/Vitis Unified IDE window which you used to run the
checkin script.
You can now use the `Git` bash to check what files under the `src` folder have been changed
and what you would need to commit to `Git`.

----
