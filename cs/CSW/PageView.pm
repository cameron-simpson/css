#!/usr/bin/perl
#
# cs::Web::CSW::PageView: display a web page
#	- Cameron Simpson <cs@zip.com.au> 6mar2000
#

=head1 NAME

cs::Web::CSW::PageView - display a web page

=head1 SYNOPSIS

use cs::Web::CSW::PageView;

=head1 DESCRIPTION

The cs::Web::CSW::PageView module provides an undecorated frame
displaying a web page.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::Web::CSW::PageView;

require Exporter;

@cs::Web::CSW::PageView::ISA=qw();

=head1 GENERAL FUNCTIONS

=over 4

=back

=head1 OBJECT CREATION

Usually this will be called as a widget within a frame.

=over 4

=item new cs::Web::CSW::PageView I<parent>, I<url>, I<prevhist>

Create a new view of a URL.

=cut

sub new($$$$)
{ my($class,$W,$url,$prevhist)=@_;

  my $w = $W->Canvas();
  my $U = $::Cache->Request($url,\&_notify);

  bless { W => $w,
	  URL => $url,
	  CACHE => $U,
	  STATE => NEW,
	  HIST => new cs::Web::CSW::History
	}, $class;
}

sub _notify($$)
{ my($fetch,$what)=@_;
warn "_notify(@_)";
}

=back

=head1 OBJECT METHODS

=over 4

=item Widget()

Return the widget associated with this object.

=cut

sub Widget($)
{ shift->{W};
}

=item Forward(I<url>)

Advance forward 

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
