# This script will create a standalone domain for a processor and its architecture
# Workspace should be set externally

set script [info script] 
set script_dir [file normalize [file dirname $script]]

puts "INFO: Running $script"

set domain_name [file tail $script_dir]

# Modify these for custom domain/BSP settings
set arch "64-bit"
set os "standalone"
set proc "<processor>"

# Destination platform needs to be made active first
platform active "<platform>"

domain create -name $domain_name -proc $proc -arch $arch -os $os

# Customize BSP, this replaces *.mss file
