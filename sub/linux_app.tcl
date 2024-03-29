# This script will create an application named after the script directory
# based on the C "Empty Application" template and for a specific processor
# It imports all source files from the src subdirectory as LINKS.
# BUG 2020.1: linker script imported as link won't build, import by setting
# linker-script app config as a workaround.
# It also sets some C/C++ build settings
# Workspace should be set externally

set script [info script] 
set script_dir [file normalize [file dirname $script]]

puts "INFO: Running $script"

set app_name [file tail $script_dir]

# Variables created by checkin.tcl
set lang "<lang>"
set domain "<domain>"
set platform "<platform>"
set sysproj "<sysproj>"
set os "linux"
	

# Handle dependent variables
if {$lang == "c"} {
	set template "Linux Empty Application"
} elseif {$lang == "c++"} {
	set template "Empty Application (C++)"
} else {
	return -code error "invalid language selection in [file tail $script]; $lang should be c or c++"
}


# unused `app create` arguments:
# -os, -arch, and -proc are inferred from -domain?
# -hw conflicts with -platform usage
# -template must be specified, otherwise Hello World by default
app create -name $app_name -lang $lang -template $template -domain $domain -platform $platform -sysproj $sysproj -os $os

# Linux Applicationdoes not create lscript.ld 
# delete all files created by the template
if { [catch {file delete -- {*}[glob -directory [file normalize [getws]/$app_name/src] *]} result] } {
    puts "INFO: emptying src/ dir after project creation failed with $result"
}

importsources -name $app_name -path $script_dir/src -soft-link

# Set project settings per build configuration (handled by checkin)