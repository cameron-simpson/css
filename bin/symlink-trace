#!/usr/bin/perl
#
# Walk path reporting symlinks.
#	- Cameron Simpson <cs@zip.com.au> 07may1997
#

use cs::Pathname;

($cmd=$0) =~ s:.*/::;

$Xit=0;

for (@ARGV)
	{ sl($_,'');
	}

exit $Xit;

sub sl
{ local($_,$indent)=@_;

  print "$indent$_\n";
  $indent.='  ';

  my($pfx,@p);

  m:^/*:;
  $pfx=$&;
  $_=$';

  @p=grep(length,split(m:/+:));

  return if ! @p;

  $_=$pfx.shift(@p);

  COMPONENT:
  while (1)
  { if (-l $_)
    { my($link)=readlink($_);
      if (! defined $link)
      { warn "$cmd: readlink($_): $!\n";
	$Xit=1;
      }
      else
      { print "$indent$_ -> $link\n";
	if ($link !~ m:^/:)
	{ $link=cs::Pathname::dirname($_)
	       ."/$link";
	}

	sl($link,"  $indent");
      }
    }

    last COMPONENT if ! @p;

    $_.='/'.shift(@p);
  }
}
