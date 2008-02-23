#!/usr/bin/perl

# account maintenance routines

require 'open2.pl';
require 'flush.pl';

package acc;

undef %pvinfo, %pvrinfo;	# pv, pv-r strings

$PV_FORMAT='%L <%C> home=%H shell=%S';
for (('supv','topic','stuid','cpath','chome','room','phone',
      'degree','postmaster','forward','caravanpark',
      'baseuid','truename','printer','caravansize'))
	{ $PV_FORMAT.=' '.$_.'=%'.$_;
	}
$PV_FORMAT.=' %numbers %strings';

$PV=0;
sub pv	{ local($recurse)=0;

	  if (!$PV)
		{ &'open2(FROMPV,TOPV,"exec ./pv -m -ix '$PV_FORMAT' -")
			|| die "can't open2(pv)";
		  $PV=1;
		}

	  &_pv;
	}

$PVR=0;
sub pvr	{ local($recurse)=1;

	  if (!$PVR)
		{ &'open2(FROMPVR,TOPVR,"exec ./pv -m -irx '$PV_FORMAT' -")
			|| die "can't open2(pv -r)";
		  $PVR=1;
		}

	  &_pv;
	}

# expect $recurse set up by pv() or pvr()
sub _pv	# @logins -> (@pv)
	{ local($PV)=($recurse ? 'PVR' : 'PV');
	  local($TO,$FROM)=('TO'.$PV, 'FROM'.$PV);
	  local($pv,@pv);

	  for (@_)
		{ $pv=($recurse ? $pvrinfo{$_} : $pvinfo{$_});
		  if (!defined($pv))
			{ &'printflush($TO,$_,"\n");
			  { if (defined($pv=<$FROM>))
				{ chop $pv;

				  if (length($pv))
					{ if ($recurse)	{ $pvrinfo{$_}=$pv; }
					  else		{ $pvinfo{$_}=$pv; }
					}
				  else
				  { undef $pv;
				  }
				}

			    push(@pv,$pv);
			  }
			}
		}

	  @pv;
	}

sub init	# (login,baseuid) -> uid or undef
	{ if (!$INITACC)
		{ &'open2(FROMINITACC,TOINITACC,"exec ./initacc -m -i")
			|| die "can't open2(initacc -m -i)";
		  $INITACC=1;
		}

	  &'printflush(TOINITACC,$_[0],' ',$_[1],"\n");

	  local($_);

	  if (defined($_=<FROMINITACC>))
		{ if (/^\d+$/)
			{ $_+=0;
			}
		  else
		  { return undef;
		  }
		}
	  else
	  { close(FROMINITACC);
	    close(TOINITACC);
	    $INITACC=0;
	    return undef;
	  }

	  $_;
	}

sub withpv	# (pvline,perlfuncname,funcargs)
	{ local($pvline)=shift;
	  local($perlfunc)=shift;
	  local(%pp,$pp_login,$pp_expiry,$pp_home,$pp_shell,@pp_numbers,@pp_strings,@pp_classes,@pp_expiries);

	  &setpp($pvline);
	  &$perlfunc;
	}

# set pp_* to the value extracted from pvline
sub setpp	# (pvline) -> void
	{ local($_)=@_;

	  undef %pp, $pp_login, $pp_expiry, $pp_home, $pp_shell, @pp_numbers, @pp_strings;
	  @pp_classes=();
	  @pp_expiries=();

	  while (1)
		{ # login name
		  last unless /^(\S+)\s+/;
		  $pp_login=$1; $pp{'login'}=$1;
		  $_=$';

		  # classes
		  last unless /^<(\S*)>\s+/;
		  { local($classes)=$1; $_=$';
		    local($_)=$classes;
		    local($exp);
		    local($c,$e);

		    while (/^([^[,]+)\[(([a-z]+|\d{6}))],*/)
			{ ($c,$e)=($1,$2); $_=$';
			  push(@pp_classes,$c);
			  $exp=&exp2time($e);
			  push(@pp_expiries,$exp);
			  if (!defined($pp_expiry) || $exp > $pp_expiry)
				{ $pp_expiry=$exp;
				}
			}
		  }

		  last unless /^home=(\/\S+)\s*/;
		  $pp_home=$1; $pp{'home'}=$1; $_=$';

		  last unless /^shell=(\/\S+)\s*/;
		  $pp_shell=$1; $pp{'shell'}=$1; $_=$';

		  # interesting fields
		  while (/^([a-z]\w*)=(\d+|"(\\["\\]|[^\\"])*")\s*/)
			{ $pp{$1}=$2; $_=$';
			}

		  # all fields
		  # numbers
		  while (/^(\d+)=(\d+),?\s*/)
			{ $pp_numbers[$1]=$2+0; $_=$';
			}

		  # strings
		  { local($ndx,$str);

		    while (/^(\d+)="((\\["\\]|[^\\"])*)",?\s*/)
			{ ($ndx,$str)=($1,$2); $_=$';
			  $str =~ s/\\(["\\])/$1/g;
			  $pp_strings[$ndx]=$str;
			}
		  }

		  last;
		}
	}

sub pp	# (recurse,login) -> ok, sets $pp... as side-effect
	{ local($recurse,$pplogin)=@_;
	  local($pv)=($recurse ? &pvr($pplogin) : &pv($pplogin));

	  return undef if !defined($pv);

	  &setpp($pv);
	  1;
	}

# date to seconds by binary chop
sub ymdhms2time
	{ local($yy,$mm,$dd,$hh,$mi,$ss)=@_;
	  local($tm_sec,$tm_min,$tm_hour,$tm_mday,$tm_mon,$tm_year,@etc);
	  local($t,$bit);

	  $t=0;

	  # start at 2^30; 2^31 seems to cause brain overload
	  for ($bit=1073741824; $bit >= 1; $bit/=2)
		{ $t+=$bit;
		  ($tm_sec,$tm_min,$tm_hour,$tm_mday,$tm_mon,$tm_year,@etc)=gmtime($t);
		  $tm_mon++;

		  # compare dates
		  if ($tm_year > $yy
		   || ($tm_year == $yy
		    && ($tm_mon > $mm
		     || ($tm_mon == $mm
		      && ($tm_mday > $dd
		       || ($tm_mday == $dd
			&& ($tm_hour > $hh
			 || ($tm_hour == $hh
			  && ($tm_min > $mi
			   || ($tm_min == $mi
			    && $tm_sec > $ss))))))))))
			# too big -- we don't want that bit
			{ $t-=$bit;
			}
		}

	  $t;
	}

sub exp2time	# expiry
	{ local($_)=@_;
	  local($t)=0;

	  # yymmdd
	  if (/^(\d\d)(\d\d)(\d\d)$/)
		{ $t=&ymdhms2time($1+0,$2+0,$3+0,0,0,0);
		}
	  # dd/mm/yy
	  elsif (/^(\d\d?)\/(\d\d?)\/(\d\d)$/)
		{ $t=&ymdhms2time($3+0,$2+0,$1+0,0,0,0);
		}
	  elsif ($_ eq 'forever')
		{ $t=0;
		}
	  elsif ($_ eq 'expired')
		{ $t=1;
		}
	  elsif ($_ eq 'structural')
		{ $t=2;
		}
	  else
	  { return undef;
	  }
	}

sub classinfo	# (class[[expiry]]) -> (class,expiry,baseuid,chome)
	{ local($_)=@_;
	  local($class,$expiry,$baseuid,$chome);

	  tr/A-Z/a-z/;
	  if (/^([^[]*)\[(.*)]$/)
		{ $class=$1; $expiry=$2;
		}
	  else
	  { $class=$_;
	    undef $expiry;
	  }

	  local($pvline)=&pvr($class);
	  &withpv(&pvr($class),_rig_classinfo);

	  print STDERR "class=$class, expiry=$expiry, baseuid=$baseuid, chome=$chome\n";

	  ($class,$expiry,$baseuid,$chome);
	}

sub _rig_classinfo
	{ if (!defined($expiry)) { $expiry=$pp_expiry; }
	  $baseuid=$pp{'baseuid'};
	  $chome=$pp{'chome'};
	}

$LIM=0;
sub lim	# (account,@lims) -> (@ok)
	{ local($acc,@lims)=@_;
	
	  if (!$LIM)
		{ open(LIM,"|lim -m -iv -- -l") || die "can't open(lim): $!";
		  $LIM=1;
		}

	  print LIM 'lim';
	  for (@lims)
		{ s/'/'\\''/g;
		  print LIM " '", $_, "'";
		}

	  &'printflush(LIM," '",$acc,"'\n");
	}

1;
