#!/usr/bin/perl
#

sub CheckDigit	# cardnumber -> ok
	{ local($_)=@_;

	  s/[-\s]+//g;

	  /\D/ && ((warn "non-digit in card number \"$_\""),
		   return undef
		  );

	  if (/^496/)	{ $_=$'; }	# special case - old scheme?

	  /\d$/;
	  my($check)=$&;		# extract check digit
	  $_=$`;

	  my(@digits)=split(//);

	  my($mult);

	  $mult=1+(@digits%2);		# allow for stripping "496"
	  for $d (@digits)
		{ $d*=$mult;
		  $mult=3-$mult;	# oscillate between 2 and 1
		}

	  my($d);

	  for $d (@digits)
		{ if ($d >= 10)
			{ my(@d2)=split(//,$d);
			  $d=$d2[0]+$d2[1];
			}
		}

	  my($total);

	  $total=0;
	  for $d (@digits)
		{ $total+=$d;
		}

	  $total%=10;
	  if ($total > 0)	{ $total=10-$total; }

	  $check == $total;
	}

1;
