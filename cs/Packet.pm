#!/usr/bin/perl
#
# A packet queue with logging. Used by Multio et al.
#	- Cameron Simpson <cs@zip.com.au>
#
# new(@extras) -> Packet
# Size -> number of packets
# Q($data,@attributes) -> add data to end of queue
# DQ(n) -> extract the front n bytes from the buffer
#	   returns a short string if the buffer isn't that big
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Log;

package cs::Packet;

sub new
	{ local($package)=shift;
	
	  bless { Buffer	=> [],		# ary of packets
		  InLog		=> new cs::Log,
		  OutLog	=> new cs::Log,
		  Debug		=> 1,
		  @_		# extras
		};
	}

sub Size{ local($this)=shift; scalar(@{$this->{Buffer}}); }

sub Q	# (this,$data,@attributes) -> void
	{ local($this)=shift;
	  local($ref)=[ @_ ];

	  push(@{$this->{Buffer}},$ref);
	  $this->{InLog}->Add(time,length($_[0]));
	}

sub _QFront # (this,@data) -> void
	{ local($this)=shift;
	  local($ref)=[ @_ ];

	  unshift(@{$this->{Buffer}},$ref);
	  $this->{InLog}->Add(time,-length($_[0]));
	}

sub DQ	# (this) -> \[ data, @attributes ] or undef
	{ local($this)=shift;

	  return undef unless $this->Size();

	  local($ref)=shift(@{$this->{Buffer}});

	  $this->{OutLog}->Add(time,length($$ref[0]));

	  $ref;
	}

1;
