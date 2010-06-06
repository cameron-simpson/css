#!/usr/bin/perl
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

require 'open3.pl';
require 'flush.pl';

package cs::RStat;

@cs::RStat::ISA=qw();

$cs::RStat::_rstat_pid=main::open3(TO,FROM,'>&STDERR','exec rstat');
($_cmd=$0) =~ s:.*/::;

die "_$cmd: can't attach to rstat: $!" if ! defined $_rstat_pid
				       || ! kill(0,$_rstat_pid);

sub new
	{ my($class,@hosts)=@_;
	  my($now)=time;
	  my(@h,$h,$this);

	  for $h (@hosts)
		{ $this=(bless { NOW => $now }, $class);
		  $this->_RStat($h);
		  push(@h,$this);
		}

	  wantarray ? @h : shift @h;
	}

sub _RStat
	{ my($this,$host)=@_;

	  print TO "$host\n";
	  &'flush(TO);

	  local($_);

	  die "$cmd: RStat::_RStat($host): premature EOF"
		unless defined ($_=<FROM>);

	  die "$cmd: RStat::_RStat($host): out of sync"
		unless /^$host:/;

	  my($field);

	  RSTAT:
	    while (defined ($_=<FROM>))
		{ chomp;
		  last RSTAT unless length;

		  if (! /^(\w+)=/)
			{ die "$cmd: bad data from rstat: [$_]";
			}

		  $field=$1;
		  $_=$';

		  $field=uc($field);
		  if (/\s/)
			{ $this->{$field}=[ grep(length,split(/\s+/)) ];
			}
		  else	{ $this->{$field}=$_;
			}
		}
	}

1;
