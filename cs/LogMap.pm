#!/usr/bin/perl
#
# Logging subsystem.
#	- Cameron Simpson <cs@zip.com.au>, 21jul94
#
# The environment variable $HOSTNAME is prefixed to 
# &logmap(mapfile)
#	Set name of file to consult for mapping info.
#	Default: $HOME/.logmap
# &loadlogmap($clearmap) -> ok
#	Reload the map file, first forgetting the old map
#	if $clearmap.
# &map($logical,$logspec) -> ok
#	Save a mapping $logical->$logspec, updating the map file.
# &logto(FILE,$logical) -> ok
#	Attach FILE to the log specified by the logspec associated with
#	$logical.
#	If the logspec commences with '>' or '|' it is passed directly to
#		open().
#	If the logspec matches ^{.*}$ then it is taken to be the body
#		of a subroutine to open a log. FILE (prefixed with the
#		caller's package name if necessary) is passed as the
#		parameter. The subroutine should return success or failure.
#		The subroutine runs in the logmap package.
#	Otherwise ">> logspec\0" is passed to open().
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Shell;
use cs::Sink;
use cs::Source;
use cs::Persist;

package cs::LogMap;

$cs::LogMap::_LogMap=defined $ENV{LOGMAP}
		? $ENV{LOGMAP}
		: "$ENV{HOME}/.logmap";

sub finish
	{ cs::Persist::finish();

	  my $log;
	  for my $mappath (keys %cs::LogMap::_Maps)
		{
		  $log=$cs::LogMap::_Maps{$mappath}->Sync($mappath);
		}
	}
sub new
	{ my($class,$mappath)=@_;
	  $mappath=$cs::LogMap::_LogMap if ! defined $mappath;

	  my $this = cs::Persist::db($mappath);

	  bless $this, $class;

	  $cs::LogMap::_Maps{$mappath}=$this;

	  $this;
	}

sub Sync
	{ # warn "Sync(@_)";
	  my($this,$mappath)=@_;

	  my $shfile = "$mappath.sh";

	  # warn "shfile=$shfile";
	  if (stat $shfile
	   && -f _
	   && open(LOGSH,"> $shfile"))
		{ # warn "writing to $shfile";
		  for (grep(/^[a-zA-Z_]\w*$/, keys %$this))
			{ print LOGSH "log_$_=",
				      cs::Shell::quote($this->{$_}),
				      "\n";
			}
		  close(LOGSH);
		}
	}

sub LogTo	# logical-name -> cs::Sink
	{ my($this,$log)=@_;

	  return undef if ! exists $this->{$log};

	  new cs::Sink APPEND, $this->{$log};
	}

sub Tail
	{ my($this,$log)=@_;

	  return undef if ! exists $this->{$log};

	  new cs::Source TAIL, $this->{$log};
	}

1;
