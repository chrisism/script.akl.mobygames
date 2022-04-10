# -*- coding: utf-8 -*-
#
# Advanced Kodi Launcher scraping engine for MobyGames.
#
# --- Information about scraping ---
# https://github.com/muldjord/skyscraper
# https://github.com/muldjord/skyscraper/blob/master/docs/SCRAPINGMODULES.md

# Copyright (c) 2016-2019 Wintermute0110 <wintermute0110@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.

# --- Python standard library ---
from __future__ import unicode_literals
from __future__ import division

import typing
import logging
import json
import re

from datetime import datetime, timedelta
from urllib.parse import quote_plus

# --- AKL packages ---
from akl import constants, platforms, settings
from akl.utils import io, net, kodi, text
from akl.scrapers import Scraper
from akl.api import ROMObj

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------------------------
# MobyGames online scraper.
#
# | Site     | https://www.mobygames.com          |
# | API info | https://www.mobygames.com/info/api |
# ------------------------------------------------------------------------------------------------
class MobyGames(Scraper):
    # --- Class variables ------------------------------------------------------------------------
    supported_metadata_list = [
        constants.META_TITLE_ID,
        constants.META_YEAR_ID,
        constants.META_GENRE_ID,
        constants.META_PLOT_ID,
        constants.META_DEVELOPER_ID,
        constants.META_ESRB_ID,
        constants.META_RATING_ID,
        constants.META_NPLAYERS_ID,
        constants.META_NPLAYERS_ONLINE_ID,
        constants.META_TAGS_ID
    ]
    supported_asset_list = [
        constants.ASSET_TITLE_ID,
        constants.ASSET_SNAP_ID,
        constants.ASSET_BOXFRONT_ID,
        constants.ASSET_BOXBACK_ID,
        constants.ASSET_CARTRIDGE_ID,
    ]
    asset_name_mapping = {
        'front cover'   : constants.ASSET_BOXFRONT_ID,
        'back cover'    : constants.ASSET_BOXBACK_ID,
        'media'         : constants.ASSET_CARTRIDGE_ID,
        'manual'        : None,
        'spine/sides'   : None,
        'other'         : None,
        'advertisement' : None,
        'extras'        : None,
        'inside cover'  : None,
        'full cover'    : None,
        'soundtrack'    : None,
        'map'           : constants.ASSET_MAP_ID
    }
    
    # This allows to change the API version easily.
    URL_games           = 'https://api.mobygames.com/v1/games'
    URL_platforms       = 'https://api.mobygames.com/v1/platforms'
    URL_game_platform   = 'https://api.mobygames.com/v1/games/{}/platforms/{}'
    
    # --- Constructor ----------------------------------------------------------------------------
    def __init__(self):
        # --- This scraper settings ---
        self.api_key = settings.getSetting('scraper_mobygames_apikey')
        # --- Misc stuff ---
        self.cache_candidates = {}
        self.cache_metadata = {}
        self.cache_assets = {}
        self.all_asset_cache = {}

        cache_dir = settings.getSettingAsFilePath('scraper_cache_dir')
        super(MobyGames, self).__init__(cache_dir)

    # --- Base class abstract methods ------------------------------------------------------------
    def get_name(self): return 'MobyGames'

    def get_filename(self): return 'MobyGames'

    def supports_disk_cache(self): return True

    def supports_search_string(self): return True

    def supports_metadata_ID(self, metadata_ID):
        return True if metadata_ID in MobyGames.supported_metadata_list else False

    def supports_metadata(self): return True

    def supports_asset_ID(self, asset_ID):
        return True if asset_ID in MobyGames.supported_asset_list else False

    def supports_assets(self): return True

    # If the MobyGames API key is not configured in the settings then disable the scraper
    # and print an error.
    def check_before_scraping(self, status_dic):
        if self.api_key:
            logger.debug('MobyGames.check_before_scraping() MobiGames API key looks OK.')
            return
        logger.error('MobyGames.check_before_scraping() MobiGames API key not configured.')
        logger.error('MobyGames.check_before_scraping() Disabling MobyGames scraper.')
        self.scraper_disabled = True
        status_dic['status'] = False
        status_dic['dialog'] = kodi.KODI_MESSAGE_DIALOG
        status_dic['msg'] = (
            'AKL requires your MobyGames API key. '
            'Visit https://www.mobygames.com/info/api for directions about how to get your key '
            'and introduce the API key in AKL addon settings.'
        )

    def get_candidates(self, search_term:str, rom:ROMObj, platform, status_dic) -> typing.List[dict]:
        # --- If scraper is disabled return immediately and silently ---
        if self.scraper_disabled:
            # If the scraper is disabled return None and do not mark error in status_dic.
            logger.debug('MobyGames.get_candidates() Scraper disabled. Returning empty data.')
            return None

        # Prepare data for scraping.
        # --- Request is not cached. Get candidates and introduce in the cache ---
        scraper_platform = convert_AKL_platform_to_MobyGames(platform)
        logger.debug('MobyGames.get_candidates() search_term        "{}"'.format(search_term))
        logger.debug('MobyGames.get_candidates() rom identifier     "{}"'.format(rom.get_identifier()))
        logger.debug('MobyGames.get_candidates() AKL platform       "{}"'.format(platform))
        logger.debug('MobyGames.get_candidates() MobyGames platform "{}"'.format(scraper_platform))
        candidate_list = self._search_candidates(
            search_term, platform, scraper_platform, status_dic)
        if not status_dic['status']: return None

        return candidate_list

    # This function may be called many times in the ROM Scanner. All calls to this function
    # must be cached. See comments for this function in the Scraper abstract class.
    def get_metadata(self, status_dic):
        # --- If scraper is disabled return immediately and silently ---
        if self.scraper_disabled:
            logger.debug('MobyGames.get_metadata() Scraper disabled. Returning empty data.')
            return self._new_gamedata_dic()

        # --- Check if search term is in the cache ---
        if self._check_disk_cache(Scraper.CACHE_METADATA, self.cache_key):
            logger.debug('MobyGames.get_metadata() Metadata cache hit "{}"'.format(self.cache_key))
            return self._retrieve_from_disk_cache(Scraper.CACHE_METADATA, self.cache_key)

        # --- Request is not cached. Get candidates and introduce in the cache ---
        logger.debug('MobyGames.get_metadata() Metadata cache miss "{}"'.format(self.cache_key))
        url_tail = '/{}?api_key={}'.format(self.candidate['id'], self.api_key)
        url = MobyGames.URL_games + url_tail
        json_data = self._retrieve_URL_as_JSON(url, status_dic)
        if not status_dic['status']: return None
        self._dump_json_debug('MobyGames_get_metadata.json', json_data)

        # --- Get extra platform specific data ---
        url_tail = f'?api_key={self.api_key}'
        url = MobyGames.URL_game_platform.format(
            self.candidate['id'], self.candidate['scraper_platform']) + url_tail

        extra_json_data = self._retrieve_URL_as_JSON(url, status_dic)
        if not status_dic['status']: return None
        self._dump_json_debug('MobyGames_get_metadata_by_platform.json', extra_json_data)

        # --- Parse game page data ---
        gamedata = self._new_gamedata_dic()
        gamedata['title']           = self._parse_metadata_title(json_data)
        gamedata['year']            = self._parse_metadata_year(json_data, self.candidate['scraper_platform'])
        gamedata['genre']           = self._parse_metadata_genre(json_data)
        gamedata['plot']            = self._parse_metadata_plot(json_data)
        gamedata['rating']          = self._parse_metadata_rating(json_data)
        gamedata['developer']       = self._parse_metadata_developer(extra_json_data)
        gamedata['esrb']            = self._parse_metadata_esrb(extra_json_data)
        gamedata['nplayers']        = self._parse_metadata_nplayers(extra_json_data)
        gamedata['nplayers_online'] = self._parse_metadata_nplayers_online(extra_json_data)
        gamedata['tags']            = self._parse_metadata_tags(extra_json_data)

        # --- Put metadata in the cache ---
        logger.debug('MobyGames.get_metadata() Adding to metadata cache "{0}"'.format(self.cache_key))
        self._update_disk_cache(Scraper.CACHE_METADATA, self.cache_key, gamedata)

        return gamedata

    # This function may be called many times in the ROM Scanner. All calls to this function
    # must be cached. See comments for this function in the Scraper abstract class.
    #
    # In the MobyGames scraper is convenient to grab all the available assets for a candidate,
    # cache the assets, and then select the assets of a specific type from the cached list.
    def get_assets(self, asset_info_id:str, status_dic):
        # --- If scraper is disabled return immediately and silently ---
        if self.scraper_disabled:
            logger.debug('MobyGames.get_assets() Scraper disabled. Returning empty data.')
            return []

        logger.debug('MobyGames.get_assets() Getting assets (ID {}) for candidate ID "{}"'.format(
            asset_info_id, self.candidate['id']))

        # --- Request is not cached. Get candidates and introduce in the cache ---
        # Get all assets for candidate. _retrieve_all_assets() caches all assets for a candidate.
        # Then select asset of a particular type.
        all_asset_list = self._retrieve_all_assets(self.candidate, status_dic)
        if not status_dic['status']: return None
        asset_list = [asset_dic for asset_dic in all_asset_list if asset_dic['asset_ID'] == asset_info_id]
        logger.debug('MobyGames::get_assets() Total assets {0} / Returned assets {1}'.format(
            len(all_asset_list), len(asset_list)))

        return asset_list

    # Mobygames returns both the asset thumbnail URL and the full resolution URL so in
    # this scraper this method is trivial.
    def resolve_asset_URL(self, selected_asset, status_dic):
        # Transform http to https
        url = selected_asset['url']
        if url[0:4] == 'http': url = 'https' + url[4:]
        url_log = self._clean_URL_for_log(url)

        return url, url_log

    def resolve_asset_URL_extension(self, selected_asset, image_url, status_dic):
        return io.get_URL_extension(image_url)

    def download_image(self, image_url, image_local_path: io.FileName):
        self._wait_for_API_request()
        # net_download_img() never prints URLs or paths.
        net.download_img(image_url, image_local_path)
        
        # failed? retry after 5 seconds
        if not image_local_path.exists():
            logger.debug('Download failed. Retry after 5 seconds')
            self._wait_for_API_request(5000)
            net.download_img(image_url, image_local_path)
        return image_local_path
        
    # --- This class own methods -----------------------------------------------------------------
    def debug_get_platforms(self, status_dic):
        logger.debug('MobyGames.debug_get_platforms() BEGIN...')
        url_tail = '?api_key={}'.format(self.api_key)
        url = MobyGames.URL_platforms + url_tail
        json_data = self._retrieve_URL_as_JSON(url, status_dic)
        if not status_dic['status']: return None
        self._dump_json_debug('MobyGames_get_platforms.json', json_data)

        return json_data

    # --- Retrieve list of games ---
    def _search_candidates(self, search_term, platform, scraper_platform, status_dic):
        # --- Retrieve JSON data with list of games ---
        search_string_encoded = quote_plus(search_term)
        if scraper_platform == '0':
            # Unknown or wrong platform case.
            url_tail = '?api_key={}&format=brief&title={}'.format(
                self.api_key, search_string_encoded)
        else:
            url_tail = '?api_key={}&format=brief&title={}&platform={}'.format(
                self.api_key, search_string_encoded, scraper_platform)
        url = MobyGames.URL_games + url_tail
        json_data = self._retrieve_URL_as_JSON(url, status_dic)
        if not status_dic['status']: return None
        self._dump_json_debug('MobyGames_get_candidates.json', json_data)

        # --- Parse game list ---
        games_json = json_data['games']
        candidate_list = []
        for item in games_json:
            title = item['title']
            scraped_akl_platform = convert_MobyGames_platform_to_AKL_platform(scraper_platform) #item['platform'])

            candidate = self._new_candidate_dic()
            candidate['id'] = item['game_id']
            candidate['display_name'] = '{} ({})'.format(title, scraped_akl_platform.long_name)
            candidate['platform'] = platform
            candidate['scraper_platform'] = scraper_platform
            candidate['order'] = 1

            # Increase search score based on our own search.
            if title.lower() == search_term.lower():          candidate['order'] += 2
            if title.lower().find(search_term.lower()) != -1: candidate['order'] += 1
            candidate_list.append(candidate)

        # --- Sort game list based on the score. High scored candidates go first ---
        candidate_list.sort(key = lambda result: result['order'], reverse = True)

        return candidate_list

    def _parse_metadata_title(self, json_data):
        title_str = json_data['title'] if 'title' in json_data else constants.DEFAULT_META_TITLE

        return title_str

    def _parse_metadata_year(self, json_data, scraper_platform):
        platform_data = json_data['platforms']
        if len(platform_data) == 0: return constants.DEFAULT_META_YEAR
        for platform in platform_data:
            if platform['platform_id'] == int(scraper_platform):
                return platform['first_release_date'][0:4]

        # If platform not found then take first result.
        return platform_data[0]['first_release_date'][0:4]

    def _parse_metadata_genre(self, json_data):
        if 'genres' in json_data:
            genre_names = []
            for genre in json_data['genres']: genre_names.append(genre['genre_name'])
            genre_str = ', '.join(genre_names)
        else:
            genre_str = constants.DEFAULT_META_GENRE

        return genre_str

    def _parse_metadata_plot(self, json_data):
        if 'description' in json_data and json_data['description'] is not None:
            plot_str = json_data['description']
            plot_str = text.remove_HTML_tags(plot_str) # Clean HTML tags like <i>, </i>
        else:
            plot_str = constants.DEFAULT_META_PLOT

        return plot_str

    def _parse_metadata_rating(self, json_data) -> str:
        if 'moby_score' in json_data and json_data['moby_score'] is not None:
            rating = json_data['moby_score']
            return rating
        return None

    def _parse_metadata_developer(self, json_data:dict) -> str:
        if not 'releases' in json_data: return None
        if len(json_data['releases']) == 0: return None
        if not 'companies' in json_data['releases'][0]: return None

        for company in json_data['releases'][0]['companies']:
            if company['role'] == 'Developed by':
                return company['company_name']
        return None

    def _parse_metadata_esrb(self, json_data:dict) -> str:
        if 'ratings' not in json_data or json_data['ratings'] is None:
            return constants.DEFAULT_META_ESRB
            
        for rating in json_data['ratings']:
            if rating['rating_system_name'] == 'ESRB Rating':
                return rating['rating_name']

        return constants.DEFAULT_META_ESRB

    def _parse_metadata_nplayers(self, json_data:dict) -> str:
        if 'attributes' in json_data:
            attributes:list = json_data['attributes']
            for attribute in attributes:
                if attribute['attribute_category_id'] == 40:
                    return self._parse_nplayers(attribute)
                    
        nplayers_str = constants.DEFAULT_META_NPLAYERS
        return nplayers_str

    def _parse_metadata_nplayers_online(self, json_data:dict) -> str:
        if 'attributes' in json_data:
            attributes:list = json_data['attributes']
            for attribute in attributes:
                if attribute['attribute_category_id'] == 38:
                    return self._parse_nplayers(attribute)
                    
        nplayers_str = constants.DEFAULT_META_NPLAYERS
        return nplayers_str

    def _parse_nplayers(self, attribute:dict) -> str:
        if not 'attribute_name' in attribute or not attribute['attribute_name']:
            nplayers_str = constants.DEFAULT_META_NPLAYERS
            return nplayers_str
                    
        nplayers_str = str(attribute['attribute_name'])
        nplayers_str = nplayers_str.replace(' Players', '')
        nplayers_str = nplayers_str.replace(' Player', '')

        if nplayers_str.isnumeric():
            return nplayers_str

        match = re.search(r'\d+\\-(\d+)', nplayers_str)
        if match is None: return constants.DEFAULT_META_NPLAYERS

        nplayers_str = match.group(1)
        return nplayers_str

    def _parse_metadata_tags(self, json_data:dict) -> list:
        tags = []
        if not 'attributes' in json_data:
            return tags

        attributes:list = json_data['attributes']
        for attribute in attributes:
            tag = None
            if attribute['attribute_category_id'] == 2: # Video Modes Supported
                tag = self._parse_tag_videomodes(attribute)
            if attribute['attribute_category_id'] == 6: # Input Devices Supported
                tag = self._parse_tag_input_devices(attribute)
            if attribute['attribute_category_id'] == 45: # Video Resolutions Supported
                tag = self._parse_tag_videoresolution(attribute)
            if attribute['attribute_category_id'] == 27: # Sound Capabilities
                tag = self._parse_tag_sound(attribute)
            if attribute['attribute_category_id'] == 52: # Multiplayer Game Modes
                tag = self._parse_tag_mp_modes(attribute)
            if attribute['attribute_category_id'] == 65: # Controller Types Supported
                tag = self._parse_tag_controllers(attribute)

            if tag is not None: tags.append(tag)
        return tags

    def _parse_tag_videomodes(self, attribute:dict) -> str:
        videomode = str(attribute['attribute_name'])
        if videomode == 'Full screen': return None
        if videomode == 'Window': return None

        videomode = videomode.replace('HDTV ', '')
        videomode = videomode.replace('Progressive Scan', '')
        videomode = videomode.replace('\u00d7', 'x')
        return videomode

    def _parse_tag_videoresolution(self, attribute:dict) -> str:
        resolution = str(attribute['attribute_name'])
        resolution = resolution.replace('\u00d7', 'x')
        return resolution

    def _parse_tag_sound(self, attribute:dict) -> str:
        sound = str(attribute['attribute_name'])
        return sound.lower()

    def _parse_tag_controllers(self, attribute:dict) -> str:
        controller_type = str(attribute['attribute_name'])
        if controller_type == 'Digital Joystick':
            return 'controller'
        return None

    def _parse_tag_mp_modes(self, attribute:dict) -> str:
        mode = str(attribute['attribute_name'])
        if mode == 'Free-for-all / One-on-one (VS)':
            return 'free-for-all'
        return mode.lower()

    def _parse_tag_input_devices(self, attribute:dict) -> str:
        device = str(attribute['attribute_name'])
        if device == 'Other Input Devices': return None
        return device.lower()

    # Get ALL available assets for game.
    # Cache all assets in the internal disk cache.
    def _retrieve_all_assets(self, candidate, status_dic):
        # --- Cache hit ---
        if self._check_disk_cache(Scraper.CACHE_INTERNAL, self.cache_key):
            logger.debug('MobyGames._retrieve_all_assets() Internal cache hit "{}"'.format(self.cache_key))
            return self._retrieve_from_disk_cache(Scraper.CACHE_INTERNAL, self.cache_key)

        # --- Cache miss. Retrieve data and update cache ---
        logger.debug('MobyGames._retrieve_all_assets() Internal cache miss "{}"'.format(self.cache_key))
        snap_assets = self._retrieve_snap_assets(candidate, candidate['scraper_platform'], status_dic)
        if not status_dic['status']: return None
        cover_assets = self._retrieve_cover_assets(candidate, candidate['scraper_platform'], status_dic)
        if not status_dic['status']: return None
        asset_list = snap_assets + cover_assets
        logger.debug('MobyGames._retrieve_all_assets() Total {} assets found for candidate ID {}'.format(
            len(asset_list), candidate['id']))

        # --- Put metadata in the cache ---
        logger.debug('MobyGames._retrieve_all_assets() Adding to internal cache "{}"'.format(self.cache_key))
        self._update_disk_cache(Scraper.CACHE_INTERNAL, self.cache_key, asset_list)

        return asset_list

    def _retrieve_snap_assets(self, candidate, platform_id, status_dic):
        logger.debug('MobyGames._retrieve_snap_assets() Getting Snaps...')
        url_tail = '/{}/platforms/{}/screenshots?api_key={}'.format(candidate['id'], platform_id, self.api_key)
        url = MobyGames.URL_games + url_tail
        json_data = self._retrieve_URL_as_JSON(url, status_dic)
        if not status_dic['status']: return None
        self._dump_json_debug('MobyGames_assets_snap.json', json_data)

        # --- Parse images page data ---
        asset_list = []
        for image_data in json_data['screenshots']:
            # logger.debug('Snap caption "{0}"'.format(image_data['caption']))
            asset_data = self._new_assetdata_dic()
            # In MobyGames typically the Title snaps have the word "Title" in the caption.
            # Search for it
            caption_lower = image_data['caption'].lower()
            if caption_lower.find('title') >= 0:
                asset_data['asset_ID'] = constants.ASSET_TITLE_ID
            else:
                asset_data['asset_ID'] = constants.ASSET_SNAP_ID
            asset_data['display_name'] = image_data['caption']
            asset_data['url_thumb'] = image_data['thumbnail_image']
            # URL is not mandatory here but MobyGames provides it anyway.
            asset_data['url'] = image_data['image']
            if self.verbose_flag: logger.debug('Found Snap {}'.format(asset_data['url_thumb']))
            asset_list.append(asset_data)
        logger.debug('MobyGames._retrieve_snap_assets() Found {} snap assets for candidate #{}'.format(
            len(asset_list), candidate['id']))

        return asset_list

    def _retrieve_cover_assets(self, candidate, platform_id, status_dic):
        logger.debug('MobyGames._retrieve_cover_assets() Getting Covers...')
        url_tail = '/{}/platforms/{}/covers?api_key={}'.format(candidate['id'], platform_id, self.api_key)
        url = MobyGames.URL_games + url_tail
        json_data = self._retrieve_URL_as_JSON(url, status_dic)
        if not status_dic['status']: return None
        self._dump_json_debug('MobyGames_assets_cover.json', json_data)

        if json_data is None:
            return []

        # --- Parse images page data ---
        asset_list = []
        for group_data in json_data['cover_groups']:
            country_names = ' / '.join(group_data['countries'])
            for image_data in group_data['covers']:
                asset_name = '{0} - {1} ({2})'.format(
                    image_data['scan_of'], image_data['description'], country_names)
                if image_data['scan_of'].lower() in MobyGames.asset_name_mapping:
                    asset_ID = MobyGames.asset_name_mapping[image_data['scan_of'].lower()]
                else:
                    logger.warning('Scan type "{}" not implemented yet.'.format(image_data['scan_of']))

                # url_thumb is mandatory.
                # url is not mandatory here but MobyGames provides it anyway.
                asset_data = self._new_assetdata_dic()
                asset_data['asset_ID'] = asset_ID
                asset_data['display_name'] = asset_name
                asset_data['url_thumb'] = image_data['thumbnail_image']
                asset_data['url'] = image_data['image']
                if self.verbose_flag: logger.debug('Found Cover {0}'.format(asset_data['url_thumb']))
                asset_list.append(asset_data)
        logger.debug('MobyGames._retrieve_cover_assets() Found {} cover assets for candidate #{}'.format(
            len(asset_list), candidate['id']))

        return asset_list

    # MobyGames URLs have the API developer id and password.
    # Clean URLs for safe logging.
    def _clean_URL_for_log(self, url):
        clean_url = url
        clean_url = re.sub(r'api_key=[^&]*&', 'api_key=***&', clean_url)
        clean_url = re.sub(r'api_key=[^&]*$', 'api_key=***', clean_url)
        # log_variable('url', url)
        # log_variable('clean_url', clean_url)

        return clean_url

    # Retrieve URL and decode JSON object.
    # MobyGames API info https://www.mobygames.com/info/api
    #
    # * When the API key is not configured or invalid MobyGames returns HTTP status code 401.
    # * When the API number of calls is exhausted MobyGames returns HTTP status code 429.
    # * When a game search is not succesfull MobyGames returns valid JSON with an empty list.
    def _retrieve_URL_as_JSON(self, url, status_dic, retry=0):
        self._wait_for_API_request()
        page_data_raw, http_code = net.get_URL(url, self._clean_URL_for_log(url))
        self.last_http_call = datetime.now()

        # --- Check HTTP error codes ---
        if http_code != 200:
            # 400 Bad Request.
            # Sent if your query could not be processed, possibly due to invalid parameter types.
            # 401 Unauthorized
            # Sent if you attempt to access an endpoint without providing a valid API key.
            # ...
            # 429 Too Many Requests
            # Sent if you make a request exceeding your API quota.
            #
            # Try go get error message from MobyGames. Even if the server returns an
            # HTTP status code it also has valid JSON.
            try:
                # log_variable('page_data_raw', page_data_raw)
                json_data = json.loads(page_data_raw)
                error_msg = json_data['message']
            except:
                error_msg = 'Unknown/unspecified error.'
            logger.error('MobyGames msg "{}"'.format(error_msg))
            
            if http_code == 429 and retry < Scraper.RETRY_THRESHOLD:
                # 360 per hour limit, wait at least 16 minutes
                wait_till_time = datetime.now() + timedelta(seconds=960)
                msg = [
                    'You\'ve exceeded the max rate limit of 360 requests/hour.'
                     f'Respect the website and wait at least till {wait_till_time}.'
                     'Want to stop scraping now instead?'
                ]
                auto_timer_ms = (datetime.now() - wait_till_time).total_seconds() * 1000
                if not kodi.dialog_yesno_timer('\n'.join(msg), timer_ms=auto_timer_ms):
                    amount_seconds = (datetime.now() - wait_till_time).total_seconds()
                    self._wait_for_API_request(amount_seconds*1000)
                    # waited long enough? Try again
                    retry_after_wait = retry + 1
                    return self._retrieve_URL_as_JSON(url, status_dic, retry_after_wait)
                else:
                    self.scraper_disabled = True
                    status_dic['status'] = False
                    status_dic['dialog'] = kodi.KODI_MESSAGE_CANCEL
                    return None
                
            self._handle_error(status_dic, 'HTTP code {} message "{}"'.format(http_code, error_msg))
            return None

        # If page_data_raw is None at this point is because of an exception in net_get_URL()
        # which is not urllib2.HTTPError.
        if page_data_raw is None:
            self._handle_error(status_dic, 'Network error/exception in net_get_URL()')
            return None

        # Convert data to JSON.
        try:
            json_data = json.loads(page_data_raw)
        except Exception as ex:
            self._handle_exception(ex, status_dic, 'Error decoding JSON data from MobyGames.')
            return None

        return json_data

# ------------------------------------------------------------------------------------------------
# TheGamesDB supported platforms mapped to AKL platforms.
# ------------------------------------------------------------------------------------------------
DEFAULT_PLAT_MOBYGAMES = 0
# * MobyGames API cannot be used withouth a valid platform.
# * If '0' is used as the Unknown platform then MobyGames returns an HTTP error
#    "HTTP Error 422: UNPROCESSABLE ENTITY"
# * If '' is used as the Unknwon platform then MobyGames returns and HTTP error
#   "HTTP Error 400: BAD REQUEST"
# * The solution is to use '0' as the unknwon platform. AKL will detect this and
#   will remove the '&platform={}' parameter from the search URL.
def convert_AKL_platform_to_MobyGames(platform_long_name) -> int:
    matching_platform = platforms.get_AKL_platform(platform_long_name)
    if matching_platform.compact_name in AKL_compact_platform_MobyGames_mapping:
        return AKL_compact_platform_MobyGames_mapping[matching_platform.compact_name]
    
    if matching_platform.aliasof is not None and matching_platform.aliasof in AKL_compact_platform_MobyGames_mapping:
        return AKL_compact_platform_MobyGames_mapping[matching_platform.aliasof]
        
    # Platform not found.
    return DEFAULT_PLAT_MOBYGAMES

def convert_MobyGames_platform_to_AKL_platform(moby_platform) -> platforms.Platform:
    if moby_platform in MobyGames_AKL_compact_platform_mapping:
        platform_compact_name = MobyGames_AKL_compact_platform_mapping[moby_platform]
        return platforms.get_AKL_platform_by_compact(platform_compact_name)
        
    return platforms.get_AKL_platform_by_compact(platforms.PLATFORM_UNKNOWN_COMPACT)

AKL_compact_platform_MobyGames_mapping = {
    '3do': 35,
    'cpc': 60, 
    'a2600': 28,
    'a5200': 33,
    'a7800': 34,
    'atari-8bit': 39, 
    'jaguar': 17, 
    'jaguarcd': 17,
    'lynx': 18,
    'atari-st': 24,
    'wswan': 48,
    'wswancolor': 49,
    'loopy': 124,
    'pv1000': 125, 
    'cvision': 29,
    'c16': 115,
    'c64': 27,
    'amiga': 19, 
    'cd32': 56, 
    'cdtv': 83,
    'vic20': 43,
    'arcadia2001': 162, 
    'avision': 210,
    'channelf': 76,
    'fmtmarty': 102,
    'superacan': 110,
    'gp32': 108,
    'vectrex': 37,
    'odyssey2': 78,
    platforms.PLATFORM_MAME_COMPACT: 143,
    'ivision': 30,
    'msdos': 2,
    'msx': 57,
    'msx2': 57,
    'windows': 3,
    'xbox': 13, 
    'xbox360': 69,
    'xboxone': 142,
    'pce': 40,
    'pcecd': 45,
    'pcfx': 59,
    'sgx': 127,
    'n3ds': 101,
    'n64': 9, 
    'n64dd': 9,
    'nds': 44,
    'ndsi': 87,
    'fds': 22,
    'gb': 10,
    'gba': 12,
    'gbcolor': 11, 
    'gamecube': 14,
    'nes': 22, 
    'pokemini': 152,
    'snes': 15, 
    'switch': 203,
    'vb': 38,
    'wii': 82,  
    'wiiu': 132,
    'ouya': 144,
    'g7400': 128,
    'studio2': 113,
    '32x': 21,
    'dreamcast': 8,
    'gamegear': 25,
    'sms': 26,
    'megadrive': 16,
    'megacd': 20,
    'pico': 103,
    'saturn': 23,
    'sg1000': 114, 
    'x68k': 106,
    'spectrum': 41,
    'zx80': 118,
    'zx81': 119,
    'neocd': 54,
    'ngp': 52,
    'ngpcolor': 53,
    'psx': 6,
    'ps2': 7,
    'ps3': 81,
    'ps4': 141,
    'psp': 46,
    'psvita': 105, 
    'tigergame': 50,
    'creativision': 212,
    'vflash': 189,
    'vsmile': 42,
    'supervision': 109
}

MobyGames_AKL_compact_platform_mapping = {}
for key, value in AKL_compact_platform_MobyGames_mapping.items():
    MobyGames_AKL_compact_platform_mapping[value] = key