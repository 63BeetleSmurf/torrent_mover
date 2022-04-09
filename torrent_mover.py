#!/usr/bin/env python3
#
# torrent_mover.py
# Copyright (C) 2022  63BeetleSmurf
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Library General Public
# License as published by the Free Software Foundation; either
# version 2 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Library General Public License for more details.
#
# You should have received a copy of the GNU Library General Public
# License along with this library; if not, write to the Free
# Software Foundation, Inc., 59 Temple Place - Suite 330, Boston,
# MA 02111-1307, USA
#

import sys
import os
import re
import yaml
import logging
from enum import Enum
from shutil import copyfile
from transmission_rpc import Client

TRANS_HOST = 'localhost'
TRANS_PORT = 9091
TRANS_USER = ''
TRANS_PASS = ''

SCRIPT_PATH  = os.path.abspath(os.path.dirname(__file__))
DIR_DOWNLOAD = os.path.join(SCRIPT_PATH, 'downloads')
DIR_FILM     = os.path.join(SCRIPT_PATH, 'films')
DIR_SERIES   = os.path.join(SCRIPT_PATH, 'series')

SEASON_PREFIX = 's'
STRIP_CHARS   = ' ._-'

DEBUG = False
DRY_RUN = False

class TorrentStatus(Enum):
    CHECK_PENDING    = 'check pending'
    CHECKING         = 'checking'
    DOWNLOADING      = 'downloading'
    DOWNLOAD_PENDING = 'download pending'
    SEEDING          = 'seeding'
    SEED_PENDING     = 'seed pending'
    STOPPED          = 'stopped'

class TorrentType(Enum):
    UNKNOWN           = 0
    MOVIE             = 1
    MOVIE_WITH_SAMPLE = 2
    EPISODE           = 3
    SEASON            = 4
    SERIES            = 5

TORRENT_COMPLETE_STATUS = [
    TorrentStatus.SEEDING.value,
    TorrentStatus.STOPPED.value,
    ]

VIDEO_FILE_EXTENSIONS = [
    '.mp4',
    '.mkv',
    '.avi',
    ]

SERIES_PATTERNS = [
    '(.*?)S(\d{1,2})E(\d{2})(.*)',
    '(.*?)\[?(\d{1,2})x(\d{2})\]?(.*)',
    '(.*?)Season.?(\d{1,2}).*?Episode.?(\d{1,2})(.*)',
    ]

def load_config():
    global TRANS_HOST, TRANS_PORT, TRANS_USER, TRANS_PASS
    global DIR_DOWNLOAD, DIR_FILM, DIR_SERIES
    global SEASON_PREFIX, STRIP_CHARS
    config_path = os.path.join(SCRIPT_PATH, 'config.yml')
    if os.path.exists(config_path):
        logging.debug("Found config file")
        with open(config_path, 'r') as fh:
            config = yaml.safe_load(fh)

        if 'transmission' in config:
            logging.debug("Loading Transmission settings")
            if 'host' in config['transmission']:
                TRANS_HOST = config['transmission']['host']
                logging.debug(f"TRANS_HOST: {TRANS_HOST}")
            if 'port' in config['transmission']:
                TRANS_PORT = config['transmission']['port']
                logging.debug(f"TRANS_PORT: {TRANS_PORT}")
            if 'user' in config['transmission']:
                TRANS_USER = config['transmission']['user']
                logging.debug(f"TRANS_USER: {TRANS_USER}")
            if 'pass' in config['transmission']:
                TRANS_PASS = config['transmission']['pass']
                logging.debug(f"TRANS_PASS: {TRANS_PASS}")
        if 'directories' in config:
            logging.debug("Loading directory settings")
            if 'download' in config['directories']:
                DIR_DOWNLOAD = config['directories']['download']
                logging.debug(f"DIR_DOWNLOAD: {DIR_DOWNLOAD}")
            if 'film' in config['directories']:
                DIR_FILM = config['directories']['film']
                logging.debug(f"DIR_FILM: {DIR_FILM}")
            if 'series' in config['directories']:
                DIR_SERIES = config['directories']['series']
                logging.debug(f"DIR_SERIES: {DIR_SERIES}")
        if 'misc' in config:
            logging.debug("Loading misc settings")
            if 'season_prefix' in config['misc']:
                SEASON_PREFIX = config['misc']['season_prefix']
                logging.debug(f"SEASON_PREFIX: {SEASON_PREFIX}")
            if 'strip_chars' in config['misc']:
                STRIP_CHARS = config['misc']['strip_chars']
                logging.debug(f"STRIP_CHARS: {STRIP_CHARS}")

def is_dry_run():
    if DRY_RUN:
        logging.info("Skipping due to dry run")
    return DRY_RUN

def get_video_files(files):
    video_files = []
    for file_info in files:
        if os.path.splitext(file_info.name)[1] in VIDEO_FILE_EXTENSIONS:
            video_files.append(file_info)
    logging.debug(f"Found {len(video_files)} video files")
    return video_files

def get_episode_data(filename):
    for pattern in SERIES_PATTERNS:
        match = re.compile(pattern, re.IGNORECASE).search(os.path.basename(filename))
        if match:
            logging.debug(f"{filename} is episode")
            return {
                'series_name': match.group(1).strip(STRIP_CHARS),
                'season_num': f"{int(match.group(2)):02d}",
                'episode_num': f"{int(match.group(3)):02d}",
                'filename': filename,
                }
    return False

def get_series_data(video_files):
    series_name = None
    seasons = {}
    for video_file in video_files:
        episode_data = get_episode_data(video_file.name)
        if series_name is None:
            series_name = episode_data['series_name']
        elif episode_data['series_name'] != series_name:
            logging.info(f"Skipping - Multiple series names found: {series_name}, {episode_data['series_name']}")
            return False
        season_num_str = str(episode_data['season_num'])
        if not season_num_str in seasons:
            seasons[season_num_str] = []
        seasons[season_num_str].append(episode_data)
    if len(seasons):
        return {
            'series_name': series_name,
            'seasons': seasons,
            'seasons_count': len(seasons),
            }
    else:
        return False

def is_movie_with_sample(video_files):
    if len(video_files) == 2:
        size_diff = (video_files[1].size - video_files[0].size) / video_files[0].size * 100
        if size_diff < 0:
            size_diff *= -1
        elif size_diff > 100:
            size_diff /= 10

        logging.debug(f"File size diff: {size_diff}")
        if size_diff >= 90.0:
            return True
    return False

def get_torrent_type(video_files):
    if len(video_files) == 1:
        episode_data = get_episode_data(video_files[0].name)
        if episode_data:
            return TorrentType.EPISODE, episode_data
        else:
            return TorrentType.MOVIE, None
    else:
        if is_movie_with_sample(video_files):
            return TorrentType.MOVIE_WITH_SAMPLE, None
        else:
            series_data = get_series_data(video_files)
            if series_data:
                if series_data['seasons_count'] == 1:
                    return TorrentType.SEASON, series_data
                else:
                    return TorrentType.SERIES, series_data
    return TorrentType.UNKNOWN, None

def get_season_dir(series_name, season_num):
    season_dir = os.path.join(DIR_SERIES, series_name, f"{SEASON_PREFIX}{season_num}")
    logging.debug(f"season_dir: {season_dir}")
    if not os.path.exists(season_dir):
        logging.debug("Creating season directory")
        if not is_dry_run():
            os.makedirs(season_dir)
    return season_dir

def move_torrent_file(source_file, target_dir):
    try:
        target = os.path.join(target_dir, os.path.basename(source_file))
        if not os.path.exists(target):
            source = os.path.join(DIR_DOWNLOAD, source_file)
            logging.info(f"Moving {source} to {target}")
            if not is_dry_run():
                copyfile(source, target)
            return True
        logging.info(f"{target} already exists")
        return True
    except:
        logging.warning(f"Error moving {target}")
        return False

def main(args):
    load_config()
    print(TRANS_HOST)
    try:
        logging.info("Connecting to Transmission client")
        trans_client = Client(host=TRANS_HOST, port=TRANS_PORT, username=TRANS_USER, password=TRANS_PASS)
    except:
        logging.critical("Could not connect to Transmission client")
        return 0

    ids_to_remove = []
    for torrent in trans_client.get_torrents():
        moved = False
        if torrent.status in TORRENT_COMPLETE_STATUS:
            logging.info(f"Checking ({torrent.id}) {torrent.name}")

            video_files = get_video_files(torrent.files())
            if len(video_files):
                torrent_type, type_data = get_torrent_type(video_files)
                logging.info(f"Torrent type: {torrent_type}")

                if torrent_type == TorrentType.MOVIE:
                    moved = move_torrent_file(video_files[0].name, DIR_FILM)
                elif torrent_type == TorrentType.MOVIE_WITH_SAMPLE:
                    if video_files[0].size < video_files[1].size:
                        video_file = video_files[1]
                    else:
                        video_file = video_files[0]
                    moved = move_torrent_file(video_file.name, DIR_FILM)
                elif torrent_type == TorrentType.EPISODE:
                    season_dir = get_season_dir(type_data['series_name'], type_data['season_num'])
                    moved = move_torrent_file(type_data['filename'], season_dir)
                elif torrent_type == TorrentType.SEASON or torrent_type == TorrentType.SERIES:
                    for season in type_data['seasons']:
                        for episode in type_data['seasons'][season]:
                            season_dir = get_season_dir(episode['series_name'], episode['season_num'])
                            moved = move_torrent_file(episode['filename'], season_dir)
                            if moved == False:
                                break
                        else:
                            continue
                        break

                if moved and torrent.is_finished:
                    logging.info("Torrent is finished")
                    ids_to_remove.append(torrent.id)

    if len(ids_to_remove):
        logging.info("Removing finished torrents")
        if not is_dry_run():
            trans_client.remove_torrent(ids=ids_to_remove, delete_data=True)

    logging.info("Done")

    return 0

if __name__ == '__main__':
    if DEBUG:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)
    sys.exit(main(sys.argv))
