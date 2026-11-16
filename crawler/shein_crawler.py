import json

from selenium import webdriver
from selenium.common.exceptions import (ElementClickInterceptedException,
                                        NoSuchElementException)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from db.database import DATABASE_URL
from db.manager import DatabaseManager
from kafka.producer import KafkaProducer
from logs.logger import logger
from utils.utils import disable_element_by_class, wait_for


class SheinCrawler:
    def __init__(self):
        self.options = Options()
        self.db_manager = DatabaseManager(database_url=DATABASE_URL)
        # self.options.add_argument("--headless")
        # self.options.binary_location = "/usr/bin/chromedriver"
        self.driver = webdriver.Chrome(options=self.options)
        self.url_base = 'https://br.shein.com/'
        self.kafka_producer = KafkaProducer()
        self.links_category = []
        self.list_category = []
        # product_ids already sent to Kafka during the current run (dedup)
        self._sent_product_ids = set()

    def run(self):
        self.driver.get(self.url_base)
        wait_for(5)
        self.clic_icone()
        self.get_links_category()
        self.extract_category_from_url()
        # Reset dedup state: dedup scope is a single run
        self._sent_product_ids = set()
        success_categories = 0
        failed_categories = 0
        total_products_sent = 0
        for url, category in zip(self.links_category, self.list_category):
            try:
                stats = self.scrape_shein_clothes_info(
                    url_clothes=url, type_clothes=category
                )
            except Exception as e:
                # A category must never abort the whole run
                logger.error(
                    f'Category {category} failed with unexpected error: {e}'
                )
                stats = {
                    'category': category,
                    'sent': 0,
                    'duplicates': 0,
                    'errors': 0,
                    'failed': True,
                    'error': str(e),
                }
            total_products_sent += stats['sent']
            if stats['failed']:
                failed_categories += 1
            else:
                success_categories += 1
            logger.info(
                f"[SUMMARY] category={stats['category']} "
                f"sent={stats['sent']} "
                f"duplicates_skipped={stats['duplicates']} "
                f"parse_errors={stats['errors']} "
                f"failed={stats['failed']} "
                f"error={stats['error']}"
            )
            wait_for(10)
        logger.info(
            f'[TOTAL] categories_ok={success_categories} '
            f'categories_failed={failed_categories} '
            f'total_products_sent={total_products_sent}'
        )
        self.driver.quit()

    def clic_icone(self):
        try:
            elemento_icone = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, 'i.iconfont.icon-close.she-close')
                )
            )
            elemento_icone.click()

        except ElementClickInterceptedException:
            wait_for(5)
            self.clic_icone()

        except Exception as e:
            logger.error(f'Error on clic_icone {e}')

    # def has_next_page(self):
    #     try:
    #         next_page = self.driver.find_element(By.CLASS_NAME, 'sui-pagination__next')
    #         if not next_page.get_attribute('disabled') == 'true':
    #             return True
    #         return False
    #     except NoSuchElementException:
    #         logger.warning('No next page')
    #         return False

    def has_next_page(self):
        try:
            next_page = self.driver.find_element(
                By.CLASS_NAME, 'sui-pagination__next'
            )
            return next_page.get_attribute('disabled') != 'true'
        except NoSuchElementException:
            logger.warning('No next page')
            return False

    def page_down_for_load_all_images(self):
        count = 0
        try:
            body = self.driver.find_element(By.TAG_NAME, 'body')
            while count < 17:
                body.send_keys(Keys.PAGE_DOWN)
                wait_for(1)
                count += 1
        except Exception as e:
            logger.error(f'Error on page_down_for_load_all_images {e}')

    def get_links_category(self):
        try:
            logger.info('Extracting links from category')
            self.driver.get(self.url_base)
            category = self.driver.find_element(
                By.CLASS_NAME, 'ccc-hotzone'
            ).find_elements(By.XPATH, '//span/a')
            for cat in category:
                self.links_category.append(
                    cat.get_attribute('href').split('?')[0]
                )

        except Exception as e:
            logger.error(f'Error on get_links_category {e}')

    # def extract_category_from_url(self):
    #     try:
    #         for url in self.links_category:
    #             parts = url.split('/')
    #             if '-c-' in parts[-1]:
    #                 # Extract category from URL when '-c-' is present
    #                 category = parts[-1].split('-c-')[0]
    #                 logger.info(f"URL : {url}, Categoria : {category}")
    #             if '-sc-' in parts[-1]:
    #                 # Extract category from URL when '-sc-' is present
    #                 category = parts[-1].split('-sc-')[0]
    #                 logger.info(f"URL : {url}, Categoria : {category}")
    #             self.list_category.append(category)
    # except Exception as e:
    #         logger.error(f'Error on extract_category_from_url {e}')

    def extract_category_from_url(self):
        try:
            for url in self.links_category:
                parts = url.split('/')
                for part in parts:
                    if '-c-' in part or '-sc-' in part:
                        category = part.split('-')[0]
                        logger.info(f'URL : {url}, Categoria : {category}')
                        self.list_category.append(category)
                        break
        except Exception as e:
            logger.error(f'Error on extract_category_from_url {e}')

    def _parse_product(self, secao, type_clothes):
        tag_a = secao.find_element('css selector', 'a')
        imgs_src = [
            img.get_attribute('data-src')
            for img in secao.find_elements(
                'css selector', '.product-card__top-wrapper img'
            )
        ]
        if len(imgs_src) == 0:
            imgs_src = [
                img.get_attribute('data-src')
                for img in secao.find_elements(
                    'css selector', '.crop-image-container img'
                )
            ]
        imgs_serializada = json.dumps(imgs_src)
        return {
            'product_id': tag_a.get_attribute('data-id'),
            'href': tag_a.get_attribute('href'),
            'data_title': tag_a.get_attribute('data-title'),
            'data_price': tag_a.get_attribute('data-price'),
            'data_id_category': tag_a.get_attribute('data-cat_id'),
            'data_sku': tag_a.get_attribute('data-sku'),
            'data_spu': tag_a.get_attribute('data-spu'),
            'data_us_price': tag_a.get_attribute('data-us-price'),
            'data_us_origin_price': tag_a.get_attribute(
                'data-us-origin-price'
            ),
            'discount': tag_a.get_attribute('data-discount'),
            'image_src': imgs_serializada,
            'product_type': type_clothes,
        }

    def _go_to_next_page(self):
        """Click the next-page button.

        Returns (True, None) on success, (False, error_message) when the
        page could not be advanced — the caller ends the category and
        keeps the products already sent.
        """
        try:
            next_page = self.driver.find_element(
                By.CSS_SELECTOR,
                '.sui-pagination__next.sui-pagination__btn',
            )
            next_page.click()
            wait_for(9)
            return True, None
        except ElementClickInterceptedException:
            logger.warning('warning on next_page')
            try:
                body = self.driver.find_element(By.TAG_NAME, 'body')
                body.send_keys(Keys.END)
                wait_for(2)
                next_page = self.driver.find_element(
                    By.CSS_SELECTOR,
                    '.sui-pagination__next.sui-pagination__btn',
                )
                next_page.click()
                wait_for(9)
                return True, None
            except Exception as e:
                logger.error(f'Error on next_page retry {e}')
                return False, str(e)
        except Exception as e:
            logger.error(f'Error on next_page {e}')
            return False, str(e)

    def scrape_shein_clothes_info(self, url_clothes, type_clothes):
        stats = {
            'category': type_clothes,
            'sent': 0,
            'duplicates': 0,
            'errors': 0,
            'failed': False,
            'error': None,
        }
        try:
            self.driver.get(url_clothes)
            wait_for(5)
            disable_element_by_class(self.driver, 'j-optimize-category')
            count = 1

            while self.has_next_page():
                self.page_down_for_load_all_images()
                itens_in_page = self.driver.find_elements(
                    'css selector', 'section[role="listitem"]'
                )
                logger.info(
                    f'Extracting {count} page from category {type_clothes} '
                )
                count += 1
                for secao in itens_in_page:
                    try:
                        new_product_data = self._parse_product(
                            secao, type_clothes
                        )
                    except Exception as e:
                        stats['errors'] += 1
                        logger.error(
                            f'Error parsing product in category '
                            f'{type_clothes}: {e}'
                        )
                        continue
                    product_id = new_product_data['product_id']
                    if product_id in self._sent_product_ids:
                        stats['duplicates'] += 1
                        logger.info(
                            f'Duplicate product {product_id} skipped '
                            f'in category {type_clothes}'
                        )
                        continue
                    self._sent_product_ids.add(product_id)
                    self.enviar_dados_para_kafka(new_product_data)
                    stats['sent'] += 1
                advanced, error = self._go_to_next_page()
                if not advanced:
                    # Pagination failed: end this category, keep what was sent
                    stats['failed'] = True
                    stats['error'] = error
                    break
        except Exception as e:
            # Nothing from a failing category propagates to run()
            stats['failed'] = True
            stats['error'] = str(e)
            logger.error(
                f'Error on scrape category {type_clothes}: {e}'
            )
        return stats

    # def send_data_to_products_topic(self, dados_json):
    #     try:
    #         dados_json = json.dumps(dados_json)
    #         dados_bytes = dados_json.encode('utf-8')
    #         self.kafka_producer.produce(topic='produtos', value=dados_bytes)
    #         logger.info("Mensagem enviada com sucesso para o tópico 'produtos'.")
    #     except Exception as e:
    #         logger.error(f"Erro ao enviar mensagem para o Kafka: {e}")

    def enviar_dados_para_kafka(self, dados_json):
        try:
            dados_json = json.dumps(dados_json)
            self.kafka_producer.produce(topic='produtos', value=dados_json)
            print("Mensagem enviada com sucesso para o tópico 'produtos'.")
        except Exception as e:
            print(f'Erro ao enviar mensagem para o Kafka: {e}')


if __name__ == '__main__':

    # Cria uma instância do SheinCrawler
    shein_crawler = SheinCrawler()
    shein_crawler.run()
