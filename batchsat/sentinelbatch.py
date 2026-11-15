import os
import time
import json
import logging
import argparse
import threading
import requests
import pandas as pd
import shapely.wkt as wkt
import rasterio
import xmltodict

import geopandas as gpd
from datetime import date
from functools import reduce
from sentinelsat import SentinelAPI
from concurrent.futures import ThreadPoolExecutor

from rasterio.mask import mask
from rasterio.io import MemoryFile
from rasterio.enums import Resampling as Rs
from rasterio.windows import Window, from_bounds


rows_from_iterrows = lambda x: x[1]
concat_dfs = lambda x, y: pd.concat([x, y], ignore_index=True, axis=1)
is_polygon_inside = lambda x: wkt.loads(x['footprint']).contains(wkt.loads(x['polygon']['polygon_envelope']))

AUTH_ERROR_STATUS_CODES = (401, 403)
MAX_ERROR_SUMMARY_LENGTH = 300
SETTLEMENT_COLUMNS = ['uuid', 'title', 'polygon_ids', 'status', 'failed_stage', 'error_summary', 'account']


class ProductStageError(Exception):
    """Failure of a single product at a specific pipeline stage.

    Carrying the stage name lets the batch loop record exactly where the
    product failed without letting the exception kill the whole batch.
    """

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(f'[{stage}] {message}')


class AccountPool:
    """Thread-safe round-robin dispenser of SciHub accounts.

    Accounts are acquired on demand (one per HTTP request) instead of being
    pre-assigned by row number. The lock guarantees that concurrent worker
    threads take accounts sequentially, so a single account is not reused
    without bounds while others sit idle.
    """

    def __init__(self, accounts: list) -> None:
        if not accounts:
            raise ValueError('at least one account is required')
        self._accounts = list(accounts)
        self._lock = threading.Lock()
        self._next = 0

    def acquire(self):
        with self._lock:
            account = self._accounts[self._next % len(self._accounts)]
            self._next += 1
        return account

    @staticmethod
    def identifier(account) -> str:
        # never expose the password, only the user name
        if isinstance(account, (list, tuple)):
            return str(account[0])
        return str(account)


class SentinelBatch:
    def __init__(self, accounts: list, cloudcoverpercentage: tuple,
        platformname: str = 'Sentinel-2',
        producttype: str = 'S2MSI2A', processinglevel = 'Level-2A',
        sleep_time: float = 0.125
    ) -> None:

        self.accounts = accounts
        self.account_pool = AccountPool(accounts)
        self.platformname = platformname
        self.producttype = producttype
        self.processinglevel = processinglevel
        self.sleep_time = sleep_time
        self.cloudcoverpercentage = cloudcoverpercentage

        logging.basicConfig(level=logging.INFO)


    def polygon_time_series(self, polygons_df: pd.DataFrame, roi_visibility: float,
        band: str, resolution: str, max_workers: int = 10, ignore_offline: bool = True
    ) -> pd.DataFrame:

        assert band in ['B01', 'B02', 'B03', 'B04', 'B05', 'B06', 'B07', 'B08', 'B8A', 'B09', 'B11', 'B12', 'AOT', 'WVP', 'SCL', 'TCI']
        assert resolution in ['10m', '20m', '60m'], 'resolution not supported'
        assert .0 <= roi_visibility <= 1., 'visibility of region of interest must be in [0, 1]'
        assert ignore_offline in [False, True], 'ignore offline products must be a bool value'

        bands_download_resolution: dict = {
            'B02': '10m', 'B03': '10m', 'B04': '10m', 'B08': '10m', 'TCI': '10m', 'AOT': '10m', 'WVP': '10m',
            'B05': '20m', 'B06': '20m', 'B07': '20m', 'B8A': '20m', 'B11': '20m', 'B12': '20m', 'SCL': '20m',
            'B01': '60m', 'B09': '60m'
        }

        self.roi_visibility = roi_visibility
        self.band = band
        self.resolution = resolution
        self.download_resolution = bands_download_resolution[band]
        self.scale_factor = float(self.resolution.replace('m', '')) / float(self.download_resolution.replace('m', ''))
        self.resampling = Rs.nearest if band == 'SCL' else Rs.cubic
        self.ignore_offline = ignore_offline

        df = polygons_df.copy()[:10]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:

            # get all the products and filter products that not fully contain its polygon
            seq = df.iterrows()
            seq = map(rows_from_iterrows, seq)
            seq = executor.map(self.products_from_polygons, seq)
            product_frames = [frame for frame in seq if frame is not None and not frame.empty]

            if not product_frames:
                logging.warning('No products retrieved for the given polygons')
                df.iloc[0:0].to_csv('products-polygons.csv', sep=';', index=False)
                self._write_settlement([])
                return

            df = reduce(concat_dfs, product_frames)
            df = df[df.apply(is_polygon_inside, axis=1)]

            size_before = df.shape[0]

            # group polygons by products, they might have repeated polygons per product
            df  = df.groupby(
                ['uuid', 'title', 'link', 'link_alternative', 'summary', 'footprint', 'filename', 'identifier', 'ondemand'],
                as_index=False
            ).agg({'polygon': lambda x: list(x)}).rename(columns={'polygon': 'polygons'})

            size_after = df.shape[0]

            reduction = round(100*(1. - size_after/size_before), 2) if size_before else 0.0
            logging.info(
                f"Processing {size_after} products. " +
                f"Reusing products that contains one or more polygons (Reduction: {reduction}%)"
            )

            # process each product independently: a failure in one product is
            # recorded in the settlement and never aborts the remaining ones
            seq = df.iterrows()
            seq = map(rows_from_iterrows, seq)
            results = list(executor.map(self._process_product, seq))

        success_rows = [row for row, _ in results if row is not None]
        records = [record for _, record in results]

        products_df = pd.DataFrame(success_rows) if success_rows else df.iloc[0:0]
        products_df.to_csv('products-polygons.csv', sep=';', index=False)

        self._write_settlement(records)

    def _process_product(self, row: pd.Series):
        """Run the whole per-product chain, isolating failures per scene.

        Returns a (row, record) tuple: row is the enriched product row on
        success (None otherwise), record is the per-scene settlement entry.
        """

        record = self._new_settlement_record(row)

        try:
            row = self.product_online_status(row, record)
            row = self.trigger_online(row, record)

            if row['online'] != 'true':
                record['status'] = 'skipped'
                record['error_summary'] = (
                    'product offline; retrieval triggered, try to download in a few hours'
                    if row['triggered'] == 'true' else 'product offline'
                )
                return None, record

            row = self.product_metadata(row, record)
            row = self.download_rasters(row, record)
            row = self.resize_rasters(row)
            row = self.save_rasters(row)

            record['status'] = 'success'
            return row, record
        except ProductStageError as e:
            record['status'] = 'failed'
            record['failed_stage'] = e.stage
            record['error_summary'] = self._summarize_error(e)
        except Exception as e:
            record['status'] = 'failed'
            record['failed_stage'] = 'unknown'
            record['error_summary'] = self._summarize_error(e)
            logging.exception(f"Unexpected error while processing product {record['uuid']}")

        return None, record

    def _new_settlement_record(self, row: pd.Series) -> dict:

        polygons = row['polygons'] if 'polygons' in row else []
        polygon_ids = ','.join(str(polygon.get('polygon_id', '')) for polygon in polygons)

        return {
            'uuid': row['uuid'],
            'title': row['title'],
            'polygon_ids': polygon_ids,
            'status': 'failed',
            'failed_stage': '',
            'error_summary': '',
            'account': ''
        }

    @staticmethod
    def _summarize_error(error: Exception) -> str:

        summary = str(error).replace('\n', ' ').replace('\r', ' ')
        return summary[:MAX_ERROR_SUMMARY_LENGTH]

    def _write_settlement(self, records: list) -> None:

        settlement_df = pd.DataFrame(records, columns=SETTLEMENT_COLUMNS)
        settlement_df.to_csv('products-settlement.csv', sep=';', index=False)

        counts = settlement_df['status'].value_counts() if records else pd.Series(dtype=int)
        succeeded = int(counts.get('success', 0))
        failed = int(counts.get('failed', 0))
        skipped = int(counts.get('skipped', 0))

        logging.info(
            f"Batch settlement written to products-settlement.csv: " +
            f"{succeeded} succeeded, {failed} failed, {skipped} skipped (offline)"
        )

    @staticmethod
    def _is_auth_error(error: Exception) -> bool:

        response = getattr(error, 'response', None)
        if response is not None and getattr(response, 'status_code', None) in AUTH_ERROR_STATUS_CODES:
            return True

        message = str(error).lower()
        return '401' in message or '403' in message or 'unauthorized' in message or 'forbidden' in message

    def _http_get(self, url: str, stage: str, record: dict, expected_status: int = 200, **kwargs) -> requests.Response:
        """GET with on-demand account acquisition and one account-switch retry.

        The account is taken from the pool per request. On an auth failure
        (401/403) the next account is acquired and the request retried once;
        any other failure is raised immediately without switching accounts.
        """

        last_status = None

        for attempt in range(2):
            account = self.account_pool.acquire()
            account_id = AccountPool.identifier(account)

            # keep every account used for this product (never the password)
            used_accounts = record.setdefault('_accounts_used', [])
            if account_id not in used_accounts:
                used_accounts.append(account_id)
            record['account'] = ','.join(used_accounts)

            time.sleep(self.sleep_time)

            try:
                r = requests.get(url, auth=account, **kwargs)
            except Exception as e:
                raise ProductStageError(stage, str(e))

            last_status = r.status_code

            if r.status_code == expected_status:
                return r

            if r.status_code in AUTH_ERROR_STATUS_CODES and attempt == 0:
                logging.warning(
                    f"Auth failure (status {r.status_code}) at stage '{stage}' " +
                    f"with account '{record['account']}', retrying once with the next account"
                )
                continue

            break

        raise ProductStageError(stage, f'Status code {last_status}')

    def products_from_polygons(self, row: pd.Series) -> pd.DataFrame:

        polygon_id = row['polygon_id']
        polygon_wkt = row['polygon_wkt']
        dt_from = row['dt_from'].replace('-', '')
        dt_to = row['dt_to'].replace('-', '')

        try:
            polygon_envelope = wkt.loads(polygon_wkt).envelope.wkt

            query_result = None
            api = None

            for attempt in range(2):
                account = self.account_pool.acquire()
                api = SentinelAPI(account[0], account[1], 'https://apihub.copernicus.eu/apihub')

                try:
                    time.sleep(self.sleep_time)

                    query_result = api.query(
                        polygon_envelope,
                        date=(dt_from, dt_to),
                        platformname=self.platformname,
                        producttype=self.producttype,
                        processinglevel=self.processinglevel,
                        cloudcoverpercentage=self.cloudcoverpercentage
                    )
                    break
                except Exception as e:
                    if self._is_auth_error(e) and attempt == 0:
                        logging.warning(
                            f"Auth failure while querying products for polygon {polygon_id} " +
                            f"with account '{AccountPool.identifier(account)}', retrying once with the next account"
                        )
                        continue
                    raise

            products = api.to_dataframe(query_result)

            products['polygon'] = products['uuid'].apply(lambda _ : {
                'polygon_id': polygon_id,
                'polygon_wkt': polygon_wkt,
                'polygon_envelope': polygon_envelope
            })

            logging.info(f"Retrieved {len(products)} products from polygon {polygon_id}")
        except Exception as e:
            logging.error(
                'Error while retrieving products: ' +
                str(e) +
                f" polygon {polygon_id}"
            )
            products = pd.DataFrame()

        return products

    def product_online_status(self, row: pd.Series, record: dict) -> pd.Series:

        uuid = row['uuid']
        url: str = f"https://scihub.copernicus.eu/dhus/odata/v1/Products('{uuid}')/Online/$value"

        r = self._http_get(url, 'online_check', record)
        status = r.content.decode('utf-8')

        logging.info(f"{uuid} is online? -> {status}")

        row['online'] = status

        return row

    def trigger_online(self, row: pd.Series, record: dict) -> pd.Series:

        online = row['online']
        if online == 'true':
            row['triggered'] = 'false'
            return row

        uuid = row['uuid']
        url: str = f"https://scihub.copernicus.eu/dhus/odata/v1/Products('{uuid}')/$value"

        self._http_get(url, 'trigger_online', record, expected_status=202, allow_redirects=True)

        row['triggered'] = 'true'
        logging.info(f"Product '{uuid}' successfully triggered to online, try to download in a few hours")

        return row

    def product_metadata(self, row: pd.Series, record: dict) -> pd.Series:

        online = row['online']
        if online != 'true':
            row['metadata'] = 'error'
            return row

        uuid = row['uuid']
        title = row['title']
        url: str = f"https://apihub.copernicus.eu/apihub/odata/v1/Products('{uuid}')/Nodes('{title}.SAFE')/Nodes('MTD_MSIL2A.xml')/$value"

        r = self._http_get(url, 'metadata', record)
        metadata = r.content.decode('utf-8').replace('\n', '')

        logging.info(f"Product {uuid} metadata successfully retrieved")

        row['metadata'] = metadata
        return row

    def download_rasters(self, row: pd.Series, record: dict) -> pd.Series:

        band_resolution = f'{self.band}_{self.resolution}'

        online = row['online']
        if online != 'true':
            row[band_resolution] = []
            return row

        metadata = row['metadata']
        uuid = row['uuid']
        polygons = row['polygons']

        try:
            metadata = xmltodict.parse(metadata)
            user_product = metadata['n1:Level-2A_User_Product']
            general_info = user_product['n1:General_Info']
            product_info = general_info['Product_Info']
            product_organisation = product_info['Product_Organisation']
            granule_list = product_organisation['Granule_List']
            granule = granule_list['Granule']
            band_list = granule['IMAGE_FILE']

            band_path = next(filter(lambda x: band_resolution in x, band_list)) + '.jp2'

            product_uri = product_info['PRODUCT_URI']
            uri_band_path = f"{product_uri}/{band_path}"
            nodes_uri_band_path = map(lambda x: f"Nodes('{x}')", uri_band_path.split('/'))
            complete_band_path = '/'.join(nodes_uri_band_path)

            url = f"https://scihub.copernicus.eu/dhus/odata/v1/Products('{uuid}')/{complete_band_path}/$value"

            r = self._http_get(url, 'download', record, stream=True, allow_redirects=True)

            r.raw.decode_content = True

            rasters = list()

            for polygon in polygons:
                with MemoryFile(r.content) as meminfile:
                    with meminfile.open(driver='JP2OpenJPEG') as src:
                        wkt = polygon['polygon_envelope']
                        bounds = gpd.GeoSeries.from_wkt([wkt], crs='EPSG:4326').to_crs(src.crs).iloc[0].bounds
                        window = from_bounds(*bounds, transform=src.transform)
                        new_window = Window(
                            max(0., window.col_off-1.),
                            max(0., window.row_off-1.),
                            window.width+2. if window.col_off + window.width+1 < src.meta['width'] else window.width,
                            window.height+2. if window.row_off + window.height+1 < src.meta['height'] else window.height
                        )

                        out_image = src.read(window=new_window)
                        out_transform = src.window_transform(new_window)
                        out_meta = src.meta
                        out_meta.update({
                            "driver": "GTiff",
                            'height': out_image.shape[1],
                            'width': out_image.shape[2],
                            'transform': out_transform
                        })

                        raster = dict()
                        raster['out_image'] = out_image
                        raster['out_meta'] = out_meta

                        rasters.append(raster)

                        logging.info(f"polygon {polygon['polygon_id']} {band_resolution} successfully downloaded")

            row[band_resolution] = rasters

            return row
        except ProductStageError:
            raise
        except Exception as e:
            raise ProductStageError('download', str(e))

    def resize_raster(self, raster: dict, scale_factor: float, resampling) -> dict:

        with MemoryFile() as memfile:
            with memfile.open(**raster['out_meta']) as src:
                src.write(raster['out_image'])

                # resample out_image to target shape
                resized_image = src.read(
                    out_shape=(
                        src.count,
                        int(src.height * scale_factor),
                        int(src.width * scale_factor)
                    ),
                    resampling=resampling
                )

                # scale image transform
                resized_transform = src.transform * src.transform.scale(
                    (src.width / resized_image.shape[2]),
                    (src.height / resized_image.shape[1])
                )

                resized_meta = src.meta
                resized_meta.update({
                    "driver": "GTiff",
                    'height': resized_image.shape[1],
                    'width': resized_image.shape[2],
                    'transform': resized_transform
                })

                resized_raster = dict()
                resized_raster['out_image'] = resized_image
                resized_raster['out_meta'] = resized_meta

                return resized_raster

    def resize_rasters(self, row: pd.Series) -> pd.Series:

        band_resolution = f'{self.band}_{self.resolution}'
        rasters = row[band_resolution]

        try:
            seq = map(lambda raster: self.resize_raster(raster, self.scale_factor, self.resampling), rasters)
            row[f'resized_{band_resolution}'] = list(seq)
        except Exception as e:
            raise ProductStageError('resample', str(e))

        return row

    def save_raster(self, polygon: dict, title: str, summary: str, raster: dict) -> None:

        polygon_id = polygon['polygon_id']
        date = summary.split(',')[0].replace('Date: ', '').replace(':', '-')
        out_meta = raster['out_meta']
        out_image = raster['out_image']

        folders = os.path.join('rasters', polygon_id)

        if not os.path.exists(folders):
            os.makedirs(folders)

        save_path = os.path.join(folders, f'{self.band}-{date}-{title}.tif')

        # write to a temporary file and atomically move it into place, so a
        # failure never leaves a half-written raster behind
        tmp_path = save_path + '.tmp'

        try:
            with rasterio.open(tmp_path, 'w', **out_meta) as src:
                src.write(out_image)
            os.replace(tmp_path, save_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def save_rasters(self, row: pd.Series) -> pd.Series:

        band_resolution = f'{self.band}_{self.resolution}'
        polygons = row['polygons']
        title = row['title']
        summary = row['summary']
        rasters = row[f'resized_{band_resolution}']

        try:
            seq = map(lambda polygon,raster: self.save_raster(polygon, title, summary, raster), polygons, rasters)
            _ = list(seq)
        except Exception as e:
            raise ProductStageError('save', str(e))

        return row
