#!/usr/bin/perl
#

use strict qw(vars);

BEGIN { use DEBUG; DEBUG::using(__FILE__); }

use Hier;

package GIF;

require Exporter;
@cs::GIF::ISA=qw();

$cs::GIF::Debug=0;

sub load
	{ my($im,$oneim)=@_;

	  if (! open(GIF,"< $im\0"))
		{ warn "can't open $im: $!";
		  return undef;
		}

	  my($g)=fload(GIF,$oneim);

	  close(GIF) || warn "close($im): $!";

	  $g;
	}

sub fload
	{ my($F,$statonly)=@_;
	  local($_);

	  if (read($F,$_,6) != 6)
		{ warn "short read";
		  return undef;
		}
	  else
	  { if (! /^gif(8[79][a-z])$/i)
		{ warn "bad header";
		  return undef;
		}
	  }

	  my($version)=$1;

	  my($s);

	  return undef unless defined ($s=_screen($F));

	  my($g)={	Version	   => $version,
			Screen => $s,
			Components => [],
		 };

	  return $g if $statonly;

	  my($p,@p);
	  my($tag);

	  Part:
	    while (defined ($tag=_byte($F)))
		{ if (defined ($p=_trailer($F)) && $p)
			{ # print STDERR "TRAILER\n";
			  last Part;
			}

		  if (defined ($p=_image($F)) && ref $p)
			{ push(@p,$p);
			}
		  elsif (defined ($p=_gce($F)) && ref $p
		      || defined ($p=_comment($F)) && ref $p
		      || defined ($p=_plaintext($F)) && ref $p
		      || defined ($p=_app($F)) && ref $p
			)
			{ push(@p,$p);
			}
		  elsif (! $Debug)
			{ last Part;
			}
		  else
		  { my($c)=_byte($F);
		    warn "BUG: unexpected GIF EOF";
		    if (defined $c)
			{ my($b)=_byte($F);
			  printf(STDERR "next byte = 0x%02x\n",$c);
			  if (defined $b)
				{ printf(STDERR "next byte = 0x%02x\n",$b);
				}
			}

		    last Part;
		  }
		} 

	  # print STDERR "GIFload: ", Hier::h2a($g), "\n";

	  $g->{Components}=@p;

	  $g;
	}

sub _byte
	{ local($_);
	  read(shift,$_,1) || return undef;
	  ord;
	}

sub _word
	{ local($_);
	  read(shift,$_,2) == 2 || return undef;

	  my($lsb,$msb)=split(//);

	  ord($lsb)+256*ord($msb);
	}

sub _data_subblock
	{ my($F)=@_;
	  my($siz);

	  return undef unless defined ($siz=_byte($F));

	  print STDERR "subblock of size $siz\n";
	  return '' if $siz == 0;

	  my($data);

	  read($F,$data,$siz) || warn "read($F,..,$siz): $!";
	  print STDERR "read ", length($data), " bytes\n";

	  return undef if length($data) != $siz;

	  $data;
	}

sub _data
	{ my($F)=@_;
	  my($data,$block);

	  while (length ($block=_data_subblock($F)))
		{ $data.=$block;
		}

	  $data;
	}

sub _screen
	{ my($F)=@_;
	  my($width,$height,$packed,$bg,$aspect)=@_;

	  return undef
	      unless defined ($width=_word($F))
		  && defined ($height=_word($F))
		  && defined ($packed=_byte($F))
		  && defined ($bg=_byte($F))
		  && defined ($aspect=_byte($F));

	  my($s)={	Width	=> $width,
			Height	=> $height,
			BGIndex	=> $bg,
			Aspect	=> $aspect
		 };

	  $s->{HasGCT}=	($packed&0x80) != 0;
	  $s->{ColorRes}=(($packed&0x70) >> 5) + 1;
	  $s->{Sort}=	($packed&0x08) != 0;
	  $s->{GCTSize}=2^(($packed&0x07)+1);

	  if ($s->{HasGCT})
		{ read($F,$s->{GCT},3*$s->{GCTSize});
		}

	  $s;
	}

sub _image
	{ my($F)=@_;
	  my($mark,$left,$top,$width,$height,$packed);

	  return undef if ! defined($mark=_byte($F));

	  if ($mark != 0x2c)
		{ if (! seek($F,-1,1))
			{ warn "can't seek($F,-1,1): $!";
			  return undef;
			}

		  return 0;
		}

	  return undef unless defined ($left=_word($F))
			   && defined ($top=_word($F))
			   && defined ($width=_word($F))
			   && defined ($height=_word($F))
			   && defined ($packed=_byte($F));

	  my($i)={	Type	=> IMAGE,
			Left	=> $left,
			Top	=> $top,
			Width	=> $width,
			Height	=> $height,
		 };

	  $i->{HasLCT}		=($packed&0x80) != 0;
	  $i->{Interlaced}	=($packed&0x40) != 0;
	  $i->{Sort}		=($packed&0x20) != 0;
	  $i->{LCTSize}		=2^(($packed&0x07)+1);

	  if ($i->{HasLCT})
		{ $i->{LCT}='';
		  read($F,$i->{LCT},3*$i->{LCTSize});
		}

	  $i;
	}

sub _gce
	{ my($F)=@_;
	  my($mark,$label,$size,$packed,$delay,$trans,$terminal,$data);

	  return undef if ! defined ($mark=_byte($F));

	  if ($mark != 0x21)
		{ if (! seek($F,-1,1))
			{ warn "can't seek($F,-1,1): $!";
			  return undef;
			}

		  return 0;
		}

	  return undef if ! defined ($label=_byte($F));

	  if ($label != 0xf9)
		{ if (! seek($F,-2,1))
			{ warn "can't seek($F,-2,1): $!";
			  return undef;
			}

		  return 0;
		}

	  return undef unless defined ($size=_byte($F))
			   && $size == 4
			   && defined ($packed=_byte($F))
			   && defined ($delay=_word($F))
			   && defined ($trans=_byte($F))
			   && defined ($data=_block($F));

	  my($g)={	Type		=> GCE,
			Disposal	=> ($packed&0x1c)>>2,
			UserInput	=> ($packed&0x20) != 0,
			Transparent	=> ($packed&0x01),
			Delay		=> $delay,
			TransColor	=> $trans,
			Data		=> $data,
		 };

	  $g;
	}

sub _comment
	{ my($F)=@_;
	  my($mark,$label,$data);

	  return undef if ! defined ($mark=_byte($F));

	  if ($mark != 0x21)
		{ if (! seek($F,-1,1))
			{ warn "can't seek($F,-1,1): $!";
			  return undef;
			}

		  return 0;
		}

	  return undef if ! defined ($label=_byte($F));

	  if ($label != 0xfe)
		{ if (! seek($F,-2,1))
			{ warn "can't seek($F,-2,1): $!";
			  return undef;
			}

		  return 0;
		}

	  return undef unless defined ($data=_block($F));

	  {	Type	=> COMMENT,
		Data	=> $data
	  };
	}

sub _plaintext
	{ my($F)=@_;
	  my($mark,$label,$data);

	  return undef if ! defined ($mark=_byte($F));

	  if ($mark != 0x21)
		{ if (! seek($F,-1,1))
			{ warn "can't seek($F,-1,1): $!";
			  return undef;
			}

		  return 0;
		}

	  return undef if ! defined ($label=_byte($F));

	  if ($label != 0x01)
		{ if (! seek($F,-2,1))
			{ warn "can't seek($F,-2,1): $!";
			  return undef;
			}

		  return 0;
		}

	  my($size,$left,$top,$width,$height,$cwidth,$cheight,$fg,$bg);

	  return undef unless defined ($size=_byte($F))
			   && $size == 12
			   && defined ($left=_word($F))
			   && defined ($top=_word($F))
			   && defined ($width=_word($F))
			   && defined ($height=_word($F))
			   && defined ($cwidth=_byte($F))
			   && defined ($cheight=_byte($F))
			   && defined ($fg=_byte($F))
			   && defined ($bg=_byte($F))
			   && defined ($data=_block($F));

	  {	Type	=> PLAIN,
		Left	=> $left,
		Top	=> $top,
		Width	=> $width,
		Height	=> $height,
		CharWidth=> $cwidth,
		CharHeight=> $cheight,
		FG	=> $fg,
		BG	=> $bg,
		Data	=> $data
	  };
	}

sub _app
	{ my($F)=@_;
	  my($mark,$label,$data);

	  return undef if ! defined ($mark=_byte($F));

	  if ($mark != 0x21)
		{ if (! seek($F,-1,1))
			{ warn "can't seek($F,-1,1): $!";
			  return undef;
			}

		  return 0;
		}

	  return undef if ! defined ($label=_byte($F));

	  if ($label != 0xff)
		{ if (! seek($F,-2,1))
			{ warn "can't seek($F,-2,1): $!";
			  return undef;
			}

		  return 0;
		}

	  my($size,$id,$auth,$data);

	  return undef unless defined ($size=_byte($F))
			   && $size == 11
			   && read($F,$id,8) == 8
			   && read($F,$auth,3) == 3
			   && defined ($data=_block($F));

	  {	Type	=> APP,
	  	ID	=> $id,
		AuthCode=> $auth,
		Data	=> $data
	  };
	}

sub _trailer
	{ my($F)=@_;
	  my($mark);

	  return undef unless defined ($mark=_byte($F));

	  if ($mark != 0x3b)
		{ if (! seek($F,-1,1))
			{ warn "can't seek($F,-1,1): $!";
			  return undef;
			}

		  return 0;
		}

	  1;
	}

1;
