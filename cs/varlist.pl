#!/usr/bin/perl
#
# Code to manipulate varlists a la cst/win/ptyserv.
#

package varlist;

sub setvar	# (var,val,@list) -> @newlist
	{ local($var,$val,@v)=@_;

	  $var.='=';
	  for (@v)
		{ if (substr($_,$[,length($var)) eq $var)
			{ $_=$var.$val;
			  return @v;
			}
		}

	  unshift(@v,$var.$val);

	  @v;
	}

sub varsof	# (@list) -> @varnames
	{ local(@names)=();

	  for (@_)
		{ /^([^=]*)=/ && push(@names,$1);
		}

	  @names;
	}

sub getvar	# (var,@list) -> val or undef
	{ local($var,@v)=@_;

	  $var.='=';
	  for (@v)
		{ if (substr($_,$[,length($var)) eq $var)
			{ return substr($_,$[+length($var));
			}
		}

	  return undef;
	}

sub v2a	# @varlist -> {...}
	{ local(@v)=@_;
	  local($vlist,$first)=('{',1);
	  local($val);

	  for (@v)
		{ /^(\w+)=/ || die "bad variable in (@v)";
		  $vlist.=($first ? ' ' : ', ').$1.'=';
		  $first=0;
		  $val=$';
		  if ($val =~ /[{}",\s]/)
			{ $val =~ s/["\\]/\\$&/g;
			  $val =~ s/\t/\\t/g;
			  $val =~ s/\n/\\n/g;
			  $vlist.='"'.$val.'"';
			}
		  else
		  { $vlist.=$val;
		  }
		}

	  $vlist.' }';
	}

sub a2v	# $vlist -> ($aftervlist,@v)
	{ local($_)=@_;
	  local(@v)=();

	  if (/^\s*{\s*/)
		{ $_=$';
		  local($var,$val);
		  while (/^(\w+=)(("([^\\]|\\.)*"|[^",\s]*))\s*,?\s*/)
			{ $var=$1; $val=$2; $_=$';
			  if ($val =~ /^".*"/)
				{ $val =~ s/^"(.*)"$/$1/;
				  $val =~ s/\\n/\n/g;
				  $val =~ s/\\t/\t/g;
				  $val =~ s/\\(.)/$&/g;
				}
			  
			  push(@v,$var.$val);
			}

		  if (/^}/)
			{ $_=$';
			}
		  else
		  { $_=$_[$[];
		    @v=();
		  }
		}

	  ($_,@v);
	}

sub pr	# (indent,@list)
	{ local($indent)=shift;
	  local($_);

	  print STDERR "pr varlist indent=$indet, list=[@_]\n";
	  while (defined($_=shift))
		{ print ' 'x$indent;

		  if (/^([^=]*)={/)
			{ { local($dummy,@v);

			    ($dummy,@v)=&a2v('{'.$');
			    print $1, "={\n";
			    &pr($indent+2,@v);
			    print ' 'x$indent, "}\n";
			  }
			}
		  else
		  { print $_, "\n";
		  }
		}
	}
1;
