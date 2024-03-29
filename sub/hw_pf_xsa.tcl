# This script will take the first *.xsa file near it and create a new platform based on it
# Workspace should be set externally

set script [info script] 
set script_dir [file normalize [file dirname $script]]

puts "INFO: Running $script"

set hw_src [lindex [glob -nocomplain $script_dir/*.xsa] 0]

puts "INFO: Found $hw_src"

set hw_name [file tail $script_dir]

# You may add extra options for custom platform creation
platform create -name "$hw_name" -hw "$hw_src" -no-boot-bsp
