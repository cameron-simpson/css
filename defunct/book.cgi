#!/usr/bin/perl -w

($cmd=$0) =~ s:.*/::;

$BDir='/u/cameron/booking';
$Bookings="$BDir/bookings";

use CGI ':standard';
use cs::Pathname;
use cs::Upd;
use cs::Misc;
use cs::Hier;
use cs::Date;

$Now=time;
$NowTM=cs::Date::time2tm($Now,1);
$NowDayCode=sprintf("%4d%02d%02d",
			$NowTM->{YEAR}+1900,$NowTM->{MM},$NowTM->{MDAY});

$Q=new CGI;

# path_info: dataset/report/{table.html/piegraph.gif/tabel.txt}
$path=$Q->path_info();
$path='' if ! defined $path;
if ($path =~ m:/$:)	{ $path.='index.html'; }
$path=cs::Pathname::norm($path);	# parse ".." etc
@path=grep(length,split(m:/+:,$path));

print header();

$task=shift(@path);
if ($task ne 'book')
	{ print h1("bad request \"$task\""), "\n",
		"Sorry, only \"book\" requests so far.\n";
	  exit 0;
	}

$rq={};
for ($Q->param())
	{ $rq->{$_}=$Q->param($_);
	}

@errs=chk_rq($rq);
if (@errs)
	{ print h1("invalid request for \"$task\""), "\n",
		ul(map(li($_)."\n",@errs)
		  ), "\n";
	  exit 0;
	}

exit 0;

sub chk_rq
	{ my($rq)=@_;
	  my(@e)=();

	  for ((HH,MM))
		{ if ( ! exists $rq->{$_}
		    || $rq->{$_} !~ /^\d+/)
			{ push(@e,"$_ ($rq->{$_}) is not an integer");
			}
		}

	  if ($rq->{HH} > 23)
		{ push(@e,"HH ($rq->{HH}) too large (0..23)");
		}

	  if ($rq->{MM} > 59)
		{ push(@e,"MM ($rq->{MM}) too large (0..59)");
		}

	  @e;
	}

sub addBooking
	{ my($b,$list)=@_;
	  my($pre)=[];
	  my($start,$end)=($b->{START},$b->{START}+$b->{DURATION}-1);

	  BOOKING:
	    while (@$list)
		{ $l=shift(@$list);
		  if ($l->{START} > $end)
			# after
			{ push(@$pre,$b,$l);
			  last BOOKING;
			}
		  if ($l->{START}+$l->{DURATION} <= $start)
			# before
			{ push(@$pre,$l);
			}
		  else
		  # must overlap
		  { push(@$pre,$l);
		    undef $b;
		    last BOOKING;
		  }
		}

	  push(@$pre,@$list);
	  @$list=@$pre;

	  $b;
	}

sub mkBooking
	{ my($start,$duration,$details)=@_;
	  { START    => $start,
	    DURATION => $duration,
	    DETAILS  => $details,
	  }
	}

sub load_bookings
	{ my($file)=@_;

	  return () if ! open(LOAD,"< $file\0");

	  my(@b)=();
	  my($b);

	  while (<LOAD>)
		{ if (! defined ($b=cs::Hier::a2h($_)))
			{ warn "$file, line $.: bad data\n";
			}
		  else	{ push(@b,$b);
			}
		}

	  close(LOAD);

	  @b;
	}

sub save_bookings
	{ my($file,@b)=@_;

	  return undef if ! open(SAVE,"> $file\0");

	  for (@b)
		{ print SAVE cs::Hier::h2a($_,0), "\n";
		}

	  close(SAVE);
	}
