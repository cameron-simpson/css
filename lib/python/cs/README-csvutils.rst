CSV file related facilities.
============================

* csv_reader(fp, encoding='utf-8', errors='replace'): python 2/3 portable interface to CSV file reading. Reads CSV data from the text file `fp` using csv.reader.

* SharedCSVFile: subclass of cs.fileutils.SharedAppendLines, for sharing an append-only CSV file.
