import os
import sqlite3

from loguru import logger as logging

from ConcurrentDatabase.Database import CreateTableLink


def preform_migrations(database):
    def find_media(title, media_type, season_num, ep_num, media_year):
        result = database.cursor().execute("SELECT media_id FROM plex_watched_media WHERE title = ? "
                                           "AND media_type = ? AND season_num = ? AND ep_num = ? "
                                           "AND media_year = ?", (title, media_type, season_num, ep_num, media_year))
        result = result.fetchone()
        if result:
            return result[0]
        else:  # Reduce the search to just title and media type (for movies) and title, media type, season and ep
            # number (for shows)
            if media_type == "movie":
                result = database.cursor().execute("SELECT media_id FROM plex_watched_media WHERE title = ? "
                                                   "AND media_type = ?", (title, media_type))
                result = result.fetchone()
                if result:
                    return result[0]
                else:
                    raise Exception(f"Media not found: {title} ({media_type})")
            elif media_type == "episode":
                result = database.cursor().execute("SELECT media_id FROM plex_watched_media WHERE title = ? "
                                                   "AND media_type = ? AND season_num = ? AND ep_num = ?",
                                                   (title, media_type, season_num, ep_num))
                result = result.fetchone()
                if result:
                    return result[0]
                else:
                    raise Exception(f"Media not found: {title} - S{season_num}E{ep_num}")
            elif media_type == "clip":
                result = database.cursor().execute("SELECT media_id FROM plex_watched_media WHERE title = ? "
                                                   "AND media_type = ?", (title, media_type))
                result = result.fetchone()
                if result:
                    return result[0]
                else:
                    raise Exception(f"Media not found: {title} (clip)")
            else:
                raise Exception(f"Invalid media type: {media_type}")

    database.update_table("plex_history_messages", 1,
                          ["ALTER TABLE plex_history_messages ADD COLUMN watch_time FLOAT",
                           "UPDATE plex_history_messages SET watch_time = session_duration"])
    # Change the primary key to message_id instead of event_hash
    database.update_table("plex_history_messages", 2,
                          ["CREATE TABLE plex_history_messages_temp (event_hash INTEGER, guild_id INTEGER, "
                           "message_id INTEGER, history_time FLOAT, title TEXT NOT NULL, "
                           "media_type TEXT NOT NULL, season_num INTEGER, ep_num INTEGER, "
                           "account_ID INTEGER, pb_start_offset FLOAT, "
                           "pb_end_offset FLOAT, media_year TEXT,"
                           " session_duration FLOAT, "
                           "watch_time FLOAT, PRIMARY KEY (message_id))",
                           "INSERT INTO plex_history_messages_temp SELECT * FROM plex_history_messages",
                           "DROP TABLE plex_history_messages",
                           "ALTER TABLE plex_history_messages_temp RENAME TO plex_history_messages"])
    # Make a backup of the database before the next migration
    if not os.path.exists("migration_backup.db"):
        backup = sqlite3.connect("migration_backup.db")
        database.backup(target=backup)
        backup.close()

    database.create_table("plex_watched_media", {"media_id": "INTEGER PRIMARY KEY AUTOINCREMENT",
                                                 "guild_id": "INTEGER NOT NULL", "title": "TEXT NOT NULL",
                                                 "media_type": "TEXT NOT NULL", "media_length": "INTEGER",
                                                 "show_id": "INTEGER", "season_num": "INTEGER", "ep_num": "INTEGER",
                                                 "media_year": "TEXT", "library_id": "TEXT NOT NULL",
                                                 "media_guid": "TEXT NOT NULL"})

    database.create_table("plex_history_events", {"event_id": "INTEGER PRIMARY KEY", "guild_id": "INTEGER NOT NULL",
                                                  "account_id": "INTEGER",
                                                  "media_id": "INTEGER NOT NULL", "history_time": "FLOAT",
                                                  "pb_start_offset": "INTEGER", "pb_end_offset": "INTEGER",
                                                  "session_duration": "INTEGER", "watch_time": "INTEGER"},
                          linked_tables=[CreateTableLink(target_table="plex_watched_media", target_key="media_id",
                                                         source_table="plex_history_events", source_key="media_id")])

    # Migrate some data from plex_history_messages to plex_history_events and plex_watched_media
    # The data only data remaining in plex_history_messages will be the message_id and guild_id

    # When copying media info into plex_watched_media, we need to prevent duplicates from being created
    # However we don't have any unique identifiers for media, so we will use all of the columns except for media_id
    # and library_name to determine if a media is a duplicate

    # Create a temporary function to find the media_id of a media item using "title", "media_type", "season_num",
    # "ep_num", "media_year"
    sqlite3.enable_callback_tracebacks(True)
    database.create_function("find_media", 5, find_media)

    table_version = database.table_version_table.get_row(table_name="plex_history_messages")
    if table_version["version"] == 2:
        database.batch_transaction([
            "INSERT INTO plex_watched_media "
            "(guild_id, title, media_type, season_num, ep_num, media_year, media_guid, library_id, media_length)"
            "SELECT DISTINCT guild_id, title, media_type, season_num, ep_num, media_year, 'N/A', 'N/A', NULL"
            " FROM plex_history_messages;",
            "-- Insert the values into plex_history_events next, we will need to use the find_media function to get the"
            "-- media_id for each row as that isn't stored in plex_history_messages",
            "INSERT INTO plex_history_events (event_id, guild_id, account_id, history_time, media_id,"
            " pb_start_offset, pb_end_offset, session_duration, watch_time)"
            "SELECT event_hash, guild_id, account_id, history_time,"
            " find_media(title, media_type, season_num, ep_num, media_year),"
            " pb_start_offset, pb_end_offset, session_duration, watch_time FROM plex_history_messages;",
            "-- Now update plex_history_messages to be of a reduced size",
            "CREATE TABLE plex_history_messages_temp (event_id INTEGER, guild_id INTEGER, message_id INTEGER,"
            "PRIMARY KEY (message_id), FOREIGN KEY (event_id) REFERENCES plex_history_events(event_id));"
            "INSERT INTO plex_history_messages_temp "
            "SELECT event_hash, guild_id, message_id FROM plex_history_messages;",
            "DROP TABLE plex_history_messages;",
            "ALTER TABLE plex_history_messages_temp RENAME TO plex_history_messages;"])
        table_version.set(version=3)
        # Reload the table schema
        database.get_table("plex_history_messages").update_schema()

    table_version = database.table_version_table.get_row(table_name="plex_history_events")
    if table_version["version"] == 0:
        # Add a device_id column to plex_history_events defaulting to null
        # Link the device_id column to the plex_devices table using the device_name column
        database.batch_transaction([
            "ALTER TABLE plex_history_events ADD COLUMN device_id TEXT DEFAULT NULL;"

        ])
        table_version.set(version=1)


    # table_version = database.table_version_table.get_row(table_name="plex_watched_media")
    # if table_version["version"] == 0:  # Set the guild_id from plex_watched_media to be a foreign key to plex_servers
    #     database.batch_transaction([
    #         "CREATE TABLE plex_watched_media_temp (media_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    #         "guild_id INTEGER NOT NULL, title TEXT NOT NULL, media_type TEXT NOT NULL, media_length INTEGER,"
    #         "show_id INTEGER, season_num INTEGER, ep_num INTEGER, media_year TEXT, library_id TEXT NOT NULL,"
    #         "media_guid TEXT NOT NULL, "
    #         "CONSTRAINT fk_plex_server_link FOREIGN KEY (guild_id) REFERENCES plex_servers(guild_id));",
    #         "INSERT INTO plex_watched_media_temp SELECT * FROM plex_watched_media;",
    #         "DROP TABLE plex_watched_media;",
    #         "ALTER TABLE plex_watched_media_temp RENAME TO plex_watched_media;"])
    #     table_version.set(version=1)

    # Setup foreign key links for discord_associations plex_devices to plex_servers
    # table_version = database.table_version_table.get_row(table_name="discord_associations")
    # if table_version["version"] == 0:
    #     database.batch_transaction([
    #         "CREATE TABLE discord_associations_temp (guild_id INTEGER, discord_user_id INTEGER, plex_id INTEGER, "
    #         "plex_email TEXT, plex_username TEXT, PRIMARY KEY (guild_id, discord_user_id), "
    #         "CONSTRAINT fk_plex_associations FOREIGN KEY (guild_id) REFERENCES plex_servers(guild_id))",
    #         "INSERT INTO discord_associations_temp SELECT * FROM discord_associations",
    #         "DROP TABLE discord_associations",
    #         "ALTER TABLE discord_associations_temp RENAME TO discord_associations"])
    #     table_version.set(version=1)
    #
    # table_version = database.table_version_table.get_row(table_name="plex_devices")
    # if table_version["version"] == 0:
    #     database.batch_transaction([
    #         "CREATE TABLE plex_devices_temp (account_id INTEGER, device_id TEXT, last_seen INTEGER, "
    #         "PRIMARY KEY (account_id, device_id), "
    #         "CONSTRAINT fk_plex_devices FOREIGN KEY (account_id) REFERENCES discord_associations(plex_id))",
    #         "INSERT INTO plex_devices_temp SELECT * FROM plex_devices",
    #         "DROP TABLE plex_devices",
    #         "ALTER TABLE plex_devices_temp RENAME TO plex_devices"])
    #     table_version.set(version=1)
    #
    # table_version = database.table_version_table.get_row(table_name="plex_history_channel")
    # if table_version["version"] == 0:
    #     database.batch_transaction([
    #         "CREATE TABLE plex_history_channel_temp (guild_id INTEGER, channel_id INTEGER, "
    #         "PRIMARY KEY (guild_id), "
    #         "CONSTRAINT fk_plex_history_channel FOREIGN KEY (guild_id) REFERENCES plex_servers(guild_id));",
    #         "INSERT INTO plex_history_channel_temp SELECT * FROM plex_history_channel",
    #         "DROP TABLE plex_history_channel",
    #         "ALTER TABLE plex_history_channel_temp RENAME TO plex_history_channel"])
    #     table_version.set(version=1)
    #
    # table_version = database.table_version_table.get_row(table_name="plex_history_events")
    # if table_version["version"] == 0:
    #     database.batch_transaction([
    #         "CREATE TABLE plex_history_events_temp (event_id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, "
    #         "account_id INTEGER, history_time INTEGER, media_id INTEGER, pb_start_offset INTEGER, "
    #         "pb_end_offset INTEGER, session_duration INTEGER, watch_time INTEGER, device_id TEXT, "
    #         "CONSTRAINT fk_plex_history_events FOREIGN KEY (guild_id) REFERENCES plex_servers(guild_id), "
    #         "CONSTRAINT fk_plex_history_events_media FOREIGN KEY (media_id) REFERENCES plex_watched_media(media_id), "
    #         "CONSTRAINT fk_plex_history_events_account FOREIGN KEY (account_id) "
    #         "REFERENCES discord_associations(plex_id), "
    #         "CONSTRAINT fk_plex_history_events_device FOREIGN KEY (device_id) REFERENCES plex_devices(device_id));",
    #         "INSERT INTO plex_history_events_temp SELECT *, null FROM plex_history_events",
    #         "DROP TABLE plex_history_events",
    #         "ALTER TABLE plex_history_events_temp RENAME TO plex_history_events"])
    #     database.get_table("plex_history_events").update_schema()
    #     table_version.set(version=1)
    #
    # table_version = database.table_version_table.get_row(table_name="plex_afs_ratings")
    # if table_version["version"] == 0:
    #     database.batch_transaction([
    #         "CREATE TABLE plex_afs_ratings_temp (media_id INTEGER, rating TEXT, account_id INTEGER, "
    #         "PRIMARY KEY (account_id, media_id), "
    #         "CONSTRAINT fk_plex_afs_ratings FOREIGN KEY (media_id) REFERENCES plex_watched_media(media_id), "
    #         "CONSTRAINT fk_plex_afs_ratings_account FOREIGN KEY (account_id)"
    #         " REFERENCES discord_associations(plex_id));",
    #         "INSERT INTO plex_afs_ratings_temp SELECT * FROM plex_afs_ratings",
    #         "DROP TABLE plex_afs_ratings",
    #         "ALTER TABLE plex_afs_ratings_temp RENAME TO plex_afs_ratings"])
    #     database.get_table("plex_afs_ratings").update_schema()
    #     table_version.set(version=1)
