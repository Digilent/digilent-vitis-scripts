# Run this script extract source files from a Vitis workspace to the src/ directory
# If ::create_path global variable is set, the source files are created there instead of src/

set script [info script] 
set script_dir [file normalize [file dirname $script]]

puts "INFO: Running $script."

if {[info exists ::create_path]} {
	set dest_dir $::create_path
} else {
	set dest_dir [file normalize $script_dir/../src]
}

puts "INFO: Targeting $dest_dir for export"
set var_overwrite_ok [dict create]

proc yes_or_no {msg} {
	while 1 {
		puts -nonewline $msg
		flush stdout;
		if {[gets stdin input] < 0 && [eof stdin]} {
			return -code error "end of file detected"
		}
		if { [regexp -nocase -line -- {^(y|n|yes|no)$} $input match] } {
			break
		}
	}
	return $match
}
proc overwrite_ok {} {
	global var_overwrite_ok;
	global dest_dir;
	if { ![dict exists $var_overwrite_ok "answer"] } {
		puts -nonewline "WARNING: Existing resources in $dest_dir will be overwritten."
		set input [yes_or_no "Continue? (y/n):"]
		if { [string equal -nocase -length 1 $input "y"] } {
			dict set var_overwrite_ok "answer" true
		} else {
			dict set var_overwrite_ok "answer" false
		}
	}
	return [dict get $var_overwrite_ok "answer"]
}

# If workspace is not set, guess and confirm with the user
if { [getws] eq "" } {
	set ws_dir [file normalize $script_dir/../ws]
	puts -nonewline "WARNING: Workspace is not set. I can try checking in the workspace from $ws_dir. "
	set input [yes_or_no "Continue? (y/n):"]
	if { [string equal -nocase -length 1 $input "n"] } {
		return -code ok
	}
	setws $ws_dir
}

# Do the hardware platforms
set pf_names {}
if { [catch {platform list}] == 0 } {
	# -dict is undocumented but got it from Xilinx support
	set pfs [platform list -dict]
	foreach pf $pfs {
		lappend pf_names [dict get $pf Name]
	}
}
puts "INFO: Found the following platform projects: $pf_names."

foreach pf $pf_names {
	if { [file exists $dest_dir/$pf/] } {
		if { ![overwrite_ok] } {
			puts "INFO: Ignoring [file normalize $dest_dir/$pf/] since already exists."
			continue;
		}
	} else {
		file mkdir $dest_dir/$pf
	}
	# For some reason it needs to be made active for report to work
	platform active $pf
	# Get the xsa
	set xsa [dict get [platform report -dict $pf] {hw spec}]
	file copy -force -- "$xsa" $dest_dir/$pf/
	set pf_subscript $dest_dir/$pf/5_hw_pf_xsa.tcl
	file copy -force -- $script_dir/sub/hw_pf_xsa.tcl "$pf_subscript"
	set fid [open "$pf_subscript" a]
	catch {
		# Export platform config
		puts $fid "\n"
		puts $fid "platform config -extra-compiler-flags fsbl \"[platform config -extra-compiler-flags fsbl]\""
		puts $fid "platform config -extra-linker-flags fsbl \"[platform config -extra-linker-flags fsbl]\""
		puts $fid "platform config -extra-compiler-flags pmufw \"[platform config -extra-compiler-flags pmufw]\""
		puts $fid "platform config -extra-linker-flags pmufw \"[platform config -extra-linker-flags pmufw]\""
	} result options
	close $fid
	return -options $options $result
	
	# Do the domains
	set domain_names {}
	if { [catch {domain list}] == 0 } {
		# -dict is undocumented but got it from Xilinx support
		set domains [domain list -dict]
		foreach d $domains {
			set d_name [dict get $d {Name}]
			# Get domain properties
			if { [string first "Auto Generated" [dict get [domain report -dict $d_name] {description}]] >= 0 } {
				puts "INFO: Skipping $d_name, because it is auto-generated."
				continue;
			}
			set os [dict get [domain report -dict $d_name] {os}]
			if { $os ne "standalone" } {
				puts "INFO: Skipping $d_name, because $os OS is not supported."
				continue;
			}
			lappend domain_names $d_name
		}
	}
	if {[llength $domain_names] != 0} {puts "INFO: Found the following domains : $domain_names."}
	
	foreach d $domain_names {
		if { [file exists $dest_dir/$d/] } {
			if { ![overwrite_ok] } {
				puts "INFO: Ignoring [file normalize $dest_dir/$d/] since already exists."
				continue;
			}
		} else {
			file mkdir $dest_dir/$d
		}
		# For some reason it needs to be made active
		domain active $d
	
		set d_subscript $dest_dir/$d/25_standalone_bsp.tcl
		puts "INFO: Generating $d_subscript."
		
		set dfid [open $d_subscript w]
		set sfid [open $script_dir/sub/standalone_bsp.tcl r]
		catch {
			# Get domain properties
			set proc [dict get [domain report -dict $d] {processor}]
			# Write to subscript's beginning
			puts $dfid "set proc \"$proc\""
			# Copy the rest of the subscript
			while { [gets $sfid line] >= 0 } {
				puts $dfid $line
			}
			# Only SOME of the config params possible
			set bsp_configs {\
			clocking\
			enable_sw_intrusive_profiling\
			hypervisor_guest\
			lockstep_mode_debug\
			microblaze_exceptions\
			predecode_fpu_exceptions\
			profile_timer\
			sleep_timer\
			stdin\
			stdout\
			ttc_select_cntr\
			zynqmp_fsbl_bsp\
			archiver\
			assembler\
			compiler\
			compiler_flags\
			exec_mode\
			extra_compiler_flags\
			}
			foreach c $bsp_configs {
				puts $dfid "bsp config $c \"[bsp config $c]\""
			}
			
		} result options
		close $dfid
		close $sfid
		return -options $options $result
	}
}

if {[info exists ::debug_prevent_fileio]} {
	set debug_prevent_fileio $::debug_prevent_fileio
} else {
	set debug_prevent_fileio 0
}

# Do the applications
set app_names ""
if { [catch {app list}] != 0 } {
	return -code error "ERROR: Workspace contains no applications"
}
set apps [app list -dict]
foreach a $apps {
	puts $a
	lappend app_names [dict get $a Name]
}
puts "INFO: Found the following applications: $app_names"

# For some reason, cannot look up sysproj association from app settings
# instead, build an app-keyed dict (lookup_sysproj) from the sysproj side
puts "INFO: Building sysproj lookup dict"
set sysprojs [sysproj list -dict]
foreach s $sysprojs {
	set sysproj_name [dict get $s Name]
	set sysproj_dict [sysproj report -name $sysproj_name -dict]
	set sysproj_apps [dict get $sysproj_dict applications]
	foreach a $sysproj_apps {
		puts "INFO: app $a in sysproj $sysproj_name"
		dict set lookup_sysproj $a $sysproj_name
	}
}

foreach app_name $app_names {
	# Make src/app directory
	if {$debug_prevent_fileio == 0} {
		if { [file exists $dest_dir/$app_name/] } {
			if { ![overwrite_ok] } {
				puts "INFO: Ignoring [file normalize $dest_dir/$app_name/] since already exists."
				continue;
			}
		} else {
			file mkdir $dest_dir/$app_name
		}
	} else {
		puts "TRACE: file mkdir $dest_dir/$app_name"
	}
	
	# Get app create arguments
	set app_dict [app report -dict $app_name]
	set platform [dict get $app_dict "platform"]
	set domain [dict get $app_dict "Domain"]
	set lang "c"; # FIXME; cannot see an obvious way of automatically determining the language
	puts "WARNING: ${app_name}'s language set to c; if c++ is required, please edit its standalone_app script"
	if {$lang == "c"} {
		set template "{Empty Application}"
	} elseif {$lang == "c++"} {
		set template "{Empty Application (C++)}"
	} else {
		return -code error "invalid language selection (must be c or c++): lang=$lang"
	}
	set sysproj [dict get $lookup_sysproj $app_name]
	
	# Copy build_app.tcl with no modifications
	if {$debug_prevent_fileio == 0} {
		file copy -force -- $script_dir/sub/build_app.tcl $dest_dir/$app_name/145_build_app.tcl
	} else {
		puts "TRACE: file copy -force -- $script_dir/sub/build_app.tcl $dest_dir/$app_name/145_build_app.tcl"
	}
	
	# Copy standalone_app.tcl while replacing <var> identifiers with the corresponding value
	set var_map [list <domain>   $domain   \
					  <platform> $platform \
					  <lang>     $lang     \
					  <sysproj>  $sysproj  ]
					  
	set infile [open $script_dir/sub/standalone_app.tcl "r"]
	if {$debug_prevent_fileio == 0} {
		set outfile [open $dest_dir/$app_name/45_standalone_app.tcl "w"]
	} else {
		set outfile stdout
	}
	puts "INFO: Generating $dest_dir/$app_name/45_standalone_app.tcl"
	
	catch {
	
		while {[gets $infile s] >= 0} {
			puts $outfile [string map $var_map $s]
		}
		
		# Add configuration settings to the end of the file
		set orig_config [app config -get -name $app_name "build-config"]
		
		foreach bc "Release Debug" {
			app config -set -name $app_name "build-config" $bc
			puts $outfile "app config -set -name \$app_name build-config $bc"
			
			# get/set configs that support -set
			set value [app config -get -name $app_name "assembler-flags"]
			puts $outfile "app config -set -name \$app_name assembler-flags \{$value\}"
			
			set value [app config -get -name $app_name "compiler-misc"]
			puts $outfile "app config -set -name \$app_name compiler-misc \{$value\}"
			
			set value [app config -get -name $app_name "compiler-optimization"]
			puts $outfile "app config -set -name \$app_name compiler-optimization \{$value\}"
			
			# -set not supported for define-compiler-symbols
			# -get list is semicolon separated
			# default no symbols means that each that is in current project can be added to the list to reproduce it
			set symbols [app config -get -name $app_name "define-compiler-symbols"]
			set symbols [split $symbols ";"]
			foreach sym $symbols {
				puts $outfile "app config -add -name \$app_name define-compiler-symbols \{$sym\}"
			}
			
			# hardcode include-path to app src dir
			puts $outfile "app config -add -name \$app_name include-path \$script_dir/src"
			
			# -set not supported for libraries
			# -get list is semicolon separated
			# default no libs means that each that is in current project can be added to the list to reproduce it
			set libs [app config -get -name $app_name "libraries"]
			set libs [split $libs ";"]
			foreach lib $libs {
				puts $outfile "app config -add -name \$app_name libraries $lib"
			}
			
			# library-search-path not implemented; consider adding dedicated folders as in repo/vivado-library, repo/local
			puts "WARNING: ${app_name} ${bc}'s library-search-path config will not be set by checkout"
			
			# hardcode linker-script link to app src dir
			puts $outfile "app config -set -name \$app_name linker-script \$script_dir/src/lscript.ld"
			
			# -set not supported for undef-compiler-symbols
			set symbols [app config -get -name $app_name "undef-compiler-symbols"]
			set symbols [split $symbols ";"]
			foreach sym $symbols {
				puts $outfile "app config -add -name \$app_name undef-compiler-symbols $sym"
			}
		}
		
		app config -set -name $app_name "build-config" $orig_config
		
		puts "WARNING: application configs written to checkout script"
		puts "         the following settings must be manually added if non-default:"
		puts "         include-path (other than app src directory)"
		puts "         library-search-path"
		
		set appsrc_path [getws]/$app_name/src
		if {$debug_prevent_fileio == 0} {
			if {[file exists $dest_dir/$app_name/src] == 0} {
				file copy -force -- $appsrc_path $dest_dir/$app_name
			} else {
				puts "WARNING $dest_dir/$app_name/src already exists; cannot copy source directory"
			}
		} else {
			puts "file copy -force -- $appsrc_path $dest_dir/$app_name"
		}
		puts "INFO: application sources placed in $appsrc_path copied to repo <app>/src directory"
		puts "WARNING: non-local sources not copied! Double-check that all sources are included in"
		puts "         version control and are soft-linked into the project/s on checkout"
	} result options
	
	close $infile
	# don't close stdout
	if {$debug_prevent_fileio == 0} {
		close $outfile
	}
	return -options $options $result
}