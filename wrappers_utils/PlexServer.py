import asyncio
import time

import plexapi.server
import requests
import threading

from loguru import logger as logging

from wrappers_utils.EventDecorator import event_manager


class PlexServer(plexapi.server.PlexServer):

    def __init__(self, *args, **kwargs):
        self.associations = kwargs.pop("discord_associations", None)
        self.database = kwargs.pop("database", None)
        self.friendlyName = "name_not_loaded"
        self.host_guild = kwargs.pop("host_guild", None)
        self._background_thread = None
        try:
            super().__init__(*args, timeout=1, **kwargs)
            self._online = True
            event_manager.trigger_event("plex_connect", plex=self)
        except requests.exceptions.ConnectionError:
            self._server_offline()

    def _server_offline(self):
        logging.warning(f"Plex server {self.friendlyName} has gone offline")
        self._online = False
        event_manager.trigger_event("plex_disconnect", plex=self)
        if self._background_thread is None or not self._background_thread.is_alive():
            self._background_thread = threading.Thread(target=self._reconnection_thread, daemon=True)
            self._background_thread.start()

    def _reconnection_thread(self):
        """Check if the server is back online"""
        while not self._online:
            try:
                super().__init__(self._baseurl, self._token, timeout=1)
                self._online = True
                event_manager.trigger_event("plex_connect", plex=self)
                logging.info(f"Plex server {self.friendlyName} has come back online")
            except requests.exceptions.ConnectTimeout:
                pass
            except requests.exceptions.ConnectionError:
                pass
            except Exception as e:
                logging.exception(e)
                pass
            finally:
                time.sleep(5)

    @property
    def online(self):
        return self._online

    async def wait_until_ready(self):
        """This method should block asynchronously until the server is ready for API calls"""
        while not self._online:
            await asyncio.sleep(1)

    def fetchItem(self, ekey, cls=None, **kwargs):
        if not self._online:
            return None
        try:
            return super().fetchItem(ekey, cls, **kwargs)
        except requests.exceptions.ConnectTimeout:
            self._server_offline()
            return None
        except requests.exceptions.ConnectionError:
            self._server_offline()
            return None

    def fetchItems(self, ekey, cls=None, container_start=None, container_size=None, **kwargs):
        if not self._online:
            return []
        try:
            return super().fetchItems(ekey, cls, container_start, container_size, **kwargs)
        except requests.exceptions.ConnectTimeout:
            self._server_offline()
            return []
        except requests.exceptions.ConnectionError:
            self._server_offline()
            return []
