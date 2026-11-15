# Developed By : NIRANJAN KUMAR G S
# From : INDIA
# Email : niranjan4@outlook.in
# Updated date : 12/sep/2017
#
# Refactor notes:
#   1. City/category link discovery and per-category detail scraping are now
#      two independently failing phases. A request/parse failure on one link
#      is recorded and skipped; the rest of the run continues.
#   2. Category counts and sold quantities that cannot be parsed are treated
#      as missing (None) instead of aborting the whole run.
#   3. Every run ends with an outcome manifest (console + outcomes.csv)
#      containing link, success/failure and failure reason.
#   4. Bug fixes: undefined `category` variable, `brought_co` typo,
#      membership test on a Tag object for "Featured", and unclosed files.

import re
import sys
from urllib.parse import urljoin

import matplotlib
matplotlib.use('Agg')  # headless-safe backend; must be set before pyplot
import matplotlib.pyplot as plt
import pandas as pd
import requests
from bs4 import BeautifulSoup

REQUEST_TIMEOUT = 15

# Outcome reason labels
REQUEST_FAILED = '请求异常'
PARSE_FAILED = '解析异常'
COUNT_MISSING = '数量缺失'


class Webscraper:
	def __init__(self, url, timeout=REQUEST_TIMEOUT):
		self.url = url
		self.timeout = timeout
		self.tags_each_place = []
		self.outcomes = []
		self.run()

	# ------------------------------------------------------------------
	# Outcome bookkeeping
	# ------------------------------------------------------------------
	def _record(self, url, stage, status, reason='', detail='', city='', category=''):
		self.outcomes.append({
			'链接': url,
			'阶段': stage,
			'城市': city,
			'分类': category,
			'状态': status,
			'失败原因': reason,
			'详情': detail,
		})

	def _safe_get(self, url, stage, city='', category=''):
		"""GET a URL; on any request failure record it and return None."""
		try:
			response = requests.get(url, timeout=self.timeout)
			response.raise_for_status()
			return response
		except requests.RequestException as exc:
			self._record(url, stage, '失败', REQUEST_FAILED, str(exc), city, category)
			return None
		except Exception as exc:  # non-HTTP failure while issuing the request
			self._record(url, stage, '失败', REQUEST_FAILED, repr(exc), city, category)
			return None

	# ------------------------------------------------------------------
	# Phase 1: entry page -> city/state "things-to-do" link discovery
	# ------------------------------------------------------------------
	def discover_places(self):
		print('CURRENT PAGE URL IS:', self.url)
		response = self._safe_get(self.url, '入口页')
		if response is None:
			return []

		try:
			soup = BeautifulSoup(response.content, 'html.parser')
			containers = soup.find_all('div', class_='col-md-12 activities-and-listings')
			place_links = []
			seen = set()
			for container in containers:
				for anchor in container.find_all('a', href=re.compile(r'^/(?:states|cities)/')):
					href = anchor.get('href')
					if not href:
						continue
					place_url = urljoin(self.url, href.rstrip('/') + '/things-to-do')
					if place_url not in seen:
						seen.add(place_url)
						place_links.append(place_url)
		except Exception as exc:  # parse failure on the entry page must not crash the run
			self._record(self.url, '入口页', '失败', PARSE_FAILED, repr(exc))
			return []

		with open('all_things.txt', 'w', encoding='utf-8') as f1:
			for place_link in place_links:
				f1.write(place_link + '\r\n')

		self._record(self.url, '入口页', '成功',
		             detail='发现 %d 个城市/州链接' % len(place_links))
		print('discovered %d place page(s)' % len(place_links))
		return place_links

	# ------------------------------------------------------------------
	# Phase 2: per-place detail scraping (each place fails independently)
	# ------------------------------------------------------------------
	def scrape_place(self, place_url):
		city = self._city_from_url(place_url)
		response = self._safe_get(place_url, '城市页', city=city)
		if response is None:
			return

		try:
			soup = BeautifulSoup(response.content, 'html.parser')
			category_blocks = soup.find_all('div', class_='filter-xor')
		except Exception as exc:
			self._record(place_url, '城市页', '失败', PARSE_FAILED, repr(exc), city)
			return

		if not category_blocks:
			self._record(place_url, '城市页', '失败', PARSE_FAILED,
			             '未发现分类区块(filter-xor)', city)
			return

		activities = []
		counts = []
		bought = []
		with open(city + '-links-things-to-do.txt', 'w', encoding='utf-8') as f2, \
				open(city + '-links-with-category-and-count.txt', 'w', encoding='utf-8') as f3:
			for block in category_blocks:
				row = self._scrape_category(block, place_url, city, f2, f3)
				if row is None:
					continue
				activities.append(row['name'])
				counts.append(row['count'])
				bought.append(row['bought'])

		self._write_place_artifacts(city, activities, counts, bought)

	def _scrape_category(self, block, place_url, city, f2, f3):
		"""Scrape one category block + its detail page. Never raises."""
		category_link = ''
		try:
			f3.write(str(block) + '\r\n')
			anchor = block.find('a')
			category_link = urljoin(place_url, anchor.get('href'))
			name = self._category_name(anchor, category_link)
			count = self._parse_count(block.get_text())
		except Exception as exc:
			self._record(category_link or place_url, '分类页', '失败',
			             PARSE_FAILED, repr(exc), city)
			return None

		f2.write(category_link + '\r\n')
		self.tags_each_place.append(category_link)
		missing = count is None

		response = self._safe_get(category_link, '分类页', city=city, category=name)
		if response is None:
			# request failure already recorded; keep the row with missing sold data
			return {'name': name, 'count': count, 'bought': None}

		try:
			soup = BeautifulSoup(response.content, 'html.parser')
			place_name_tags = soup.find_all('li', class_='grid-item case02')
			sold_values = []
			sold_attempts = 0
			for place_details in place_name_tags:
				place = place_details.find('div', class_='name')
				# fix: check the element's text instead of membership on the Tag
				if place is not None and 'Featured' in place.get_text():
					continue
				brought = place_details.find('span', class_='sold')
				if brought is None:
					continue
				sold_attempts += 1
				value = self._parse_int(brought.get_text())
				if value is None:
					continue  # unparseable sold count -> treat this item as missing
				sold_values.append(value)
		except Exception as exc:
			self._record(category_link, '分类页', '失败',
			             PARSE_FAILED, repr(exc), city, name)
			return {'name': name, 'count': count, 'bought': None}

		if sold_attempts > 0 and not sold_values:
			total_bought = None
			missing = True
		else:
			total_bought = sum(sold_values)

		if missing:
			detail = '分类计数缺失' if count is None else '已售数量无法解析'
			self._record(category_link, '分类页', '失败', COUNT_MISSING,
			             detail, city, name)
		else:
			self._record(category_link, '分类页', '成功', '', '', city, name)
		return {'name': name, 'count': count, 'bought': total_bought}

	# ------------------------------------------------------------------
	# Per-place CSV + pie chart artifacts (original output preserved)
	# ------------------------------------------------------------------
	def _write_place_artifacts(self, city, activities, counts, bought):
		df = pd.DataFrame({
			'Activities': activities,
			'Total_Count': counts,
			'Total_Bought': bought,
		})
		# Preserve original behavior: a zero bought total becomes 1;
		# missing (None/NaN) values stay missing.
		df['Total_Bought'] = df['Total_Bought'].apply(
			lambda value: 1 if pd.notna(value) and value == 0 else value)
		df['efficieny'] = (
			100 * (df['Total_Bought'] - df['Total_Count']) / df['Total_Bought']
		)

		print(df)
		df.to_csv(city + '.csv')

		if not activities:
			# nothing to chart when every category failed; CSV is still written
			print('no usable categories for %s, pie chart skipped\n' % city)
			return
		pie_values = df['Total_Bought'].fillna(0).astype(float).replace(0, 1)
		plt.pie(pie_values.tolist(), labels=activities,
		        autopct='%1.1f%%', shadow=True, startangle=90)
		plt.title(city, bbox={'facecolor': '0.8', 'pad': 5})
		plt.draw()
		plt.savefig(city + '.png')
		plt.clf()
		print('\n')

	# ------------------------------------------------------------------
	# End-of-run outcome manifest
	# ------------------------------------------------------------------
	def write_outcomes(self):
		columns = ['链接', '阶段', '城市', '分类', '状态', '失败原因', '详情']
		manifest = pd.DataFrame(self.outcomes, columns=columns)
		manifest.to_csv('outcomes.csv', index=False, encoding='utf-8-sig')

		print('\n=== 本轮采集结局清单 ===')
		for row in self.outcomes:
			reason = row['失败原因'] or '-'
			detail = row['详情'] or ''
			print('[%s] %s | %s | %s%s'
			      % (row['状态'], row['阶段'], row['链接'], reason,
			         (' | ' + detail) if detail else ''))
		print('结局清单已写入 outcomes.csv，共 %d 条记录\n' % len(self.outcomes))

	def run(self):
		try:
			place_links = self.discover_places()
			for place_url in place_links:
				print('current page ' + place_url)
				try:
					self.scrape_place(place_url)
				except Exception as exc:  # last-resort guard for one place
					self._record(place_url, '城市页', '失败',
					             PARSE_FAILED, repr(exc),
					             self._city_from_url(place_url))
			self.write_outcomes()
		except Exception as exc:
			# Even an unexpected top-level failure leaves a checkable manifest
			self._record(self.url, '整体', '失败', PARSE_FAILED, repr(exc))
			self.write_outcomes()

	# ------------------------------------------------------------------
	# Small parsing helpers
	# ------------------------------------------------------------------
	@staticmethod
	def _parse_int(text):
		if text is None:
			return None
		match = re.search(r'\d+', text)
		return int(match.group()) if match else None

	@classmethod
	def _parse_count(cls, text):
		# Original shape: "Sightseeing (12)" -> 12 (last parenthesized number)
		parenthesized = re.findall(r'\((\d+)\)', text or '')
		if parenthesized:
			return int(parenthesized[-1])
		return cls._parse_int(text)

	@staticmethod
	def _category_name(anchor, category_link):
		if anchor is not None:
			text = anchor.get_text(strip=True)
			text = re.sub(r'\s*\(\d+\)\s*$', '', text).strip()
			if text:
				return text
		slug = [part for part in category_link.split('/') if part][-1]
		return slug.replace('-', ' ').title()

	@staticmethod
	def _city_from_url(url):
		parts = [part for part in url.split('/') if part]
		if 'things-to-do' in parts:
			slug = parts[parts.index('things-to-do') - 1]
		else:
			slug = parts[-1]
		return slug.replace('-', ' ').title()


if __name__ == '__main__':
	target = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8000/'
	Webscraper(target)
