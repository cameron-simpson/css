#!/usr/bin/perl
#
# Routines to manipulate pathnames, mostly lexical.
#	- Cameron Simpson <cs@zip.com.au> 24oct95
#

=head1 NAME

cs::Pathname - manipulate data hierachies

=head1 SYNOPSIS

use cs::Pathname;

=head1 DESCRIPTION

This module does stuff with files and filenames.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use POSIX;
use cs::Misc;

package cs::Pathname;

=head1 FUNCTIONS

=over 4

=item pathid(I<path>)

Return a unique id string for the named file.
Current implementation: "rdev:ino".

=cut

sub pathid($)
{ my($path)=@_;

  my @s = lstat($path);

  return undef if ! @s;

  "$s[0]:$s[1]";
}

sub dirents($)
{ my($dir)=@_;
  my(@e);

  return () if ! opendir(D,$dir);
  @e=grep(length && $_ ne '.' && $_ ne '..', readdir(D));
  closedir(D);

  @e;
}

sub dirname($)
{ local($_)=@_;

  if (m|(.*[^/])/+|)
	{ $_=$1; }
  elsif (m|^/+|)
	{ $_='/'; }
  else	{ $_='.'; }

  $_;
}

sub basename($)
{ local($_)=@_;

  $_=norm($_);

  s:.*/([^/]):$1:;

  $_;
}

sub catpath($$)
{ my($dir,$path)=@_;

  return $path if ! length $dir;
  "$dir/$path";
}

sub absname($;$)
{ my($path,$dir)=@_;
  $dir=pwd() if ! defined $dir;

  return $path if $path =~ m:^/:;
  return catpath($dir,$path);
}

# safe rename - doesn't tromp target file if present
sub saferename	# (from,to) -> success
{ my($from,$to,$noremove)=@_;
  $noremove=0 if ! defined $noremove;
  
  my $ok = 0;

  if (link($from,$to))
  { $ok=1;
    if (!$noremove && !unlink($from))
    { warn "unlink($from): $!, $from still linked to $to\n";
    }
  }
  elsif ($! == &POSIX::EXDEV)
  # cross device link
  { if (lstat($to))
    { warn "$main::cmd: $to exists\n";
    }
    else
    { if (!open(RENAME_FROM,"<$from"))
      { warn "$main::cmd: can't open $from for read: $!\n";
      }
      else
      { if (!open(RENAME_TO,">$to"))
	{ warn "$main::cmd: can't open $to for write: $!\n";
	}
	else
	{ $ok=1;
	  while (<RENAME_FROM>)
	  { if (! print RENAME_TO)
	    { warn "$::cmd: cs::Pathname::saferename($from,$to): $!";
	      $ok=0;
	    }
	  }

	  close(RENAME_TO);

	  if ($ok && ($noremove || unlink($from)))
	  { }
	  else
	  { $ok=0;
	    warn "$main::cmd: can't unlink $from ($!), unlinking $to\n";
	    if (!unlink($to))
	    { warn "$main::cmd: can't unlink $to: $!\n\tboth $from and $to now exist\n";
	    }
	  }
	}

	close(RENAME_FROM);
      }
    }
  }
  else
  { warn "$main::cmd: link($from,$to): $!\n";
  }

  return $ok;
}

sub firstfile	# (base,exts...) -> first non-empty file base.ext
{ my($base,@exts)=@_;
  my($e,$f);

  for $e (@exts)
	{ return $base.$e if -s $base.$e;
	}

  return undef;
}

# parse . and .. entries
sub norm	# path -> norm-path
{ my($path)=@_;
  my($pfx,@pieces);

  $path =~ m|^/*|;
  $pfx=$&;
  $path=$';

  @pieces=grep(length,split(m|/+|,$path));

  my(@path);

  for (@pieces)
  { if ($_ eq '' || $_ eq '.')
    {}
    elsif ($_ eq '..')
    { pop(@path);
    }
    else
    { push(@path,$_);
    }
  }

  $path=$pfx.join('/',@path);
  $path='.' if ! length $path;

  $path;
}

undef $cs::Pathname::_Pwd;
sub pwd
{ if (! defined $cs::Pathname::_Pwd)
  { $cs::Pathname::_Pwd=`pwd`;
    if ($? != 0)
    { undef $cs::Pathname::_Pwd;
      return undef;
    }

    chomp $cs::Pathname::_Pwd;
  }

  $cs::Pathname::_Pwd;
}
sub cd
{ if (! chdir(@_))
  { warn "$::cmd: chdir(@_): $!";
    return undef;
  }

  undef $cs::Pathname::_Pwd;
  1;
}

sub fullpath	# path -> fullpath
{ my($path)=@_;

  if ($path !~ m|^/|)
  { my($pwd)=&pwd;
    return $path if ! defined $pwd;
    chomp($pwd);

    $pwd.='/' unless $pwd =~ m|/$|;
    $path=$pwd.$path;
  }

  norm($path);
}

sub ident	# path or stat-array -> undef or ident
{ my(@s)=@_;

  if (@s == 1)
  { return undef if ! (@s=stat(shift(@s)));
  }

  "$s[6]:$s[0]:$s[1]";
}

sub makedir($;$$);
sub makedir($;$$)
{ my($dir,$perms,$verbose)=@_;
  $perms=0777 if ! defined $perms;
  $verbose=0 if ! defined $verbose;

  return 1 if -d "$dir/.";

  my($super)=cs::Pathname::dirname($dir);
  makedir($super,$perms) || return 0;

  warn "mkdir $dir\n" if $verbose;
  if (! mkdir($dir,$perms))
  { warn "$::cmd: mkdir($dir): $!\n";
    return 0;
  }

  1;
}

sub needfiledir($;$)
{ my($file)=shift;

  ## warn "needfiledir($file)\n";
  makedir(dirname($file),$_[0]);
}

sub untilde
{ local($_)=shift;

  /^~/ || return $_;

  length($`) ? userdir($`) : $ENV{HOME};
}

sub userdir
{ my($u)=shift;
  my(@pw);

  if (! defined $cs::Pathname::_PwEnts{$u})
  { $cs::Pathname::_PwEnts{$u}=[ getpwnam($u) ];
    if (! @{$cs::Pathname::_PwEnts{$u}})
    { warn "$::cmd: userdir($u): who is $u?";
    }
  }

  @pw=@{$cs::Pathname::_PwEnts{$u}};

  return undef unless @pw;

  $pw[7];
}

# rename a bunch of files in a directory
sub vrename
{ my($dir,$map,$verbose)=@_;
  $dir='.' if ! length $dir;
  $dir.='/' unless $dir =~ m:/$:;
  $verbose=1 if ! defined $verbose;

  ::need(cs::Upd) if $verbose;

  my(@now)=keys %$map;
  my(@errs)=();

  my($from,$to);
  local($_);

  # normalise pathnames
  # assumes no collisions (i.e. no "a" and "dir/a")
  for (@now)
  { if (! m:^/:)
    { $from="$dir$_";
      if (exists $map->{$from})
      { push(@errs,
	      "both \"$_\" and \"$from\" in map, losing \"$from\" => \"$map->{$from}\"");
      }

      $map->{$from}=$map->{$_};
      delete $map->{$_};
    }
    else
    { $from=$_;
    }

    $to=$map->{$from};
    if ($to !~ m:^/:)
    { $map->{$from}="$dir$to";
    }
  }

  my($err);

  # shuffle files
  @now=sort { $a <=> $b } keys %$map;
  MOVE:
    while (@now)
    { $from=shift(@now);

      next MOVE if ! exists $map->{$from};

      $to=$map->{$from};
      delete $map->{$from};

      next MOVE if $from eq $to;

      $verbose && ::out("$from => $to");

      if (-e $to)
      { if (! exists $map->{$to})
	# terminal - bitch and skip
	{ $err="\"$to\" exists, cancelling move of \"$from\"";
	  $verbose && ::err("$err\n");
	  push(@errs,$err);
	}

	else
	{ my($prefrom)=cs::Pathname::tmpnam($dir);

	  my($preto)=$map->{$to};
	  delete $map->{$to};

	  if (-e $prefrom)
	  { $err="\"$prefrom\" exists, cancelled move of \"$to\" and thus of \"$from\"";
	    $verbose && ::err("$err\n");
	    push(@errs,$err);
	  }
	  elsif (! saferename($to,$prefrom))
	  { $err="saferename(\"$to\",\"$prefrom\"): $!: cancelled move of \"$to\" and thus of \"$from\"";
	    $verbose && ::err("$err\n");
	    push(@errs,$err);
	  }
	  else
	  { $verbose && ::out("$to => $prefrom");

	    # move this from tmp spot to final later
	    $map->{$prefrom}=$preto;
	    push(@now,$prefrom);

	    # reschedule for another go now the way is clear
	    $map->{$from}=$to;
	    unshift(@now,$from);
	  }
	}
      }
      elsif (! saferename($from,$to))
      { $err="saferename(\"$from\",\"$to\"): $!";
	$verbose && ::err("$err\n");
	push(@errs,$err);
      }
      else
      {
      }
    }

  # just return errors in array context
  return @errs if wantarray;

  # otherwise warn and return boolean
  @errs == 0;
}

$cs::Pathname::_tmpnamSeq=0;
sub tmpnam
{ my($tmpdir)=@_;
  $tmpdir=cs::Misc::tmpDir() if ! defined $tmpdir;

  my($tmpnam);

  while (1)
  { $tmpnam="$tmpdir/tmp$$.".$cs::Pathname::_tmpnamSeq++;
    if (! -e $tmpnam)
    { ## warn "tmpnam returns \"$tmpnam\"";
      return $tmpnam;
    }
  }
}

sub cpperm($$)
{ my($src,$dest)=@_;

  my $ok = 1;

  my @s = stat($src);
  if (! @s)
  { warn "$::cmd: stat($src): $!\n";
    $ok=0;
  }
  else
  { my $perms = $s[2] & 07777;
    my $setid = $s[2] & 07000;

    if ($setid != 0)
    # check ownerships
    {
      my @d = stat($dest);
      if (! @d)
      { warn "$::cmd: stat($dest): $!\n";
	$ok=0;
      }
      else
      { if (($setid & 04000) && $s[4] != $d[4]
	 || ($setid & 02000) && $s[5] != $d[5])
	{ if (! chown($s[4],$s[5],$dest))
	  { warn "$::cmd: $src is set[ug]id but can't make ownership match on $dest: $!\n\tdropping set[ug]id bits\n";
	    $perms&=~06000;
	    $ok=0;
	  }
	}
      }
    }

    if (! chmod($perms,$dest))
    { warn "$::cmd: chmod($dest,".sprintf("0%04o",$perms)."): $!\n";
      $ok=0;
    }
  }

  $ok;
}

=item compare(file1,file2)

Compare the contents of two files.
Returns:
B<undef> if one of the files can't be opened or if a read fails.
B<0> if the files are identical.
B<-1> if the files differ and I<file1> is a prefix of I<file2>.
B<-2> if the files differ and I<file1>'s differing byte is less than I<file2>'s byte.
B<1> if the files differ and I<file2> is a prefix of I<file1>.
B<2> if the files differ and I<file2>'s differing byte is less than I<file1>'s byte.

=cut

sub compare($$)
{ my($f1,$f2)=@_;

  if (! open(compare_FILE1,"< $f1\0"))
  { warn "$0: open($f1): $!\n";
    return undef;
  }

  if (! open(compare_FILE2,"< $f2\0"))
  { warn "$0: open($f2): $!\n";
    close(compare_FILE2);
    return undef;
  }

  my $eof1 = 0;
  my $eof2 = 0;
  my $buf1 = '';
  my $buf2 = '';
  my $nread;

  do
  {
    if (!$eof1 && length($buf1) == 0)
    { if (! defined ($nread=sysread(compare_FILE1,$buf1,8192)))
      { warn "$::cmd: sysread($f1): $!";
	close(compare_FILE1);
	close(compare_FILE2);
	return undef;
      }

      $eof1=($nread == 0);
    }
    # POST: $buf1 not empty or $eof1

    if (!$eof2 && length($buf2) == 0)
    { if (! defined ($nread=sysread(compare_FILE2,$buf2,8192)))
      { warn "$::cmd: sysread($f2): $!";
	close(compare_FILE1);
	close(compare_FILE2);
	return undef;
      }

      $eof2=($nread == 0);
    }
    # POST: $buf2 not empty or $eof2

    if (length($buf1) == 0)
    { if (length($buf2) == 0)
      {
	close(compare_FILE1);
	close(compare_FILE2);
        return 0;
      }
      else
      {
	close(compare_FILE1);
	close(compare_FILE2);
        return -1;
      }
    }
    elsif (length($buf2) == 0)
    {
      close(compare_FILE1);
      close(compare_FILE2);
      return 1;
    }
    # POST: $buf1 not empty and $buf2 not empty

    # compare common data prefix
    my $cmplen = (length($buf1) < length($buf2) ? length($buf1) : length($buf2));
    my $cmp = substr($buf1,0,$cmplen) cmp substr($buf2,0,$cmplen);
    ##warn "cmp(\"".substr($buf1,0,$cmplen)."\" <=> \"".substr($buf2,0,$cmplen)."\": $cmp\n";
    if ($cmp < 0) { return -2; }
    if ($cmp > 0) { return 2; }

    # discard matching data
    substr($buf1,0,$cmplen)='';
    substr($buf2,0,$cmplen)='';
  }
  while (1);
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
