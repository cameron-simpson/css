#!/usr/bin/perl
#
# Routines to manipulate entries in an assoc array whose values are
# attribute-value pairs. Normally the array will be a DBM file.
# The data format is contrived so as to contain no newlines and thus may be
# trivially saved as text files, one line per entry.
#	- Cameron Simpson, 16jan94
#

package attrdbm;

sub setattrs	# (ARY,key,@attrs) -> void
	{ local($ARY,$key,@attrs)=@_;
	  local($dbm,%attrs,$_);

	  if ($ARY !~ /'/)
		{ local($package,@etc)=caller;
		  $ARY=$package."'".$ARY;
		}

	  eval "\$_=\$$ARY{\$key}";
	  if (defined)
		{ local(@dbmattrs)=&dbm2attrs($_);

		  for (&dbm2attrs($_))
			{ /^(\w+)=/;
			  $attrs{$1}=$';
			}
		}

	  for (@attrs)
		{ /^(\w+)=/;
		  $attrs{$1}=$';
		}

	  local(@keys)=keys %attrs;

	  for (@keys)
		{ delete $attrs{$_} if !length($attrs{$_});
		}

	  @attrs=();
	  for (keys %attrs)
		{ push(@attrs,"$_=$attrs{$_}");
		}

	  eval "\$$ARY{\$key}=&attrs2dbm(\@attrs)";
	}

sub getattr	# (dbmentry,attr) -> value or undef
	{ local(@attrs)=&dbm2attrs(shift);
	  local($_);
	  for (@attrs)
		{ if (/^$_[0]=/)
			{ return $';
			}
		}

	  undef;
	}

sub dbm2attrs	# dbmentry -> @attrs
	{ local($_)=@_;
	  local($key,$value,@attrs);

	  while (/^\s*(\w+)=/)
		{ $key=$1;
		  $_=$';
		  if (/^'(([^']|\\.)*)'/)
			{ $value=$1;
			  $_=$';
			  $value =~ s/\\(.)/$1/g;
			}
		  else
		  { /^[^\s,]*/;
		    $value=$&;
		    $_=$';
		  }

		  push(@attrs,"$key=$value");
		  s/^[\s,]+//;
		}

	  wantarray ? @attrs : shift(@attrs);
	}

sub attrs2dbm	# @attrs -> dbm
	{ local(@attrs)=@_;
	  local($dbm,$_);

	  $dbm='';
	  for (@attrs)
		{ /^\w+=/;
		  $dbm.=' ' if length $dbm;
		  $dbm.=$&;
		  $_=$';
		  if (/^[^\s,']*$/)
			{ $dbm.=$_;
			}
		  else
		  { s/['\\]/\\$&/g;
		    $dbm.="'$_'";
		  }
		}

	  $dbm;
	}

sub attrs	# (dbm,@attributes) -> @attrs
	{ local($dbm,@keys)=@_;
	  local(%keys,$_,@attrs,%values);

	  for (@keys)	{ $keys{$_}=1; }

	  @attrs=&dbm2attrs($dbm);

	  if ($#keys < $[)
		{ for (@attrs)
			{ /^(\w+)=/;
			  $values{$1}=$';
			}

		  return %values;
		}

	  for (@attrs)
		{ /^(\w+)=/;
		  if (defined $keys{$1})
			{ $values{$1}=$';
			}
		}

	  @attrs=();
	  for (@keys)
		{ push(@attrs,"$values{$_}");
		}

	  wantarray ? @attrs : shift(@attrs);
	}

sub fload	# FILE -> %attrs
	{ local($FILE)=shift;
	  local(%a);

	  while (<$FILE>)
		{ chop;
		  if (/^\s*(\S+)\s*(.*)/)
			{ $a{$1}=$2;
			}
		  else
		  { print STDERR "$'cmd: $FILE, line $.: ignoring attrdbm line \"$_\"\n";
		  }
		}

	  %a;
	}

sub fsave	# (FILE,%attrs) -> void
	{ local($FILE,%a)=@_;
	  local($_);

	  for (sort keys %a)
		{ print $FILE $_, ' ', $a{$_}, "\n";
		}
	}

1;
