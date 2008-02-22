#!/usr/bin/perl
#
# Curses-based browser, expects to be sub-classed.
#	- Cameron Simpson <cs@zip.com.au> 20sep96
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Mail::Folder_New;	# XXX - move into CBrowse/MailDir.pm
use cs::Misc;
use cs::SubVDis;

package cs::CBrowse;

$cs::CBrowse::_HeadRatio=0.2;	# default ratio of head to screen

sub new
	{ my($class,$dis,$dir)=(shift,shift,shift);
	  my($this)=bless { DIS		=> $dis,	# display
			    HSEP	=> '-',		# head/body sep char
			    HRATIO	=> $_HeadRatio,	# ratio of head/screen
			    FLAGS	=> 0,
			    THIS	=> $dir,
			  }, $class;

	  $this->_SetSizes();

	  my($headers,$body);
	  my($hdis,$bdis);

	  $hdis=new cs::SubVDis $dis, $this->{HLINES}, 0;
	  $bdis=new cs::SubVDis $dis, $this->{BLINES}, $this->{HLINES}+$this->HasSep();

	  $this->{HDRS}={ DIS		=> $hdis,
			  TOP		=> 0,
			  CURRENT	=> 0,
			  THIS		=> $dir,
			};
	  $this->{BODY}={ DIS		=> $bdis,
			  TOP		=> 0,
			  CURRENT	=> 0,
			  THIS		=> undef,
			};

	  warn "new cs::CBrowse=\n".cs::Hier::h2a($this,1);

	  $this->Init(@_);	# caller's init stuff

	  $this;
	}

sub _SetSizes
	{ my($this)=shift;
	  my($nlines)=$this->Rows();
	  my($hlines)=int($nlines*$_HeadRatio);
	  my($blines)=$nlines-$this->HasSep()-$hlines;

	  $this->{HLINES}=$hlines;
	  $this->{BLINES}=$blines;
	  $this->{WIDTH}=$this->{DIS}->Cols();
	}

sub Rows{ my($this)=shift; $this->{DIS}->Rows(@_); }
sub Cols{ my($this)=shift; $this->{DIS}->Cols(@_); }

sub HasSep
	{ length(shift->{HSEP}) > 0;
	}

sub Init
	{
	}

sub Body
	{ my($this)=shift;
	  my($hdrs)=$this->{HDRS};
	  my($dir)=$hdrs->{THIS};

	  $dir->Item($this->{BODY}->{CURRENT});
	}

sub ReDraw
	{ my($this)=shift;
	  my($dis);
	  my($part);
	  my($i,$j);
	  my($rows);
	  my($top);

	  $part=$this->{HDRS};
	  $dis=$part->{DIS};
	  $dir=$part->{THIS};
	  $rows=$dis->Rows();
	  $top=$part->{TOP};

	  for ($i=0; $i < $rows; $i++)
		{ $j=$i+$top;

		  $dis->Move(0,$i);

		  if (1 || $j == $part->{CURRENT})
			{ $dis->Bold();
			}

		  print STDERR "dir=".cs::Hier::h2a($dir,0)."\n";
		  $dis->Out($this->Clip($this->HeaderLine($j),$dis));

		  if (1 || $j == $this->{CURRENT})
			{ $dis->Normal();
			}

		}

	  my($hassep)=$this->HasSep();

	  if ($hassep)
		{ $dis->Move(0,$this->{HLINES});
		  $dis->Out($sep x $this->{WIDTH});
		}

	  $part=$this->Body();

	  if (defined $part)
		{ $dis=$part->{DIS};
		  $rows=$dis->Rows();
		  $top=$part->{TOP};

		  for ($i=0; $i < $rows; $i++)
			{ $j=$i+$top;

			  $dis->Move(0,$i);
			  $dis->Out($this->Clip($this->BodyLine($j),$dis));
			}
		}
	}

sub HeaderLine
	{ my($this,$entry)=@_;

	  my($E)=$this->{DIR}->Entry($entry);
	  return undef if ! defined $E;

	  my($H)=$E->{HDRS};

	  "$entry ".$H->Hdr(FROM)." - ".$H->Hdr(SUBJECT);
	}

sub Clip
	{ my($this,$line,$width)=@_;

	  if (! defined $width)
		{ $width=$this->{WIDTH} if ! defined $width;
		}
	  elsif (ref $width)
		{ $width=$width->Cols();
		}

	  $line=::detab($line);
	  if (length($line) > $width)
		{ $line=substr($line,$[,$width);
		}

	  $line;
	}

sub KeyStroke
	{ my($this,$c)=@_;

	  if ($c eq 'q')	{ return 0; }	# quit

	  if ($c eq '-')	{
				}
	  else
	  { warn "bad keystroke '$c'";
	  }

	  1;
	}

sub PageUp
	{ my($this,$nlines)=@_;

	  $nlines=$blines-$this->{OVERLAP}
		if ! defined $nlines;

	  if (($this->{BTOP}-=$nlines) < 0)
	  	{ $this->{BTOP}=0;
		}

	  $this->{FLAGS}|=$F_REDRAW;
	}

sub PageDown
	{ my($this,$nlines)=@_;

	  $nlines=$blines-$this->{OVERLAP}
		if ! defined $nlines;

	  $this->{BTOP}+=$nlines;
	  $this->{FLAGS}|=$F_REDRAW;
	}

1;

package cs::CBrowse::MailDir;

@cs::CBrowse::ISA=qw(cs::CBrowse);

sub new
	{ my($class,$dis,$dirname)=(shift,shift,shift);
	  die "\$dirname undefined" if ! defined $dirname;

	  my($dir)=new cs::Mail::Folder_New $dirname;
	  return undef if ! defined $dirname;

	  cs::CBrowse::new($class,$dis,$dir,@_);
	}

sub Init
	{ my($this)=@_;

	  warn "into Init(@_)";

	  $this->{H}={};
	  $this->{I}=[];

	  $this->UpdateIndex();
	}

sub UpdateIndex
	{ my($this)=shift;
	  my($M)=$this->{THIS};	# mailbox object
	  my($H)=$this->{H};	# header index

	  warn "M=".cs::Hier::h2a($M);
	  my(@e)=sort { $a <=> $b } $M->Entries();

	  $this->{I}=[ @e ];

	  # compute inverted index
	  my($R)=$this->{R}={};

	  for $i (0..$#e)
		{ $R->{$e[$i]}=$i;
		}
	}

sub KeyStroke
	{ my($this,$c)=@_;

	  if ($c eq 'q')	{ return 0; }	# quit

	  if ($c eq '-')	{
				}
	  else
	  { return SUPER->KeyStroke();
	  }

	  1;
	}

sub _Ndx2Item
	{ my($this,$ndx)=@_;

	  return undef if $ndx > $#{$this->{I}};

	  $this->{I}[$ndx];
	}

sub _Hdrs
	{ my($this,$ndx)=@_;
	  my($H)=$this->{H};

	  return $H->{$ndx} if defined $H->{$ndx};

	  my($M)=$this->{THIS};
	  my($e);

	  print STDERR "fetching headers from $ndx ...\n";

	  return undef if ! defined ($e=$M->Entry($ndx));

	  $e->{HDRS};
	}

sub HeaderLine
	{ my($this,$ndx)=@_;
	  my($e,$item);

	  return '' if ! defined ($item=$this->_Ndx2Item($ndx));

	  local($_)=sprintf("%5d ",$item);

	  return $_ if ! defined ($e=$this->_Hdrs($item));

	  $_.=sprintf("%-24s %s",$e->Hdr(FROM),$e->Hdr(SUBJECT));

	  $_;
	}

# NB: returns ref to line array
sub _Lines
	{ my($this,$ndx)=@_;
	  my($N)=$this->{N};

	  if (! defined($N) || $N != $ndx)
		{ $this->{L}=[ $this->_GetLines($ndx) ];
		  $this->{N}=$ndx;
		}

	  $this->{L};
	}

sub _GetLines
	{ my($this,$ndx)=@_;
	  my($item)=$this->_Ndx2Item($ndx);
	  my($M)=$this->{THIS};
	  my($s)=$M->Source($item);

	  return () if ! defined $s;

	  my($m)=new cs::MIME $s;

	  return () if ! defined $m;

	  my($h)=$m->{HDRS};

	  my(@l)=(	  'From:         '.$h->Hdrs(FROM),
                          'Subject:      '.$h->Hdr(SUBJECT),
		 );

	  local($_);

	  if (length ($_=$h->Hdr(ORGANIZATION)))
		{ push(@l,"Organization: $_"); }
	  if (length ($_=$h->Hdr(CC)))
		{ push(@l,"CC:           $_"); }
	  if (length ($_=$h->Hdr(REPLY_TO)))
		{ push(@l,"Reply-To:     $_"); }
	  if (length ($_=$h->Hdr(NEWSGROUPS)))
		{ push(@l,"Newsgroups:   $_"); }
	  if (length ($_=$h->Hdr(X_URL)))
		{ push(@l,"X-URL:        $_"); }

	  push(@l,'',$m->{DS}->GetAllLines());

	  @l;
	}

sub BodyLine
	{ my($this,$lineno)=@_;
	  my($L)=$this->_Lines($this->{CURRENT});

	  print STDERR "L=$L\n";
	  my($n)=scalar(@$L);
	  print STDERR "n=$n, lineno=$lineno\n";
	  if ($n <= $lineno)
		{ return '~';
		}

	  my($l)=$$L[$lineno];
	  chomp($l);

	  print STDERR "line=[$l]\n";

	  $l;
	}

1;
