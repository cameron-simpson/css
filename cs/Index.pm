#!/usr/bin/perl
#
# General index package with various backends.
#	- Cameron Simpson <cs@zip.com.au> 17may96
#

use strict qw(vars);

use cs::Upd;
use cs::IO;

package cs::Index;

sub new
	{ my($class,$type)=(shift,shift);
	  local($this)={ TYPE => $type };

	  if ($type eq DBM)	{ new_DBM(@_); }
	  else
	  { err("unknown Index type \"$type\"\n");
	    $this=undef;
	  }

	  return undef if ! defined $this;

	  bless $this, $class;
	}

sub DESTROY
	{ local($this)=shift;

	  if ($this->{TYPE} eq DBM)	{ &DBM_DESTROY; }
	}

sub DBM_new
	{ my($file,$mode)=@_;
	  my($db);

	  $db={};
	  if (! dbmopen(%$db,$file,$mode))
		{ $this=undef;
		  return;
		}

	  $this->{DB}=$this;
	}

sub DBM_DESTORY
	{ dbmclose(%{$this->{DB}});
	}

sub FLAT_new
	{ my($file,$ro)=@_;
	  my($F);

	  if (! defined ($F=Open(($ro ? '<' : '+<'),$file)))
		{ err("Index(FLAT,$file): $!\n");
		  $this=undef;
		}

	  $this->{FNAME}=$file;
	  $this->{FILE}=$F;
	}

sub FLAT_
sub FLAT_DESTROY
	{ FLAT_sync();
	  close($this->{FILE});
	}

1;
