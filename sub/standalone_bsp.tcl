# This script will create a standalone domain for a processor and its architecture
# Workspace should be set externally

set script [info script] 
set script_dir [file normalize [file dirname $script]]

puts "INFO: Running $script"

set domain_name [file tail $script_dir]

# Modify these for custom domain/BSP settings

set os "<os>"
set proc "<processor>"
set keep_boot_domain "<auto_boot_domain_exists>"

# Destination platform needs to be made active first
platform active "<platform>"

domain create -name $domain_name -proc $proc -os $os

if {$keep_boot_domain == 1} {
	platform config -create-boot-bsp
	platform write
}

# Customize BSP, this replaces *.mss file
