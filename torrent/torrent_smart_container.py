import threading
from typing import List, Dict

from RTN import parse

from debrid.alldebrid import AllDebrid
from debrid.premiumize import Premiumize
from debrid.realdebrid import RealDebrid
from debrid.torbox import TorBox
from torrent.torrent_item import TorrentItem
from utils.cache import cache_results
from utils.general import season_episode_in_filename
from utils.logger import setup_logger


class TorrentSmartContainer:
    def __init__(self, torrent_items: List[TorrentItem], media):
        self.logger = setup_logger(__name__)
        self.__itemsDict: Dict[TorrentItem] = self.__build_items_dict_by_infohash(torrent_items)
        self.__media = media

    def get_hashes(self):
        return list(self.__itemsDict.keys())

    def get_items(self):
        return list(self.__itemsDict.values())

    def get_direct_torrentable(self):
        direct_torrentable_items = []
        for torrent_item in self.__itemsDict.values():
            if torrent_item.privacy == "public" and torrent_item.file_index is not None:
                direct_torrentable_items.append(torrent_item)

    def get_best_matching(self):
        best_matching = []
        self.logger.debug(f"Amount of items: {len(self.__itemsDict)}")
        for torrent_item in self.__itemsDict.values():
            self.logger.debug(f"-------------------")
            self.logger.debug(f"Checking {torrent_item.raw_title}")
            self.logger.debug(f"Has torrent: {torrent_item.torrent_download is not None}")
            if torrent_item.torrent_download is not None:  # Torrent download
                self.logger.debug(f"Has file index: {torrent_item.file_index is not None}")
                if torrent_item.file_index is not None:
                    # If the season/episode is present inside the torrent filestructure (movies always have a
                    # file_index)
                    best_matching.append(torrent_item)
            else:  # Magnet
                best_matching.append(torrent_item)  # If it's a movie with a magnet link

        return best_matching

    def cache_container_items(self):
        threading.Thread(target=self.__save_to_cache).start()

    def __save_to_cache(self):
        public_torrents = list(filter(lambda x: x.privacy == "public", self.get_items()))
        cache_results(public_torrents, self.__media)

    def update_availability(self, debrid_response, debrid_type, media):
        if debrid_type is RealDebrid:
            self.__update_availability_realdebrid(debrid_response, media)
        elif debrid_type is AllDebrid:
            self.__update_availability_alldebrid(debrid_response, media)
        elif debrid_type is Premiumize:
            self.__update_availability_premiumize(debrid_response)
        elif debrid_type is TorBox:
            self.__update_availability_torbox(debrid_response, media)
        else:
            raise NotImplemented

    def __update_availability_realdebrid(self, response, media):
        for info_hash, details in response.items():
            if "rd" not in details:
                continue

            torrent_item: TorrentItem = self.__itemsDict[info_hash]

            files = []
            self.logger.info(torrent_item.type)
            if torrent_item.type == "series":
                for variants in details["rd"]:
                    for file_index, file in variants.items():
                        self.logger.info(file["filename"])
                        clean_season = media.season.replace("S", "")
                        clean_episode = media.episode.replace("E", "")
                        numeric_season = int(clean_season)
                        numeric_episode = int(clean_episode)
                        if season_episode_in_filename(file["filename"], numeric_season, numeric_episode):
                            self.logger.info("File details 2")
                            self.logger.info(file["filename"])
                            files.append({
                                "file_index": file_index,
                                "title": file["filename"],
                                "size": file["filesize"]
                            })
            else:
                for variants in details["rd"]:
                    for file_index, file in variants.items():
                        self.logger.info("File details 3")
                        self.logger.info(file["filename"])
                        files.append({
                            "file_index": file_index,
                            "title": file["filename"],
                            "size": file["filesize"]
                        })

            self.__update_file_details(torrent_item, files)

    def __update_availability_alldebrid(self, response, media):
        if response["status"] != "success":
            self.logger.error(f"Error while updating availability: {response}")
            return

        for data in response["data"]["magnets"]:
            if data["instant"] == False:
                continue

            torrent_item: TorrentItem = self.__itemsDict[data["hash"]]

            files = []
            self.__explore_folders(data["files"], files, 1, torrent_item.type, media.season,
                                   media.episode)

            self.__update_file_details(torrent_item, files)

    def __update_availability_torbox(self, response, media):
        for torrent_hash, data in response.items():

            if not torrent_hash or torrent_hash not in self.__itemsDict:
                self.logger.warning(f"Hash {torrent_hash} not found in itemsDict.")
                continue
            torrent_item: TorrentItem = self.__itemsDict[torrent_hash]
            files = []

            self.__explore_folders(
                    folder=data.get("files", []),
                    files=files,
                    file_index=1,
                    type=torrent_item.type,
                    season=media.season,
                    episode=media.episode
            )
            self.__update_file_details(torrent_item, files)

    def __update_availability_premiumize(self, response):
        if response["status"] != "success":
            self.logger.error(f"Error while updating availability: {response}")
            return

        torrent_items = self.get_items()
        for i in range(len(response["response"])):
            if bool(response["response"][i]):
                torrent_items[i].availability = response["transcoded"][i] == True


    def __update_file_details(self, torrent_item, files):
        if len(files) == 0:
            return

        file = max(files, key=lambda file: file["size"])
        torrent_item.availability = True
        torrent_item.file_index = file["file_index"]
        torrent_item.file_name = file["title"]
        torrent_item.size = file["size"]

    def __build_items_dict_by_infohash(self, items: List[TorrentItem]):
        self.logger.debug(f"Building items dict by infohash ({len(items)} items)")
        items_dict = dict()
        for item in items:
            if item.info_hash is not None:
                self.logger.debug(f"Adding {item.info_hash} to items dict")
                if item.info_hash in items_dict:
                    self.logger.debug(f"Duplicate info hash found: {item.info_hash}")
                items_dict[item.info_hash] = item
        return items_dict

    # Simple recursion to traverse the file structure returned by AllDebrid
    def __explore_folders(self, folder, files, file_index, type, season=None, episode=None):
        if type == "series":
            for file in folder:
                if "e" in file or "files" in file:
                    sub_folder = file.get("e") or file.get("files")
                    file_index = self.__explore_folders(sub_folder, files, file_index, type, season,
                                                        episode)
                    continue

                file_name = file.get("n") or file.get("name")
                file_size = file.get("s") or file.get("size", 0)
                if not file_name:
                    self.logger.warning(f"Filename missing for : {file}")
                    continue

                if season_episode_in_filename(file_name, season, episode):
                    files.append({
                        "file_index": file_index,
                        "title": file_name,
                        "size": file_size
                    })
                file_index += 1

        elif type == "movie":
            file_index = 1
            for file in folder:
                if "e" in file or "files" in file:
                    sub_folder = file.get("e") or file.get("files")
                    file_index = self.__explore_folders(sub_folder, files, file_index, type)
                    continue

                file_name = file.get("n") or file.get("name")
                file_size = file.get("s") or file.get("size", 0)

                if not file_name:
                    self.logger.warning(f"Filename missing for : {file}")
                    continue

                files.append({
                    "file_index": file_index,
                    "title": file_name,
                    "size": file_size
                })
                file_index += 1

        return file_index
