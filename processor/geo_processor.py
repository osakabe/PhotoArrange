import csv
import logging
import os
import zipfile

import numpy as np
import requests
from scipy.spatial import cKDTree

from core.utils import get_app_data_dir

logger = logging.getLogger(__name__)


class GeoProcessor:
    def __init__(self):
        self.geo_dir = os.path.join(get_app_data_dir(), "geo")
        self.cities_file = os.path.join(self.geo_dir, "cities1000.txt")
        self.admin1_file = os.path.join(self.geo_dir, "admin1CodesASCII.txt")

        self.tree = None
        self.cities_data = []
        self.admin1_map = {}

        if not os.path.exists(self.geo_dir):
            os.makedirs(self.geo_dir, exist_ok=True)

        self._ensure_data_exists()
        self._load_data()

    def _ensure_data_exists(self):
        # 1. cities1000.zip
        if not os.path.exists(self.cities_file):
            zip_path = os.path.join(self.geo_dir, "cities1000.zip")
            logger.info("Downloading cities1000.zip from GeoNames...")
            url = "http://download.geonames.org/export/dump/cities1000.zip"
            try:
                r = requests.get(url, timeout=30)
                with open(zip_path, "wb") as f:
                    f.write(r.content)
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extract("cities1000.txt", self.geo_dir)
                os.remove(zip_path)
            except Exception as e:
                logger.error(f"Failed to download/extract cities1000.zip: {e}")

        # 2. admin1CodesASCII.txt
        if not os.path.exists(self.admin1_file):
            logger.info("Downloading admin1CodesASCII.txt from GeoNames...")
            url = "http://download.geonames.org/export/dump/admin1CodesASCII.txt"
            try:
                r = requests.get(url, timeout=30)
                with open(self.admin1_file, "wb") as f:
                    f.write(r.content)
            except Exception as e:
                logger.error(f"Failed to download admin1CodesASCII.txt: {e}")

    def _load_data(self):
        if not os.path.exists(self.cities_file):
            return

        logger.info("Loading GeoNames data into memory...")
        try:
            coords = []
            # cities1000.txt format: tab-separated
            # 0:geonameid, 1:name, 2:asciiname, 3:alternatenames, 4:lat, 5:lon,
            # 8:country_code, 10:admin1...
            with open(self.cities_file, "r", encoding="utf-8") as f:
                reader = csv.reader(f, delimiter="\t")
                for row in reader:
                    if len(row) < 11:
                        continue
                    lat = float(row[4])
                    lon = float(row[5])
                    coords.append([lat, lon])
                    self.cities_data.append(
                        {"name": row[1], "country_code": row[8], "admin1_code": row[10]}
                    )

            if coords:
                self.tree = cKDTree(np.array(coords))
                logger.info(f"Geodata loaded: {len(coords)} cities.")

            # Load Admin1 mapping
            if os.path.exists(self.admin1_file):
                with open(self.admin1_file, "r", encoding="utf-8") as f:
                    reader = csv.reader(f, delimiter="\t")
                    for row in reader:
                        # format: CC.Admin1Code \t Name \t NameASCII \t ID
                        if len(row) >= 2:
                            self.admin1_map[row[0]] = row[1]
        except Exception as e:
            logger.error(f"Error loading geodata: {e}")

    def get_location(self, lat, lon):
        if self.tree is None:
            return None

        try:
            # Query the nearest city
            dist, idx = self.tree.query([lat, lon])
            city = self.cities_data[idx]

            country = city["country_code"]
            admin1_key = f"{country}.{city['admin1_code']}"
            prefecture = self.admin1_map.get(admin1_key, city["admin1_code"])

            return {
                "country": country,
                "prefecture": prefecture,
                "city": city["name"],
                "latitude": lat,
                "longitude": lon,
            }
        except Exception as e:
            logger.error(f"Error searching location for {lat}, {lon}: {e}")
        return None
