use cs::Hier;
use cs::Layout;

$L=new cs::Layout 80;

@a=();

@words=<STDIN>;
die "no words" if ! @words;

for (@words)	{ chomp; }

$t=new cs::Layout::FixedText @words;

for $a ($L->Put($t))
	{ if (@a == 0 || $a[0]->{Y} == $a->{Y})
		{ warn "X=$a->{X}, Y=$a->{Y}: $a->{VALUE}->{TEXT}->[0]" if !@a
		|| $a->{VALUE}->{TEXT}->[0] eq 'LinedSource.pm';
		  push(@a,$a);
		}
	  else
	  { doline(@a);
	    @a=$a;
	    warn "X=$a->{X}, Y=$a->{Y}: $a->{VALUE}->{TEXT}->[0]";
	  }
	}

doline(@a) if @a;

sub doline
	{ my(@a)=@_;

	  warn "DOLINE: a=".cs::Hier::h2a(\@a);
	  my($x)=0;
	  my($a);

	  for $a (@a)
		{ if ($x < $a->{X})
			{ print " " x ($a->{X}-$x);
			  $x=$a->{X};
			}

	  	  warn "a=".cs::Hier::h2a($a,0);
		  print join('|',@{$a->{VALUE}->{TEXT}});
		  $x+=length($a->{VALUE}->{TEXT});
		}

	  print "\n";
	}
