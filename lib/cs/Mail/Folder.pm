#!/usr/bin/perl
#
# Code to access a mail folder, current just a directory of items.
# This actually supports two classes of things with disjoint method sets.
#	1: the hook for the folder itself, supporting
#		new(dir)
#		Entries -> @entries
#	2:	Entry(key) -> (type,object)
#		Type is either Source for a message, or Directory for
#		a browsable object.
#
#	- Cameron Simpson <cs@zip.com.au> 15aug96
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Mail::Misc;
use cs::Source;
use cs::Sink;
use cs::MIME;
use cs::Pathname;
use cs::Extractor;

# in case
$ENV{MAILDIR}="$ENV{HOME}/private/mail" unless defined $ENV{MAILDIR};

package cs::Mail::Folder;

undef %cs::Mail::Folder::_Folders;

sub new
{ my($class,$dir)=@_;

  my $this;

  if (! exists $cs::Mail::Folder::_Folders{$dir})
  { $cs::Mail::Folder::_Folders{$dir}={ DIR => $dir };
    bless $cs::Mail::Folder::_Folders{$dir}, $class;
  }

  $cs::Mail::Folder::_Folders{$dir};
}

sub Entries
{ my($dir)=shift->{DIR};
  my(@e)=grep(/^\d/,cs::Pathname::dirents($dir));

  for (@e)
  { s/^(\d+).*/$1/;
  }

  @e;
}

sub Source
{ my($this,$entry)=@_;
  my($dir)=$this->{DIR};
  my($path)="$dir/$entry";

  new cs::Source (PATH, (-d $path ? "$path/headers" : $path));
}

# return a cs::MIME object attached to the named entry, or undef
sub Entry
{ my($this,$entry)=@_;
  my($s)=$this->Source($entry);

  return undef if ! defined $s;

  $s=new cs::MIME $s;

  return undef if ! defined $s;

  # XXX - recurse down multipart messages? no - leave for browser

  $s;
}

sub FileItem	# folder->(srcpath,{LINK|COPY|MOVE})
{ my($this,$orig,$mode)=@_;

  my(@c)=caller;die "INCOMPLETE from [@c]";

  if (ref $orig)
  # presumably a cs::MIME object
  # just hand it off to the MIME filer
  { return $this->File($orig);
  }

  { my@c=caller;
    warn "$0: no impl to file orig=\"$orig\", mode=\"$mode\" from [@c]";
    return undef;
  }
}

# file a cs::MIME object into a folder
# NB: since this reads the object's {DS} field, the object is now "used"
# returns n (the item number) or undef
sub File($$;$$)	# cs::MIME -> n or undef
{ my($this,$M,$nodissect,$orig)=@_;
  $nodissect=1 if ! defined $nodissect;

  ## {my(@c)=caller;warn "...File(@_) from [@c]";}

  my($H,$dir)=($M,$this->{DIR});
  ## warn "get body...";
  my($rawbody)=$M->Body();
  ## warn "got body";

  if (defined $orig && ! ref $orig)
  { ## warn "LinkToN($this->{DIR},$M,$nodissect,$orig)";
    return $this->LinkToN($orig);
  }

  ## my(@c)=caller;warn "File($this->{DIR}) with no \$orig from [@c]";

  # make a copy and link that
  my $tmp = "$dir/.$::cmd.$$";

  my($E);
  my($realtmp,$tmpext);

  # plain file
  ## warn "make sink $tmp";
  $E=new cs::Sink PATH, $tmp;
  if (! defined $E)
  { warn "$::cmd: can't create \"$tmp\": $!";
    return undef;
  }

  $realtmp=$E->Path();	# note new pathname
  ## warn "realtmp=$realtmp";

  # check out any extensions
  $realtmp =~ /(\.pgp)?(\.gz)?$/;
  $tmpext=$&;

  $M->WriteItem($E);

  ## warn "LinkToN...";
  my $n = $this->LinkToN($realtmp);

  return undef if ! defined $n;

  if (! unlink($realtmp))
  { warn "$::cmd: unlink($realtmp): $!";
  }

  $n;
}

# link an existing file into a folder
sub LinkToN
{ my($this,$orig,$ext)=@_;
  $ext='' if ! defined $ext;

  ## warn "LinkToN(@_)";

  my $n;

  if ($this->{LASTMAX} > 0)
  { $n = ++$this->{LASTMAX};
  }
  else
  { my(@e)=$this->Entries();

    FINDMAX:
    for (@e)
    { /^\d+/ || next FINDMAX;
      if ($& > $n)
      { $n=$&+1;
      }
    }
  }

  my $dir = $this->{DIR};
  my $ok =0;
  my $target;

  MOVETO_N:
    while (1)
    { $target="$dir/$n$ext";
      if (! -e $target
       && cs::Pathname::saferename($orig,$target,1))
	    { $ok=1;
	      last MOVETO_N;
	    }

      last MOVETO_N if ! -e $target;
      $n++;
    }

  if (! $ok)
  { warn "$::cmd: can't link($orig,$target): $!";
    return undef;
  }

  $this->{LASTMAX}=$n;

  $n;
}

sub fullname	# (shortpath) -> fullpath
{ local($_)=@_;
  my($o)=$_;

  s:^~/:$ENV{HOME}/:;
  s:^=:+corresp/:;
  s:^\+:$ENV{MAILDIR}/:;

  ## warn "[$o] -> [$_]" if 1; $o =~ /articles/;

  $_;
}

sub shortname	# (pathname) -> indicator
{ local($_)=@_;

  if ($_ eq $ENV{MAILDIR})
	{ $_='+';
	}
  elsif (length($_) > length($ENV{MAILDIR})
      && substr($_,$[,length($ENV{MAILDIR})+1) eq "$ENV{MAILDIR}/")
	{ $_='+'.substr($_,$[+length($ENV{MAILDIR})+1);
	}
  elsif (length($_) > length($ENV{HOME})
      && substr($_,$[,length($ENV{HOME})+1) eq "$ENV{HOME}/")
	{ $_='~'.substr($_,$[+length($ENV{HOME})+1);
	}

  s:^\+corresp/:=:;

  $_;
}

1;
