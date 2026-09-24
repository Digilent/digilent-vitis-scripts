#!/usr/bin/env bash

# This script is useful for cleaning up the 'project'
# directory of a Digilent Vitis-project git repository
###
# Run the following command to change permissions of
# this 'cleanup' file if needed:
# chmod u+x cleanup.sh
###

script_dir=$(dirname "${BASH_SOURCE[0]}")

# Remove directories/subdirectories, but never Git metadata (.git can be a
# directory in a standalone clone or a file in a submodule; either way, do
# not let it be swept up as a generic "other file" below).
find "$script_dir" -mindepth 1 -name '.git' -prune -o -type d -exec rm -rf {} +

# Remove any other files than:
find "$script_dir" -name '.git' -prune -o -type f ! -name 'cleanup.sh'  \
			 ! -name 'cleanup.cmd' \
			 ! -name 'checkin.py'  \
			 ! -name 'checkout.py' \
			 ! -name 'misc.py'	   \
			 ! -name '_vitis.ps1'  \
			 ! -name '_vitis.bat'  \
			 ! -name '_vitis.sh'   \
			 ! -name 'LICENSE'     \
			 ! -name 'README.md'   \
			 ! -name '.gitignore'  \
			   -exec rm -rf {} +
