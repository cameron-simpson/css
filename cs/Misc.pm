#!/usr/bin/perl
#
# Miscellaneous routines.
#	- Cameron Simpson <cs@zip.com.au> 31jul96
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

require 'flush.pl';	# for ::flush()
use cs::Math;

($::cmd=$0) =~ s:.*/::;

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

sub ::log
{ my(@c)=caller;
  warn join('',@_)." at @c";;
}

## if ($ENV{SYSTEMID} eq 'zip')
## { $cs::SiteName='zip.com.au';
##   $cs::Organisation='ZipWorld';
##   ## $cs::RootDN="o=$CISRA::Organisation, c=AU";
## }
## elsif ($ENV{SYSTEMID} eq 'cisra')
## { ::need(CISRA::Misc);
##   $cs::SiteName='zip.com.au';
##   $cs::Organisation='Canon Information Systems Research Australia';
##   $cs::RootDN="o=$CISRA::Organisation, c=AU";
## }
## elsif ($ENV{USER} eq 'cameron')
## { $cs::SiteName='zip.com.au';;
##   $cs::Organisation='ZipWorld';
##   ## $cs::RootDN="o=$CISRA::Organisation, c=AU";
## }

## sub ::new
## 	{ my($class)=shift;
## 	  warn "N1($class @_)";
## 	  ::need($class);
## 	  warn N2;
## 	  new $class @_;
## 	}

sub member
{ my($str)=shift;
  scalar(grep($_ eq $str,@_));
}

# uniq, preserving order (drop trailing dups)
sub ::uniq
{
  ## if (! @_)
  ##	{my(@c)=caller; warn "uniq(@_) from [@c]";}

  my(%u,@u);
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

  wantarray ? @u : [ @u ];
}

sub max	{ cs::Math::max(@_) }
sub min	{ cs::Math::min(@_) }

sub detab($;$)	# (tabbed,tabsize) -> untabbed
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
sub reftype
{ my $ref = shift;

  return undef unless ref($ref);

  my $type;

  foreach $type (qw(SCALAR ARRAY HASH CODE IO)) {
	return $type if UNIVERSAL::isa($ref,$type);
  } 
  return undef;
}

package cs::Misc;

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

1;
