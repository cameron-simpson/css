sub basename	# (@pathnames) -> @basenames
	{ local(@paths)=@_;

	  for (@paths)
		{ s,/+$,,;
		  s,.*/,,;
		  length || ($_='.');
		}

	  wantarray ? @paths : pop(@paths);
	}

sub dirname	# (@pathnames) -> @dirnames
	{ local(@paths)=@_;
	  local($pfx);

	  for (@paths)
	  	{ m,^(/?/?)/*,; $pfx=$1; $_=$';	# collect leading slashes
	  	  s,/+$,,;			# strip trailing slashes
	  	  s,[^/]+$,,;			# strip basename
	  	  s,/+$,,;			# strip trailing slashes again
	  	  length($pfx) || ($pfx='./');	# no null paths
		  $_=$pfx.$_;			# prefix + tail
		  s/\/+$//;			# no trailing slashed
		}

	  wantarray ? @paths : pop(@paths);
	}

sub catpath	# (dir,path) -> fullpath
	{ local($_,$path)=@_;

	  if (length == 0)
		{ return $path;
		}
	  elsif (length($path) == 0)
		{ return $_;
		}
	  else
	  { return m,/$, ? "$_$path" : "$_/$path";
	  }
	}

# safe rename - doesn't tromp target file if present
sub rename	# (from,to) -> success
	{ local($from,$to)=@_;
	  local($ok);

	  $ok=0;
	  if (link($from,$to))
		{ $ok=1;
		  if (!unlink($from))
			{ print STDERR "$cmd: unlink($from): $!, $from still linked to $to\n";
			}
		}
	  elsif ($! == &EXDEV)
		# cross device link
		{ if (lstat($to))
			{ print STDERR "$cmd: $to exists\n";
			}
		  else
		  { if (!open(RENAME_FROM,"<$from"))
			{ print STDERR "$cmd: can't open $from for read: $!\n";
			}
		    else
		    { if (!open(RENAME_TO,">$to"))
			{ print STDERR "$cmd: can't open $to for write: $!\n";
			}
		      else
		      { while (<RENAME_FROM>)
				{ print RENAME_TO;
				}

			close(RENAME_TO);

			if (unlink($from))
				{ $ok=1;
				}
			else
			{ print STDERR "$cmd: can't unlink $from ($!), unlinking $to\n";
			  if (!unlink($to))
				{ print STDERR "$cmd: can't unlink $to: $!\n\tboth $from and $to now exist\n";
				}
			}
		      }

		      close(RENAME_FROM);
		    }
		  }
		}
	  else
	  { print STDERR "$cmd: link($from,$to): $!\n";
	  }

	  return $ok;
	}

sub inode	# pathname -> inode or undef
	{ local($dev,$ino,@etc)=stat(shift);

	  defined($ino) ? $ino : undef;
	}

sub tilde	{ local($_)=shift; s:^$ENV{HOME}/:~/:o; $_; }
sub untilde	{ local($_)=shift; s:^~/:$ENV{HOME}/:; $_; }

1;
