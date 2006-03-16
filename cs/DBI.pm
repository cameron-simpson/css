#!/usr/bin/perl
#
# Subclass of the DBI class. Supplies some handy things.
#	- Cameron Simpson <cs@zip.com.au> 27jun1999
#

=head1 NAME

cs::DBI - convenience routines for working with B<DBI>

=head1 SYNOPSIS

use cs::DBI;

=head1 DESCRIPTION

An assortment of routines for doing common things with B<DBI>.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::DMY;
use DBI;

package cs::DBI;

@cs::DBI::ISA=qw(DBI);

$cs::DBI::_now=time;

=head1 GENERAL FUNCTIONS

=over 4

=item isodate(I<gmt>)

Return an ISO date string (B<I<yyyy>-I<mm>-I<dd>>)
in local time
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

  if (! defined $dbh)
  { my @c=caller;die "dbh UNDEF from [@c]";
  }
  elsif (! ref $dbh)
  { my @c=caller;die "dbh ($dbh) not a ref from [@c]";
  }

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

  if (! defined $dbh)
  { my @c=caller;warn "dbh UNDEF from [@c]";
  }

  return $cs::DBI::_cachedQuery{$stkey}
	if defined $cs::DBI::_cachedQuery{$stkey};

  $cs::DBI::_cachedQuery{$stkey} = $dbh->prepare($sql);
}

=item dosql(I<dbh>,I<sql>,I<execute-args...>)

Perform the SQL command I<sql> on database I<dbh> with the
I<execute-args> supplied.

=cut

sub dosql
{ my($dbh,$sql)=(shift,shift);

  ::debug("dosql: sql=\n$sql\nargs = [".join(',',map(defined($_) ? $_ : 'UNDEF', @_))."]\n");
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

=item sqlWhere(I<dbh>,I<sql>,I<where1>,I<where-arg1>,I<where2>,I<where-arg2>,...)

Obtain a statement handle
with a conjunctive WHERE constraint
specified by ANDing together the strings I<whereB<n>>.
Return value is an array
which is empty on error
and otherwise contains B<(I<sth>,I<where-args...>)>
where I<sth> is the new statement handle
and I<where-args> is the I<where-argB<n>> values.

=cut

sub sqlWhere
{ my($dbh,$sql,@w)=@_;

  my ($fullsql,@args) = sqlWhereText($sql,@w);
  my $sth = sql($dbh,$fullsql);
  return () if ! defined $sth;

  ($sth,@args);
}

=item sqlWheretext(I<sql>,I<where-arg1>,I<where2>,I<where-arg2>,...)

Construct an SQL statement
with a conjunctive WHERE constraint
specified by ANDing together the strings I<whereB<n>>.
Return value is an array
containing the SQL statement (a string)
and the arguments with which to execute it
(the I<where-argB<n>> values).

=cut

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

=item sqlWhere2(I<dbh>,I<sql>,I<where>,I<where-args...>)

Obtain a statement handle
with an arbitrary WHERE constraint
specified by the string I<where> and the following arguments
I<where-args>.
Return value is an array
which is empty on error
and otherwise contains B<(I<sth>,I<where-args...>)>
where I<sth> is the new statement handle.

=cut

sub sqlWhere2
{ my($dbh,$sql,@w)=@_;

  my ($fullsql,@args) = sqlWhereText($sql,@w);
  my $sth = sql($dbh,$fullsql);
  return () if ! defined $sth;

  ($sth,@args);
}

=item delByKey(I<dbh>,I<table>,I<keyfield>,I<keys...>)

Obtain a statement handle
to DELETE records whose I<keyfield>s
have values in the list I<keys...>.
Note: if I<keys> is empty an expensive no-op is generated.

=cut

sub delByKey
{ my($dbh,$table,$keyfield)=(shift,shift,shift);
  
  my $sql = 'DELETE FROM $table';

  my $sep = 'WHERE';
  for my $key (@_)
  { $sql.=" $sep $keyfield = ?";
    $sep='OR';
  }

  # safety net
  if ($sep eq 'WHERE')
  { $sql.=" WHERE FALSE";
  }

  return sqlWhere2($dbh,$sql,@_);
}

=item lookupDatedIds(I<dbh>,I<table>,I<srcidfield>,I<destidfield>,I<id>,I<when>)

Return the ids from the field I<destidfield>
for records from I<table> overlapping the date I<when>
and which has I<srcidfield> equal to the supplied I<id>.
The parameter I<when> is optional, and defaults to today.

=cut

sub lookupDatedIds($$$$$;$)
{ my($dbh,$table,$srcfield,$destfield,$id,$when)=@_;
  $when=cs::DBI::isodate() if ! defined $when;

  my $sth = dosql($dbh,
		  "SELECT $destfield FROM $table WHERE $srcfield = ? AND (ISNULL(START_DATE) OR START_DATE <= ?) AND (ISNULL(END_DATE) OR END_DATE >= ?)",
		  $id,$when,$when);
  if (!$sth)
  { warn "$::cmd: lookupDatedIds($table,$srcfield=$id -> $destfield) fails";
    return ();
  }

  my $h;
  my @ids = ();
  while (defined ($h=$sth->fetchrow_hashref()))
  { push(@ids,$h->{$destfield});
  }

  return @ids;
}

=item followDatedRecords(I<dbh>,I<srctable>,I<desttable>,I<srcidfield>,I<destidfield>,I<desttableidfield>,I<id>,I<when>)

Lookup the dated table I<srctable>
and return the records from the table I<desttable> whose field I<desttableidfield>
matches the field I<destidfield> from I<srctable>
for records from I<srctable> overlapping the date I<when>
and which has I<srcidfield> equal to the supplied I<id>.
The parameter I<when> is optional, and defaults to today.

=cut

sub followDatedRecords($$$$$$$;$)
{ my($dbh,$srctable,$desttable,$srcfield,$destfield,$desttablefield,$id,$when)=@_;
  $when=cs::DBI::isodate() if ! defined $when;

  my $sth = dosql($dbh,
		  "SELECT t2.*
			FROM $srctable as t1, $desttable as t2
			WHERE t1.$srcfield = ?
			  AND (ISNULL(t1.START_DATE) OR t1.START_DATE <= ?) AND (ISNULL(t1.END_DATE) OR t1.END_DATE >= ?)
			  AND t2.$desttablefield = t1.$destfield",
		  $id,$when,$when);

  if (!$sth)
  { ##warn "$::cmd: followDatedRecords($srctable,$srcfield=$id -> $desttable.$desttablefield) fails";
    return ();
  }

  my $h;
  my @rows = ();
  while (defined ($h=$sth->fetchrow_hashref()))
  { push(@rows,$h);
  }

  return @rows;
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

  ## warn "get datedRecords(..,table=$table,keyfield=$keyfield,key=$key,when=$when,all=$all)...";

  my $D = cs::DBI::arraytable($dbh,$table);

  my @d =
	  $all
	  ? grep($_->{$keyfield} eq $key, @$D)
	  : grep($_->{$keyfield} eq $key
	      && $_->{START_DATE} le $when
	      && ( ! defined $_->{END_DATE} || ! length $_->{END_DATE} || $when le $_->{END_DATE}),
		@$D)
  ;

  return sort cmpDatedRecords @d;
}

=item cmpDatedRecords()

Compare for order the two dated records
references by the global variables B<$a> and B<$b>,
for use in B<sort>s.

=cut

sub cmpDatedRecords
{
  my $cmp;

  my $sa = $a->{START_DATE};
  my $sb = $b->{START_DATE};

  $cmp = defined $sa
	 ? defined $sb
	   ? $sa cmp $sb	# both set - compare
	   : 1			# B->START is undef -> earlier
	 : defined $sb
	   ? -1			# A->START is undef -> earlier
	   : 0
	 ;			# both undef

  return $cmp if $cmp != 0;

  my $ea = $a->{END_DATE};
  my $eb = $b->{END_DATE};

  $cmp = defined $ea
	  ? defined $eb
	    ? $ea cmp $eb	# both set - compare
	    : -1		# B undef - A is earlier
	  : defined $eb
	    ? 1			# A undef - B is earlier
	    : 0			# neither set - the same
	  ;

  $cmp;
}

=item datedRecordsBetween(I<dbh>,I<table>,I<start>,I<end>,I<keyfield>,I<key>

Retrieve active records from the specified I<table>
with the specified I<key> value
whose dates overlap the period denoted by I<start> and I<end>.
The (I<keyfield>, I<key>) pair is optional;
if omitted, all records overlapping the period will be returned.

The records are returned ordered from oldest to most recent.

=cut

sub datedRecordsBetween($$$$;$$)
{ my($dbh,$table,$start,$end,$keyfield,$key)=@_;

  my $all = defined $keyfield;

  my $D = cs::DBI::arraytable($dbh,$table);

  my @d = grep(defined $keyfield
		? $_->{$keyfield} eq $key
		: 1,
	       grep( ! ( $_->{START_DATE} gt $end
		      || (defined $_->{END_DATE} && $_->{END_DATE} lt $start)
		       ),
		     @$D))
          ;

  return sort cmpDatedRecords @d;
}

sub addDatedRecord
{ my($dbh,$table,$start,$end,$rec,@delwhere)=@_;

  ####warn "addDatedRecord(start=$start,end=$end)...";
  if (! defined $start && ! defined $end)
  { my(@c)=caller;
    die "$::cmd: cs::DBI::addDatedRecord($table): neither \$start nor \$end defined\n\tfrom [@c]\n\t";
  }

  if (defined($start) && length($start)
   && defined($end) && length($end)
   && $start gt $end)
  { my(@c)=caller;
    die "$::cmd: cs::DBI::addDatedRecord($table): start ($start) > end ($end)\n\tfrom [@c]\n\t";
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

  $rec->{START_DATE}=$start if defined $start && length $start;
  $rec->{END_DATE}=$end if defined $end && length $end;
  insert($dbh,$table, keys %$rec)->ExecuteWithRec($rec);
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
  my $today = new cs::DMY $when;
  my $prevwhen = $today->Prev()->IsoDate();

  my ($sql, @args) = sqlWhereText("UPDATE $table SET END_DATE = ?", @delwhere);
  $sql .= " AND START_DATE <= ? AND ISNULL(END_DATE)";

  ##warn "delDatedRecord: SQL=\n$sql\n";

  my $sth = sql($dbh, $sql);
  if (! defined $sth)
  { warn "$::cmd: cs::DBI::delDatedRecord($table): can't make sql to delete old records";
    return undef;
  }

  $sth->execute($prevwhen,@args,$when);
}

=item cropDatedRecords(I<dbh>,I<table>,I<start>,I<end>,I<where>,I<where-args...>)

Crop dated records which overlap the period I<start>-I<end>.
At least one of I<start> and I<end> must be defined.
If supplied, the optional parameters
I<where> and I<where-args> specify
a further constraint on the records eligible for cropping.
Returns success.

=cut

sub cropDatedRecords
{ my($dbh,$table,$start,$end,$xwhere,@xwargs)=@_;
  undef $start if defined $start && ! length $start;
  undef $end   if defined $end   && ! length $end;

  my $context="cs::DBI::cropDatedRecords(dbh=$dbh,table=$table,start=".(defined $start ? $start : 'UNDEF').",end=".(defined $end ? $end : 'UNDEF').",xwhere=$xwhere,xwargs=[@xwargs]";

  ##warn "$context\n\t";

  if (!defined($start) && !defined($end))
  { my@c=caller;
    die "$::cmd: $context:\n\tneither \$start nor \$end is defined!\n\tfrom [@c]\n\t";
  }

  if (defined($start) && defined($end) && $start gt $end)
  { my@c=caller;
    die "$::cmd: $context:\n\t\$start ($start) > \$end ($end)\n\tfrom [@c]n\\t";
  }

  my $sql;
  my $sth;
  my $xsql=(defined $xwhere ? " AND $xwhere" : "");

  my $ok = 1;

  if (defined $start)
  { my $prev_start = (new cs::DMY $start)->Prev()->IsoDate();

    if (defined $end)
    # both defined
    {
      my $next_end = (new cs::DMY $end)->Next()->IsoDate();

      ## warn "CROP BOTH: prev_start='$prev_start', next_end='$next_end'\n";

      # delete swallowed records
      #
      # delete where NOT(ISNULL(START_DATE)) AND START_DATE >= $start
      #       AND NOT(ISNULL(END_DATE)) AND END_DATE <= $end
      #       AND ...
      $sql="DELETE FROM $table\n"
	    ." WHERE NOT(ISNULL(START_DATE)) AND START_DATE >= ?\n"
	    ."   AND NOT(ISNULL(END_DATE)) AND END_DATE <= ?\n"
	    .$xsql
	    ;

      ## warn "DELETE SWALLOWED:\n$sql\n[$start $end @xwargs]\n";
      if (!defined($sth=dosql($dbh,$sql,$start,$end,@xwargs)))
      { warn "$::cmd: $context:\n\tcan't dosql($sql)";
	$ok=0;
      }

      # crop lower records
      #
      # update set END_DATE = prev($start)
      #	where (ISNULL(START_DATE) OR START_DATE < $start)
      #	  AND NOT(ISNULL(END_DATE))
      #	  AND END_DATE >= $start
      #	  AND END_DATE <= $end
      #	  AND ...
      $sql="UPDATE $table SET END_DATE = ?\n"
	  ." WHERE (ISNULL(START_DATE) OR START_DATE < ?)\n"
	  ."   AND NOT(ISNULL(END_DATE))\n"
	  ."   AND END_DATE >= ?\n"
	  ."   AND END_DATE <= ?\n"
	  .$xsql
	  ;

      ## warn "CROP LOWER:\n$sql\n";
      if (!defined($sth=dosql($dbh,$sql,$prev_start,$start,$start,$end,@xwargs)))
      { warn "$::cmd: $context:\n\tcan't dosql($sql)";
	$ok=0;
      }

      # crop higher records
      #
      # update set START_DATE = next($end)
      # where (ISNULL(END_DATE) OR END_DATE < $end)
      # AND NOT(ISNULL(START_DATE))
      # AND START_DATE <= $end
      # AND START_DATE >= $start
      # AND ...
      $sql="UPDATE $table SET START_DATE = ?\n"
	  ." WHERE (ISNULL(END_DATE) OR END_DATE < ?)\n"
	  ."   AND NOT(ISNULL(END_DATE))\n"
	  ."   AND START_DATE <= ?\n"
	  ."   AND START_DATE >= ?\n"
	  .$xsql
	  ;

      ## warn "CROP HIGHER:\n$sql\n";
      if (!defined($sth=dosql($dbh,$sql,$next_end,$end,$end,$start,@xwargs)))
      { warn "$::cmd: $context:\n\tcan't dosql($sql)";
	$ok=0;
      }

      # split spanning records
      #
      # locate:
      # select WHERE (ISNULL(START_DATE) OR START_DATE < $start)
      # AND (ISNULL(END_DATE) OR END_DATE > $end)
      # AND ...
      #
      # crop:
      # update set END_DATE=prev($start)
      # WHERE (ISNULL(START_DATE) OR START_DATE < $start)
      # AND (ISNULL(END_DATE) OR END_DATE > $end)
      # AND ...
      #
      # insert new upper halves:
      # for each selected
      # { START_DATE=next($end)
      # }
      # insert selected
      $sql="SELECT * FROM $table\n"
	  ." WHERE (ISNULL(START_DATE) OR START_DATE < ?)\n"
	  ."   AND (ISNULL(END_DATE) OR END_DATE > ?)\n"
	  .$xsql
	  ;

      ## warn "SELECT SPANNING:\n$sql\n";
      if (!defined($sth=sql($dbh,$sql)))
      { warn "$::cmd: $context:\n\tcan't parse sql($sql)";
	$ok=0;
      }
      else
      { my @r = fetchall_hashref($sth,$start,$end,@xwargs);

	if (@r)
	{
	  $sql="UPDATE $table SET END_DATE = ?\n"
	      ." WHERE (ISNULL(START_DATE) OR START_DATE < ?)\n"
	      ."   AND (ISNULL(END_DATE) OR END_DATE > ?)\n"
	      .$xsql
	      ;

	  ## warn "UPDATE SPANNING:\n$sql\n";
	  if (!defined($sth=dosql($dbh,$sql,$prev_start,$start,$end,@xwargs)))
	  { warn "$::cmd: $context:\n\tcan't dosql($sql)";
	    $ok=0;
	  }
	  else
	  # update worked - add top halves
	  {
	    my $ins = insert($dbh,$table,grep($_ ne 'ID', keys %{$r[0]}));
	    if (! defined $ins)
	    { warn "$::cmd: $context:\n\tcan't construct insert object\n\t";
	      $ok=0;
	    }
	    else
	    {
	      for my $r (@r)
	      { $r->{START_DATE}=$next_end;
		delete $r->{ID};
	      }

	      $ins->ExecuteWithRec(@r);
	    }
	  }
	}
      }
    }
    else
    # only $start defined
    {

      ## warn "CROP low-: prev_start='$prev_start'\n";

      # delete swallowed records
      #
      # delete where NOT(ISNULL(START_DATE))
      # AND START_DATE >= $start
      # AND ...
      $sql="DELETE FROM $table\n"
	    ." WHERE NOT(ISNULL(START_DATE)) AND START_DATE >= ?\n"
	    .$xsql
	    ;

      ## warn "DELETED SWALLOWED:\n$sql\n\t";
      if (!defined($sth=dosql($dbh,$sql,$start,@xwargs)))
      { warn "$::cmd: $context:\n\tcan't dosql($sql)";
	$ok=0;
      }

      # crop lower records
      #
      # update set END_DATE = prev($start)
      # where (ISNULL(START_DATE) OR START_DATE < $start)
      # AND (ISNULL(END_DATE) OR END_DATE >= $start)
      # AND ...
      $sql="UPDATE $table SET END_DATE = ?\n"
	  ." WHERE (ISNULL(START_DATE) OR START_DATE < ?)\n"
	  ."   AND (ISNULL(END_DATE) OR END_DATE >= ?)\n"
	  .$xsql
	  ;

      ## warn "CROP LOWER:\n$sql\n\t";
      if (!defined($sth=dosql($dbh,$sql,$prev_start,$start,$start,@xwargs)))
      { warn "$::cmd: $context:\n\tcan't dosql($sql)";
	$ok=0;
      }
    }
  }
  else
  # only $end defined
  {
    my $next_end = (new cs::DMY $end)->Next()->IsoDate();

    # delete swallowed records
    #
    # delete where NOT(ISNULL(END_DATE))
    # AND END_DATE <= $end
    # AND ...
    $sql="DELETE FROM $table\n"
	." WHERE NOT(ISNULL(END_DATE)) AND END_DATE <= ?\n"
	.$xsql
	;

    if (!defined($sth=dosql($dbh,$sql,$end,@xwargs)))
    { warn "$::cmd: $context:\n\tcan't dosql($sql)";
      $ok=0;
    }

    # crop higher records
    #
    # update set START_DATE = next($end)
    # where (ISNULL(END_DATE) OR END_DATE > $end
    # AND NOT(ISNULL(START_DATE))
    # AND START_DATE <= $end
    # AND ...
    $sql="UPDATE $table SET START_DATE = ?\n"
	." WHERE (ISNULL(END_DATE) OR END_DATE > ?)\n"
	."   AND (ISNULL(START_DATE) OR START_DATE <= ?)\n"
	.$xsql
	;

    if (!defined($sth=dosql($dbh,$sql,$next_end,$end,$end,@xwargs)))
    { warn "$::cmd: $context:\n\tcan't dosql($sql)";
      $ok=0;
    }
  }

  return $ok;
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

  warn "cleanDates(..,table=$table,keyfield=$keyfield,keys=[@keys])...";
  if (! @keys)
  {
    my $sql = "SELECT $keyfield FROM $table";
    my $sth = sql($dbh,$sql);
    if (! defined $sth)
    { warn "$::cmd: cleanDates($dbh,$table,$keyfield): can't make SQL statement handle";
      return;
    }

    my $n = $sth->execute();
    if (! defined $n)
    { warn "$::cmd: execute($sql) fails";
      return;
    }

    my $a = $sth->fetchall_arrayref();
    @keys = ::uniq(map($_->[0], @$a));
  }

  KEY:
  for my $key (@keys)
  {
    my @D = reverse cs::DBI::datedRecords($dbh,$table,$keyfield,$key,undef,1);
    ## warn "D=".cs::Hier::h2a(\@D,1);

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
	my $sql = "DELETE FROM $table WHERE $keyfield = ? AND ";

	if (length $start)
	{ $sql.='START_DATE = ?';
	  push(@sqlargs,$start);
	}
	else
	{ $sql.='ISNULL(START_DATE)';
	}

	$sql.=" AND ";

	if (length $end)
	{ $sql.='END_DATE = ?';
	  push(@sqlargs,$end);
	}
	else
	{ $sql.='ISNULL(END_DATE)';
	}

	## warn "DOSQL:\n\t$sql\n\t@sqlargs\n";

	dosql($dbh,$sql,@sqlargs);
      }
      elsif (! length $end || $end ge $prev_start)
      { my $nend = cs::DMY->new($prev_start)->Prev()->IsoDate();
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

	  ## warn "DOSQL:\n\t$sql\n\t@sqlargs\n";

	  dosql($dbh,$sql,@sqlargs);
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

	  ## warn "DOSQL:\n\t$sql\n\t@sqlargs\n";

	  dosql($dbh,$sql,@sqlargs);
	}
      }

      $prev_start=$start;
    }
  }
}

=item cropDates(I<dbh>,I<table>,I<keyfield>,I<key>,I<start>,I<end>)

Crop the dates in the specified (I<dbh>,I<table>)
where the field I<keyfield> equals I<key>
and the date fields overlap the period I<start>-I<end>.
Records completely overlapped by the period are dropped.

BUG: records which completely overlap the period are untouched
because I haven't decided how to deal with holes.

=cut

sub cropDates($$$$$$)
{ my($dbh,$table,$keyfield,$key,$start,$end)=@_;

  my $sql;

  $sql = "DROP FROM $table WHERE $keyfield = ? AND START_DATE >= ? AND END_DATE <= ?";
  dosql($dbh,$sql,$key,$start,$end);

  my $prestart = (new cs::DMY $start)->Prev()->IsoDate();

  $sql = "UPDATE $table SET END_DATE = ? WHERE $keyfield = ? AND START_DATE <= ? AND (ISNULL(END_DATE) OR (END_DATE >= ? AND END_DATE <= ?))";
  dosql($dbh,$sql,$prestart,$key,$start,$start,$end);
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

  ::debug("cs::DBI::insert: SQL is [$sql]");
  bless [ $dbh, $table, $sql, sql($dbh,$sql), $dfltok, @fields ];
}

sub insertrow($$$)	# dbh,table,hashref
{ my($dbh,$table,$h)=@_;

  my @k = sort grep(length,keys %$h);
  dosql($dbh,"INSERT INTO $table(".join(',',@k).") VALUES (".join(",",map('?',@k)).")",
	map($h->{$_},@k));
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
  ##warn "ExecuteWithRec: sql =\n$sql\n\tfields = [@fields]";

  my $ok = 1;

  # hack - lock the table if we're inserting 5 or more records,
  # for speed
  my $locked = (@_ > 5 && lock_table($dbh,$table));

  INSERT:
  while (@_)
  { my $rec = shift(@_);
    ::debug("INSERT rec = ".cs::Hier::h2a($rec,1));
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
    # (probably the '' quote escape in SQL syntax - cameron 17mar2006)
    ## for (@execargs) { $_=' ' if defined && ! length; }


    {my@c=caller;::debug("sth=".cs::Hier::h2a($sth,1)."\nexecargs[@execargs]\nfrom [@c]");}

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
  ##::debug("statement=[$statement]");
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
