#!/usr/bin/perl
#
# General extraction code.
#	- Cameron Simpson <cs@zip.com.au> 30may1997
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

package cs::Extractor;

sub new
	{ my($class,$dir)=@_;
	  $dir=cs::Misc::tmpDir()."/x$$" if ! defined $dir;

	  if (! mkdir($dir,0777))
		{ 
		  warn "mkdir($dir): $!";
		  return undef;
		}

	  bless { DIR		=> $dir,
		  N		=> 0,
		  ENTRIES	=> [],
		}, $class;
	}

sub Entries
	{ @{shift->{ENTRIES}};
	}

sub Sink
	{ my($this)=@_;

	  my($n)=$this->{N}+1;
	  my($path)="$this->{DIR}/$n";

	  my($sink)=new cs::Sink (PATH, $path);

	  return undef if ! defined $sink;

	  $this->{N}=$n;

	  $sink;
	}

sub Extract
	{ my($this,$src)=@_;
	  my($sink)=$this->Sink();

	  return undef if ! defined $sink;

	  my($data);

	  while (defined ($data=$src->Read()) && length($data))
		{ $sink->Put($data);
		}

	  push(@{$this->{ENTRIES}},$sink->{PATH});

	  $sink->{PATH};
	}

1;
