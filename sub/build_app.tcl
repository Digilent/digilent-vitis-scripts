# This script will build the application named after the script directory
# Workspace should be set externally

set script [info script] 
set script_dir [file normalize [file dirname $script]]

puts "INFO: Running $script"

set app_name [file tail $script_dir]

# Variables created by checkin.tcl
set configr "<configr>"

app config -set -name $app_name build-config $configr
app build -name $app_name