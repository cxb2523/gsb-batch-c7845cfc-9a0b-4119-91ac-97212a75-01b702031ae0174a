import aiohttp
import os
from .exporter import MarkdownExporter


class CrawlerEngine:
	def __init__(self, scrapers, config):
		self.scrapers = scrapers
		self.config = config
		self.exporter = MarkdownExporter(config)

	async def run(self):
		# 创建统一的 Session，复用 TCP 连接
		async with aiohttp.ClientSession() as session:
			all_papers = []
			global_stats = {}
			source_statuses = []
			years = self.config.get('years', [])

			for scraper in self.scrapers:
				name = scraper.conference_name
				print(f"--- Launching {name} Scraper ---")
				try:
					papers, stats = await scraper.run(session)
				except Exception as e:
					# 隔离单个来源的失败：记录来源与错误，继续后续来源
					error_msg = f"{type(e).__name__}: {e}"
					print(f"!!! {name} scraper failed: {error_msg}")
					source_statuses.append({"name": name, "ok": False, "error": error_msg})
					# 失败来源统计置零，不伪造数据
					global_stats[name] = {year: {"scanned": 0, "found": 0} for year in years}
					continue
				all_papers.extend(papers)
				global_stats[name] = stats
				source_statuses.append({"name": name, "ok": True, "error": None})

			# 无论成功与否，全部来源结束后仍导出一次报告
			self.exporter.save(all_papers, global_stats, source_statuses)
