#!/usr/bin/perl
#
# Code for autofiling RFC822 messages as distinct numbered files in folder
# directories. Currently pipes to filemail, but one day will be inlined
# and filemail will require this library file.
#	- Cameron Simpson, cs@zip.com.au, 20jun94
#

package filemail;

$pcounter=0;		# used to make pipe names
$tcounter=0;		# used to make temp file names
undef %ref, %fd;	# indexed by dir name

sub file	# (dir,neednl,@lines) -> ok
	{ local($dir)=shift;

	  return 0 unless &filein($dir);

	  local($tmpfile,$ok);
	  $ok=defined($tmpfile=&tmplines);
	  if ($ok)
		{ print $fd{$dir} $tmpfile, "\n";
		}

	  &filed($dir);

	  $ok;
	}

sub filein	# dir -> ok
	{ local($dir)=@_;
	  local($ok)=1;

	  if ($ref{$dir} == 0)
		{ local($fd)="filemail'fd".$pcounter++;
		  local($shdir,$pipe);
			=($dir,

		  ($shdir=$dir) =~ s:':'\\'':g;
		  $pipe="exec 3<&0; filemail -I -S '$shdir' <&3 3<&- & exit 0";
		  if (!open($fd,"| $pipe"))))
			{ print STDERR "$cmd: can't pipe to '$pipe': $!\n";
			  $ok=0;
			}
		  else
			{ $fd{$dir}=$fd;
			}
		}

	  if ($ok)
		{ $ref{$dir}++;
		}

	  $ok;
	}

sub filed	# dir
	{ local($dir)=@_;

	  if (!defined $ref{$dir})
		{ warn "$cmd: no pipe open for dir $dir";
		  return;
		}

	  if (--$ref{$dir} == 0)
		{ close($fd{$dir});
		  delete $ref{$dir};
		  delete $fd{$dir};
		}
	}

sub tmplines
	{ local($neednl,$tmpfile)=(shift,"/tmp/$'cmd.$$.".$tcounter++);

	  if (!open(TMPFILE,"> $tmpfile\0"))
		{ print STDERR "$'cmd: can't open temp file $tmpfile: $!\n";
		  return 0;
		}

	  local($ok)=1;

	  TMPFILE:
	    for (@_)
		{ if ( ! (print TMPFILE ($_,$neednl ? "\n" : '')))
			{ print STDERR "$'cmd: writing to temp file $tmpfile: $!\n";
			  $ok=0;
			  last TMPFILE;
			}
		}

	  if (!close(TMPFILE))
		{ print STDERR "$'cmd: closing temp file $tmpfile: $!\n";
		  $ok=0;
		}

	  $ok;
	}
