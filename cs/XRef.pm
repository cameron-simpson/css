#!/usr/bin/perl
#
# Build and emit a cross reference of HREFs.
#	- Cameron Simpson <cs@zip.com.au> 06jul97
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

package cs::XRef;

sub new
	{ my($class,$key)=@_;
	  
	  bless { REFERENCES	=> [],	# things references by this category
		  CATEGORIES	=> {},	# subcategories
		  DESC		=> '',	# category title
		  KEY		=> $key,
		}, $class;
	}

sub Add
	{ my($this,$ref)=@_;

	  my(@cats)=@{$ref->{CATEGORIES}};
	  my($cat,$subcat,$p);

	  # for each categorisation of the new ref
	  for $cat (@cats)
		{
		  # point at xref category tree
		  $p=$this;

		  # walk down to leaf
		  for $subcat (grep(length,split(m|[\s/]+|,$cat)))
			{
			  $p=$p->{CATEGORIES};
			  $p->{$subcat}=new cs::XRef($subcat) if ! exists $p->{$subcat};
			  $p=$p->{$subcat};
			}

		  # add ref to leaf
		  push(@{$p->{REFERENCES}},$ref);
		}
	}

# emit markup for a category tree
sub MarkUp	# mfunc
	{ my($this,$level,$mfunc,$pretag)=@_;
	  $level=1 if ! defined $level;
	  $mfunc=\&_mFunc if ! defined $mfunc;
	  $pretag='' if ! defined $pretag;

	  my($tag)=$this->{KEY};
	  $pretag=(length $pretag ? "$pretag-$tag" : $tag);

	  my(@sub)=();
	 
	  my($subMU,$subDepth);

	  for (@{$this->{CATEGORIES}})
		{ ($subMU,$subDepth)=$_->MarkUp($level+1,$mfunc,$pretag);
		  push(@sub,{ DEPTH	=> $subDepth,
			      MU	=> $subMU,
			      KEY	=> $_->{KEY},
			    });
		}

	  my($depth)=::max(0,map($_->{DEPTH},@sub));

	  if (@{$this->{CATEGORIES}} > 1
	   || @{$this->{REFERENCES}})
		{ $depth++;
		}

	  my(@mu);

	  if (@{$this->{REFERENCES}})
	    { if (@{$this->{CATEGORIES}})
		{ @mu=(["H$level",[A,{NAME=>$pretag},$this->Desc()]],"\n",
		       "References: ",
		        _joinMU(",\n",
				map($mfunc($_),
				    @{$this->{REFERENCES}}))],"\n",
		       ]
		      );
		}

	  ($mu,$depth);
	}

sub _joinMU
	{ my($sep)=shift;
	  my(@j);

	  for (@_)
		{ push(@j,$sep) if @j;
		  push(@j,$_);
		}

	  @j;
	}

sub _mFunc
	{
	  my($ref)=@_;

	  [A,{HREF => $ref->{URL}},$ref->{TITLE}];
	}

1;
