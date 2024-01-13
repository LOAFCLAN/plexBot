import datetime
import typing

import discord
import plexapi
import asyncio

from loguru import logger as logging


class SessionWatcher:

    def __init__(self, session: plexapi.video.Video, server, callback) -> None:

        try:
            logging.info(f"Creating SessionWatcher for {session.title} ({session.year}) [{session.guid}] "
                         f"on {server.friendlyName} "
                         f"for {session.usernames[0]} ({session.player.machineIdentifier})")

            self.callback = callback
            self.server = server

            if not session.isFullObject:
                session.reload(checkFiles=False)
                if not session.isFullObject:
                    raise Exception("Session is still partial")

            media = session.media[0]

            # self.initial_media = copy(media)
            self.media = media
            self.initial_session = session
            self.session = session
            self.guid = session.guid

            # Get the hardware ID of the device that is playing the video
            self.device_id = session.player.machineIdentifier
            self.account_id = session.player.userID
            if self.account_id == 1:  # Btw nick, I still hate you for this
                self.account_id = self.server.myPlexAccount().id
            table = self.server.database.get_table("plex_devices")
            table.update_or_add(device_id=self.device_id, account_id=self.account_id,
                                last_seen=datetime.datetime.now().timestamp())

            self.end_offset = self.session.viewOffset

            self.start_time = datetime.datetime.now()
            self.alive_time = datetime.datetime.utcnow()
            self.watch_time = 0
            self.last_update = datetime.datetime.utcnow()

        except Exception as e:
            logging.error(f"Error creating SessionWatcher for {session.title}"
                          f" ({session.year}) on {server.friendlyName}")
            logging.exception(e)
            raise e

    async def refresh_session(self, session: plexapi.video.Video) -> None:
        self.session = session
        self.media = session.media[0]

        if not self.session.isFullObject:
            self.session.reload(checkFiles=False)
            if not self.session.isFullObject:
                raise Exception("Session is still partial")

        self.end_offset = self.session.viewOffset
        # Check if the media is playing
        if self.session.players[0].state == "playing":
            # Add the time since the last update to the watch time
            self.watch_time += (datetime.datetime.utcnow() - self.last_update).total_seconds()
        self.last_update = datetime.datetime.utcnow()

    async def session_expired(self):
        await self.callback(self)

    def _session_compare(self, other, attribute: str) -> bool:
        if hasattr(self.session, attribute) and hasattr(other, attribute):
            return getattr(self.session, attribute) == getattr(other, attribute)

    def _user_compare(self, other) -> bool:
        if hasattr(self.session, "usernames") and hasattr(other, "usernames"):
            return self.session.usernames[0] == other.usernames[0]

    def __str__(self):
        return f"{self.session.title}@{self.server.friendlyName}"

    def __eq__(self, other):
        if isinstance(other, SessionWatcher):  # This comparison happens when a session is being closed
            return self.guid == other.guid and self._user_compare(other) and self.device_id == other.device_id
        else:
            try:  # This comparison happens when a session is being created
                if getattr(other.player, "machineIdentifier", None) is None:
                    logging.warning(f"machineIdentifier returned None for {other.title} {self.account_id}")
                    return False
                return self._session_compare(other, "title") and self._user_compare(other) and \
                    self._session_compare(other, "guid") and \
                    self.device_id == getattr(other.player, "machineIdentifier", None)
            except Exception as e:
                logging.error(f"Error comparing {self} to {other}")
                logging.exception(e)
                return False

    def __iter__(self):
        yield self


class SessionChangeWatcher:
    """Binds to a plexapi.Server and fires events when sessions start or stop"""

    max_sessions = 15
    max_per_user = 2

    def __init__(self, server_object: plexapi.server, callback: typing.Callable, channel: discord.TextChannel) -> None:
        self.server = server_object
        self.watchers = []
        self.callbacktoback = callback
        self.failback = None
        self.channel = channel
        self.task = asyncio.get_event_loop().create_task(self.observer())
        logging.info(f"Created SessionChangeWatcher for {self.server.friendlyName}")

    async def observer(self):
        while self.server is not None:
            if len(self.watchers) > self.max_sessions:
                logging.error(f"SessionChangeWatcher for {self.server.friendlyName} exceeded max sessions,"
                              f"terminating watcher")
                return
            try:
                sessions = self.server.sessions()
                for session in sessions:
                    try:
                        already_exists = False
                        for watcher in self.watchers:
                            if watcher == session:
                                await watcher.refresh_session(session)
                                already_exists = True
                                break
                        if not already_exists:
                            watcher = SessionWatcher(session, self.server, self.callback)
                            self.watchers.append(watcher)
                    except Exception as e:
                        logging.error(f"Error refreshing session {session.title}: {e}")
                        logging.exception(e)

                for watcher in self.watchers:
                    try:
                        session_still_exists = False
                        for session in sessions:
                            if watcher == session:
                                session_still_exists = True
                                break
                        if not session_still_exists:
                            await watcher.session_expired()
                    except Exception as e:
                        logging.error(f"Error checking for continued existence of session {watcher.session.title}: {e}")
                        logging.exception(e)

            except Exception as e:
                logging.error(f"Error checking sessions: {e}")
                logging.exception(e)
            finally:
                await asyncio.sleep(1.5)

    async def bot_shutdown(self):
        logging.info(f"Shutting down SessionChangeWatcher for {self.server.friendlyName}")
        for watcher in self.watchers:
            logging.info(f"Dumping session {watcher.session.title} for {watcher.session.usernames[0]}")
            await watcher.session_expired()
        self.task.cancel()

    async def callback(self, watcher: SessionWatcher):
        try:
            await self.callbacktoback(watcher, self.channel)
        except Exception as e:
            logging.error(f"Error in callback: {e}")
            logging.exception(e)
        finally:
            self.watchers.remove(watcher)
