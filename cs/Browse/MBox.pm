#!/usr/local/lib/perl5
#
# Browse class for mailbox directory.
#	- Cameron Simpson <cs@zip.com.au> 23dec96
#

use strict qw(vars);


BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }


use cs::Browse;
use cs::Source;
use cs::Sink;
use cs::Hier;
use cs::RFC822;


package cs::Browse::MBox;


@cs::Browse::MBox::ISA=(cs::Browse);


sub new
	{ my($class,$dir)=@_;
	  my($this)=new cs::Browse MBOX;


	  $this->{DIR}=$dir;


	  bless $this, $class;


	  $this->_Init();


	  $this;
	}


sub DESTROY
	{ my($this)=shift;


	  cs::Browse::DESTROY($this);
	}


sub _PathTo
	{ my($this,$basename)=@_;
	  $this->{DIR}.'/'.$basename;
	}
sub _IndexFile { shift->_PathTo('.index.hier') }


sub _LoadNdx
	{ my($this)=shift;
	  my($i)=new cs::Source (PATH,$this->_IndexFile());


	  return if ! defined $i;


	  local($_);
	  my($h);


	  NDX:
	    while (defined ($_=$i->GetLine()) && length)
		{ $h=cs::Hier::a2h($_);
		  next NDX if ! ref $h;
		  $this->_Ndx($h);
		}
	}


sub _SaveNdx
	{ my($this)=shift;
	  my($i)=new cs::Sink (PATH,$this->_IndexFile());


	  return undef if ! defined $i;


	  my($h);


	  for ($this->Keys())
		{ $h=$this->Info($_);
		  $i->Put(cs::Hier::h2a($h), "\n");
		}


	  1;
	}


sub _Keys
	{ my($this)=shift;
	  my($dir)=$this->{DIR};


	  return () if ! opendir(_KEYS,$dir);
	  print STDERR "opendir($dir)\n";


	  my(@k)=grep(/^\d+(\.(gz|Z|pgp))*$/,readdir(_KEYS));
	  print STDERR "k=[@k]\n";
	  for (@k)
		{ s/\..*//;
		}


	  @k;
	}


sub Stat
	{ my($this,$key)=@_;
	  my($s)=new cs::Source (PATH,$this->_PathTo($key));


	  return undef if ! defined $s;


	  my($hdrs)=new cs::RFC822;
	  $hdrs->SourceExtract($s);


	  { KEY	=> $key,
	    HDRS => $hdrs,
	  };
	}


sub OneLine { my($this,$key)=@_;
	      my($h)=$this->Info($key);
	      return undef if ! defined $h;
	      my($l)=sprintf("%5d %s",$key,$h->{HDRS}->Hdr(SUBJECT));
	      $l =~ s/\s+\n\s*/; /g;
	      $l;
	    }


1;
