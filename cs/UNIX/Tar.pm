#!/usr/bin/perl
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Upd;
use cs::Source;
use cs::SubSource;

package cs::UNIX::Tar;

$cs::UNIX::Tar::_TBLOCK=512;	# tar blocksize
$cs::UNIX::Tar::_NAMSIZ=100;	# size of name field

# name mode uid gid size mtime chksum linkflag linkname
$cs::UNIX::Tar::_hdrfmt="a${_NAMSIZ}A8A8A8A12A12A8Ca${_NAMSIZ}";

sub new
	{ my($class,$s)=@_;
	  my($this)={ DS => $s };

	  bless $this, $class;
	}

sub _ReadHdr
	{ my($this)=shift;
	  my($s)=$this->{DS};

	  local($_)=$s->NRead($_TBLOCK);
	  return undef if ! defined;

	  if (length != $_TBLOCK)
		{ warn "bad tar header read - asked for $_TBLOCK, got ".length;
		  return undef;
		}

	  my($name,$mode,$uid,$gid,$size,$mtime,$chksum,$linkflag,$linkname)
		=unpack($_hdrfmt,$_);

	  # cs::Upd::err("hdrfmt=$_hdrfmt\n");
	  # cs::Upd::err("mode=$mode, uid=$uid, gid=$gid, size=$size\n");

	  $mode=oct($mode);
	  $uid=oct($uid);
	  $gid=oct($gid);
	  $size=oct($size);
	  $mtime=oct($mtime);
	  $chksum=oct($chksum);

	  $name =~ s/\0+$//;
	  $linkname =~ s/\0+$//;

	  my($hdr)={ NAME	=> $name,
		     MODE	=> $mode,
		     UID	=> $uid,
		     GID	=> $gid,
		     SIZE	=> $size,
		     MTIME	=> $mtime,
		     CHKSUM	=> $chksum,
		     LINKFLAG	=> $linkflag,
		     _DATA      => $_,
		   };

	  if ($linkflag)
		{ $hdr->{LINKNAME}=$linkname;
		}
	  
	  $hdr;
	}

sub Index
	{ my($this)=shift;
	  my($hdr);
	  my(@i);
	  my($skip,$skipped);

	  INDEX:
	    while (defined ($hdr=$this->_ReadHdr()) && length $hdr->{NAME})
		{ $hdr->{OFFSET}=$this->{DS}->Tell();
		  $skip=$_TBLOCK*int(($hdr->{SIZE}+$_TBLOCK-1)/$_TBLOCK);

		  # cs::Upd::err("hdr=", cs::Hier::h2a($hdr), "\n");
		  # cs::Upd::err("size=$hdr->{SIZE}, skip=$skip\n");

		  push(@i,$hdr);

		  $skipped=$this->{DS}->Skip($skip);
		  if (! defined $skipped)
			{ cs::Upd::err("skip fails, aborting index\n");
			  last INDEX;
			}

		  if ($skipped != $skip)
			{ cs::Upd::err("Skip($skip): only skipped $skipped\n");
			  last INDEX;
			}
		}

	  @i;
	}

# Accept an unused Source attached to a tar file,
#	 a ref to an index made from the same tar file
#	 a list of names to retrieve
# Return an array of { NAME => name, INDEX => index-entry, DS => SubSource }
# which can be processed (in order!) to get each file.
# The returned array order may not be the same as the order of the original
# request list, as the original list is unordered and the return is in the
# order of the files in the archive.
#
sub Fetch	# Tar Source, \@index, @filenames
	{ my($this,$s,$ndx,@names)=@_;
	  my($i,$f,@f);

	  FETCH:
	    for $i (@$ndx)
		{ next FETCH unless grep($_ eq $i->{NAME}, @names);

		  $f=new cs::SubSource $s, $i->{OFFSET}, $i->{SIZE};
		  if (! defined $f)
			{ warn("couldn't make SubSource for $i->{NAME}");
			  next FETCH;
			}

		  push(@f,{ NAME	=> $i->{NAME},
			    INDEX	=> $i,
			    DS		=> $f,
			  }
		      );
		}

	  @f;
	}

1;
