#!/usr/bin/perl
#
# General routines built on Sinks and Sources.
#	- Cameron Simpson <cs@zip.com.au>
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Pathname;

package cs::IO;

$cs::IO::_UseIO=0;

$cs::IO::F_ASYNC=0x01;
$cs::IO::F_NOCLOSE=0x02;	# no close on Sink/Source DESTROY
$cs::IO::F_STICKYEOF=0x04;
$cs::IO::F_HADEOF=0x08;
$cs::IO::F_RAWWRITES=0x10;	# print instead of syswrite

if ($cs::IO::_UseIO)
{
  ::need(IO);
  ::need(IO::Select);

  $cs::IO::_Selection=new IO::Select;
  undef %cs::IO::_SourceSelection;	# bound to an IO::Handle
  undef %cs::IO::_SourceTable;		# not bound - use HasData method
  undef %cs::IO::_SinkSelection;
}

sub selAddSource
{ die "selAddSource(@_)" if ! $cs::IO::_UseIO;

  my($s)=@_;
  my($type)=$s->{TYPE};

  if (! ($s->{FLAGS}&$cs::IO::F_ASYNC))
  { my(@c)=caller;
    die "not an asynchronous cs::Source [".cs::Hier::h2a($s)."] from [@c]";
  }

  if ($type eq FILE)
  { $cs::IO::_Selection->add($s->{IO});
    $cs::IO::_SourceSelection{$s->{IO}->fileno()}=$s;
  }
  else
  { $cs::IO::_SourceTable{"$s"}=$s;
  }
}

sub selAddSink
{ die "selAddSink(@_)" if ! $cs::IO::_UseIO;

  my($s)=@_;
  my($type)=$s->{TYPE};

  if (! ($s->{FLAGS}&$cs::IO::F_ASYNC))
  { my(@c)=caller;
    die "not an asynchronous cs::Sink [".cs::Hier::h2a($s,0)."] from [@c]";
  }

  if ($type eq FILE)
  { $cs::IO::_Selection->add($s->{IO});
    $cs::IO::_SinkSelection{$s->{IO}->fileno()}=$s;
  }
  else	{ $cs::IO::_SinkTable{"$s"}=$s;
	}
}

sub selDelSource
{ die "selDelSource(@_)" if ! $cs::IO::_UseIO;

  my($s)=@_;
  my($type)=$s->{TYPE};

  if ($type eq FILE)
  { $cs::IO::_Selection->remove($s->{IO});
    delete $cs::IO::_SourceSelection{$s->{IO}->fileno()};
  }
  else
  { delete $cs::IO::_SourceTable{"$s"};
  }
}

sub selDelSink
{ die "selDelSink(@_)" if ! $cs::IO::_UseIO;

  my($s)=@_;
  my($type)=$s->{TYPE};

  if ($type eq FILE)
  { $cs::IO::_Selection->remove($s->{IO});
    delete $cs::IO::_SinkSelection{$s->{IO}->fileno()};
  }
  else
  { delete $cs::IO::_SinkTable{"$s"};
  }
}

sub poll
{ die "poll(@_)" if ! $cs::IO::_UseIO;

  my($rd,$wr)=([],[]);
  my($didio)=0;
  my(@f,$s);

  print STDERR "poll ...\n";

  # collect waiting data
  @f=keys %cs::IO::_SourceSelection;
  if (@f)
  { for ($cs::IO::_Selection->can_read(@f))
    { $s=$cs::IO::_SourceSelection->{$_};
      push(@$rd,$s);
      $s->PollIn() && $didio++;
    }
  }

  @f=keys %cs::IO::_SourceTable;
  if (@f)
  { for (@f)
    { if ($cs::IO::_SourceTable{$_}->HasData())
      { $cs::IO::_SourceTable{$_}->PollIn()
      }
    }
  }

  # dispatch pending data
  @f=keys %cs::IO::_SinkSelection;
  if (@f)
  { for ($cs::IO::_Selection->can_write(@f))
    { $s=$cs::IO::_SinkSelection->{$_};
      push(@$wr,$s);
      $s->PollOut() && $didio++;
    }
  }

  # return lists or total active
  wantarray ? ($rd,$wr) : $didio;
}

undef %cs::IO::_DirPrefs;

@cs::IO::_Filters=qw(Z gz pgp);	# in order of preference and application
%cs::IO::_Filter=('Z' 	=> { TYPE	=> COMPRESS,
			     EXT	=> 'Z',
			     TO		=> 'compress',
			     FROM	=> 'uncompress',
			   },
		  'bz2'	=> { TYPE	=> COMPRESS,
			     EXT	=> 'bz2',
			     TO		=> 'bzip2 -9',
			     FROM	=> 'bunzip2',
			   },
		  'gz'	=> { TYPE	=> COMPRESS,
			     EXT	=> 'gz',
			     TO		=> 'gzip -9',
			     FROM	=> 'gunzip',
			   },
		  'pgp'	=> { TYPE	=> ENCRYPT,
			     EXT	=> 'pgp',
			     TO		=> 'pgp -fe "$PGPID"',
			     FROM	=> 'pgp -fd',
			   },
	 );

sub dupR { _dup('<',shift); }
sub dupW { _dup('>',shift); }
sub _dup
{ my($io,$handle)=@_;
  my($newhandle)=mkHandle();
  open($newhandle,"$io&$handle")
	? $newhandle : $handle;
}

# take a target filename and some preferences
# and return actual name and filters chosen (ordered)
# prefs is undef or "filter,filter,..." (unordered)
# 
sub choose	# (filename,prefs) -> (chosen-name,filters[ordered])
{ my($basefile,$prefs)=@_;

  warn "basefile not set" if ! defined $basefile;

  # see if it's already there

  my($match);
  my(@filters);

  if (-e $basefile && ! -d $basefile)
  { $match=$basefile;
    @filters=();
  }
  else
  # look for shortest basefile(.ext)+
  # where every ext is a known filter
  { my(@matches)=<$basefile.*>;
    local($_);

    MATCH:
    for my $m (sort
	      { length($a) <=> length ($b) } # fewest filters hack
	      @matches)
    { $_=$m;
      $_=substr($_,$[+length($basefile));

      next MATCH unless /^\./ && ! /\.$/;

      @filters=();

      TOKEN:
	while (/^(\.+|[^.]+)/)
	{ if ($& eq '.')
	  {
	  }
	  elsif (! defined $cs::IO::_Filter{$&})
	  { next MATCH;
	  }
	  else
	  { push(@filters,$&);
	  }

	  $_=$';
	}

      if (! length)
      { $match=$m;
	last MATCH;
      }
    }
  }

  if (defined $match)
  { return ($match,@filters);
  }

  # missing? generate target name from preferences
  # leave create/open to caller

  # collect preferences
  my(@prefs)=();

  if (defined $prefs)
  { @prefs=_a2preflist($prefs);
  }
  else
  # use prefs from dir
  { my($dir)=cs::Pathname::dirname($basefile);
    my($prefs)="$dir/.ioprefs";

    if (defined $cs::IO::_DirPrefs{$dir})
    { @prefs=_a2preflist($cs::IO::_DirPrefs{$dir});
    }
    else
    { # load special preferences
      if (open(IOP,"< $prefs\0"))
      { @prefs=_a2preflist(join('',<IOP>));
	close(IOP);
      }

      $cs::IO::_DirPrefs{$dir}=join(',',@prefs);
    }
  }

  my(%ispref,%types,@trypref);
  map($ispref{$_}=1,@prefs);

  # make filename with preferred extensions
  $match=$basefile;

  # for each filter in order
  for (@cs::IO::_Filters)
  { # if wanted and not already redundant
    if ($ispref{$_} && ! $types{$cs::IO::_Filter{$_}->{TYPE}})
    { push(@filters,$_);
      $match.=".$_";

      # record type of filter selected
      $types{$cs::IO::_Filter{$_}->{TYPE}}=1;
    }
  }

  ($match,@filters);
}

sub _a2preflist
{ grep(length,split(/[\s,]+/,shift));
}

sub mkHandle { cs::Misc::mkHandle(@_) }

sub tmpnam
{ my(@c)=caller;
  warn "forwarding tmpnam(@_) to cs::Pathname::tmpnam from [@c]";
  ::need(cs::Pathname);
  cs::Pathname::tmpnam(@_);
}

sub tmpfile
{ my($tmpnam);
  my($FILE)=mkHandle();

  return undef if ! defined ($tmpnam=tmpnam());

  if (! open($FILE,">+ $tmpnam"))
	{ warn "open($tmpnam,update): $!";
	  return undef;
	}

  unlink($tmpnam) || warn "unlink($tmpnam): $!";

  $FILE;
}

sub openR
{ my($file,@filters)=@_;

  return undef if ! -e $file;

  my($io);

  if ($cs::IO::_UseIO)
	{ $io=new IO::File;
	}
  else	{ $io=mkHandle();
	}

  my($openstr)=$file;

  if (! @filters)
  # short open - tidy up name
  { if ($openstr !~ m:^/:)
    { $openstr="./$openstr";
    }

    $openstr="< $openstr\0";
  }
  else
  # construct shell pipeline
  {
    # quote filename
    $openstr =~ s:':'\\'':g;

    # was just " <'$openstr' "
    # but bash doesn't parse that:-(
    $openstr="exec <'$openstr'; ";

    FILTER:
    for (reverse @filters)
    { if (! defined $cs::IO::_Filter{$_})
      { warn "openR(@_): ignoring unknown filter \"$_\"";
	next FILTER;
      }

      $openstr.=" $cs::IO::_Filter{$_}->{FROM} |";
    }

    ##warn "_openR(@_): [$openstr]";
  }

  ($cs::IO::_UseIO ? $io->open($openstr) : open($io,$openstr))
	? $io : undef;
}

sub openW
{ my($append,$file,@filters)=@_;

  my($io);

  if ($cs::IO::_UseIO)
	{ $io=new IO::File;
	}
  else	{ $io=mkHandle();
	}

  my($openstr)=$file;
  my($openmode)=($append ? '>>' : '>');

  if (! @filters)
	# short open - tidy up name
	{ if ($openstr !~ m:^/:)
		{ $openstr="./$openstr";
		}

	  $openstr="$openmode $openstr\0";
	}
  else
  # construct shell pipeline
  {
    # quote filename
    $openstr =~ s:':'\\'':g;
    $openstr="$openmode'$openstr'";

    FILTER:
      for (reverse @filters)
	{ if (! defined $cs::IO::_Filter{$_})
		{ warn "IO::_openW(@_): ignoring unknown filter \"$_\"";
		  next FILTER;
		}

	  $openstr="| $cs::IO::_Filter{$_}->{TO} $openstr";
	}

    # print STDERR "openW(@_): \"$openstr\"\n";
  }

  ($cs::IO::_UseIO ? $io->open($openstr) : open($io,$openstr))
	? $io : undef;
}

# return a filehandle open for read/write
# normally the handle will be attached to an unlinked file
# in the specified directory
sub cacheFile	# dir[,unlink] -> (filehandle[,path])
	{ my($dir,$unlink)=@_;
	  $unlink=1 if ! defined $unlink;
	  $dir=cacheDir() if ! defined $dir;

	  # delimit leading whitespace
	  $dir="./$dir" if $dir =~ /^\s/;

	  my($F)=mkHandle();

	  my($n,$s,$f);

	  N:
	    for ($n=1; 1; $n++)
		{ $f="$dir/$n";
		  # if (-e $f)	{ system("ls -ld $f"); }

		  next N if -e $f;

		  last N if open($F,"+> $f\0");

		  my($err)="$!";
		  next N if -e $f;

		  warn "open(+>$f): $err";
		  return undef;
		}

	  if ($unlink && ! unlink($f))
		{ warn "cacheFile(): unlink($F): $!";
		}

	  wantarray && ! $unlink
		? ($F,$f)
		: $F;
	}

sub cacheDir
	{ my($dir);

	  $dir=cs::Misc::tmpDir()."/Cache";

	  if (! -d "$dir/." && ! mkdir($dir,0777))
		{ warn "mkdir($dir): $!";
		}

	  $dir;
	}

sub fionread
	{ my($F)=@_;

	  eval "require 'sys/filio.ph'"; die $@ if $@;

	  my($n,$i);
	  my($ctl)=&FIONREAD;
	  print "ctl=$ctl\n";

	  $i='';
	  if (! defined ($n=ioctl($F,$ctl,$i)))
		{ warn "ioctl($F,FIONREAD,..): $!";
		  return undef;
		}

	  print STDERR "n=$n, i=$i\n";

	  $n;
	}

1;
