# set variables specific to the repo
set script_dir [file normalize [file dirname [info script]]]
set sw_dir [file dirname $script_dir]
source [file join $sw_dir "workspace_info.vitis.tcl"]

# create a single hardware platform
platform create -name $platform_name -hw $xsa_file -proc ps7_cortexa9_0 -os standalone
platform generate $platform_name
puts "platform $platform_name created"
app create -name $app_name -template $app_template -proc $app_proc -platform $platform_name -domain $app_domain -lang $app_lang
puts "application project $app_name created"
puts "system project ${app_name}_system created"
# note: addsources may have an additional flag that allows it to use a link.
#       this is currently unimplemented in this script
importsources -name $app_name -path $app_sources -linker-script

# save current active domain
set orig_domain [domain active]

foreach mss_file $mss_files {
    set domain_name [file tail [file dirname $mss_file]]
    domain active $domain_name
    domain config -mss $mss_file
    puts "configured domain $domain_name from $mss_file"
}
# restore original active domain
domain active $orig_domain