#!/usr/bin/perl
#

=head1 NAME

cs::Newsrc - manipulate newsrc files

=head1 SYNOPSIS

use cs::Newsrc;

=head1 DESCRIPTION

This module loads and rewrites newsrc files.
It optional has support for multiple news servers.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Range;
use cs::Source;
use cs::Sink;

package cs::Newsrc;

=head1 GENERAL FUNCTIONS

=over 4

=item parseLine(I<line>)

Parse a newsrc I<line>
and return I<group>, I<server>, I<subscribed> and a B<cs::Range> object
specifying the article range.
Returns the empty list on syntax errors.
Returns the empty string for I<server>
if there is no B<@I<server>> appended to the group name.

=cut

sub parseLine($)
{ local($_)=@_;

  chomp;
  s/^\s+//;
  s/\s+$//;
  return () unless length;

  ## warn "[$_]";
  #        news        .group             @host     :port        :
  #       1          2                  3  4       5            6
  if (! /^([^.@\s:!]+(\.[^.@\s:!]+)*)\s*(\@([^\s:]+(:\d+)?))?\s*([:!])\s*/)
  { return ();
  }

  my($group,$server,$subbed)=($1,$4,($6 eq ':'));
  my $R = new cs::Range $';

  return ($group,$server,$subbed,$R);
}

=back

=head1 OBJECT CREATION

=over 4

=item new cs::Newsrc I<file>

Return a new B<cs::Newsrc> object attached to I<file>.
Returns B<undef> if I<file> exists but cannot be read.
The file will be rewritten on object destruction.

=cut

sub new($$;$)
{ my($class,$file,$dfltserver)=@_;
  $dfltserver="" if ! defined $dfltserver;

  my $this = bless { FILE => $file,
		     INVALID => 0,
		     GROUPS => {},
		     GROUPLIST => [],
		     SUBBED => {},
		   }, $class;
  
  $dfltserver=$this->DefaultServer($dfltserver);

  my $s = new cs::Source (PATH, $file);
  if (! defined $s)
  { my $err = "$!";
    if (-e $file)
    { warn "$::cmd: new cs::Newsrc $file: $err\n";
      return undef;
    }
  }
  else
  { local($_);

    my $lineno = 0;

    RCLINE:
    while (defined($_=$s->GetLine()) && length)
    {
      $lineno++;

      my($group,$server,$subbed,$R)=parseLine($_);

      if (! defined $group)
      { warn "$::cmd: $file, line $lineno: syntax error\n";
	next RCLINE;
      }

      $server='' if ! defined $server;
      $server=$this->DefaultServer() if ! length $server;
      $server =~ s/:119$// if length $server;
      $group="$group\@$server";

      $this->AddGroup($group,$R,$subbed);
    }
  }

  $this;
}

sub DESTROY()
{ my($this)=@_;
  $this->Sync() unless $this->{INVALID};
}

=back

=head1 OBJECT METHODS

=over 4

=item Groups()

Return a list of the groups in the newsrc,
subscribed or not.

=cut

sub Groups($)
{ @{shift->{GROUPLIST}};
}

=item AddGroup(I<group>,I<range>,I<subscribed>)

Add a new I<group> to the newsrc,
with optional seen articles I<range>
(a B<cs::Range>, default empty)
and status I<subscribed> (default true).
It is an error to call this for a known group.

=cut

sub AddGroup($$;$$)
{ my($this,$group,$R,$subbed)=@_;
  $group=$this->_AbsGroup($group);
  $R=new cs::Range if ! defined $R;
  $subbed=1 if ! defined $subbed;

  if (exists $this->{GROUPS}->{$group})
  { die "$::cmd: repeated addition of group \"$group\"";
  }

  push(@{$this->{GROUPLIST}}, $group);
  $this->SetRange($group, $R);
  $this->Subscribe($group,$subbed);
}

=item UnshiftGroup(I<group>)

Move the I<group> to the front of the ordering.

=cut

sub UnshiftGroup($$)
{ my($this,$group)=@_;
  $group=$this->_AbsGroup($group);

  if (! grep($_ eq $group, $this->Groups()))
  { $this->AddGroup($group);
  }

  @{$this->{GROUPLIST}}=(
			  $group,
			  grep($_ ne $group, @{$this->{GROUPLIST}})
			);
}

=item PushGroup(I<group>)

Move the I<group> to the back of the ordering.

=cut

sub PushGroup($$)
{ my($this,$group)=@_;
  $group=$this->_AbsGroup($group);

  if (! grep($_ eq $group, $this->Groups()))
  { $this->AddGroup($group);
  }

  @{$this->{GROUPLIST}}=(
			  grep($_ ne $group, @{$this->{GROUPLIST}}),
			  $group
			);
}

=item Sync(I<file>)

Write the current state of the object to the specified I<file>
(or the file supplied to B<new> if not specified).

=cut

sub Sync($;$$)
{ my($this,$file,$dropdflt)=@_;
  $file=$this->{FILE} if ! defined $file;

  ## warn "Sync(file=$file)";

  my $s = new cs::Sink (PATH,$file);
  if (! defined $s)
  { warn "$::cmd: cs::Newsrc::Sync($file): $!\n";
    return 0;
  }

  my $dfltsfx = '@'.$this->DefaultServer;

  for my $group ($this->Groups())
  { my $keygrp = $group;
    ## warn "write \"$group\"";

    if ($dropdflt && substr($group,-length($dfltsfx)) eq $dfltsfx)
    { $group=substr($group,$[,length($group)-length($dfltsfx));
    }

    $s->Put($group,
	    ($this->Subscribe($keygrp) ? ':' : '!'),
	    " ",
	    $this->Range($keygrp)->Text(),
	    "\n");
  }

  undef $s;

  1;
}

=item DefaultServer(I<server>)

Set or return the default server associated with this file.

=cut

sub DefaultServer($;$)
{ my($this,$dflt)=@_;

  return $this->{DFLTSERVER} if ! defined $dflt;

  $dflt=$ENV{NNTPSERVER} if ! length $dflt;
  $dflt =~ s/:119$//;

  $this->{DFLTSERVER}=$dflt;
}

sub _AbsGroup($$)
{ my($this,$group)=@_;
  $group.="\@".$this->DefaultServer() unless $group =~ /\@/;
  $group;
}

=item MatchesServer(I<group>,I<server>)

Check if a group belongs to the specified server.

=cut

sub MatchesServer($$$)
{ my($this,$group,$server)=@_;
  $group=$this->_AbsGroup($group);
  $server =~ s/:119$//;
  $server=$this->DefaultServer() if ! length $server;

  substr($group,-length($server)-1) eq "\@$server";
}

=item Range(I<group@server>)

Return the range associated with the specified I<group@server>.

=cut

sub Range($$)
{ my($this,$group)=@_;

  $group=$this->_AbsGroup($group);

  my $G = $this->{GROUPS};

  if (! exists $G->{$group})
  { $this->AddGroup($group);
  }

  $G->{$group};
}

=item SetRange(I<group@server>, I<range>)

Set the B<cs::Range> object associated with I<group@server> to I<range>.

=cut

sub SetRange($$$)
{ my($this,$group,$R)=@_;

  $group=$this->_AbsGroup($group);
  $this->{GROUPS}->{$group}=$R;
}

=item Subscribe(I<group>, I<status>)

Set or return the subscription status of the I<group>.

=cut

sub Subscribe($$;$)
{ my($this,$group,$subbed)=@_;

  $group=$this->_AbsGroup($group);

  die "$::cmd: cs::Newsrc::Subscribe(@_) when no group \"$group\" known"
  if ! exists $this->{GROUPS}->{$group};

  my $S = $this->{SUBBED};

  if (! defined $subbed)
  { return 0 if ! exists $S->{$group};
    return $S->{$group};
  }

  $S->{$group}=$subbed;
}

=back

=head1 SEE ALSO

cs::NNTP(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt> 11may2000

=cut

1;
