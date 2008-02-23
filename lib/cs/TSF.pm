#!/usr/bin/perl
#
# TSF - tab separated files.
#	- Cameron Simpson <cs@zip.com.au> 11jun98
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::TSF;	# cs::ALL::useAll();

sub getTSSrc($;$$)	# source -> @{record}
{ my($s,$sep,$botch)=@_;
  $sep="\t" if ! defined $sep;
  $botch=0 if ! defined $botch;

  local($_);

  return () if ! defined ($_=$s->GetLine()) || ! length;

  my(@keys)=splitTSLine($_,$sep);
  my(@r)=();
  my(@f,$r);

  LINE:
    while (defined ($_=$s->GetLine()) && length)
    {
      ## print STDERR '.';
      $r={};
      @f=splitTSLine($_,$sep);

      if (@f != @keys && ! $botch)
      { warn "$::cmd: wrong number of fields! [@f]";
	next LINE;
      }

      for my $i (0..$#f)
      { $r->{$keys[$i]}=$f[$i];
      }

      push(@r,$r);
    }

  ## print STDERR "\n";

  @r;
}

sub splitTSLine($$)
{
  local($_)=shift;
  my $sep = shift;

  my $slen = length $sep;
  die if ! $slen;

  chomp;
  my(@fields);
  my $i;

  my $lastWasSep=1;
  while (length)
  { if (substr($_,0,$slen) eq $sep)
	  # null field
	  { push(@fields,'');
	  }
    elsif (/^"([^"]*)"/)
	  { push(@fields,$1);
	    $_=$';
	  }
    elsif (/^[-+]?\d+(\.\d*)/)
	  { push(@fields,$&+0);
	    $_=$';
	  }
    elsif (($i=index($_,$sep)) > 0)
	  { push(@fields,substr($_,0,$i));
	    substr($_,0,$i)='';
	  }
    else	{ push(@fields,$_);
	    $_='';
	  }

    # consume separator
    if (substr($_,0,$slen) eq $sep)
	  { substr($_,0,$slen)='';
	    $lastWasSep=1;
	  }
    else	{ $lastWasSep=0;
	  }
  }

  push(@fields,'') if $lastWasSep;

  @fields;
}

1;
