#!/usr/bin/perl
#
# Make a Source attached to the output of a program, whose input
# comes from the Source constructed with the args supplied.
# $pipefn is a ref to a function to munge the Source and supply the output.
# See MD5.pm for an example use.
#	- Cameron Simpson <cs@zip.com.au> 28aug96
#

use strict qw(vars);

use cs::IO;
use cs::Source;

package cs::PipeDecode;

# new cs::PipeDecode (fnptr, args-array-ref, cs::Source)
# new cs::PipeDecode (fnptr, args-array-ref, args-for-new-cs::Source)
# new cs::PipeDecode (fnptr, args-array-ref) # fnptr doesn't use the cs::Source
sub new
	{ my($class,$pipefn,$fnargs,$s)=(shift,shift,shift,shift);

	  if (! defined $s)
		{}	# pipefn doesn't use it I guess
	  elsif (! ref $s)
		{
		  my($new_s);

		  $new_s=new cs::Source ($s, @_);
		  if (! defined $new_s)
			{ warn "can't make cs::Source($s @_): possible error: $!";
			  return undef;
			}

		  $s=$new_s;
		}

	  my($F)=cs::IO::mkHandle();
	  my($pid);

	  if (! defined ($pid=open($F,"-|")))
		{ warn "pipe/fork: $!";
		  return undef;
		}

	  if ($pid > 0)
		# parent
		{ my($pd_s);

		  $pd_s=new cs::Source (FILE, $F);
		  if (! defined $pd_s)
			{ warn "can't make cs::Source from $F: $!";
			  return undef;
			}

		  $pd_s->{FLAGS}&=~$cs::IO::F_NOCLOSE;

		  return $pd_s;
		}

	  if (defined $s)
		# fork again, copy source to subprocess's STDIN
		{
		  if (! defined ($pid=open($F,"-|")))
			{
			  die "grandchild: pipe/fork: $!";
			}

		  if ($pid == 0)
			# grandchild, copy source to STDOUT
			{
			  local($_);

			  while (defined ($_=$s->Read()) && length)
				{
				  print STDOUT $_;
				}

			  exit 0;
			}

		  # attach STDIN to source
		  open(STDIN,"<&$F") || die "can't dup $F to STDIN: $!";
		  close($F);
		}

	  # child - STDOUT is to parent
	  # pass source to pipefn, which writes to stdout
	  exit &$pipefn(@$fnargs);
	}

1;
