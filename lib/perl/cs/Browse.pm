#!/usr/local/lib/perl5
#
# Basic browse class.
#	- Cameron Simpson <cs@zip.com.au> 29jul96
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Browse;

sub new
	{ my($class,$type)=(shift,shift);

	  die if $type ne DIR 

	  my($this)={ TYPE => $type,
		      NDX  => {},
		    };


	  bless $this, $class;

	  $this;
	}
sub _Init
	{ my($this)=shift;


	  $this->_LoadNdx();
	  $this->Sync();
	}


sub DESTROY
	{ my($this)=shift;
	  $this->_SaveNdx();
	}


sub Sync
	{ my($this)=shift;
	  my(@_k)=$this->_Keys();
	  my($h);


	  _K:
	    for (@_k)
		{ next _K if defined $this->Info($_);
		  $h=$this->Stat($_);
		  next _K if ! defined $h;
		  $h->{KEY}=$_;
		  $this->_Ndx($h);
		}
	}


sub Keys
	{ my($this)=shift;
	  keys %{$this->{NDX}};
	}


sub Info
	{ my($this,$key)=@_;
	  $this->{NDX}->{$key};
	}


sub _Ndx
	{ my($this,$h)=@_;
	  $this->{NDX}->{$h->{KEY}}=$h;
	}


sub Visit
	{ my($this,$V)=@_;
	  my($VTOC)=new cs::SubVDis ($V,int($V->Rows()*0.25),0);
	  my($VBody)=new cs::SubVDis ($V,$V->Rows()-$VTOC->Rows(),$VTOC->Rows());


	  my($c)=bless { B       => $this,
			 VTOC    => $VTOC,
			 VBody   => $VBody,
			 CURRENT => 0,
			 TOP	 => 0,
			 KEYS	 => [ sort { $a <=> $b } $this->Keys() ],
		       }, cs::Browse;


	  $c->_TOC($VTOC);
	  $V->Sync();


	  local($Done,$Count)=(0,0);


	  my($onkey)={
			'q'	=> sub { $Done=1; },
		     };


	  my($k);


	  KEYSTROKE:
	    while (! $Done && defined ($k=_getkey()))
		{ if (defined $onkey->{$k})
			{ &{$onkey->{$k}}();
			}
		  else
		  { $V->Bell();
		  }
		}
	}


sub _getkey
	{ local($_);


	  return $_ if sysread(STDIN,$_,1);


	  undef;
	}


sub _TOC
	{ my($this)=@_;
	  my($v)=$this->{VTOC};
	  my($keyoffset)=$this->{TOP};
	  my($nrows)=$this->{VTOC}->Rows();
	  my($keys)=$this->{KEYS};
	  my($row,$tline);


	  TOC:
	    for ($row=0, $keyoffset=$this->{TOP}; 
		  $row < $nrows;
		  $row++, $keyoffset++)
		{ print STDERR "_TOC: row=$row, keyoffset=$keyoffset\n";
		  if ($keyoffset > $#$keys)
			{ $tline='';
			}
		  else	{ $tline=$this->{B}->OneLine($keys->[$keyoffset]);
			}


		  $v->Move(1,$row);
		  $v->Out($tline);
		}
	}


1;
