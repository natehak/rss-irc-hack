#  This Source Code Form is subject to the terms of the Mozilla Public
#  License, v. 2.0. If a copy of the MPL was not distributed with this
#  file, You can obtain one at http://mozilla.org/MPL/2.0/.

import codecs
import functools
import json
import re
import time
from typing import List

from irc3 import asyncio
import irc3
from irc3.plugins.command import command

import feedparser
import requests

irc_channel = '#rss' # This is for debug purposes.

async def fetch_feed(url: str) -> str:
    loop = asyncio.get_event_loop()
    headers = {'User-Agent': 'tiny lil irc rss bot'}
    fut = loop.run_in_executor(None, functools.partial(requests.get, headers=headers, timeout=5), url)
    resp = await fut
    return resp.text

@irc3.plugin
class Rss(object):
    """Lets channels subscribe to RSS feeds."""
    def __init__(self, bot):
        self.bot = bot
        self.bot.include('irc3.plugins.userlist')
        self.log = self.bot.log
        self.joined_channels = set()
        self.bot.create_task(self.startup())

    async def startup(self):
        while True:
            if getattr(self.bot, 'protocol', None) and irc_channel in self.bot.channels: break # Check if connected to IRC
            else: await asyncio.sleep(.001, loop=self.bot.loop)

        self.load_feeds() # feed_name -> url, channel, seen, refresh, last_check
        for feed in self.feeds:
            self.bot.create_task(self.poll_rss(feed))

    async def poll_rss(self, feed_name):
        feed = self.feeds[feed_name]
        seen = set(feed['seen'])
        self.log.info('scheduled {}...'.format(feed_name))
        while True:
            self.log.info('checking {}...'.format(feed_name))
            diff = time.time() - feed['last_check'] 
            if diff < feed['refresh']:
                self.log.info('{} waiting {} seconds...'.format(feed_name, int(diff)))
                await asyncio.sleep(int(diff), loop=self.bot.loop)
            feed['last_check'] = time.time()
            try:
                feed_text = await fetch_feed(feed['url'])
            except:
                self.log.info('{} timed out...'.format(feed_name))
                await asyncio.sleep(feed['refresh'] * 2, loop=self.bot.loop)
                continue
            # self.log.info('data: {}'.format(feed_text))
            p = feedparser.parse(feed_text)
            for entry in p.entries:
                # self.log.info('entry: {}'.format(entry.link))
                if entry.link not in seen:
                    feed['seen'].append(entry.link)
                    seen.add(entry.link)
                    await self.bot.privmsg(feed['channel'], '\x02{}:\x0F {}'.format(entry.title, entry.link))
            if len(feed['seen']) > 200:
                feed['seen'] = feed['seen'][len(feed['seen'])-200:]
            self.save_feeds()
            self.log.info('finished checking {}...'.format(feed_name))
            await asyncio.sleep(feed['refresh'], loop=self.bot.loop)

    @command(permission='view')
    def reload(self, mask, target, args):
        """Reload configs. This doesn't actually work right...

           %%reload
        """
        self.load_feeds()
        self.bot.privmsg(irc_channel, 'reload successful')

    @command(permission='view')
    def add_feed(self, mask, target, args):
        """Add feed to channel.

           %%add_feed <name> <url> <channel> <refresh>
        """
        channel = args['<channel>']
        self.feeds[args['<name>']] = {'url': args['<url>'], 'channel': channel, 'refresh': int(args['<refresh>']), 'seen': [], 'last_check': 0}
        self.bot.send_line('JOIN %s' % channel)
        self.save_feeds()
        self.bot.create_task(self.poll_rss(args['<name>']))

    def load_feeds(self):
        self.feeds = self.get_state('feeds', {})
        for feed in self.feeds:
            self.bot.send_line('JOIN %s' % self.feeds[feed]['channel'])

    def save_feeds(self):
        self.save_state('feeds', self.feeds)

    def get_state(self, key, default):
        """Obtain persisted state.

        If no state is present, returns the value specified in ``default``.
        """
        verify_state_key(key)

        path = '%s.json' % key
        try:
            with codecs.open(path, 'rb', 'utf-8') as fh:
                return json.load(fh, encoding='utf-8')
        except FileNotFoundError:
            return default

    def save_state(self, key, value):
        """Save persisted state."""
        verify_state_key(key)
        path = '%s.json' % key
        with codecs.open(path, 'wb', 'utf-8') as fh:
            json.dump(value, fh, sort_keys=True, indent=4)


def verify_state_key(key):
    """Raise if a key used for state doesn't match expected format.

    We require keys have well-defined names to prevent things like path
    traversal exploits.
    """
    if not re.match('^[a-zA-Z0-9_-]+$', key):
        raise ValueError('state key must be alphanumeric + -+')
