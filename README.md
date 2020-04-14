# digilent-vitis-scripts
Set of scripts for managing Vitis workspaces with git.

## Glossary
  * XSCT = Xilinx Software Command-Line Tool
  * XSA = Xilinx Shell Architecture, handoff file including all relevent data from a Vivado design, including address maps, instantiated IP, etc.
  * TODO


----
## Quick Guide

TODO: Simple instructions for cloning and checking out a repository using this as a submodule

Repositories using this submodule should be cloned recursively (`git clone --recursive <URL>`), or recursively initialized and updated, if already cloned non-recursively (`git submodule update --init --recursive`).

When launching Vitis, whether through Vivado's Tools menu, or on its own, the workspace should be specified as the parent repository's sw/workspace folder.

Individual scripts present in this repository can be run through the use of XSCT, which is built into Vitis. This tool can be opened from the Vitis GUI through the *Xilinx > XSCT Console* option in the menu bar at the top of the window. Upon launch, XSCT's current working directory is set to the Vitis install directory. To recreate the workspace, the following sequence of commands is recommended:

`cd [getws]; source ../scripts/create_workspace.xsct.tcl`

**Note:** *The current working directory is irrelevant to the functionality of the scripts in this submodule. The cd command is used only to simplify the path used in the source command.*

----
## File Structure

The parent repository must contain all Vitis-related information in one directory, which will be referred to as `sw`. This folder must contain the following:

  * `workspace` - Working directory for the local repository.
  * `scripts` - digilent-vitis-scripts, this submodule.
  * `workspace_info.xsct.tcl` - Script for the Xilinx Software Command-Line Tool that contains information about the workspace not handled by source files.
  * `handoff/*.xsa` - Single handoff file, as exported from Vivado. Used to recreate the platform project.
  * `app/<app name>/<sources>` - Application project sources for each application project present in the workspace.
  * `bsp/<domain name>/*.mss` - MSS files for each domain present in the (single) platform project.
  * `lib` - *Placeholder* directory intended for submodule libraries depended upon by applications in the workspace.

Several notes must be made about this model and its current implementation:
  - Multiple application projects are not currently handled. This is a high priority issue.
  - It does not currently handle modified FSBL sources or BIF files.
  - Several settings are not able to be automatically resolved, requiring that the workspace_info script be manually edited - particularly the application project's language and template .

----
## Scripts
### create_workspace.xsct.tcl

Populates the parent repository's sw/workspace folder with a Vitis Workspace using sources and information pulled from the parent repository.

----
### config_workspace.xsct.tcl
#### Intent

Pulls changes to sources without symlinks from version control into the workspace.

#### Current Implementation

Updates the platform based on the XSA file and each domain based on the corresponding MSS files.

----
### checkin_workspace.xsct.tcl

Not yet implemented.

Writes a mostly-complete workspace_info.tcl script into the parent repository, collects and copies workspace sources and configuration files into the parent repo's sw directory. Writes a template gitignore into the parent repo's sw directory.