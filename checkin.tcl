# Run this script extract source files from a Vitis workspace to the src/ directory
# If ::create_path global variable is set, the source files are created there instead of src/

set script [info script] 
set script_dir [file normalize [file dirname $script]]
set auto_boot_domain_exists 0

puts "INFO: Running $script."

if {[info exists ::create_path]} {
	set dest_dir $::create_path
} else {
	set dest_dir [file normalize $script_dir/../src]
}

if {[info exists ::debug_prevent_fileio]} {
	set debug_prevent_fileio $::debug_prevent_fileio
} else {
	set debug_prevent_fileio 0
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


# Get all the values from apps
# We should get  values in the fallowing format {domain *_domain_fsbl platform *_hw_pf}
set apps_values [dict values [app list -dict]]

# Create array for storing workspace hw platforms 
set apps_hw_plf ""

# Create list with all the items from a apps_values
foreach item $apps_values {
		lappend apps_hw_plf $item
	}

# Get all the platforms
foreach item $apps_hw_plf {
	if {[regexp {platform\s+(.*)} $item all value]} {
		lappend pf_names $value }
			}
puts "INFO: Found the following platform projects: $pf_names."

# Do the hardware platforms
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
	set pf_dest [file normalize $dest_dir/$pf/]
	# Get the xsa
	set xsa [file normalize [dict get [platform report -dict $pf] {hw spec}]]
	set pf_subscript [file normalize $pf_dest/5_hw_pf_xsa.tcl]
	
	if {$debug_prevent_fileio == 0} {
		# Check if xsa is linked from src already, copy if not
		if { [file dirname $xsa] ne $pf_dest } {
			file copy -force -- "$xsa" $pf_dest
		}
		file copy -force -- $script_dir/sub/hw_pf_xsa.tcl "$pf_subscript"
		set fid [open "$pf_subscript" a]
	} else {
		puts "TRACE: file copy -force -- \"$xsa\" $pf_dest"
		puts "TRACE: file copy -force -- $script_dir/sub/hw_pf_xsa.tcl \"$pf_subscript\""
		set fid stdout
	}
	
	catch {
		# Export platform config
		puts $fid "\n"
		puts $fid "platform config -extra-compiler-flags fsbl \"[platform config -extra-compiler-flags fsbl]\""
		puts $fid "platform config -extra-linker-flags fsbl \"[platform config -extra-linker-flags fsbl]\""
		puts $fid "platform config -extra-compiler-flags pmufw \"[platform config -extra-compiler-flags pmufw]\""
		puts $fid "platform config -extra-linker-flags pmufw \"[platform config -extra-linker-flags pmufw]\""
	} result options
	if {$debug_prevent_fileio == 0} {
		close $fid
	}
	return -options $options $result
	
	# Do the domains
	set domain_names {}
	if { [catch {domain list}] == 0 } {
		# -dict is undocumented but got it from Xilinx support
		# Get domains from existing keys
		set domains [dict keys [domain list -dict]]
		foreach d_name $domains {
			# Get domain properties
			if { [string first "Auto Generated" [dict get [domain report -dict $d_name] {description}]] >= 0 } {
				puts "INFO: Skipping $d_name, because it is auto-generated."
				set auto_boot_domain_exists 1
				continue;
			}
			set os [dict get [domain report -dict $d_name] {os}]
			if { $os ne "standalone" && $os ne "linux" } {
				puts "INFO: Skipping $d_name, because $os OS is not supported."
				continue;
			}
			lappend domain_names $d_name
		}
	}
	if {[catch {domain list}] != 0 }  {puts "INFO: Found the following domains : $domain_names."}
	
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
		
		# Check what type of domain is 
		set os  [dict get [domain report -dict $d] {os}]
		
		
		if {$os eq "standalone"} {
			set d_subscript $dest_dir/$d/25_standalone_bsp.tcl
		} else {
			set d_subscript $dest_dir/$d/25_linux_domain.tcl
		}  

		puts "INFO: Generating $d_subscript."
		
		if {$debug_prevent_fileio == 0} {
			set dfid [open $d_subscript w]
		} else {
			set dfid stdout
		}
		
		if {$os eq "standalone"} {
			set sfid [open $script_dir/sub/standalone_bsp.tcl r]
		} else {
			set sfid [open $script_dir/sub/linux_domain.tcl r]
		}
		catch {
		

		# Get domain specific properties 
			set proc [dict get [domain report -dict $d] {processor}]
		
			set var_map [list <processor>   $proc   \
                              <platform>    $pf \
							  <os> $os \
							  <auto_boot_domain_exists> $auto_boot_domain_exists  \
					    ]

	
			# Copy the subcript while replacing variables
			while { [gets $sfid line] >= 0 } {
				puts $dfid [string map $var_map $line]
			}
	
			# NOTE: Here we use internal cmds ::json::json2dict and builtin_bsp due to the lack of "bsp listparams -dict"
			# As such, we have implemented our own.



			# Get os bsp settings
			if {$os eq "standalone"} {
				foreach k [dict keys [::json::json2dict [builtin_bsp -listparam -os]]] v [dict values [::json::json2dict [builtin_bsp -listparam -os]]] {
					if {$k != ""} {
						puts $dfid "bsp config $k \"$v\""
					}
				}
				# Get proc bsp settings
				foreach k [dict keys [::json::json2dict [builtin_bsp -listparam -proc]]] v [dict values [::json::json2dict [builtin_bsp -listparam -proc]]] {
					if {$k != ""} {
						puts $dfid "bsp config $k \"$v\""
					}
				}

				# Get lib bsp settings
				foreach k [dict keys [::json::json2dict [builtin_bsp -listparam -lib]]] v [dict values [::json::json2dict [builtin_bsp -listparam -lib]]] {
					if {$k != ""} {
						puts $dfid "bsp config $k \"$v\""
					}
				}
			
		set bspsettings "" 			
			# Get os bsp settings			
			if {$os eq "standalone"} {
			# Catch any exception that may be caused by not having any lib set
				if { [catch {bsp getlibs -dict}] == 0 } {
			#Get all the libs	
					set bsplibs [dict keys [bsp getlibs -dict]]
						puts "INFO: Found the fallowing bsp libs settings for $d : $bsplibs"
					}					
			# Get all the bsp settings
					foreach item $bsplibs {
							lappend bspsettings $item 
							}
							
						puts "INFO: Wee need to set the fallowing bsp libs : $bspsettings for $d"
						
						
					foreach k $bspsettings {
						if {$k != ""} {
							puts $dfid "bsp setlib -name $k"
						}
					}			
				}
		    }

		} result options
		if {$debug_prevent_fileio == 0} {
			close $dfid
		}
		close $sfid
		return -options $options $result
	}
}

# Do the applications
set app_names ""
if { [catch {app list}] != 0 } {
	return -code error "ERROR: Workspace contains no applications"
}

#Get all the keys from apps
set apps [dict keys [app list -dict]] 
foreach a $apps {
	lappend app_names $a
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
		
	
	# The user must select the application language 
	global lang;
	set lang_invalid true
	while { $lang_invalid } {
		puts "WARNING: You must select the application language (${app_name}) in order to continue! Choose an option: (1/2):"
		puts "1.C"
		puts "2.C++"
		set input [gets stdin]
		switch $input {
			1 {
				 set lang "c"
				 set lang_invalid false
			}
			2 {
				 set lang "c++"
				 set lang_invalid false
			}
			default {
				set lang_invalid true
			}
		}
	
	}
	
	
	# Get app create arguments 
	set app_dict [app report -dict $app_name]
	set platform [dict get $app_dict "platform"]
	set domain [dict get $app_dict "Domain"]
	set os [dict get [domain report -dict $domain] {os}]
	set app_configr [app config -name $app_name -get build-config]

	set sysproj [dict get $lookup_sysproj $app_name]
	
	# Copy build_app.tcl with no modifications
	# if {$debug_prevent_fileio == 0} {
	# 	file copy -force -- $script_dir/sub/build_app.tcl $dest_dir/$app_name/145_build_app.tcl
	# } else {
	# 	puts "TRACE: file copy -force -- $script_dir/sub/build_app.tcl $dest_dir/$app_name/145_build_app.tcl"
	# }
	
	# Copy build_app.tcl while replacing <var> identifiers with the corresponding value for a standalone app 
	set var_map_build_app [list <configr> $app_configr]
	
	if { $os eq "standalone" } {
		set in_build_app_file  [open $script_dir/sub/build_app.tcl "r"]
		set out_build_app_file [open $dest_dir/$app_name/145_build_app.tcl "w"]
		}
		
	while {[gets $in_build_app_file s] >= 0} {
		puts $out_build_app_file [string map $var_map_build_app $s]
	}
	
	
	close $in_build_app_file 
	
	if {$debug_prevent_fileio == 0} {
		close $out_build_app_file
	}


	
	
	# Copy standalone_app.tcl while replacing <var> identifiers with the corresponding value for a standalone app 
	set var_map [list <domain>   $domain   \
					  <platform> $platform \
					  <lang>     $lang     \
					  <sysproj>  $sysproj  ]
					  

  
	if { $os eq "standalone" } {
			set infile  [open $script_dir/sub/standalone_app.tcl "r"]
		} else {
			set infile  [open $script_dir/sub/linux_app.tcl "r"]
	}  

	if {$debug_prevent_fileio == 0} {
			if { $os eq "standalone" }  {
				set outfile [open $dest_dir/$app_name/45_standalone_app.tcl "w"]
			} else {
				set outfile [open $dest_dir/$app_name/45_linux_app.tcl "w"]
			}
	} else {
			set outfile stdout
	} 
	 
	puts "INFO: Generating 45_${os}_app.tcl."

	
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
			
			# Standalone apps work without linker-misc, however linux projects need --sysroot=${SYSROOT} to be added as a flag
			set value [app config -get -name $app_name "linker-misc"]
			puts $outfile "app config -set -name \$app_name linker-misc \{$value\}"
			
			# hardcode linker-script link to app src dir
			# Linker script only makes sense for baremetal application
			if {$os eq "standalone"} {
				puts $outfile "app config -set -name \$app_name linker-script \$script_dir/src/lscript.ld"
			}
			
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
				puts "WARNING: $dest_dir/$app_name/src already exists; cannot copy source directory"
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

# When all sources are checked into src/, copy checkout script and README too
if {$debug_prevent_fileio == 0} {
	file copy -force -- [file normalize $script_dir/checkout.tcl] $dest_dir/
	file copy -force -- [file normalize $script_dir/README.md] [file normalize $dest_dir/]
	# Attempt to query git HEAD hash
	if { [catch { set fid [open "|git -C \"$script_dir\" rev-parse HEAD" r] } errmsg] } {
		puts "WARNING: $errmsg"
		puts "WARNING: git command cannot be found (not in PATH?), so could not append $dest_dir/README.md with script versioning info."
	} elseif { [gets $fid hash] eq -1 } {
		puts "WARNING: git error so could not append $dest_dir/README.md with script versioning info."
	} else {
		set dfid [open [file normalize $dest_dir/README.md] a]
		catch {
			puts $dfid "\nThis README.md was created by the following commit hash:\n$hash"
		} result options
		close $dfid
		close $fid
		return -options $options $result
	}
}

# Copy cleanup scripts to ws/, which is the default destination for checkout.tcl
if {$debug_prevent_fileio == 0} {
	if {[file exists $dest_dir/../ws] == 0} {
		file mkdir $dest_dir/../ws
	}
	file copy -force -- [file normalize $script_dir/sub/cleanup._sh] [file normalize $dest_dir/../ws/cleanup.sh]
	file copy -force -- [file normalize $script_dir/sub/cleanup._cmd] [file normalize $dest_dir/../ws/cleanup.cmd]
}

# Copy template gitignore
if {$debug_prevent_fileio == 0} {
	set gitignore [file normalize $dest_dir/../.gitignore]
	if {[file exists $gitignore] == 0} {
		set copy_ok 1
	} else {
		set input [yes_or_no "WARNING: $gitignore exists, overwrite it? (y/n):"]
		if { [string equal -nocase -length 1 $input "n"] } {
			set copy_ok 0
		} else {
			set copy_ok 1
		}
	}
	if {$copy_ok} {
		file copy -force -- [file normalize $script_dir/sub/template.gitignore] $gitignore
	}
}

puts "INFO: Checked in workspace [getws] to $dest_dir"
