#!/usr/bin/perl
#
# Encapsulated PostScript.
#	- Cameron Simpson <cs@zip.com.au> 21oct94
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::EPS;

sub new	# (@EPS)
	{ shift;	# toss package type
	  local($ref);

	  $ref={ 'PS'	=> \@_,
		 'Comments' => [],
		 'Parsed' => 0,
		 'EPSF'	=> 2.0
	       };

	  bless $ref;

	  $ref->_Locate(0,0);
	  $ref->_Parse;

	  $ref;
	}

sub _Parse	# this -> void
	{ local($this)=shift;
	  local($incomments)=1;
	  local($_,@PS,@oPS);

	  return if $$this{'Parsed'};

	  $$this{'XScale'}=1.0;
	  $$this{'YScale'}=1.0;
	  $$this{'XOff'}=0;
	  $$this{'YOff'}=0;

	  @oPS=@{$$this{'PS'}};

	  ParseHeader:
	    while (@oPS)
		{ $_=shift(@oPS);
		  if (/^%!\s*ps-adobe-\S*\s+EPSF-(\d+(\.\d+)?)/i)
			{ $$this{'EPSF'}=$1+0;
			}
		  elsif (/^%%\s*BoundingBox\s*:\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)/)
			{ $$this{'LX'}=$1+0;
			  $$this{'LY'}=$2+0;
			  $$this{'HX'}=$3+0;
			  $$this{'HY'}=$4+0;
			}
		  else
		  { push(@PS,$_);
		  }
		}

	  $$this{'PS'}=\@PS;
	  $$this{'Parsed'}=1;
	}

sub debug{local($this)=shift;
	  print STDERR "[@_]: this=$this\n";
	  print STDERR "  Parsed=$$this{'Parsed'}\n";
	  print STDERR "  LX=$$this{'LX'}\n";
	  print STDERR "  LY=$$this{'LY'}\n";
	  print STDERR "  HX=$$this{'HX'}\n";
	  print STDERR "  HY=$$this{'HY'}\n";
	  print STDERR "  XOff=$$this{'XOff'}\n";
	  print STDERR "  YOff=$$this{'YOff'}\n";
	  print STDERR "  XScale=$$this{'XScale'}\n";
	  print STDERR "  YScale=$$this{'YScale'}\n";
	}

# generate PostScript to draw the EPS with its LL at the current point.
sub PS	# this -> @PostScript
	{ local($this)=shift;

	  $this->_Parse();

	  local(@text)=@{$$this{'PS'}};

	  # print STDERR "PS: text=[@text]\n";

	  unshift(@text,
		"%!PS-Adobe-3.0 EPSF-$$this{'EPSF'}\n",
		"%%BoundingBox: $$this{'LX'} $$this{'LY'} $$this{'HX'} $$this{'HY'}\n",
		@{$$this{'Comments'}},
		"gsave\n",
		"currentpoint translate newpath\n",
		$$this{'XScale'}, " ", $$this{'YScale'}, " scale\n",
		$$this{'XOff'}, " ", $$this{'YOff'}, " translate\n");
	  push(@text,
		"grestore\n");

	  @text;
	}

sub _Locate
	{ local($this,$x,$y)=@_;

	  $this->_Parse();

	  local($dx,$dy)=($$this{'LX'}-$x,$$this{'LY'}-$y);

	  $$this{'XOff'}-=$dx;
	  $$this{'YOff'}-=$dy;
	  $$this{'LX'}=$x;
	  $$this{'LY'}=$y;
	  $$this{'HX'}-=$dx;
	  $$this{'HY'}-=$dy;
	}

sub DX	{ local($this)=shift;

	  $this->_Parse();

	  ($$this{'HX'}-$$this{'LX'})*$$this{'XScale'};
	}

sub DY	{ local($this)=shift;

	  $this->_Parse();

	  ($$this{'HY'}-$$this{'LY'})*$$this{'YScale'};
	}

sub ScaleDX
	{ local($this,$newdx)=@_;
	  
	  $this->Scale($newdx / $this->DX());
	}

sub ScaleDY
	{ local($this,$newdy)=@_;

	  $this->Scale($newdy / $this->DY());
	}

sub ScaleX
	{ local($this,$xscale)=@_;

	  $$this{'XScale'}*=$xscale;
	}

sub ScaleY
	{ local($this,$yscale)=@_;

	  $$this{'YScale'}*=$yscale;
	}

sub Scale
	{ local($this,$scale)=@_;
	  $this->ScaleX($scale);
	  $this->ScaleY($scale);
	}

1;
