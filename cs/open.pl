#!/usr/bin/perl
#

require 'cs/package.pl';

sub open	# (FILE,mode,file) -> ok
	{ local($FILE,$MODE,$file)=@_;

	  if ($file !~ m:^[./]:)
		{ $file='./'.$file;
		}

	  $FILE=caller(0)."'$FILE" unless $FILE =~ /'/;

	  open($FILE,$MODE.' '.$file."\0");
	}

$_subopen_handle='SUBOPEN0000';
sub subopen	# (mode,file) -> FILE or undef
	{ local($submode,$subfile)=@_;
	  local($subFILE)="main'".$_subopen_handle++;

	  &open($subFILE,$submode,$subfile) ? $subFILE : undef;
	}

sub subdup	# (mode,FILE) -> newFILE
	{ local($submode,$subFILE)=@_;
	  local($newFILE)="main'".$_subopen_handle++;

	  &abs(subFILE);
	  open($newFILE,"$submode&$subFILE") ? $newFILE : undef;
	}

sub openlist	# @list -> FILE or undef
	{ local($pid);
	  local($FILE)="main'".$_subopen_handle++;

	  if (!defined($pid=open($FILE,'-|')))
		{ print STDERR "$'cmd: can't pipe and fork: $!\n";
		  return undef;
		}

	  if ($pid == 0)
		{ local($_);
		  for (@_)
			{ print STDOUT $_;
			}
		  close($FILE);
		  exit 0;
		}

	  $FILE;
	}

1;	# for require
