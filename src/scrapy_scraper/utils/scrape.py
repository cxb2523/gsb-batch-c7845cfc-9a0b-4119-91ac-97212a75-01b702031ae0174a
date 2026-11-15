#!/usr/bin/env python3

from . import array, file, general, stopwatch

from bs4 import BeautifulSoup

from twisted.python.failure             import Failure
from scrapy.spidermiddlewares.httperror import HttpError
from scrapy.http                        import Request, HtmlResponse
from playwright.async_api               import Request as PlaywrightRequest, Page as PlaywrightPage, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

import asyncio, dataclasses, jsbeautifier, os, random, scrapy, scrapy.crawler, scrapy.utils.project, time, typing, urllib.parse

STATUS_ERROR = -1
NO_RECURSION = -1

# Minimum time in seconds between two intermediate result snapshots.
SNAPSHOT_INTERVAL = 1.0

# Final state of a crawl record.
STATE_SUCCESS    = "success"    # the URL was fetched successfully
STATE_HTTP_ERROR = "http_error" # the URL returned an HTTP error status code
STATE_ERROR      = "error"      # the URL could not be fetched (network, timeout, etc.)

@dataclasses.dataclass
class Crawled:
	url         : str  = ""
	status      : int  = STATUS_ERROR
	is_start_url: bool = False
	state       : str  = STATE_SUCCESS # one of the STATE_* constants; defaults to a successful crawl
	error       : str  = "" # error message, empty on success

@dataclasses.dataclass
class Links:
	in_scope    : list[str] = dataclasses.field(default_factory = list)
	out_of_scope: list[str] = dataclasses.field(default_factory = list)

@dataclasses.dataclass
class Stats:
	total  : int = 0
	success: int = 0
	failed : int = 0

@dataclasses.dataclass
class Collection:
	crawled: list[Crawled]                          = dataclasses.field(default_factory = list[Crawled])
	links  : Links                                  = dataclasses.field(default_factory = Links)
	stats  : Stats                                  = dataclasses.field(default_factory = Stats)
	_revision: int                                  = dataclasses.field(default = 0, init = False, repr = False, compare = False)
	_index : dict[str, int]                         = dataclasses.field(default_factory = dict[str, int], init = False, repr = False, compare = False)

	@property
	def revision(self) -> int:
		"""
		Number of changes made to the collection, used to track unsaved records.
		"""
		return self._revision

	def record(self, crawled: Crawled) -> bool:
		"""
		Record the final state of a URL.\n
		Each URL is kept exactly once: a successful crawl replaces an earlier failure, while a failure
		never overwrites a success, and repeated failures are not counted twice.\n
		Returns 'True' if the collection changed.
		"""
		index = self._index.get(crawled.url)
		if index is not None:
			entry = self.crawled[index]
			if entry.state == STATE_SUCCESS or crawled.state != STATE_SUCCESS:
				return False
			self.crawled[index] = crawled
		else:
			self._index[crawled.url] = len(self.crawled)
			self.crawled.append(crawled)
		self._revision += 1
		return True

	def extend_links(self, in_scope: list[str], out_of_scope: list[str]):
		"""
		Add extracted links to the collection.
		"""
		self.links.in_scope.extend(in_scope)
		self.links.out_of_scope.extend(out_of_scope)
		if in_scope or out_of_scope:
			self._revision += 1

	def rebuild_index(self):
		"""
		Rebuild the URL index, e.g. after the records were re-sorted.
		"""
		self._index = {entry.url: index for index, entry in enumerate(self.crawled)}

	def update_stats(self):
		"""
		Recount the final states.
		"""
		success = sum(1 for entry in self.crawled if entry.state == STATE_SUCCESS)
		self.stats.total   = len(self.crawled)
		self.stats.success = success
		self.stats.failed  = self.stats.total - success

	def to_dict(self):
		"""
		Convert the collection to a JSON-serializable dictionary.\n
		The original 'crawled' and 'links' keys are preserved; 'stats' is additive.
		"""
		self.update_stats()
		return {
			"crawled": [dataclasses.asdict(entry) for entry in self.crawled],
			"links"  : dataclasses.asdict(self.links),
			"stats"  : dataclasses.asdict(self.stats)
		}

class ScrapyScraperSpider(scrapy.Spider):

	def __init__(
		self,
		urls           : list[str],
		whitelist      : list[str],
		playwright     : bool,
		playwright_wait: float,
		recursion      : int,
		headers        : dict[str, str],
		cookies        : dict[str, str],
		user_agents    : list[str],
		proxy          : str,
		downloads      : str,
		screenshots    : str,
		out            : str,
		debug          : bool
	):
		"""
		Class for managing Scrapy's spider.
		"""
		self.name              = "ScrapyScraperSpider"
		self.start_urls        = urls
		self.allowed_domains   = whitelist
		self.__playwright      = playwright
		self.__playwright_wait = playwright_wait
		self.__crawl           = recursion > NO_RECURSION
		self.__headers         = headers
		self.__cookies         = cookies
		self.__user_agents     = user_agents
		self.__user_agents_len = len(self.__user_agents)
		self.__proxy           = proxy
		self.__downloads       = downloads
		self.__screenshots     = screenshots
		self.__out             = out
		self.__debug           = debug
		self.__context         = 0
		self.__collection      = Collection()
		self.__saved              = 0     # number of changes in the last persisted snapshot
		self.__saved_at           = 0.0   # time of the last persisted snapshot
		self.__snapshot_scheduled = False # whether a trailing snapshot is pending
		self.__snapshot_error     = False # whether the last snapshot failed to save

	def __print_start_urls(self):
		"""
		Print start URLS.
		"""
		general.print_green("Start URLs:")
		for url in self.start_urls:
			print(url)

	def __print_allowed_domains(self):
		"""
		Print allowed [sub]domains.
		"""
		if self.allowed_domains:
			general.print_cyan("Allowed [sub]domains:")
			for domain in self.allowed_domains:
				print(f"*.{domain}")
		else:
			general.print_red("Domain whitelisting is off!")

	def start_requests(self):
		"""
		Main method.
		"""
		self.__print_start_urls()
		self.__print_allowed_domains()
		print(general.get_timestamp("Collecting..."))
		print("Press CTRL + C to exit early - results will be saved, please be patient")
		for url in self.start_urls:
			yield scrapy.Request(
				url         = url,
				headers     = self.__get_headers(),
				cookies     = self.__cookies,
				meta        = self.__get_metadata(True, True),
				errback     = self.__error,
				callback    = self.__success,
				dont_filter = False
			)

	def start(self):
		"""
		Scrapy 2.13+ consumes 'start()' instead of 'start_requests()'.\n
		Bridge the two so that the spider works on both older and newer Scrapy versions.
		"""
		async def generator():
			for request in self.start_requests():
				yield request
		return generator()

	def __get_headers(self) -> dict[str, str]:
		"""
		Get HTTP request headers.
		"""
		default_headers = {
			"User-Agent"               : self.__get_user_agent(),
			"Accept-Language"          : "en-US, *",
			"Accept"                   : "*/*",
			"Referer"                  : "https://www.google.com/",
			"Upgrade-Insecure-Requests": "1"
		}
		headers = {}
		for name, value in default_headers.items():
			if value:
				headers[name.lower()] = value
		for name, value in self.__headers.items(): # override
			headers[name.lower()] = value
		return headers

	def __get_user_agent(self):
		"""
		Get a [random] user agent.\n
		Returns an empty string if there are no user agents.
		"""
		user_agent = ""
		if self.__user_agents_len > 0:
			user_agent = self.__user_agents[random.randint(0, self.__user_agents_len - 1)]
		return user_agent

	def __get_metadata(self, is_start_url = False, take_screenshot = False) -> dict[str, typing.Any]:
		"""
		Get Scrapy's request metadata.
		"""
		self.__context += 1
		tmp                                = {}
		tmp["playwright"                 ] = self.__playwright
		tmp["playwright_context"         ] = str(self.__context)
		tmp["playwright_include_page"    ] = self.__playwright
		tmp["playwright_context_kwargs"  ] = {}
		tmp["playwright_context_kwargs"  ]["ignore_https_errors"] = True
		tmp["playwright_context_kwargs"  ]["java_script_enabled"] = True
		tmp["playwright_context_kwargs"  ]["accept_downloads"   ] = False
		tmp["playwright_context_kwargs"  ]["bypass_csp"         ] = False
		tmp["playwright_page_goto_kwargs"] = {"wait_until": "load"}
		tmp["proxy"                      ] = self.__proxy
		tmp["cookiejar"                  ] = 1
		tmp["dont_merge_cookies"         ] = False
		tmp["is_start_url"               ] = is_start_url    # custom attribute
		tmp["take_screenshot"            ] = take_screenshot # custom attribute
		return tmp

	# ------------------------------------

	def closed(self, reason: str):
		"""
		On close callback.\n
		This callback also runs when the crawl is interrupted, so every record collected
		up to that point is saved to the output file.
		"""
		self.__collection.crawled.sort(key = lambda x: x.url.casefold(), reverse = True)
		self.__collection.rebuild_index()
		self.__collection.links.in_scope = sorted(array.unique(self.__collection.links.in_scope), key = str.casefold, reverse = True)
		self.__collection.links.out_of_scope = sorted(array.unique(self.__collection.links.out_of_scope), key = str.casefold, reverse = True)
		self.__collection.update_stats()
		print(f"Total unique URLs crawled: {self.__collection.stats.total}")
		print(f"Total unique URLs crawled successfully: {self.__collection.stats.success}")
		print(f"Total unique URLs failed: {self.__collection.stats.failed}")
		print(f"Total unique in-scope links extracted: {len(self.__collection.links.in_scope)}")
		print(f"Total unique out-of-scope links extracted: {len(self.__collection.links.out_of_scope)}")
		stopwatch.stopwatch.stop()
		if len(self.__collection.crawled) > 0:
			file.overwrite(general.jdump(self.__collection.to_dict()), self.__out)

	def __save_snapshot(self):
		"""
		Persist the records collected so far, so that not even a forced shutdown loses them.\n
		Writes are throttled, but a pending write is always scheduled once the throttle interval elapses.
		"""
		if self.__collection.revision <= self.__saved:
			return
		if time.monotonic() - self.__saved_at >= SNAPSHOT_INTERVAL:
			self.__flush_snapshot()
		else:
			self.__schedule_snapshot()

	def __schedule_snapshot(self):
		"""
		Schedule a trailing snapshot, so that the last records are not left unsaved.
		"""
		if self.__snapshot_scheduled:
			return
		try:
			loop = asyncio.get_running_loop()
		except RuntimeError:
			return
		self.__snapshot_scheduled = True
		loop.call_later(SNAPSHOT_INTERVAL, self.__flush_snapshot)

	def __flush_snapshot(self):
		"""
		Write the current records to the output file if there are any unsaved ones.
		"""
		self.__snapshot_scheduled = False
		revision = self.__collection.revision
		if revision <= self.__saved:
			return
		if file.overwrite(general.jdump(self.__collection.to_dict()), self.__out, verbose = False):
			self.__saved          = revision
			self.__saved_at       = time.monotonic()
			self.__snapshot_error = False
		elif not self.__snapshot_error:
			# report a write failure once instead of on every snapshot
			self.__snapshot_error = True
			print("Intermediate results could not be saved, will retry until the crawl ends")

	# ------------------------------------

	async def __error(self, failure: Failure):
		"""
		Error callback.\n
		Records the final state of a failed URL, either an HTTP error or a non-HTTP failure (network, timeout, etc.).
		"""
		request : Request      = failure.request
		response: HtmlResponse = failure.value.response if failure.check(HttpError) else None
		status = response.status if response is not None else STATUS_ERROR
		state  = STATE_HTTP_ERROR if status > STATUS_ERROR else STATE_ERROR
		url    = request.url
		error  = str(failure.value).splitlines()
		error  = error[0] if error else type(failure.value).__name__
		if self.__playwright:
			page: PlaywrightPage = request.meta.get("playwright_page")
			if page is not None:
				try:
					await page.close()
					await page.context.close()
				except (PlaywrightError, PlaywrightTimeoutError):
					pass
		self.__collection.record(Crawled(url, status, request.meta.get("is_start_url", False), state, error))
		self.__save_snapshot()
		self.__print_error(status, url, error)

	def __print_error(self, status: int, url: str, message: str):
		"""
		Print error.
		"""
		if self.__debug:
			if status > STATUS_ERROR:
				url = f"{status} {url}"
			general.print_red(f"[ ERROR ] {url} -> {message}")

	# ------------------------------------

	async def __success(self, response: HtmlResponse):
		"""
		Success callback.
		"""
		status  = response.status
		url     = response.url
		content = ""
		if self.__playwright:
			page: PlaywrightPage = response.request.meta["playwright_page"]
			if self.__playwright_wait > 0:
				await asyncio.sleep(self.__playwright_wait)
			content = await page.content()
			if self.__screenshots and response.meta.get("take_screenshot", False):
				await self.__screenshot(url, page)
			await page.close()
			await page.context.close()
		else:
			content = response.body
		if self.__downloads:
			self.__download(url, content)
		in_scope_links, out_of_scope_links = self.__extract_links(url, HtmlResponse(url = url, body = content, encoding = "UTF-8") if self.__playwright else response)
		self.__collection.record(Crawled(url, status, response.meta.get("is_start_url", False), STATE_SUCCESS))
		self.__collection.extend_links(in_scope_links, out_of_scope_links)
		self.__save_snapshot()
		self.__print_success(status, url)
		# --------------------------------
		if self.__crawl:
			for link in in_scope_links:
				yield response.follow(
					url         = link,
					headers     = self.__get_headers(),
					cookies     = self.__cookies,
					meta        = self.__get_metadata(False, False),
					errback     = self.__error,
					callback    = self.__success,
					dont_filter = False
				)

	def __print_success(self, status: int, url: str):
		"""
		Print success.
		"""
		if self.__debug:
			general.print_green(f"[ OK ] {status} {url}")

	# ------------------------------------

	async def __screenshot(self, url: str, page: PlaywrightPage):
		"""
		Take a screenshot.
		"""
		filename = os.path.join(self.__screenshots, self.__get_url_filename(url)) + ".png"
		if not os.path.exists(filename):
			try:
				await page.screenshot(path = filename, full_page = False)
			except (PlaywrightError, PlaywrightTimeoutError) as ex:
				self.__print_exception(url, str(ex))

	def __download(self, url: str, content: str | bytes):
		"""
		Download a JavaScript file.
		"""
		if url.lower().endswith(".js"):
			filename = os.path.join(self.__downloads, self.__get_url_filename(url))
			if not os.path.exists(filename):
				try:
					soup = BeautifulSoup(content, "html.parser")
					open(filename, "w").write(jsbeautifier.beautify(soup.get_text()))
				except Exception as ex:
					self.__print_exception(url, str(ex))

	def __get_url_filename(self, url: str):
		"""
		Derive a filename from a URL.
		"""
		obj      = urllib.parse.urlsplit(url)
		scheme   = obj.scheme.lower()
		domain   = obj.netloc.lower()
		port     = str(obj.port) if obj.port else ""
		path     = obj.path.strip("/").replace("/", ".")
		return (".").join(part for part in [scheme, domain, port, path] if part)

	# ------------------------------------

	def __extract_links(self, url: str, response: HtmlResponse):
		"""
		Extract links.\n
		Returns a unique list.
		"""
		in_scope = []
		out_of_scope = []
		for tag, attr in (("script", "src"),("a", "href"),("link", "href")):
			try:
				tmp1, tmp2 = self.__extract_links_xpath(url, response, tag, attr)
				in_scope.extend(tmp1)
				out_of_scope.extend(tmp2)
			except (UnicodeEncodeError, AttributeError) as ex:
				self.__print_exception(url, str(ex))
		return array.unique(in_scope), array.unique(out_of_scope)

	def __extract_links_xpath(self, url: str, response: HtmlResponse, tag: str, attr: str):
		"""
		Extract links based on the specified XPath.
		"""
		in_scope = []
		out_of_scope = []
		for link in response.xpath(f"//{tag}[@{attr}]"):
			link = link.xpath(f"@{attr}").get()
			if not link:
				continue
			obj = urllib.parse.urlsplit(link)
			if obj.scheme and obj.scheme.lower() not in ["http", "https"]:
				continue
			if not obj.scheme and not obj.netloc:
				in_scope.append(urllib.parse.urljoin(url, link))
				continue
			if obj.scheme and obj.netloc:
				if self.__is_in_scope(obj.netloc):
					in_scope.append(link)
				else:
					out_of_scope.append(link)
		return array.unique(in_scope), array.unique(out_of_scope)

	def __is_in_scope(self, domain: str):
		"""
		Check if a domain name is in the scope.
		"""
		domain = domain.lower()
		return not self.allowed_domains or any(domain == allowed or domain.endswith(f".{allowed}") for allowed in self.allowed_domains)

	# ------------------------------------

	def __print_exception(self, url: str, message: str):
		"""
		Print exception.
		"""
		if self.__debug:
			general.print_red(f"[ EXCEPTION ] {url} -> {message}")

# ----------------------------------------

class ScrapyScraper:

	def __init__(
		self,
		urls                      : list[str],
		whitelist                 : list[str],
		playwright                : bool,
		playwright_wait           : float,
		concurrent_requests       : int,
		concurrent_requests_domain: int,
		sleep                     : float,
		random_sleep              : bool,
		auto_throttle             : float,
		retries                   : int,
		recursion                 : int,
		request_timeout           : float,
		headers                   : dict[str, str],
		cookies                   : dict[str, str],
		user_agents               : list[str],
		proxy                     : str,
		downloads                 : str,
		screenshots               : str,
		out                       : str,
		debug                     : bool
	):
		"""
		Class for managing Scrapy's runner.
		"""
		self.__urls                       = urls
		self.__whitelist                  = whitelist
		self.__playwright                 = playwright
		self.__playwright_wait            = playwright_wait
		self.__concurrent_requests        = concurrent_requests
		self.__concurrent_requests_domain = concurrent_requests_domain
		self.__sleep                      = sleep
		self.__random_sleep               = random_sleep
		self.__auto_throttle              = auto_throttle
		self.__retries                    = retries
		self.__recursion                  = recursion
		self.__request_timeout            = request_timeout # all timeouts
		self.__headers                    = headers
		self.__cookies                    = cookies
		self.__user_agents                = user_agents
		self.__proxy                      = proxy
		self.__downloads                  = downloads
		self.__screenshots                = screenshots
		self.__out                        = out
		self.__debug                      = debug
		self.__headless_browser           = True
		self.__browser_type               = "chromium" # Playwright's headless browser
		self.__handle_sigint              = False
		self.__max_redirects              = 10

	def __page_block(self, request: PlaywrightRequest):
		"""
		Types of content to block while using Playwright's headless browser.\n
		Skipped if taking screenshots.
		"""
		return request.resource_type in ["fetch", "stylesheet", "image", "ping", "font", "media", "imageset", "beacon", "csp_report", "object", "texttrack", "manifest"]

	def run(self):
		"""
		Configure the settings and run the Chad Extractor spider.
		"""
		if not file.confirm_overwrite(self.__out):
			print("The results will not be saved, exiting")
			return
		settings = scrapy.utils.project.get_project_settings()
		# --------------------------------
		settings["COOKIES_ENABLED"         ] = True
		settings["DOWNLOAD_TIMEOUT"        ] = self.__request_timeout # connect / read timeout
		settings["DOWNLOAD_DELAY"          ] = self.__sleep
		settings["RANDOMIZE_DOWNLOAD_DELAY"] = self.__random_sleep
		settings["HTTPPROXY_ENABLED"       ] = bool(self.__proxy)
		# --------------------------------
		settings["EXTENSIONS"]["scrapy.extensions.throttle.AutoThrottle"] = 100
		# --------------------------------
		settings["AUTOTHROTTLE_ENABLED"           ] = self.__auto_throttle > 0
		settings["AUTOTHROTTLE_DEBUG"             ] = False
		settings["AUTOTHROTTLE_START_DELAY"       ] = self.__sleep
		settings["AUTOTHROTTLE_MAX_DELAY"         ] = self.__sleep + 30
		settings["AUTOTHROTTLE_TARGET_CONCURRENCY"] = self.__auto_throttle
		# --------------------------------
		settings["CONCURRENT_REQUESTS"           ] = self.__concurrent_requests
		settings["CONCURRENT_REQUESTS_PER_DOMAIN"] = self.__concurrent_requests_domain
		settings["RETRY_ENABLED"                 ] = self.__retries > 0
		settings["RETRY_TIMES"                   ] = self.__retries
		settings["REDIRECT_ENABLED"              ] = self.__max_redirects > 0
		settings["REDIRECT_MAX_TIMES"            ] = self.__max_redirects
		settings["DEPTH_LIMIT"                   ] = self.__recursion if self.__recursion > NO_RECURSION else 1
		# --------------------------------
		settings["ROBOTSTXT_OBEY"                      ] = False
		settings["TELNETCONSOLE_ENABLED"               ] = False
		settings["LOG_ENABLED"                         ] = False
		settings["REQUEST_FINGERPRINTER_IMPLEMENTATION"] = "2.7"
		# --------------------------------
		if self.__playwright:
			settings["DOWNLOAD_HANDLERS"]["https"] = "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler"
			settings["DOWNLOAD_HANDLERS"]["http" ] = "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler"
			settings["TWISTED_REACTOR"           ] = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
			settings["PLAYWRIGHT_LAUNCH_OPTIONS" ] = {
				"headless"     : self.__headless_browser,
				"handle_sigint": self.__handle_sigint,
				"proxy"        : {"server": self.__proxy} if self.__proxy else None
			}
			settings["PLAYWRIGHT_BROWSER_TYPE"              ] = self.__browser_type
			settings["PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT"] = self.__request_timeout * 1000
			settings["PLAYWRIGHT_ABORT_REQUEST"             ] = None if self.__screenshots else self.__page_block
		# --------------------------------
		scrapy_scraper_spider = scrapy.crawler.CrawlerProcess(settings)
		scrapy_scraper_spider.crawl(ScrapyScraperSpider, self.__urls, self.__whitelist, self.__playwright, self.__playwright_wait, self.__recursion, self.__headers, self.__cookies, self.__user_agents, self.__proxy, self.__downloads, self.__screenshots, self.__out, self.__debug)
		scrapy_scraper_spider.start()
		scrapy_scraper_spider.join()
