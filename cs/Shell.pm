#!/usr/bin/perl
#
# Assorted shell related things.
#	- Cameron Simpson <cs@zip.com.au>
#

use strict qw(vars);

use cs::Misc;

package cs::Shell;

# escape special characters for passing to the shell
sub quote
{ my(@args)=@_;
  local($_);

  for (@args)
  { if (! length || /[^-\w.]/)
    { s/'/$&\\$&$&/g;
      $_="'$_'";
    }
  }

  wantarray ? @args : join(' ',@args);
}

sub sputvars
{ my($s,$force,$mode,@vars)=@_;
  @vars=keys %ENV if ! @vars;

  ## warn "sputvars(@_): vars=[@vars]";

  die "$::cmd: bad mode \"$mode\""
	unless grep($_ eq $mode,SH,CSH,PERSIST);

  ::need(cs::Hier) if $mode eq PERSIST;

 VAR:
  for my $var (sort @vars)
  {
    next VAR if ! defined $ENV{$var};

    if (! $force && ($mode eq SH || $mode eq CSH))
    { $s->Put("test -n \"\$$var\" || ");
    }

    my $v;
    $v=quote($ENV{$var}) if $mode eq SH || $mode eq CSH;

    if ($mode eq CSH)
    { $s->Put("setenv $var $v\n");
    }
    elsif ($mode eq SH)
    { $s->Put("{ $var=$v; export $var; }\n");
    }
    else
    { cs::Hier::putKVLine($s,$var,$ENV{$var});
    }
  }
}

sub putvars
{ ::need(cs::Sink);
  my $s = new cs::Sink (FILE,select);
  sputvars($s,@_);
}

sub mkpath { join(':',@_); }

sub statpath
{ my(@p)=@_;
  @p=split(/:/, $p[0]) if @p < 2;

  my %got;	# paths
  my %igot;	# dev:inode
  my @s;

  mkpath(grep(length			# drop empties
	   && !$got{$_}			# new path
	   && ($got{$_}=1)
	   && (@s=stat($_))		# exists
	   && !$igot{"$s[1]:$s[2]"}	# new object
	   && ($igot{"$s[1]:$s[2]"}=1),
		@p));
}

1;
