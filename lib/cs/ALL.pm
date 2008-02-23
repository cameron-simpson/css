#!/usr/bin/perl
#
# Load all subclasses of a class.
#	- Cameron Simpson <cs@zip.com.au> 23jul96
#
# Usage:
#  In the top-level Foo.pm file, put
#	use cs::ALL;
#	...
#	package Foo;
#	...
#	cs::ALL::useAll();
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Pathname;
use File::Find;

package cs::ALL;

sub useAll
	{ # print STDERR "useAll(@_)\n";
	  my($file,$pkg)=@_;
	  my(@call);

	  if (@_ < 2)
		{ @call=caller;
		}

	  if (! defined $file)	{ $file=$call[1]; }
	  if (! defined $pkg)	{ $pkg=$call[0]; }

	  my($dir);
	  ($dir=$file) =~ s/\.pm$//;

	  my(@files);

	  main::find(
		sub { /\.pm$/ && push(@files,$File::Find::name) },
		$dir);

	  for (@files)
		{ # print STDERR "$_ -> ";
		  substr($_,$[,length($dir)+1)='';
		  s=\.pm$==;
		  s=/=::=g;
		  $_="${pkg}::$_";
		  # print STDERR "$_\n";
		}

	  my($ok)=1;

	  for (@files)
		{ eval "use $_";
		  if ($@)
			{ $ok=0;
			  die $@;
			}
		}

	  $ok;
	}

1;
