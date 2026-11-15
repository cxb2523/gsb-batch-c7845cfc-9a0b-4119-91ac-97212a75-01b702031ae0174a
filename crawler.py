import os
import sys
import requests
from ruamel.yaml import YAML
from bs4 import BeautifulSoup

BASE_URL = 'https://stackoverflow.com/questions/tagged/c'
SORT = '?sort=votes'
PAGE = '&page='
PAGE_SIZE_URL = '&pageSize='

PAGE_SIZE = 15
NUM_ANSWERS = 3

STATUS_SUCCESS = 'success'
STATUS_FAILURE = 'failure'
STATUS_PARTIAL = 'partial'

STATUS_LABELS = {
	STATUS_SUCCESS: '成功',
	STATUS_FAILURE: '失败',
	STATUS_PARTIAL: '部分成功',
}

REASON_REQUEST_ERROR = '请求异常'
REASON_MISSING_QUESTION = '缺少题目正文'
REASON_MISSING_ANSWERS = '回答结构缺失'
REASON_PARSE_ERROR = '解析异常'
REASON_MISSING_QUESTION_LIST = '缺少题目列表'

headers = {
	'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/53.0.2785.143 Safari/537.36'
}

def emit(text=''):
	"""Print a line without ever letting console encoding kill the crawl."""
	try:
		print(text)
	except UnicodeEncodeError:
		encoding = getattr(sys.stdout, 'encoding', None) or 'ascii'
		print(text.encode(encoding, 'replace').decode(encoding, 'replace'))

def empty_counts():
	return {'total': 0, STATUS_SUCCESS: 0, STATUS_FAILURE: 0, STATUS_PARTIAL: 0}

def exception_detail(exc):
	name = type(exc).__name__
	message = str(exc).strip()
	if message:
		return name + ': ' + message
	return name

def make_outcome(status, title, url, question=None, answers=None, missing_answers=0, reason=None):
	return {
		'title': title,
		'url': url,
		'status': status,
		'question': question,
		'answers': answers if answers is not None else [],
		'missing_answers': missing_answers,
		'reason': reason,
	}

def make_failure_outcome(title, url, reason):
	return make_outcome(STATUS_FAILURE, title, url, reason=reason)

def extract_question_text(soup):
	"""Return the question body text, or None when the page layout is unexpected."""
	postcell = soup.find('div', class_='postcell')
	if postcell is None:
		return None
	body = postcell.find('div', class_='post-text')
	if body is not None:
		text = body.get_text()
		if text.strip():
			return text
	children = list(postcell)
	if len(children) > 1:
		text = children[1].get_text()
		if text.strip():
			return text
	return None

def extract_answers(soup):
	"""Return (expected_count, parsed_answers) for at most NUM_ANSWERS answers.

	expected_count is how many answers the page claims to expose (capped at
	NUM_ANSWERS); parsed_answers holds only those whose structure could be read.
	"""
	cells = soup.find_all('div', class_='answercell')
	expected = len(cells)
	if expected > NUM_ANSWERS:
		expected = NUM_ANSWERS
	answers = []
	for cell in cells[:NUM_ANSWERS]:
		post = cell.find('div', class_='post-text')
		if post is None:
			continue
		answers.append(post.get_text())
	return expected, answers

def parse_question(url, title):
	"""Crawl and parse a single question.

	Never raises for request/parse failures: every question gets an outcome
	dict describing success, partial success or failure so one bad question
	cannot stop the rest of the page.
	"""
	try:
		response = requests.get(url, headers=headers, timeout=10)
		status_code = getattr(response, 'status_code', None)
		if status_code is not None and status_code >= 400:
			return make_failure_outcome(
				title, url, REASON_REQUEST_ERROR + ': HTTP ' + str(status_code))
		soup = BeautifulSoup(response.text, 'html.parser')
	except (KeyboardInterrupt, EOFError, SystemExit):
		raise
	except requests.exceptions.RequestException as exc:
		return make_failure_outcome(
			title, url, REASON_REQUEST_ERROR + ': ' + exception_detail(exc))
	except Exception as exc:
		return make_failure_outcome(
			title, url, REASON_PARSE_ERROR + ': ' + exception_detail(exc))

	question = extract_question_text(soup)
	if question is None:
		return make_failure_outcome(title, url, REASON_MISSING_QUESTION)

	expected, answers = extract_answers(soup)
	missing = expected - len(answers)
	if expected > 0 and not answers:
		# The page advertises answers but none of them could be parsed.
		return make_failure_outcome(
			title, url,
			REASON_MISSING_ANSWERS + '（' + str(expected) + ' 条回答均无法解析）')
	if missing > 0:
		return make_outcome(
			STATUS_PARTIAL, title, url, question=question, answers=answers,
			missing_answers=missing,
			reason=REASON_MISSING_ANSWERS + ': 缺失 ' + str(missing) + ' 条回答')
	return make_outcome(
		STATUS_SUCCESS, title, url, question=question, answers=answers)

def print_outcome(outcome):
	emit("------------------------------")
	emit(outcome['title'])
	emit("link: " + outcome['url'])
	if outcome['status'] == STATUS_FAILURE:
		emit("[" + STATUS_LABELS[outcome['status']] + "] " + outcome['reason'])
		return
	emit(outcome['question'])
	emit("List of answers:\n\n")
	for answer in outcome['answers']:
		emit("===>")
		emit(answer)
	if outcome['status'] == STATUS_PARTIAL:
		emit("[部分成功] 缺失回答数量: " + str(outcome['missing_answers']))

def summarize(outcomes):
	counts = empty_counts()
	counts['total'] = len(outcomes)
	for outcome in outcomes:
		counts[outcome['status']] += 1
	return counts

def print_page_summary(page_result):
	counts = page_result['counts']
	emit()
	emit("====================")
	emit("第 " + str(page_result['page']) + " 页结局汇总: " + page_result['url'])
	if page_result['error']:
		emit("页面级错误: " + page_result['error'])
	emit("题目总数=" + str(counts['total'])
		+ "，成功=" + str(counts[STATUS_SUCCESS])
		+ "，失败=" + str(counts[STATUS_FAILURE])
		+ "，部分成功=" + str(counts[STATUS_PARTIAL])
		+ " (success=" + str(counts[STATUS_SUCCESS])
		+ ", failure=" + str(counts[STATUS_FAILURE])
		+ ", partial=" + str(counts[STATUS_PARTIAL]) + ")")
	for outcome in page_result['outcomes']:
		line = ("  [" + STATUS_LABELS[outcome['status']] + "] "
			+ outcome['title'] + " | " + outcome['url'])
		if outcome['status'] == STATUS_FAILURE:
			line += " | " + outcome['reason']
		elif outcome['status'] == STATUS_PARTIAL:
			line += (" | 缺失回答 " + str(outcome['missing_answers']) + " 条")
		emit(line)

def build_page_url(page_no):
	return BASE_URL + SORT + PAGE + str(page_no) + PAGE_SIZE_URL + str(PAGE_SIZE)

def crawl_page(page_no):
	"""Crawl one listing page and return its page result without raising."""
	page_url = build_page_url(page_no)
	result = {
		'page': page_no,
		'url': page_url,
		'outcomes': [],
		'counts': empty_counts(),
		'error': None,
	}
	emit('crawling page ' + str(page_no) + ': ' + page_url)
	try:
		response = requests.get(page_url, headers=headers, timeout=10)
		status_code = getattr(response, 'status_code', None)
		if status_code is not None and status_code >= 400:
			result['error'] = REASON_REQUEST_ERROR + ': HTTP ' + str(status_code)
			return result
		source_code = response.text
		soup = BeautifulSoup(source_code, 'html.parser')
	except (KeyboardInterrupt, EOFError, SystemExit):
		raise
	except requests.exceptions.RequestException as exc:
		result['error'] = REASON_REQUEST_ERROR + ': ' + exception_detail(exc)
		return result
	except Exception as exc:
		result['error'] = REASON_PARSE_ERROR + ': ' + exception_detail(exc)
		return result

	links = soup.find_all('a', {'class': 'question-hyperlink'})
	if not links:
		result['error'] = REASON_MISSING_QUESTION_LIST
		return result

	for link in links[:PAGE_SIZE]:
		href = link.get('href')
		title = link.get_text(strip=True) or '(无标题)'
		if not href:
			url = page_url
		elif href.startswith('http://') or href.startswith('https://'):
			url = href
		else:
			url = 'http://stackoverflow.com/' + href.lstrip('/')
		try:
			outcome = parse_question(url, title)
		except (KeyboardInterrupt, EOFError, SystemExit):
			raise
		except Exception as exc:
			# parse_question already isolates failures; this is a last-resort
			# guard so one bad question can never abort the page.
			outcome = make_failure_outcome(
				title, url, REASON_PARSE_ERROR + ': ' + exception_detail(exc))
		result['outcomes'].append(outcome)
		print_outcome(outcome)

	result['counts'] = summarize(result['outcomes'])
	return result

def crawl_pages(num_pages):
	"""Crawl num_pages listing pages; questions are isolated from each other.

	Returns a list of page results so callers can verify every outcome.
	"""
	start = 1
	current_page = start
	end = start + num_pages
	results = []
	while current_page != end:
		try:
			page_result = crawl_page(current_page)
		except (KeyboardInterrupt, EOFError, SystemExit):
			emit("\nStopped by user!")
			break
		results.append(page_result)
		print_page_summary(page_result)
		current_page += 1
	return results

def main():
	crawl_pages(2)
	emit('\nDone!')

if __name__ == '__main__':
	main()
