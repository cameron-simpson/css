#!/usr/bin/perl
#
# Routines to manipulate pathnames, mostly lexical.
#	- Cameron Simpson <cs@zip.com.au> 24oct95
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use POSIX;
use cs::Misc;

package cs::Pathname;

# unique ID for file
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

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
