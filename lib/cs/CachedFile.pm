#!/usr/bin/perl
#
# Monitor a file (well, anything stat()able really) and call a
# reload routine if it gets updated.
#	- Cameron Simpson <cs@zip.com.au> 22oct98
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::CachedFile;

@cs::CachedFile::ISA=qw();

sub new($$$)
{ my($class,$path,$reloadfn,$state)=@_;
  $state={} if ! defined $state;

  bless { PATH	=> $path,
	  RELOAD=> $reloadfn,
	  MTIME => undef,
	  STATE	=> $state,
	}, $class;
}

sub State { shift->{STATE}; }
sub Path  { shift->{PATH}; }

sub Reset
{ my($this)=@_;

  undef $this->{MTIME};
  $this->{STATE}={};
}

sub Poll
{ my($this)=@_;

  my $path = $this->Path();

  my(@s);

  if (! (@s=stat $path))
  { $this->Reset();
  }
  elsif (! defined $this->{MTIME}
      || $s[9] > $this->{MTIME}
	)
  { $this->Reset();
    $this->{MTIME}=$s[9];
    &{$this->{RELOAD}}($this);
  }

  $this->State();
}

1;
