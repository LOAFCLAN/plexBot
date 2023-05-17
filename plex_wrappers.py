import asyncio
import datetime
import traceback
import typing
from copy import copy, deepcopy
from typing import Iterator

import discord
import plexapi
from discord.ext import commands
from plexapi.server import PlexServer

from loguru import logger as logging







# class PlexSessionSnapshot:
#
#     def __init__(self, session: plexapi.video.Video):
#         self.view_offset = deepcopy(session.viewOffset)
#         self.duration = deepcopy(session.duration)
