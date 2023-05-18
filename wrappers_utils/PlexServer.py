import plexapi.server
import requests

from loguru import logger as logging

class PlexServer(plexapi.server.PlexServer):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, timeout=1, **kwargs)
        self._online = True
        # self.associations = None
        # self.database = None

    def _server_offline(self):
        logging.warning(f"Plex server {self.friendlyName} has gone offline")
        self._online = False

    def fetchItem(self, ekey, cls=None, **kwargs):
        if not self._online:
            return None
        try:
            return super().fetchItem(ekey, cls, **kwargs)
        except requests.exceptions.ConnectTimeout:
            self._server_offline()
            return None
        except requests.exceptions.ConnectionError:
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
            return []
