#!/usr/bin/perl
#
# Image routines.
# We expect the PBMPLUS or NetPBM tools to be in our execution path.
#	- Cameron Simpson <cs@zip.com.au> 24oct95
#
# imsize(path) -> (x,y) or undef
#	Return image dimensions.
# mkthumbnail(thumbnail-filename[,force]) -> ok
#	Create thumbnail from larger image.
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Hier;
use cs::Upd;
use cs::Misc;
# use Image::Size;

package cs::Image;

$cs::Image::Debug=1;
$cs::Image::Silent = ! $cs::Image::Debug;
$cs::Image::Verbose= $cs::Image::Debug;
$cs::Image::DoCache=1;
$cs::Image::NoThumbs=0;
undef %cs::Image::_Cache;

if (exists $ENV{BINARIES} && length $ENV{BINARIES})
	{ $cs::Image::_DJPEG="$ENV{BINARIES}/djpeg"; }
else	{ $cs::Image::_DJPEG='djpeg'; }

sub new	{ ::need(cs::Image::Mapped); cs::Image::Mapped::new(@_); }

sub move{ my($dx,$dy,$p)=@_;
	  my(@p)=();
	  my($x,$y);

	  while (@$p)
		{ ($x,$y)=(shift(@$p),shift(@$p));
		  push(@p,$x+$dx,$y+$dy);
		}

	  @$p=@p;
	}

sub imsize
	{ my($im)=@_;
	  ::need(Image::Size);
	  my($x,$y)=imgsize($im);

	  return ($x,$y) if $x > 0;

	  return undef if ! -e $im;

	  my($s)=imstat($im);

	  return undef unless defined $s
			   && defined $s->{X}
			   && defined $s->{Y};

	  ($s->{X},$s->{Y});
	}

sub mkthumbnail	# (thumbnail-filename[,force]) -> ok
	{ local($_)=shift;
	  my($force)=@_;

	  return undef unless /-small\.gif$/i;

	  return 1 if ! $force && ($cs::Image::NoThumbs || -s $_);

	  my($original)=&'choosefile($`,'.jpg','.gif');

	  return undef unless defined $original;

	  $cs::Image::Silent || cs::Upd::err "thumbnail $_ ...\n";
	  if ($original =~ /\.gif$/i)
		{ system("thumbnail -G <$original >$_");
		}
	  else
		{ system("thumbnail <$original >$_");
		}

	  if ($? != 0)
		{ cs::Upd::err "$'cmd: thumbnail $original fails\n";
		  if (! unlink $_)
			{ cs::Upd::err "$'cmd: unlink($_): $!\n";
			}

		  return 0;
		}

	  1;
	}

sub flushstat
	{ undef %cs::Image::_Cache;
	}

sub imstat
	{ my($im)=@_;
	  my(@stat);

	  # warn "imstat($im)";
	  if (! open(IMSTAT,"< $im\0"))
		{ if (-e $im)
			{ warn "can't open($im): $!";
			}
		  return undef;
		}

	  @stat=stat IMSTAT;

	  my($id)="$stat[1]:$stat[0]:$stat[6]";

	  if (defined $cs::Image::_Cache{$id})
		{ # cs::Upd::err "cached copy for $im - $id\n";
		  close(IMSTAT) || warn "close($im): $!";
		  return $cs::Image::_Cache{$id};
		}

	  my($s)={ STAT => [ @stat ],
		   Size => $stat[7]
		 };

	  if (0 && $im =~ /\.gif$/i)
		{ if (! open(IMSTAT,"< $im\0"))
			{ warn "open($im): $!";
			}
		  else
		  { ::need(cs::Image::Mapped);
		    my($g)=cs::Image::Mapped::loadGIF(cs::Image::IMSTAT);
		    close(IMSTAT);

		    if (defined $g)
			{ $s->{X}=$g->DX();
			  $s->{Y}=$g->DY();
			}
		  }
		}
	  else
	  # other image format
	  { my($pnm);
	    my($redirect)="<$im";

	    if ($im =~ /\.gif$/i)
		{ $pnm="exec giftoppm 2>/dev/null $redirect"; }
	    elsif ($im =~ /\.jpg$/i)
		{ $pnm="exec $cs::Image::_DJPEG -pnm $redirect"; }
	    else
	    { cs::Upd::err "$im: unrecognised image type\n";
	      $pnm='';
	    }

	    ## warn "pnm=[$pnm]";
	    if (! length $pnm)	{}	# nothing to do about it
	    elsif (! open(PNM,"$pnm |"))
		{ cs::Upd::err "can't pipe from \"$pnm\": $!\n";
		  close(IMSTAT) || warn "close($im): $!";
		  return undef;
		}
	    else
	    { local($_);

	      if (defined ($_=<PNM>)	# P6
	       && defined ($_=<PNM>)	# x y
	       && /(\d+)\s+(\d+)/)
		{ $s->{X}=$1;
		  $s->{Y}=$2;
		}

	      close(PNM);
	    }
	  }

	  close(IMSTAT);

	  $cs::Image::_Cache{$id}=$s if $cs::Image::DoCache;

	  $s;
	}
1;
