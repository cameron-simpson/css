#!/usr/bin/perl
#
# Miscellaneous routines.
#	- Cameron Simpson <cs@zip.com.au> 31jul1996
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

$::DEBUG=0 if ! defined $::DEBUG;

require 'flush.pl';	# for ::flush()
use cs::Math;

package cs::Misc;

($::cmd=$0) =~ s:.*/::;
$::warningContext=$::cmd;

undef %cs::Misc::_used_package;
sub ::need
{ my(@pkgs)=@_;
  my($cp)=caller;

  for my $package (@pkgs)
  {
    if (! $cs::Misc::_used_package{$package})
    {
      eval "package $cp; use $package";
      die $@ if $@;
      $cs::Misc::_used_package{$package}=1;
    }
  }

}

# debug_level:
#   0 - quiet
#   1 - progress reporting
#   2 - verbose progress reporting
#   3 or more - more verbose, and activates the debug() function
#
$cs::Misc::debug_level=0;
$cs::Misc::debug_level=1 if -t STDERR;
$cs::Misc::debug_level=3 if exists $::ENV{DEBUG}
			 && length $::ENV{DEBUG}
			 && $::ENV{DEBUG} ne 0;

sub ::debugif
{ my($level)=shift(@_);
  warn(join(' ',@_)."\n") if $cs::Misc::debug_level >= $level;
}
sub ::progress	{ ::debugif(1,@_); }
sub ::verbose	{ ::debugif(2,@_); }
sub ::debug	{ ::debugif(3,@_); }

sub ::log
{ local($_)=join('',@_);
  $_.="\n" unless /\n$/;
  ::debug($_);
}

sub ::member
{ my($str)=shift;
  scalar(grep($_ eq $str,@_));
}

# uniq, preserving order (drop trailing dups)
sub ::uniq
{
  ## if (! @_)
  ##	{my(@c)=caller; warn "uniq(@_) from [@c]";}

  my @u;

  if (@_ < 2)
  { @u=@_;	# optimisation
  }
  else
  {
    my %u;

    for my $k (@_)
    {
      if (! defined $k)
      { ## my(@c)=caller;warn "undef in uniq(@_) from [@c]";
      }
      elsif (! exists $u{$k})
      { $u{$k}=1;
	push(@u,$k);
      }
    }
  }

  wantarray ? @u : [ @u ];
}

sub ::max	{ cs::Math::max(@_) }
sub ::min	{ cs::Math::min(@_) }

# merge one hash into another
sub ::addHash($$)
{ my($h1,$h2)=@_;

  for my $k (keys %$h2)
  { $h1->{$k}=$h2->{$k};
  }
}

# remove keys of one hash from another
sub ::subHash($$)
{ my($h1,$h2)=@_;

  for my $k (keys %$h2)
  { delete $h1->{$k};
  }
}

sub ::detab($;$)	# (tabbed,tabsize) -> untabbed
{ my($line,$tabsize)=@_;
  $tabsize=8 if ! defined $tabsize;

  if (! defined $line)
  { ::need(cs::DEBUG);
    warn "\$line not defined\n";
    cs::DEBUG::pstack();
    die;
  }

  return '' if ! length $line;

  local($_);

  # Bug in regexps?
  # s/\t/' ' x ($tabsize-(length($`)%$tabsize))/eg;

  $_='';
  ## {my(@c)=caller;warn "line=[$line] from [@c]";}
  for my $chunk (split(/\t/,$line))
  { $_.=$chunk;
    $_.=(' ' x ($tabsize-(length($_) % $tabsize)));
  }

  s/[ \t]+$//;

  $_;
}

# code courtesy of Graham Barr <gbarr@pobox.com>.
sub ::reftype
{ my $ref = shift;

  return undef unless ref($ref);

  my $type;

  foreach $type (qw(SCALAR ARRAY HASH CODE IO)) {
	return $type if UNIVERSAL::isa($ref,$type);
  } 
  return undef;
}

sub tmpDir
{ defined $ENV{TMPDIR}
    ? $ENV{TMPDIR}
    : defined $ENV{TMP}
      ? $ENV{TMP}
      : -d "$ENV{HOME}/tmp/."
	? "$ENV{HOME}/tmp"
	: "/usr/tmp";
}

# generate a new file handle name
$cs::Misc::_MkHandle='Handle00000';
sub mkHandle
{
  # warn "depreciated mkHandle($oldStyle) called from ["
  #     .join(' ',caller)."]";

  my($oldStyle)=shift;
  $oldStyle=0 if ! defined $oldStyle;

  my($h)="cs::Misc::".$cs::Misc::_MkHandle++;
  $h =~ s/::/'/g if $oldStyle;
  $h;
}

sub editor(;$)
{ my($dflt)=@_;
  $dflt='vi' if ! defined $dflt;

  defined $ENV{EDITOR} ? $ENV{EDITOR} : $dflt;
}

sub openr
{ my($FILE,$path)=@_;

  open( $FILE,
	      ( $path =~ /\.gz$/
		? "gunzip <'$path' |"
		: $path =~ /\.Z$/
		  ? "uncompress <'$path' |"
		  : $path =~ /\.bz2$/
		    ? "bunzip2 <'$path' |"
		    : "< $path\0"
	      )
      );
}

sub openw
{ my($FILE,$path)=@_;

  open( $FILE,
	      ( $path =~ /\.gz$/
		? "| gzip -9 >'$path'"
		: $path =~ /\.Z$/
		  ? "| compress >'$path'"
		  : $path =~ /\.bz2$/
		    ? "| bzip2 -9 >'$path'"
		    : "> $path\0"
	      )
      );
}

1;
