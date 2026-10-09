#!/usr/bin/env bash
# Remove generated project contents while keeping launcher/helper files.
# Usage: chmod u+x cleanup.sh && ./cleanup.sh

script_dir=$(dirname "${BASH_SOURCE[0]}")

# Remove subdirectories but keep Git metadata.
find "$script_dir" -mindepth 1 -name '.git' -prune -o -type d -exec rm -rf {} +

# Remove non-allowlisted files.
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
			 ! -name '.keep'       \
			   -exec rm -rf {} +
