#!/usr/bin/perl
#
# Routines to run other Perl scripts like separate commands
# but with minimal fork/exec overhead.
#	- Cameron Simpson, 18jan94
#

package perlproc;

sub exec	# (script,@ARGV) -> void
	{ local($0,@ARGV)=@_;
	  $xit=0;
	  local($xit)=0;
	  local(@_)=@ARGV;
	  do $0;
	  exit $xit;
	}

sub pipefrom	# (FILE,script,@ARGV) -> FILE or undef
	{ local($FILE)=shift;
	  local($package,$file,$line)=caller;
	  local($pid);

	  $FILE="$package'$FILE" unless $FILE =~ /'/;
	  return undef unless defined($pid=open($FILE,"-|"));
	  return $FILE if $pid == 0;
	  &exec;
	}

sub pipeto	# (FILE,script,@ARGV) -> FILE or undef
	{ local($FILE)=shift;
	  local($package,$file,$line)=caller;
	  local($pid);

	  $FILE="$package'$FILE" unless $FILE =~ /'/;
	  return undef unless defined($pid=open($FILE,"|-"));
	  return $FILE if $pid == 0;
	  &exec;
	}

1;
