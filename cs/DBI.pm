#!/usr/bin/perl
#
# Subclass of the DBI class. Supplies some handy things.
#	- Cameron Simpson <cs@zip.com.au> 27jun99
#

=head1 NAME

cs::DBI - convenience routines for working with B<DBI>

=head1 SYNOPSIS

use cs::DBI;

=head1 DESCRIPTION

An assortment of routines for doing common things with B<DBI>.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use DBI;

package cs::DBI;

@cs::DBI::ISA=qw(DBI);

$cs::DBI::_now=time;

=head1 GENERAL FUNCTIONS

=over 4

=item mydb(I<mysql-id>,I<dbname>)

Return a database handle for my personal mysql installation.
I<mysql-id> is an optional string representing the database
to contact; it defaults to B<mysql@I<systemid>> where I<systemid>
comes from the B<SYSTEMID> environment variable.
This key is passed to B<cs::Secret::get> to obtain the database login keys.
I<dbname> is the name of the database to which to attach.
It defaults to B<CS_DB>.

=cut

sub mydb(;$$)
{ my($id,$dbname)=@_;
  $id="mysql\@$ENV{SYSTEMID}" if ! defined $id;
  $dbname='CS_DB' if ! defined $dbname;

  if ( ! defined $cs::DBI::_mydb{$id} )
  { ::need(cs::Secret);
    my $s = cs::Secret::get($id);
    my $login = $s->Value(LOGIN);
    my $password = $s->Value(PASSWORD);
    my $host = $s->Value(HOST);	$host='mysql' if ! defined $host;
    ## warn "$login\@$host: $password\n";

    $cs::DBI::_mydb{$id}=DBI->connect("dbi:mysql:$dbname:$host",$login,$password);
  }

  ## $cs::DBI::_mydb{$id}->trace(1,"$ENV{HOME}/tmp/mydb.trace");

  $cs::DBI::_mydb{$id};
}

=item isodate(I<gmt>)

Return an ISO date string (B<I<yyyy>-I<mm>-I<dd>>)
for the supplied UNIX B<time_t> I<gmt>.
If not supplied, I<gmt> defaults to now.

=cut

# ISO date from GMT
sub isodate
{ my($gmt)=@_;
  $gmt=$cs::DBI::_now if ! defined $gmt;

  my @tm = localtime($gmt);

  sprintf("%d-%02d-%02d", $tm[5]+1900, $tm[4]+1, $tm[3]);
}

=item hashtable(I<dbh>,I<table>,I<keyfield>,I<where>,I<preload>)

Return a reference to a hash tied to a database table,
which may then be manipulated directly.
This is not as efficient as doing bulk changes via SQL
(because every manipulation of the table
does the matching SQL, incurring much latency)
but it's very convenient.
The optional parameter I<where>
may be used to supply a B<WHERE> clause to the underlying SQL query.
If the optional parameter I<preload> is true
the entire table is fetched at instantiation time
with a single SQL call,
thus bypassing the latency
(a win if memory is plentiful and the table is not too large).

=cut

undef %cs::DBI::_HashTables;

sub hashtable($$$;$$)
{ my($dbh,$table,$keyfield,$where,$preload)=@_;
  $where='' if ! defined $where;
  $preload=0 if ! defined $preload;

  return $cs::DBI::_HashTables{$dbh,$table,$keyfield,$where}
  if exists $cs::DBI::_HashTables{$dbh,$table,$keyfield,$where};

  my $h = {};

  ::need(cs::DBI::Table::Hash);
  if (! tie %$h, cs::DBI::Table::Hash, $dbh, $table, $keyfield, $where, $preload)
  { return undef;
  }

  $cs::DBI::_HashTables{$dbh,$table,$keyfield,$where}=$h;
}

=item hashentry(I<keyvalue>,I<dbh>,I<table>,I<keyfield>,I<where>,I<preload>)

Return a B<cs::DBI::Table::RowObject> representing the row
whose I<keyfield> matches I<keyvalue>
from the table specified by I<dbh>, I<table> and I<where>
as for B<hashtable> above.
This object is suitable for subclassing
by a table specific module.

=cut

sub hashentry($$$$;$$)
{ my($keyvalue,$dbh,$table,$keyfield,$where,$preload)=@_;

  ::need(cs::DBI::Table::RowObject);
  cs::DBI::Table::RowObject->fetch($keyvalue,$dbh,$table,$keyfield,$where,$preload);
}

=item arraytable(I<dbh>,I<table>,I<where>)

Return a reference to an array tied to a database table,
which may then be manipulated directly.
The optional parameter I<where>
may be used to supply a B<WHERE> clause to the underlying SQL query.

=cut

undef %cs::DBI::_ArrayTables;

sub arraytable($$;$)
{ my($dbh,$table,$where)=@_;
  $where='' if ! defined $where;

  return $cs::DBI::_ArrayTables{$dbh,$table,$where}
  if exists $cs::DBI::_ArrayTables{$dbh,$table,$where};

  my $a = [];

  ::need(cs::DBI::Table::Array);
  if (! tie @$a, cs::DBI::Table::Array, $dbh, $table, $where)
  { return undef;
  }

  $cs::DBI::_ArrayTables{$dbh,$table,$where}=$a;
}

=item sql(I<dbh>,I<sql>)

Return a statement handle for the SQL command I<sql> applied to database I<dbh>.
This handle is cached for later reuse.

=cut

# return a statement handle for some sql
# it is cached for speed, since most SQL gets reused
sub sql($$)
{ my($dbh,$sql)=@_;
  if (! defined $dbh)
  { my @c=caller;warn "dbh UNDEF from [@c]";
  }

  my $stkey="$dbh $sql";

  ## my @c = caller;
  ## warn "sql($dbh,\"$sql\") from [@c]";

  return $cs::DBI::_cachedQuery{$stkey}
	if defined $cs::DBI::_cachedQuery{$stkey};

  $cs::DBI::_cachedQuery{$stkey} = $dbh->prepare($sql);
}

=item dosql(I<dbh>,I<sql>,I<execute-args...>)

Perform the SQL command I<sql> on database I<dbh> with the
I<execute-args> supplied.

=cut

# as above, but then perfomrs the statement handle's execute() method
#	dosql(dbh,sql,execute-args)
sub dosql
{ my($dbh,$sql)=(shift,shift);

  my $sth = sql($dbh,$sql);
  return undef if ! defined $sth;

  $sth->execute(@_)
  ? $sth : undef;
}

=item query(I<dbh>,I<table>,I<fields...>)

Return a statement handle to query I<table> in database I<dbh>
where all the specified I<fields> have specific values
(values to be supplied when the statement handle is executed).

=cut

# exact select on multiple fields
# returned statement handle for query to fetch specified records
sub query
{ my($dbh,$table)=(shift,shift);
  if (! defined $dbh)
  { my @c=caller;warn "dbh UNDEF from [@c]";
  }

  my $query = "SELECT * FROM $table";
  my $sep   = " WHERE ";

  for my $field (@_)
  { $query.="$sep$field = ?";
    $sep=" AND ";
  }

  ## warn "query: SQL=[$query]";

  sql($dbh, $query);
}

=item fetchall_hashref(I<sth>,I<execute-args...>)

Execute the statement handle I<sth> with the supplied I<execute-args>,
returning an array of hashrefs representing matching rows.

=cut

# execute statement handle and return all results as array of records
sub fetchall_hashref
{ my($sth)=shift;

  if (! wantarray)
  { my(@c)=caller;
    die "$0: cs::DBI::fetchall_hashref not in array context from [@c]";
  }

  return () if ! $sth->execute(@_);

  my $h;
  my @rows = ();
  while (defined ($h=$sth->fetchrow_hashref()))
  { push(@rows,$h);
  }

  @rows;
}

=item find(I<dbh>,I<table>,I<field>,I<value>)

Return an array of matching row hashrefs from I<table> in I<dbh>
where I<field> = I<value>.

=cut

# return records WHERE $field == $key
sub find($$$$)
{ my($dbh,$table,$field,$key)=@_;

  ## warn "find $table.$field = $key";

  if (! wantarray)
  { my(@c)=caller;
    die "$0: cs::DBI::find not in array context from [@c]"; 
  }

  my $sth = query($dbh,$table,$field);
  if (! defined $sth)
  { warn "$::cmd: can't make query on $dbh.$table where $field = ?";
    return ();
  }

  ## warn "doing fetchall";
  fetchall_hashref($sth,$key);
}

=item findWhen(I<dbh>,I<table>,I<field>,I<value>,I<when>)

Return an array of matching row hashrefs from I<table> in I<dbh>
where I<field> = I<value>
and the columns START_DATE and END_DATE span the time I<when>.
The argument I<when> is optional and defaults to today.

=cut

# return records WHERE $field == $key and the START/END_DATEs overlap $when
sub findWhen($$$$;$)
{ my($dbh,$table,$field,$key,$when)=@_;
  $when = isodate() if ! defined $when;

  my $sth = sql($dbh,"SELECT * FROM $table where $field = ? AND START_DATE <= ? AND (ISNULL(END_DATE) OR END_DATE >= ?)");
  return () if ! defined $sth;

  fetchall_hashref($sth,$key,$when,$when);
}

=item when(I<dbh>,I<table>,I<when>)

Return an array of matching row hashrefs from I<table> in I<dbh>
ehere the columns START_DATE and END_DATE span the time I<when>.
The argument I<when> is optional and defaults to today.

=cut

# return records WHERE the START/END_DATEs overlap $when
sub when($$;$)
{ my($dbh,$table,$when)=@_;
  $when = isodate() if ! defined $when;

  my $sth = sql($dbh,"SELECT * FROM $table where START_DATE <= ? AND (ISNULL(END_DATE) OR END_DATE >= ?)");
  return () if ! defined $sth;

  fetchall_hashref($sth,$when,$when);
}

=item updateField(I<dbh>,I<table>,I<field>,I<value>,I<where-field,where-value,...>)

Set the I<field> to I<value> in the I<table> in database I<dbh>
for records where the specified I<where-field> = I<where-value> pairs
all match.

=cut

# update fields in a table
# extra values are (f,v) for conjunctive "WHERE f = v" pairs
sub updateField
{ my($dbh,$table,$field,$value)=(shift,shift,shift,shift);

  my $sql = "UPDATE $table SET $field = ?";
  ## warn "\@_=[@_]";
  my @args = $value;
  my $sep = " WHERE ";
  while (@_ >= 2)
  { my($f,$v)=(shift,shift);
    ## warn "f=$f, v=$v";
    $sql.=$sep."$f = ?";
    push(@args,$v);
    $sep=" AND ";
  }

  ## warn "sql=[$sql], args=[@args]";

  dosql($dbh,$sql,@args);
}

=item lock_table(I<dbh>,I<table>)

Lock the specified I<table> in database I<dbh>.

=cut

sub lock_table($$)
{ my($dbh,$table)=@_;
  dosql($dbh,"LOCK TABLES $table WRITE");
}

=item unlock_tables(I<dbh>)

Release all locks held in database I<dbh>.

=cut

sub unlock_tables($)
{ my($dbh)=@_;
  dosql($dbh,"UNLOCK TABLES");
}

# return a statement handle with conjunctive WHERE constraints
# returns an ARRAY
#	empty on error
#	(sth, @args) if ok
sub sqlWhere
{ my($dbh,$sql,@w)=@_;

  my ($fullsql,@args) = sqlWhereText($sql,@w);
  my $sth = sql($dbh,$fullsql);
  return () if ! defined $sth;

  ($sth,@args);
}

sub sqlWhereText
{ my($sql,@w)=@_;

  my $sep = ' WHERE ';
  my @args = ();
  while (@w >= 2)
  { my($f,$v)=(shift(@w), shift(@w));
    push(@args,$v);
    $sql.=$sep."$f = ?";
    $sep=' AND ';
  }

  return ($sql, @args);
}

=item datedRecords(I<dbh>,I<table>,I<keyfield>,I<key>,I<when>,I<alldates>)

Retrieve active records from the specified I<table>
with the specified I<key> value.
If the optional parameter I<alldates> is true (it defaults to false)
return all the records for this I<key>, as an array of hashrefs.
Otherwise return only those records which overlap the day I<when>.
The parameter I<when> is optional, and defaults to today.

The records are returned ordered from oldest to most recent.

=cut

sub datedRecords($$$$;$$)
{ my($dbh,$table,$keyfield,$key,$when,$all)=@_;
  $when=cs::DBI::isodate() if ! defined $when;
  $all=0 if ! defined $all;

  my $D = cs::DBI::arraytable($dbh,$table);

  my @d =
	  $all
	  ? grep($_->{$keyfield} eq $key, @$D)
	  : grep($_->{$keyfield} eq $key
	      && $_->{START_DATE} le $when
	      && ( ! length $_->{END_DATE} || $when le $_->{END_DATE}),
		@$D)
  ;

  return
      sort { my $sa = $a->{START_DATE};
	     my $sb = $b->{START_DATE};

	     return $sa cmp $sb if $sa ne $sb;

	     my $ea = $a->{END_DATE};
	     my $eb = $b->{END_DATE};

	     return 0 if $ea eq $eb;

	     # catch open ended ranges
	     return 1 if ! length $eb;
	     return -1 if ! length $ea;

	     $ea cmp $eb;
	   }
      @d;
}

sub addDatedRecord
{ my($dbh,$table,$when,$rec,@delwhere)=@_;
  if (! defined $when)
  { my(@c)=caller;
    die "$::cmd: cs::DBI::addDatedRecord($table): \$when undefined from [@c]";
  }

  if (@delwhere)
  # delete old records first
  { warn "$0: addDatedRecord with delwhere=[@delwhere]";
    my ($sth, @args) = sqlWhere($dbh,'DELETE FROM $table',@delwhere);
    if (! defined $sth)
    { warn "$::cmd: cs::DBI::addDatedRecord($table): can't make sql to delete old records";
      return undef;
    }

    $sth->execute(@args);
  }

  $rec->{START_DATE}=$when;
  cs::DBI::insert($dbh,$table, keys %$rec)->ExecuteWithRec($rec);
}

sub delDatedRecord
{ my($dbh,$table,$when,@delwhere)=@_;
  if (! defined $when)
  { my(@c)=caller;
    die "$::cmd: cs::DBI::delDatedRecord($table): \$when undefined from [@c]";
  }
  if (@delwhere < 2)
  { my(@c)=caller;
    die "$::cmd: cs::DBI::delDatedRecord($table): no \@delwhere from [@c]";
  }

  # set closing date of the day before the deletion day
  my $today = new cs::Day $when;
  my $prevwhen = $today->Prev()->Code();

  my ($sql, @args) = sqlWhereText("UPDATE $table SET END_DATE = ?", @delwhere);
  $sql .= " AND START_DATE <= ? AND ISNULL(END_DATE)";

  my $sth = sql($dbh, $sql);
  if (! defined $sth)
  { warn "$::cmd: cs::DBI::delDatedRecord($table): can't make sql to delete old records";
    return undef;
  }

  $sth->execute($prevwhen,@args,$when);
}

=item cleanDates(I<dbh>,I<table>,I<keyfield>,I<keys...>)

Edit the specified I<table>
such that no records for a single value of I<keyfield> overlap.
Later records are presumed to be more authoritative than earlier records.
Earlier records overlapping later fields have their B<END_DATE> fields cropped.
B<Warning>:
earlier records completely overlapped by later records
are B<discarded>.
This may not be what you want.

If the optional list of I<keyfields>, I<keys...>, is supplied
then only those values will have their dates cleaned.
The default behaviour is to clean the entire table.

=cut

sub cleanDates
{ my($dbh,$table,$keyfield,@keys)=@_;

  my $sth = cs::DBI::query($dbh,"SELECT $keyfield FROM $table");
  if (! defined $sth)
  { warn "$::cmd: cleanDates($dbh,$table,$keyfield): can't make SQL statement handle";
    return;
  }

  if (! @keys)
  { my $a = $sth->fetchall_arrayref();
    @keys = ::uniq(map($_->[0], @$a));
  }

  KEY:
  for my $key (@keys)
  {
    my @D = reverse cs::DBI::datedRecords($dbh,$table,$keyfield,$key,undef,1);

    next KEY if ! @D;

    my $prev_start = shift(@D)->{START_DATE};

    # prune earlier records
    while (@D)
    { my $D = shift(@D);
      my $start = $D->{START_DATE};
      my $end   = $D->{END_DATE};

      if (length $end && $end lt $start)
      {
	my @sqlargs=($key,$start);
	my $sql = "DELETE FROM $table WHERE $keyfield = ? AND START_DATE = ? AND ";

	if (length $end)
	{ $sql.='END_DATE = ?';
	  push(@sqlargs,$end);
	}
	else
	{ $sql.='ISNULL(END_DATE)';
	}

	nl("$sql\n\t[@sqlargs]");
	cs::DBI::dosql($dbh,$sql,@sqlargs);
      }
      elsif (! length $end || $end ge $prev_start)
      { my $nend = cs::Day->new($prev_start)->Prev()->Code();
	if ($nend lt $start)
	{
	  my @sqlargs=($key,$start);
	  my $sql = "DELETE FROM $table WHERE $keyfield = ? AND START_DATE = ? AND ";

	  if (length $end)
	  { $sql.='END_DATE = ?';
	    push(@sqlargs,$end);
	  }
	  else
	  { $sql.='ISNULL(END_DATE)';
	  }

	  nl("$sql\n\t[@sqlargs]");
	  cs::DBI::dosql($dbh,$sql,@sqlargs);
	}
	else
	{
	  my @sqlargs=($nend,$key,$start);
	  my $sql = "UPDATE $table SET END_DATE = ? WHERE $keyfield = ? AND START_DATE = ? AND ";

	  if (length $end)
	  { $sql.='END_DATE = ?';
	    push(@sqlargs,$end);
	  }
	  else
	  { $sql.='ISNULL(END_DATE)';
	  }

	  nl("$sql\n\t[@sqlargs]");
	  cs::DBI::dosql($dbh,$sql,@sqlargs);
	}
      }

      $prev_start=$start;
    }
  }
}

=item last_id()

Return the id of the last item inserted with B<ExecuteWithRec> below.

=cut

# return the id if the last record inserted by ExecuteWithRec
undef $cs::DBI::_last_id;
sub last_id()
{ $cs::DBI::_last_id;
}

=item addRow(I<dbh>,I<table>,I<record>)

Add a row represented by the hashref I<record>
to I<table> in database I<dbh>.
Returns the B<last_id> value.

=cut

sub addRow($$$)
{ my($dbh,$table,$r)=@_;

  my $ins = insert($dbh,$table,keys %$r);
  return undef if ! defined $ins;

  $ins->ExecuteWithRec($r)
  ? last_id()
  : undef;
}

=back

=head1 OBJECT CREATION

=over 4

=item insert(I<dbh>,I<table>,I<dfltok>,I<fields...>)

Create a new B<cs::DBI> object
for insertion of rows into I<table> in database I<dbh>.
If the parameter I<dfltok> is supplied as 0 or 1
it governs whether it is permissible for the inserted rows
to lack values for the I<fields> named;
the default is for all named I<fields> to be required.
Once created,
this object may be used with the
B<ExecuteWithRec> method below.

=cut

# return a cs::DBI object which accepts the ExecuteWithRec method,
# used to insert records
sub insert	# dbh,table[,dfltok],fields...
{ my($dbh,$table)=(shift,shift);
  my $dfltok=0;
  if (@_ && $_[0] =~ /^[01]$/)
  { $dfltok=shift(@_)+0;
  }
  my @fields = @_;

  my $sql = "INSERT INTO $table ("
	  . join(',',@fields)
	  . ") VALUES ("
	  . join(',',map('?',@fields))
	  . ")";

  ## warn "SQL is [$sql]";
  bless [ $dbh, $table, $sql, sql($dbh,$sql), $dfltok, @fields ];
}

=back

=head1 OBJECT METHODS

=over 4

=item ExecuteWithRec(I<record-hashrefs...>)

Insert the records
described by the I<record-hashrefs>
into the appropriate table.

=cut

sub ExecuteWithRec
{ my($isth)=shift;

  my($dbh,$table,$sql,$sth,$dfltok,@fields)=@$isth;

  ## warn "stashing record:\n".cs::Hier::h2a($rec,1);
  ## warn "sth=$sth, \@isth=[ @$isth ]\n";
  ## warn "fields=[@fields]";

  my $ok = 1;

  # hack - lock the table if we're inserting 5 or more records,
  # for speed
  my $locked = (@_ > 5 && lock_table($dbh,$table));

  INSERT:
  while (@_)
  { my $rec = shift(@_);
    ## warn "INSERT rec = ".cs::Hier::h2a($rec,1);
    my @execargs=();

    for my $field (@fields)
    { if (! exists $rec->{$field})
      { if ($dfltok)
	{ $rec->{$field}=undef;
	}
	else
	{ ::need(cs::Hier);
	  die "$::cmd: ExecuteWithRec(): no field \"$field\": rec="
	      .cs::Hier::h2a($rec,1);
	}
      }
      elsif (! defined $rec->{$field})
      { ## warn "$field = UNDEF!";
	## $rec->{$field}='';
      }

      push(@execargs, $rec->{$field});
    }

    # for some reason, text fields can't be empty - very bogus
    ## for (@execargs) { $_=' ' if defined && ! length; }


    if (! $sth->execute(@execargs))
    { warn "$::cmd: ERROR with insert";
      my @c = caller;
      warn "called from [@c]\n";
      warn "execargs=".cs::Hier::h2a(\@execargs,0)."\n";
      $ok=0;
      last INSERT;
    }
    else
    { ## warn "INSERT OK, noting insertid";
      ## XXX: was 'insertid'; may break if we ever leave mysql
      $cs::DBI::_last_id=$sth->{'mysql_insertid'};
    }
  }

  unlock_tables($dbh) if $locked;

  $ok;
}

# given a date (ISO form) select the entries from the given table
# which contain the date
sub SelectDateRanges($$$;$)
{ my($this,$table,$constraint,$when)=@_;
  $constraint=( defined $constraint && length $constraint
	     ? "$constraint AND "
	     : ''
	     );
  $when=isodate() if ! defined $when;

  my $dbh = $this->{DBH};

  $when=$dbh->quote($when);

  my $statement = "SELECT * FROM $table WHERE $constraint START_DATE <= $when AND ( ISNULL(END_DATE) OR END_DATE >= $when )";
  ## warn "statement=[$statement]";
  dosql($dbh,$statement);
}

# as above, but return the data
sub GetDateRanges
{ my($this)=shift;

  my $sth = $this->SelectDateRanges(@_);
  return () if ! defined $sth;
  fetchall_hashref($sth);
}

=back

=head1 SEE ALSO

DBI(3), cs::DBI::Table::RowObject(3)

=head1 AUTHOR

Cameron Simpson <cs@zip.com.au>

=cut

1;
