#!/usr/bin/perl
#
# Extension to Tk.
#	- Cameron Simpson <cs@zip.com.au> 19dec1999
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use Tk;
use cs::Misc;

package cs::Tk;

@cs::Tk::ISA=qw(MainWindow);

undef $cs::Tk::_mainWin;

=head1 FUNCTIONS

=over 4

=item mainWindow()

Return the core B<cs::Tk> object used to make more B<cs::Tk::*> objects.

=cut

sub mainWindow
{ if (! defined $cs::Tk::_mainWin)
  { $cs::Tk::_mainWin = MainWindow->new(@_);
    bless $cs::Tk::_mainWin, cs::Tk;
  }

  $cs::Tk::_mainWin;
}

sub _widget
{ my($csClass)=shift;
  $csClass="cs::Tk::".$csClass unless $csClass =~ /^cs::Tk::/;

  ::need($csClass);
  my $w = eval "${csClass}::new($csClass,\@_)"; die $@ if $@;
  $w;
}

=item csClock(I<parent>)

Return a B<cs::Tk::Clock> object with parent widget I<parent>.

=cut

sub csClock
{ _widget(cs::Tk::Clock,@_);
}

=item csFetchURL(I<parent>)

Return a B<cs::Tk::FetchURL> object with parent widget I<parent>.
This is really only in the B<cs::Tk> stuff to take advantage of the event loop.

=cut

sub csFetchURL
{ _widget(cs::Tk::FetchURL,@_);
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;
