#!/usr/bin/perl
#
# Package indexing images.
#	- Cameron Simpson <cs@zip.com.au> 05sep96
#
# Complete redisgn and recode. - cameron 13apr99
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use MD5;
use cs::Persist;

package cs::Image::DB;

sub new($$;$$)
{ my($class,$dir,$rw,$dbpath)=@_;
  $rw=0 if ! defined $rw;
  $dbpath="$dir/.imdb" if ! defined $dbpath;

  my $this = { DBPATH	=> $dbpath,
	       DIR	=> $dir,
	       DB	=> cs::Persist::db($dbpath,$rw),
	     };

  bless $this, $class;
}

# compute hash for file
sub _hashFile
{ my($file)=@_;

  return undef if ! open(F,"< $file\0");
  my @s = stat(F);
  
  my $md5 = new MD5;
  $md5->reset();
  $md5->addfile(F);
  close(F);

  $md5->hexdigest()."-".$s[7];
}

# add record for file given relative path
sub AddPath
{ my($this,$path)=@_;
  $this->AddFile($this->Path2File($path));
}

# actual pathname for file
sub Path2File
	{ my($this,$path)=@_;
	  "$this->{DIR}/$path";
	}

# add record for file given absolute path
sub AddFile
	{ my($this,$file)=@_;
	  $file="./$file" if $file !~ m:/:;	# ick!

	  die "AddFile($file): not inside \"$this->{DIR}\""
		if substr($file,0,length($this->{DIR})+1)
		ne "$this->{DIR}/";

	  my $path = substr($file,length($this->{DIR})+1);

	  my $I = $this->ByPath($path);
	  die "AddFile($file): entry for $path already exists!"
		if defined $I;

	  return undef if ! stat($file);
	  die "AddFile($file): not a file!" if ! -f _;

	  my $hash = _hashFile($file);
	  return undef if ! defined $hash;

	  $I=$this->ByHash($hash);
	  if (defined $I)
	  { push(@{$I->{PATHS}}, $path);
	  }
	  else
	  { my $db = $this->{DB};
	    $db->{$hash}
		= $I
		= bless { PATHS => [ $path ],
			};
		;
	  }

	  my $rev = $this->_Reverse();

	  $rev->{$path}=$hash;

	  $I;
	}

# return blessed record from hashcode
sub ByHash($$)
	{ my($this,$hash)=@_;
	  my($db)=$this->{DB};

	  return undef if ! exists $this->{$hash};
	  bless $this->{$hash};
	}

# return blessed record from pathname
sub ByPath($$)
	{ my($this,$path)=@_;
	  my $rev = $this->_Reverse();
	
	  return undef if ! exists $rev->{$path};
	  my $db = $this->{DB};
	  my $hash = $rev->{$path};
	  die "rev($path)=$hash, but no entry for it!" if ! exists $db->{$hash};
	  bless $db->{$hash};
	}

sub _Reverse($)
	{ my($this)=@_;

	  return $this->{REV} if exists $this->{REV};

	  warn "compute reverse for $this->{DBPATH}...";

	  my $db = $this->{DB};
	  my $rev = {};

	  for my $hash (keys %$db)
	  { my $I = $db->{$hash};

	    for my $path (@{$I->{PATHS}})
	    { $rev->{$path}=$hash;
	    }
	  }

	  $this->{REV}=$rev;
	}

1;
