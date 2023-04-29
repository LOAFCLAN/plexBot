class DynamicEntry:
    """
    A class that allows you to access an entry in a database as if it were an object.
    """

    def __init__(self, table, load_tuple=None, **kwargs):
        self.columns = table.columns
        self.table = table
        self.database = table.database
        self.primary_keys = table.primary_keys
        self._values = {}
        self._previous_values = {}
        self._dirty = False

        if load_tuple is not None:
            for i in range(len(load_tuple)):
                if i < len(self.columns):
                    self._values[self.columns[i].name] = load_tuple[i]

        for key in kwargs:
            if key in self.columns:
                self._values[key] = kwargs[key]
            else:
                raise KeyError(f"Column {key} does not exist in table {self.table.table_name}")

        # Add columns that were not specified and have a default value
        for column in self.columns:
            if column.name not in self._values and column.default_value is not None:
                self._values[column.name] = column.default_value

        self._previous_values = self._values.copy()

    def __getitem__(self, item):
        # This form of item setting does not access the database and is only in memory
        if isinstance(item, int):  # Select by index
            if len(self.columns) > item >= 0:
                return self._values[self.columns[item].name]
            else:
                raise IndexError(f"Column index {item} is out of range for table {self.table.table_name}")
        elif isinstance(item, str):  # Select by column name
            if item in self.columns:
                return self._values[item]
            else:
                raise KeyError(f"Column {item} does not exist in table {self.table.table_name}")
        else:
            raise TypeError(f"Invalid key type {type(item)}")

    def __setitem__(self, key, value):
        """
        Sets the value of the entry but does not flush the changes to the database
        :param key: The column to set
        :param value: The value to set
        :return:
        """
        if isinstance(key, int):  # Select by index
            if len(self.columns) > key >= 0:
                self._values[self.columns[key].name] = value
                self._dirty = True
            else:
                raise IndexError(f"Column index {key} is out of range for table {self.table.table_name}")
        elif isinstance(key, str):  # Select by column name
            if key in self.columns:
                self._values[key] = value
                self._dirty = True
            else:
                raise KeyError(f"Column {key} does not exist in table {self.table.table_name}")
        else:
            raise TypeError(f"Invalid key type {type(key)}")

    def set(self, **kwargs):
        """
        Sets the values of the entry and immediately flushes the changes to the database
        :param kwargs: The values to set
        :return:
        """
        for key in kwargs:
            if key in self.columns:
                self._dirty = True
                self._values[key] = kwargs[key]
            else:
                raise KeyError(f"Column {key} does not exist in table {self.table.table_name}")
        self.flush()

    def get(self, key):
        # This form of item setting accesses the database
        if key in self.columns:
            self.refresh()
            return self._values[key]
        else:
            raise KeyError(f"Column {key} does not exist in table {self.table.table_name}")

    def flush(self):
        """
        Flushes the changes to the database if there are any
        :return:
        """
        if self._dirty:
            # Build the query
            changed_values = {}
            for key in self._values:
                if key in self._previous_values and self._values[key] != self._previous_values[key]:
                    changed_values[key] = self._values[key]
                elif key not in self._previous_values:
                    changed_values[key] = self._values[key]
            if len(changed_values) == 0:
                return
            sql = f"UPDATE {self.table.table_name} SET "
            for key in changed_values:
                sql += f"{key} = ?, "
            sql = sql[:-2] if sql.endswith(", ") else sql  # Remove the trailing comma and space if there is one
            sql += f" WHERE {self._entry_where_clause()}"
            values = tuple(changed_values.values())
            # values += [self._values[column.name] for column in self.columns if column.primary_key]
            # print(sql, values)
            result = self.database.run(sql, values)
            if result.rowcount == 0:  # If the rowcount was 0 then the entry does not exist in the database
                raise KeyError(f"Entry does not exist in table {self.table.table_name}")
            self._dirty = False
            self._previous_values = self._values.copy()

    def flush_many(self) -> str:
        """
        Called by this entry's table to flush all entries in the table in one transaction
        :return:
        """
        if self._dirty:
            # Build the query
            changed_values = {}
            for key in self._values:
                if key in self._previous_values and self._values[key] != self._previous_values[key]:
                    changed_values[key] = self._values[key]
                elif key not in self._previous_values:
                    changed_values[key] = self._values[key]
            if len(changed_values) == 0:
                return ""
            sql = f"UPDATE {self.table.table_name} SET "
            for key, value in changed_values.items():
                column = self.columns[self.columns.index(key)]
                sql += f"{key} = {column.safe_value(value)}, "
            sql = sql[:-2] if sql.endswith(", ") else sql
            sql += f" WHERE {self._entry_where_clause()}"
            return sql

    def refresh(self):
        """
        Refreshes the values from the database
        :return:
        """
        sql = f"SELECT * FROM {self.table.table_name} WHERE {self._entry_where_clause()}"
        self._values = {self.columns[i].name: value for i, value in enumerate(self.database.run(sql).fetchone())}

    def delete(self):
        """
        Deletes the entry from the database
        :return:
        """
        primary_key_values = [self._values[column.name] for column in self.columns if column.primary_key]
        sql = f"DELETE FROM {self.table.table_name} WHERE {self._entry_where_clause()}"
        self.database.run(sql, tuple(primary_key_values))
        del self

    def _entry_where_clause(self):
        """
        Returns a complete WHERE clause to find this entry in the database even if the table has no primary keys
        Will contain no ?'s and will be ready to be inserted into a SQL statement
        :return:
        """
        primary_keys = [column.name for column in self.columns if column.primary_key]
        primary_key_values = [self._values[column.name] for column in self.columns if column.primary_key]
        sql = ""
        if self.primary_keys:
            sql += " AND ".join([f"{primary_keys[i]}= {self.columns[i].safe_value(primary_key_values[i])}"
                                 for i in range(len(primary_keys))])
        else:  # Use all previous values (filtering out None values)
            for column in self.columns:
                if self._previous_values[column.name] is not None:
                    sql += f"{column.name} = {column.safe_value(self._previous_values[column.name])} AND "
                else:
                    sql += f"{column.name} IS NULL AND "
            sql = sql[:-5]
        return sql

    def _column_wrappers_to_sql(self):
        """
        returns a list of column names
        :return:
        """
        return [column.name for column in self.columns]

    def __iter__(self):
        for column in self.columns:
            yield column

    def __len__(self):
        return len(self.columns)

    def __str__(self):
        # The primary keys should be prefixed with a * to indicate that they are primary keys
        string = f"{self.table.table_name}("
        for i, column in enumerate(self.columns):
            if column.primary_key:
                string += f"*{column.name}={self._values[column.name]}"
            else:
                string += f"{column.name}={self._values[column.name] if column.name in self._values else None}"
            if i != len(self.columns) - 1:
                string += ", "
        string += ")"
        return string

    def __repr__(self):
        return self.__str__()

    def __hash__(self):
        """Return a hash of the primary key."""
        return hash(tuple(self.primary_keys))

    def __eq__(self, other):
        if isinstance(other, DynamicEntry):
            return self.primary_keys == other.primary_keys
        elif isinstance(other, tuple):
            return self.primary_keys == other
        elif isinstance(other, dict):
            if self.primary_keys:
                return self.primary_keys == tuple(other[key] for key in self.primary_keys)
            else:  # compare all values
                return self._values == other
        else:
            raise TypeError(f"Cannot compare DynamicEntry to {type(other)}")
