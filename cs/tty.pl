#!/usr/bin/perl

require 'stat.pl';

sub isatty
{ local($_)=$_[0];
  my($isatty);
  # local($dev,$ino,$mode,@etc);

  if (/^\d+$/)
  { if (!open(_FD_ISATTY,"<&$_"))
    { warn "isatty: can't open &$_: $!\n";
      return undef;
    }

    $isatty = -t _FD_ISATTY;
    # ($dev,$ino,$mode,@etc)=stat _FD_ISATTY;
    # no close since it may eat the fd
  }
  elsif (/^[A-Z_]+$/)
  { # ($dev,$ino,$mode,@etc)=eval "stat $_";
    $isatty = eval "-t $_";
  }
  else
  { # ($dev,$ino,$mode,@etc)=stat($_);
    $isatty = -t $_;
  }

  $isatty;

#	  return (defined($mode)
#		? POSIX->S_ISCHR($mode)
#		: undef);
}

1;
