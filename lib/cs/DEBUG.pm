#!/usr/bin/perl

use strict qw(vars);

package cs::DEBUG;

%cs::DEBUG::Trace=( USE => defined($ENV{'csPLDEBUG'})
			&& length($ENV{'csPLDEBUG'}) > 0,
	 EXPORT => 1
       );

{ package main;

  if (defined $ENV{'csPLDEBUG'})
  { my $dbg=join(';',
	      map('warn "DEBUG: debugging package '.$_.'\n");$cs::'.$_.'::DEBUG=1',
		  grep(/^[a-z_][:\w]*$/i,split(/,+/,$ENV{'csPLDEBUG'}))));
    eval $dbg;
    warn "cs::DEBUG: $@ in [$dbg]" if $@;
  }
}

# $Exporter::Verbose=$Trace{EXPORT};

sub using
{ my(@c)=caller(1);
  ## pstack();
  print STDERR "$0: using @_ from [@c]\n" if $cs::DEBUG::Trace{USE};
}

sub err
{ my($err,$arg)=@_;

  cs::Upd::err($err);
  if (defined $arg)
  { warn "arg = ".cs::Hier::h2a($arg,1)."\n";
    pstack();
  }
}

sub pstack
{ my(@s)=cstack(1);
  my($p,$f,$l,$sub);

  for (@s)
  { ($p,$f,$l,$sub)=@$_;
    warn "$f:$l: ${p}::$sub\n";
  }
}

sub cstack
{ my($i)=shift;
  my(@s,@c);

  $i=0 unless defined $i;

  CALL:
  while (@c=caller($i))
  { push(@s,[ @c ]);
    $i++;
  }

  @s;
}

sub phash
{ my($h)=@_;

  my(@c)=caller;
  warn "phash($h) from [@c]\n";
  for my $hkey (sort keys %$h)
  { my($val)=$h->{$hkey};
    warn "\t$hkey=$val\n";
    if (ref $val
     && ::reftype($val) eq ARRAY)
    {
      for my $i (0..$#$val)
      { warn "\t$hkey\[$i]=$val->[$i]\n";
      }
    }
  }
}

1;
