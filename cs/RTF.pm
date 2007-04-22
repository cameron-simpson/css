#!/usr/bin/perl
#
# Parser for RTF.
#	- Cameron Simpson <cs@zip.com.au> 13jun95
#
# newtok(Input[,State]) -> new cs::Tokenise
# Tok -> new token or undef
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Tokenise;

package cs::RTF;

sub newtok	#  (InputFn[,State]) -> new cs::Tokenise
	{ die "$'cmd: usage: RTF->newtok(inputfnref)"
		unless @_ == 1 || @_ == 2;

	  # print STDERR "RTF::new(@_)\n";
	  bless Tokenise::new(\&match,@_);
	}

sub Tok	{ Tokenise::Tok(@_); }

sub match	# (Data,State) -> (token,tail) or undef
	{ local($_,$State)=@_;
	  local($tok,$tail);

	  if (/[^\\{}]+/)
		{ $tok=$&;
		  $tail=$';
		}
	  elsif (/^[{}]/)
		{ $tok=$&;
		  $tail=$';
		}
	  elsif (/^\\([^a-z])/)
		{ $tok={ Control => $1 };
		  $tail=$';
		}
	  elsif (/^\\([a-z]+)(-?\d+)?( |[^a-z\d])/)
		{ $tok={ Control => $1 };
		  $tail=$';
		  if (length $2)
			{ $tok->{Numeric}=$2+0;
			}

		  if ($3 ne ' ')
			{ $tail=$3.$tail;
			}
		}
	  else	{ return undef; }

	  print STDERR "RTF::match: [",&Hier::h2a($tok), "]\n";
	  ($tok,$tail);
	}

1;
