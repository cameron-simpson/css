#!/usr/bin/perl
#
# CSV - comma separated files.
#	- Cameron Simpson <cs@zip.com.au> 03apr2001
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::CSV;	# cs::ALL::useAll();

sub save
{
  my($sink,@r)=@_;

  my %keys;

  # locate all the keys
  for my $r (@r)
  {
    for my $k (keys %$r)
    { $keys{$k}=1;
    }
  }

  my @k = sort keys %keys;

  $sink->Put(ary2csv(@k));

  for my $r (@r)
  { $sink->Put(ary2csv(map(exists $r->{$_} ? $r->{$_} : undef, @k)));
  }
}

sub savehash($$$)
{ my($sink,$hashref,$keyname)=@_;

  my @r = ();
  my $h;
  my $H;

  for my $key (sort keys %$hashref)
  {
    $H = $hashref->{$key};
    $h = {};
    for my $hkey (keys %$H)
    { $h->{$hkey}=$H->{$hkey};
    }

    $h->{$keyname}=$key;

    push(@r, $h);
  }

  save($sink, @r);
}

sub ary2csv
{ my(@a)=@_;

  local($_);

  my $first=1;

  for my $a (@a)
  { if (! $first)	{ $_.=","; }
    if (defined $a)	{ $_.=a2csv($a); }
    $first=0;
  }

  return "$_\n";
}

sub a2csv($)
{ local($_)=@_;

  s/"/$&$&/g;

  return "\"$_\"";
}

sub load($;$)
{
  my($src,$hdrs)=@_;
  $hdrs=1 if ! defined $hdrs;

  my(@r)=();
  local($_);

  LINE:
  while (defined($_=$src->GetLine()) && length)
  { if (/^([^"]*"[^"]*")*[^"]*"[^"]*$/)
    { my $next;
      CONT:
      while (defined($next=$src->GetLine()) && length)
      { $_.=$next;
	last CONT if $next =~ /^([^"]*"[^"]*")*[6']*"[^"]*$/;
      }
    }

    
  }

  return @r;
}

1;
