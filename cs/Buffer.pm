#!/usr/bin/perl
#
# A buffer with logging. Used by Multio et al.
#	- Cameron Simpson <cs@zip.com.au>
#
# new(@extras) -> Buffer
# Size -> length of buffer
# Q(@data) -> add @data to end of buffer
# DQ(n) -> extract the front n bytes from the buffer
#	   returns a short string if the buffer isn't that big
#

use strict qw(vars);


##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }


use cs::Log;


package cs::Buffer;


sub new
	{ local($package)=shift;
	
	  bless { Buffer	=> '',
		  InLog		=> new cs::Log,
		  OutLog	=> new cs::Log,
		  Log0		=> 0,
		  Debug		=> 1,
		  @_		# extras
		};
	}


sub Size{ local($_)=shift; length(${$_[0]->{Buffer}});
	}


sub Q	# (this,@data) -> void
	{ local($this)=shift;


	  ${$this->{Buffer}}.=join('',@_);


	  local($l0,$now,$_)=(${$this->{Log0}},time);


	  for $_ (@_)
		{ if (length || $l0)
			{ $this->{InLog}->Add($now,length);
			}
		}
	}


sub _QFront # (this,@data) -> void
	{ local($this)=shift;


	  ${$this->{Buffer}}=join('',@_).${$this->{Buffer}};


	  local($l0,$now,$_)=(${$this->{Log0}},time);


	  for $_ (@_)
		{ if (length || $l0)
			{ $this->{OutLog}->Add($now,-length);
			}
		}
	}


sub DQ	# (this,n) -> front-n-removed
	{ local($this,$n)=@_;
	  local($len)=length ${$this->{Buffer}};


	  if ($len < $n)
		{ warn "$'cmd: ${this}::DQ($n): only $len bytes available\n"
			if $this->{Debug};
		  $n=$len;
		}


	  local($_)=substr(${$this->{Buffer}},0,$n);


	  substr(${$this->{Buffer}},0,$n)='';


	  if ($n || ${$this->{Log0}})
		{ $this->{OutLog}->Add(time,length);
		}


	  $_;
	}


1;
