require 'cs/pathname.pl';

# ensure a directory exists
sub mkdir	# (dir) -> ok
	{ local($dir)=shift;

	  # print STDERR "isdir($dir)?\n";
	  -d "$dir/."
		|| (&mkdir(&dirname($dir))
		 && (-d "$dir/."
		  || ( # (print STDERR "mkdir($dir)\n"),
			mkdir($dir,0777)
		     )
		    )
		   )
	  ;
	}

sub dirents	# (dir) -> (@entries)
	{ opendir(cs_dir'DIR,$_[0]) || return undef;
	  local(@entries)=grep($_ ne '.' && $_ ne '..',readdir(cs_dir'DIR));
	  closedir(cs_dir'DIR);
	  @entries;
	}

undef %dirents;
sub cacheddirents	# (dir) -> @dirents
	{ local($_)=shift;
	  local(@entries);

	  if (defined($dirents{$_}))
		{ @entries=split(/\0/,$dirents{$_});
		}
	  else
	  { @entries=&dirents($_);
	    $dirents{$_}=join("\0",@entries);
	  }

	  @entries;
	}

1;
