#!/usr/bin/perl
#
# Parse a Mykefile.
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Myke::File;

sub new
	{ my($class,$myke,$src,$name,$sameLevel,$lineno)=@_;
	  $name=$src->Name() if ! defined $name;
	  $sameLevel=0       if ! defined $sameLevel;
	  $lineno=0          if ! defined $lineno;

	  $myke->{LEVEL}++ if ! $sameLevel;

	  bless { MYKE	=> $myke,
		  NAME	=> $name,
		  LEVEL	=> $myke->{LEVEL},
		  LINENO => $lineno,
		  DS	=> $src,
		}, $class;
	}

sub ReadLine
	{ my($this)=@_;

	  local($_);

	  my($src)=$this->{DS};

	  return undef if ! defined ($_=$src->GetLine())
		       || ! length;

	  my($context)="$this->{NAME}, line ".++$this->{LINENO};
	  my($nline);

	  # handle slosh extension, stripping slosh
	  while (/\\$/)
		{ if (! defined ($nline=$src->GetLine())
		   || ! length $nline)
			{ warn "$context: unexpected EOF, expected slosh extension\n";
			  return undef;
			}

		  $this->{LINENO}++;

		  s/\\$//;
		  $_.=$nline;
		}

	  chomp;

	  wantarray ? ($_,$context) : $_;
	}

sub ReadFile
	{ my($this)=@_;

	  my(@ifstack)=();
	  my($active)=1;

	  my($context,$oline);
	  local($_);

	  LINE:
	    while (1)
		{ ($_,$context)=$this->ReadLine();
		  last LINE if ! defined;

		  $oline=$_;

		  if (/^:/)
			{ 
			}
		  elsif (! $active)
			{
			}
		  #           macro        (     args         ,...                )      =
		  elsif (/^\s*[a-z_]\w*(\s*\((\s*[a-z_]\w*(\s*,\s*[a-z_]\w*)*)?\s*\))?\s*=/)
			# macro definition
			{
			  my($macro,@args,$mvalue);

			  # macro name
			  /^\s*([a-z_]\w*)\s*/;
			  $macro=$1;
			  $_=$';

			  # arguments
			  if (/^\(([\s\w,]*)\)\s*/)
				{ my($args)=$&;
				  $_=$';

				  @args=grep(length,split(/[\s,]+/,$args));
				}
			  else	{ @args=();
				}

			  /^=\s*/ || die "huh? no \"=\" [$_]";
			  $mvalue=$';

			  $this->DefMacro($context,$macro,[@args],$mvalue);
			}
		  else
		  # 
		  {
		  die "XXX: incomplete";
		  }
		}
	}

sub DefMacro
	{ my($this,$context,$macro,$args,$mvalue)=@_;
	  my($myke)=$this->{MYKE};

	  my($mp);

	  if (defined ($mp=$myke->FindMacro($macro))
	   && $mp->{LEVEL} >= $this->{LEVEL})
		{ warn "$context: redefinition of macro \$$macro\n"
		      ."\tprevious definition was at $mp->{CONTEXT}\n";
		}
	  else
		{
		  $myke->SetMacro($context,$this->{LEVEL},$macro,$args,$mvalue);
		}
	}

sub MkPPContext
	{ my($this,$context,$ifcond,$active)=@_;

	  { CONTEXT	=> $context,
	    IFCOND	=> $ifcond,
	    IFPART	=> 1,
	    ACTIVE	=> $active,
	  };
	}

1;
