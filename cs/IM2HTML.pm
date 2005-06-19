#!/usr/bin/perl
#
# Generate HTML indexing a directory full of JPEG images.
# Use descriptions from corresponding .html files if present.
#	- Cameron Simpson <cs@zip.com.au> 30mar95
#
# Convert to Perl.	- cameron, 11oct95
#
# new dir
#

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use strict qw(vars);

use cs::HTML;
use cs::Pathname;
##use cs::Date;
use cs::Misc;
use cs::Source;
use cs::IFMSink;
use cs::Persist;
use cs::Stat;
## use cs::Image::DB;
use Image::Size;
require 'flush.pl';

package cs::IM2HTML;

END   { $cs::IM2HTML::_shellFD && close(_shellFD); }

@cs::IM2HTML::ISA=(cs::HTML);

$cs::IM2HTML::WebPage='http://www.cskk.ezoshosting.com/cs/im2html/';

$cs::IM2HTML::Pfx='imindex';	# base of aux files
$cs::IM2HTML::DotDir='.im2html';# where all the cruft goes
$cs::IM2HTML::DoThumbs=1;	# thumbnail creation
$cs::IM2HTML::FrameTarget='_view'; # TARGET for image HREFs
$cs::IM2HTML::TableWidth=4;
$cs::IM2HTML::TableLength=3;
$cs::IM2HTML::DoImStat=1;
$cs::IM2HTML::Order=ASCII;	# or MTIME
$cs::IM2HTML::ThumbMax=80;	# max edge length for thumbnails
$cs::IM2HTML::ThumbType=JPG;	# preferred thumbnail type - JPG, PNG or GIF

# table control variables
$cs::IM2HTML::_intable=0;
$cs::IM2HTML::_inrow=0;
$cs::IM2HTML::_TDs=0;
$cs::IM2HTML::_TRs=0;

# global image db
$cs::IM2HTML::_db={};

sub new
{ my($class,$dir,$inherit)=@_;
  $inherit={} if ! defined $inherit;

  ## $dir=cs::Pathname::norm($dir);

  ## system("ls -ldL $dir");
  return undef if ! stat($dir) || ! -d _;

  ## warn "new $dir...";
  my($this);

  $this=
  bless { DIR		=> $dir,
	  DB		=> $cs::IM2HTML::_db,
	  FILES		=> [],
	  SUBDIRS	=> [],
	  DIRENTS	=> [],
	  HTML		=> {},
	  PFX		=> $cs::IM2HTML::Pfx,
	  DOTDIR	=> $cs::IM2HTML::DotDir,
	  DOTHUMBS	=> $cs::IM2HTML::DoThumbs,
	  DOIMSTAT	=> $cs::IM2HTML::DoImStat,
	  TABLEWIDTH	=> $cs::IM2HTML::TableWidth,
	  TABLELENGTH	=> $cs::IM2HTML::TableLength,
	  ORDER		=> $cs::IM2HTML::Order,
	  THUMBMAX	=> $cs::IM2HTML::ThumbMax,
	  THUMBTYPE	=> $cs::IM2HTML::ThumbType,
	  FRAMETARGET	=> $cs::IM2HTML::FrameTarget,
	  DIDTHUMB	=> {},
	  IMSIZE	=> {},
	  THSIZE	=> {},
	}, $class;

  local($_);

  # inherit particular parameters
  for (PFX,DOTDIR,DOTHUMBS,DOIMSTAT,TABLEWIDTH,TABLELENGTH,
	THUMBMAX,THUMBTYPE,FRAMETARGET)
  { $this->{$_}=$inherit->{$_} if exists $inherit->{$_};
  }

  $this;
}

# object for subdirectory
sub SubNew($$)	{ my($this,$subdir)=@_;
		  new cs::IM2HTML $this->SubPath($subdir);
		}

sub Dir($)	{ shift->{DIR}; }
sub Pfx($)	{ shift->{PFX}; }
sub Basename($) { cs::Pathname::basename(shift->Dir()) }
sub SubPath($$) { my($this,$rpath)=@_;
		  $this->Dir()."/$rpath";
		}
sub DotDir($)	{ shift->{DOTDIR}; }
sub DotDirRPath($$){my($this,$rpath)=@_;
		  $this->{DOTDIR}."/".$rpath;
		}
sub DotDirPath($$){my($this,$rpath)=@_;
		  $this->Dir()."/".$this->DotDirRPath($rpath);
		}
sub ParamPath($$){ my($this,$part)=@_;
		   $this->SubPath($this->Pfx().'.'.$part);
		 }

# hook for reading data within directory
sub Source($$)	{ my($this,$rpath)=@_;

		  my $subpath=$this->SubPath($rpath);
		  new cs::Source (PATH,$subpath);
		}
sub PartSource($$){ my($this,$part)=@_;
		    $this->Source($this->Pfx().'.'.$part);
		  }
sub FetchSubPath($$){ my($this,$path)=@_;
		      my $s = $this->Source($path);
		      return undef if ! defined $s;
		      $s->Fetch();
		    }
sub FetchParam($$){ my($this,$part)=@_;
		    $this->FetchSubPath($this->Pfx().'.'.$part);
		  }

sub WriteHTMLFile($$;)
{ my($this,$rpath)=(shift,shift);

  ## warn "writing ".$this->SubPath($rpath)."\n";
  my $s = $this->Sink($rpath);

  return 0 if ! defined $s;

  cs::HTML::tok2s(2,$s,[HTML,[HEAD],[BODY,@_]],"\n");
  1;
}

# hook for writing data within directory
sub Sink($$)	{ my($this,$rpath)=@_;

		  my $subpath=$this->SubPath($rpath);
		  cs::Pathname::needfiledir($subpath) || return undef;
		  new cs::IFMSink (PATH,$subpath);
		}
sub PartSink	{ my($this,$part)=@_;
		  $this->Sink($this->Pfx().'.'.$part);
		}

sub Title
	{ my($this,$tag)=@_;
	  if (! exists $this->{TITLE})
		{ $this->{TITLE}=$this->_Title($tag);
		}

	  $this->{TITLE};
	}
sub _Title
	{ my($this,$tag)=@_;
	  $tag=$this->Basename() if ! defined $tag;

	  my $title;
	  if (! length ($title=$this->FromParam("title")))
		{ $title="Index of ".$this->Dir();
		}
	  else
		{ chomp($title);

		  my($lc1,$lc2)=($tag,$title);

		  $lc1 =~ tr/-A-Z/_a-z/;
		  $lc2 =~ tr/-A-Z/_a-z/;
		  if ($lc1 ne $lc2)
			{ $title="$tag - $title";
			}
		}

	  $title;
	}

sub TitleImage($)
	{ my($this)=@_;

	  my $im = $this->FromParam("thumb");
	  if (! length $im)
	  { my @im = $this->Images();
	    $im=(@im ? $im[0] : "");
	  }

	  return undef if ! length $im;
	  $im;
	}
sub TitleThumb($)
	{ my($this)=@_;
	  my $im = $this->TitleImage();
	  return undef if ! defined $im;
	  $this->ThumbOf($im);
	}

sub _Stat($$)
	{ my($this,$rpath)=@_;
	  stat($this->SubPath($rpath));
	}
sub IsDir($$)
	{ my($this,$rpath)=@_;
	  $this->_Stat($rpath) && -d _;
	}
sub IsFile($$)
	{ my($this,$rpath)=@_;
	  $this->_Stat($rpath) && -f _;
	}

sub Dirents($)
{ my($this)=@_;

  if (! @{$this->{DIRENTS}})
  { $this->{DIRENTS}=[ sort { $a cmp $b; }
			      grep(/^[^.]/,
				   cs::Pathname::dirents($this->Dir())
			     )
		       ];
  }

  @{$this->{DIRENTS}};
}

sub Hrefs($)
	{ my($this)=@_;
	  grep(/\.href$/i, $this->Files());
	}

sub Images($)
	{ my($this)=@_;
	  grep(/\.(gif|jpg|png)$/i, $this->Files());
	}

sub Files($)
	{ my($this)=@_;

	  if (! @{$this->{FILES}})
		{ $this->{FILES}=[ grep(/^[^.]/ && $this->IsFile($_),
					$this->Dirents()) ];
		}

	  @{$this->{FILES}};
	}

sub SubDirs
	{ my($this)=@_;

	  if (! @{$this->{SUBDIRS}})
		{ $this->{SUBDIRS}=[ grep(/^[^.]/
					  && $_ ne CVS
					  && $this->IsDir($_),
					  $this->Dirents()) ];
		}

	  @{$this->{SUBDIRS}};
	}

sub SubDirObjs
	{ my($this)=@_;
	  map((new cs::IM2HTML $this->SubPath($_)), $this->SubDirs());
	}

sub Credit
	{ my($this)=shift;
	  my($when)=@_ ? shift : time;
	  my(@tm)=localtime($when);

	  ('Index created by ',
	   [ A, { TARGET => "_top",
		  HREF => $cs::IM2HTML::WebPage,
		}, "im2html" ],
	   '.',
	  );
	}

sub ImageIndices($)
{ my($this)=@_;

  my @rows=();
  my $row =[];
  my $low;
  my $high;

  for my $im ($this->Images())
  {
    my($ix,$iy)=$this->ImageSize($im);
    my($tx,$ty)=$this->ThumbSize($im);

    my $desc = $im;
    $desc =~ s:\.[^.]+$::;
    $desc .= '.html';

    # table cell with thumb and markup
    my $imHref = { HREF => "../$im" };
    $imHref->{TARGET}=$this->{FRAMETARGET} if length $this->{FRAMETARGET};

    my $imattrs = { SRC => "../".$this->ThumbOf($im), BORDER => 0, };
    if (defined($tx) && $tx > 0)
    { $imattrs->{WIDTH}=$tx;
    }
    if (defined($ty) && $ty > 0)
    { $imattrs->{HEIGHT}=$ty;
    }

    # make a table cell
    my @imdesc
     = ( [TD, {ALIGN => CENTER, VALIGN => CENTER, CELLPADDING => "20%"},
	      [A, $imHref,
		  $im,
		  [BR],
		  [IMG, $imattrs],
		  [BR],
		  "${ix}x${iy}",
		  ",", ["&nbsp;"],
		  niceSize($this->FileSize($im)),
		  ]] );

    ## warn "check desc [$desc]";
    if (! $this->IsFile($desc) || ! -s _)
    # no markup - simple image
    { $high=$im;
      if (! @$row)
      { $low=$im;
	## warn "LOW=$im";
      }
      push(@$row, @imdesc);
      if (@$row >= $this->{TABLEWIDTH})
      { warn "PUSH: low=$low, high=$high\n";
	push(@rows,{ LOW => $low, HIGH => $high, HTML => $row});
	$row=[];
      }
    }
    else
    # image with description
    { if (@$row)
      { ## warn "PUSH: low=$low, high=$high\n";
	push(@rows,{ LOW => $low, HIGH => $high, HTML => $row });
	$row=[];
      }

      push(@imdesc, [TD, { VALIGN => MIDDLE, ALIGN => LEFT },
			 [INCLUDE, {SRC => $this->SubPath($desc)}]]);
      ## warn "INCLUDE ".$this->SubPath($desc);
      push(@rows, { LOW => $im, HIGH => $im, HTML => [ @imdesc ]});
    }
  }

  # grab pending row, if any
  if (@$row)
  { ## warn "PUSH: low=$low, high=$high\n";
    push(@rows,{ LOW => $low, HIGH => $high, HTML => $row});
    $row=[];
  }

  # construct indices
  my @ndx=();

  while (@rows)
  { ##warn "LENGTH=$this->{TABLELENGTH}, nrows=".scalar(@rows).", [@rows]";
    my @front = splice(@rows,0,$this->{TABLELENGTH});
    push(@ndx, { LOW => $front[0]->{LOW},
		 HIGH => $front[$#front]->{HIGH},
		 HTML => [ [TABLE, {BORDER => 0},
				   map([TR, @{$_->{HTML}}], @front) ]
			 ],
	       });
  }

  @ndx;
}

$cs::IM2HTML::_shellFD=0;
sub _shell
{ if (! $cs::IM2HTML::_shellFD)
	{ if (! open(_shellFD,"| exec nice /bin/sh -x"))
		{ warn "can't pipe to /bin/sh: $!";
		  return undef;
		}

	  $cs::IM2HTML::_shellFD=1;
	}

  ## warn "@_";
  print _shellFD @_;
  &'flush(cs::IM2HTML::_shellFD);
}

sub ThumbOf($$)
{ my($this,$im)=@_;

  my($x,$y)=$this->ImageSize($im);
  my($xs,$ys)=$this->XYScale($x,$y);

  ($xs == $x && $ys == $y)
  ? $im				# small enough to leave alone
  : $this->DotDirRPath("$im.".lc($this->{THUMBTYPE}))
  ;
}

sub ThumbSize($$)
{ my($this,$im)=@_;
  if (! exists $this->{THSIZE}->{$im})
  { $this->{THSIZE}->{$im}=[ $this->XYScale(
				$this->ImageSize($im)) ];
  }

  @{$this->{THSIZE}->{$im}};
}

sub ImageSize($$)
{ my($this,$im)=@_;
  if (! exists $this->{IMSIZE}->{$im})
  { $this->{IMSIZE}->{$im}=[ Image::Size::imgsize(
				$this->SubPath($im)) ];
  }

  @{$this->{IMSIZE}->{$im}};
}

sub FileSize($$)
{ my($this,$rpath)=@_;
  my @s = stat($this->SubPath($rpath));
  return undef if ! @s;
  $s[7];
}

sub niceSize($)
{ my($size)=@_;

  if ($size < 1024)	{ "${size}c"; }
  else
  { $size=int($size/1024);
    if ($size < 1024)	{ "${size}k"; }
    else
    { $size=int($size/1024);
      if ($size < 1024)	{ "${size}M"; }
      else
      { $size=int($size/1024);
	if ($size < 1024){ "${size}G"; }
	else
	{ $size=int($size/1024);
	  "${size}T";
	}
      }
    }
  }
}

# compute thumbnail size
sub XYScale($$$;$$)
{ my($this,$x,$y,$maxx,$maxy)=@_;
  $maxx=$this->{THUMBMAX} if ! defined $maxx;
  $maxy=$maxx if ! defined $maxy;

  my $xscale = ($x > $maxx*1.1	# permit 10% bloat
		? $maxx/$x
		: 1
	       );
  my $yscale = ($y > $maxy*1.1	# permit 10% bloat
		? $maxy/$y
		: 1
	       );

  my $scale = ($xscale < $yscale ? $xscale : $yscale);

  (int($x*$scale), int($y*$scale));
}

sub MakeThumbnail($$;$)
{ my($this,$im,$force)=@_;
  $force=0 if ! defined $force;

  my $impath = $this->SubPath($im);
  die "$::cmd: no image \"$impath\"" if ! $this->IsFile($im);

  my $thpath = $this->ThumbOf($im);

  return if ! $force && $this->{DIDTHUMB}->{$im};

  my($th);

  $th=$this->ThumbOf($im);
  my $thpath = $this->SubPath($th);
  if (! cs::Pathname::needfiledir($thpath))
  { warn "$::cmd: can't make dir for $thpath: $!\n";
    return;
  }

  ## warn "mkthumb for $im\n";
  $this->{DIDTHUMB}->{$im}=1;
  _shell("[ -s '$impath' ] && { "
	 .( $force ? "" : "[ -s '$thpath' ] || ")
	 ."gm convert - -geometry $this->{THUMBMAX}x$this->{THUMBMAX} -"
##	 ."mkthumbnail"
##	 .($im =~ /\.gif$/i
##	   ? ' -G'
##	   : $im =~ /\.png$/i
##	     ? ' -P'
##	     : $im =~ /\.jpg$/i
##	       ? ' -J'
##	       : ''
##	  )
##	 ." -m $this->{THUMBMAX}"
	 ." <'$impath'"
	 ." >'$thpath'"
	 ."; }\n")
	 ;
}

sub FromParam
{ my($this,$param)=@_;

  my $txt=$this->FromFile("$this->{PFX}.$param");
  # warn "param($param)=[$txt]";
  $txt;
}
sub FromFile
{ my($this,$base)=@_;
  # warn "FromFile($base)";

  my($s);

  return "" if ! defined ($s=$this->Source($base));

  local($_);

  $_=$s->Get();

  s/^\s+//;
  s/\s+$//;
  s/[ \t]+\n/\n/g;

  $_;
}

1;
