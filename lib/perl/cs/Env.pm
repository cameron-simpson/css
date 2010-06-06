#!/usr/bin/perl
#
# Tweak the environment.
#	- Cameron Simpson <cs@zip.com.au> 
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Env;

@cs::Env::ISA=qw();

{ my($name,$passwd,$uid,$gid,$quota,$comment,$gecos,$dir,$shell)
	=getpwuid($>);
  my($fullname)=$gecos;
  my($hostname)=$ENV{HOSTNAME};

  $fullname =~ s/,.*//;
  $fullname =~ s/^\s+//;
  $fullname =~ s/\s+$//;

  if (! length $hostname)
	{ ($hostname=`hostname`) =~ s/\s+//g;
	}

  dflt(    USER,	$name,
	    HOME,	$dir,
	    SHELL,	$shell,
	    NAME,	$fullname,
	    HOSTNAME,	$hostname,
	    PAGER,	'less',
	    EDITOR,	'vi',
	    CONSOLE,	'/dev/console');
}

sub dflt	# (key,default,key,default,...) -> void
	{ my($key,$dflt);

	  while (($key,$dflt)=splice(@_,$[,2))
		{ if (! defined($ENV{$key})
		   || !length($ENV{$key}))
			{ $ENV{$key}=$dflt;
			}
		}
	}

sub set	# (key,value,...) -> void
	{ my($key,$value);

	  while (($key,$value)=splice(@_,$[,2))
		{ $ENV{$key}=$value;
		}
	}

1;
