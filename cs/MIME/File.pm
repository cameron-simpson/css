#!/usr/bin/perl
#
# Handle MIME messages as structured data,
# and load and save from/to files and dirs.
#	- Cameron Simpson <cs@zip.com.au> 
#

use strict qw(vars);

use cs::Misc;
use cs::Source;
use cs::Sink;
use cs::MIME;

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::MIME::File;

@cs::MIME::File::ISA=qw();

sub new
	{ my($class,$path)=@_;

	  my $this
	   = bless { HDR	=> new cs::RFC822,	# headers
		     TYPE	=> TEXT,
		     SUBTYPE	=> PLAIN,
		     PRETEXT	=> '',
		     PARTS	=> [],
		     POSTTEXT	=> '',
		     CONTENT	=> '',
		   }, $class;

	  if (defined $path)
		{ $this->{PATH}=$path;
		  $this->Load($path);
		}

	  $this;
	}

sub Hdrs { shift->{HDR}; }
sub Type { shift->{TYPE}; }
sub SubType{shift-> { SUBTYPE}; }
sub Parts { @{shift->{PARTS}}; }

sub Hdr	{ my($this)=shift; $this->Hdrs()->(@_); }

sub AddPart
	{ my($this,$part)=@_;

	  my $type = $this->Type();
	  my $parts = $this->Parts();

	  if (@$parts > 0 && $type ne MULTIPART)
		# XXX - maybe auto-generate a multipart
		{ warn "$0: can't add to non-multipart message";
		  return;
		}

	  push(@$parts,$part);
	}

sub Load
	{ my($this,$path)=@_;
	  if (! defined $path)
		{ die "$0: load from where?" if ! exists $this->{PATH};
		  $path=$this->{PATH};
		}

	  return undef if ! stat($path);

	  if (! -d _)
		{ my $s = new cs::Source (PATH,$path);
		  return undef if ! defined $s;

		  $this->_LoadSrc($s);
		}
	  else
	  {
	  XXXXX
	}

sub _LoadSrc
	{ my($this,$s)=@_;

	  my $M = new cs::MIME $s;

	  my($pieces,$pretext,$posttext)=$M->Pieces();

	  $this->{HDR}=$M->Hdrs();
	  $this->{TYPE}=$M->Type();
	  $this->{SUBTYPE}=$M->SubType();
	  $this->{PRETEXT}=$pretext;
	  $this->{POSTTEXT}=$posttext;

	  if ($this->{TYPE} eq MULTIPART)
		{ for my $subsrc (@$pieces)
			{ my $subPiece = new cs::MIME::File;
			  $subPiece->_LoadSrc($subsrc);
			  $this->AddPart($subPiece);
			}
		}
	  else
	  { $this->{CONTENT}=$M->Get();
	  }
	}

1;
