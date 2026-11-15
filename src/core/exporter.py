import os
import re
import time


class MarkdownExporter:
	def __init__(self, config):
		self.output_dir = config.get('output_dir', 'results')
		self.filename = config.get('output_filename', 'papers.md')
		self.keywords = config.get('keywords', [])
		self.years = config.get('years', [])
		self.conferences = config.get('conferences', [])
		self.journals = config.get('journals', [])
		self.journals_only = bool(self.journals) and not self.conferences

		if not os.path.exists(self.output_dir):
			os.makedirs(self.output_dir)

	@staticmethod
	def _normalize_title(title: str) -> str:
		"""规范化标题用于跨来源去重：小写、去标点、压缩空白"""
		return re.sub(r'[^a-z0-9]+', ' ', (title or '').lower()).strip()

	def _dedupe_papers(self, papers):
		"""按规范化标题去重，保留先出现的条目，返回 (去重后列表, 被去重条数)"""
		seen = set()
		unique = []
		duplicates = 0
		for paper in papers:
			key = self._normalize_title(paper.get('title', ''))
			if key and key in seen:
				duplicates += 1
				continue
			if key:
				seen.add(key)
			unique.append(paper)
		return unique, duplicates

	def _write_source_status(self, f, stats, source_statuses, duplicates_removed):
		"""在报告末尾写入“来源状态”小节"""
		f.write("\n## Source Status\n\n")
		if not source_statuses:
			f.write("No source status information available.\n")
			return

		if not any(s['ok'] for s in source_statuses):
			f.write("⚠️ **All sources failed.** No papers were collected in this run.\n\n")

		for s in source_statuses:
			name = s['name']
			if s['ok']:
				conf_stats = stats.get(name, {})
				scanned = sum(d.get('scanned', 0) for d in conf_stats.values())
				found = sum(d.get('found', 0) for d in conf_stats.values())
				f.write(f"- ✅ **{name}**: success — scanned {scanned}, found {found}.\n")
			else:
				f.write(f"- ❌ **{name}**: failed — scanned 0, found 0. Reason: {s['error']}\n")

		f.write(f"\n**Duplicates removed (normalized title):** {duplicates_removed}\n")

	def save(self, papers, stats, source_statuses=None):
		def _slug(text: str) -> str:
			return "-".join(text.lower().split()) if text else "all"

		keywords_folder = _slug(" ".join(self.keywords))

		# 跨来源按规范化标题去重，保留先出现的条目
		papers, duplicates_removed = self._dedupe_papers(papers)

		# 按会议与年份分组输出
		grouped = {}
		for paper in papers:
			key = (paper['source'], paper['year'])
			grouped.setdefault(key, []).append(paper)

		# 所有来源都失败（或没有任何论文）时，仍生成一份汇总报告，不静默退出
		if not grouped:
			folder = os.path.join(self.output_dir, keywords_folder)
			os.makedirs(folder, exist_ok=True)
			filepath = os.path.join(folder, self.filename)
			with open(filepath, "w", encoding="utf-8") as f:
				f.write("# Paper-Tunneling Report\n")
				f.write(f"**Generated on:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
				f.write(f"**Keywords:** {', '.join(self.keywords)}\n")
				f.write(f"**Years:** {', '.join(str(y) for y in self.years)}\n\n")
				f.write("⚠️ **No papers were collected in this run.** "
						"All sources failed or returned no matching papers.\n")
				self._write_source_status(f, stats, source_statuses, duplicates_removed)
			print(f"📄 Report saved to: {filepath}")
			return

		for (conf, year), items in grouped.items():
			items.sort(key=lambda x: x['title'])
			folder = os.path.join(self.output_dir, keywords_folder, _slug(conf), str(year))
			os.makedirs(folder, exist_ok=True)
			filename = self.filename
			if len(self.years) != 1:
				year_token = str(year) if year else "all"
				filename = f"{_slug(conf)}_{keywords_folder}_{year_token}.md"
			filepath = os.path.join(folder, filename)

			with open(filepath, "w", encoding="utf-8") as f:
				f.write("# Paper-Tunneling Report\n")
				f.write(f"**Generated on:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
				f.write(f"**Keywords:** {', '.join(self.keywords)}\n")
				f.write(f"**Years:** {year}\n")
				label = "Journals" if self.journals_only else "Conferences"
				f.write(f"**{label}:** {_slug(conf)}\n\n")

				f.write(f"## {conf} {year}\n\n")
				for paper in items:
					f.write(f"### [{paper['title']}]({paper['url']})\n")
					f.write(f"**Authors:** {paper['authors']}\n\n")
					f.write(f"**Abstract:**\n{paper['abstract']}\n\n")
					f.write("---\n\n")

				f.write("\n### Statistics\n")
				conf_stats = stats.get(conf, {})
				data = conf_stats.get(year)
				if data:
					f.write(f"[{conf} {year}]: Scanned {data['scanned']} papers, {data['found']} found matching keywords.\n")

				self._write_source_status(f, stats, source_statuses, duplicates_removed)

			print(f"📄 Report saved to: {filepath}")
