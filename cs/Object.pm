#!/usr/bin/perl
#
# Core object. Really just a dummy DESTROY so we can always call
# SUPER::DESTROY.
#	- Cameron Simpson <cs@zip.com.au> 21jul97
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Object;

@cs::Object::ISA=();

# ([preserve,]hashref,class[,TIEHASH-args])
sub reTIEHASH
	{
	  {my(@c)=caller;warn "reTIEHASH(@_) from [@c]";}

	  my($preserve)=($_[0] =~ /^[01]$/
			? shift(@_)
			: 1
			);
	  my($phash,$impl)=(shift,shift);

	  my %tmp;

	  if ($preserve)
		{
		  # copy the contents
		  for my $key (keys %$phash)
			{ $tmp{$key}=$phash->{$key};
			}
		}

	  tie(%$phash,$impl,@_)
		|| die "tie($phash,$impl,@_) fails";

	  if (! defined $preserve)
		# ignore
		{}
	  elsif ($preserve)
		# overwrite
		{
		  # put the contents back
		  for my $key (keys %tmp)
			{ $phash->{$key}=$tmp{$key};
			}
		}
	  else
		# supply if missing - a bit dubious
		{
		  for my $key (keys %tmp)
			{ $phash->{$key}=$tmp{$key}
				if ! exists $phash->{$key};
			}
		}
	}

sub DESTROY	{}

1;
