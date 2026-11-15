#coding=utf-8
from functions import *
from emailsender import *
from email_infor import *
import requests
import time
import random
import os
import os.path
from datetime import datetime, timedelta


# add header for the crawler
headers = {'User-Agent':'Mozilla/5.0 (Windows; U; Windows NT 6.1; en-US; rv:1.9.1.6) Gecko/20091201 Firefox/3.5.6'}

# Add in your search list!
search_list = ["所罗门群岛", "斯洛伐克", "贝宁", "圣多美和普林西比", "埃及", "中非", "冈比亚", "以色列", "科特迪瓦", "佛得角", "亚美尼亚", "波斯尼亚", "阿尔巴尼亚",
               "比利时", "马来西亚", "伊拉克", "苏里南", "津巴布韦", "伊朗", "布隆迪", "巴勒斯坦", "秘鲁", "立陶宛", "几内亚比绍", "智利", "新加坡", "卡塔尔", "利比亚",
               "萨摩亚", "墨西哥", "朝鲜", "缅甸", "柬埔寨", "英国", "巴西", "阿富汗", "日本", "格鲁吉亚", "巴基斯坦", "爱沙尼亚", "孟加拉", "毛里塔尼亚", "马尔代夫",
               "匈牙利", "沙特", "尼日尔", "拉脱维亚", "文莱", "哈萨克斯坦", "波兰", "安道尔", "卢森堡", "塞拉利昂", "阿曼", "台湾", "印度", "毛里求斯", "斯洛文尼亚",
               "韩国", "古巴", "希腊", "蒙古", "纳米比亚", "乍得", "摩纳哥", "埃塞俄比亚", "丹麦", "挪威", "哥伦比亚", "格林纳达", "摩洛哥", "德国", "斯里兰卡", "苏丹",
               "汤加", "澳大利亚", "新西兰", "叙利亚", "突尼斯", "刚果金", "阿根廷", "阿尔及利亚", "南非", "奥地利", "乌干达", "特立尼达和多巴哥", "喀麦隆", "塞舌尔",
               "葡萄牙", "保加利亚", "不丹", "东帝汶", "乌拉圭", "委内瑞拉", "瑞士", "玻利维亚", "西班牙", "摩尔多瓦", "加纳", "土库曼斯坦", "圭亚那", "吉尔吉斯",
               "坦桑尼亚", "尼日利亚", "塔吉克斯坦", "乌兹别克斯坦", "阿联酋", "马里", "瑞典", "白俄罗斯", "多哥", "法国", "罗马尼亚", "圣卢西亚", "俄罗斯", "赞比亚",
               "加蓬", "科威特", "卢旺达", "几内亚", "塞内加尔", "赤道几内亚", "泰国", "瑙鲁", "厄瓜多尔", "老挝", "荷兰", "马耳他", "越南", "尼泊尔", "博茨瓦纳",
               "利比里亚", "约旦", "多米尼克", "爱尔兰", "也门", "安哥拉", "吉布提", "巴林", "瓦努阿图", "土耳其", "美国", "刚果布", "塞浦路斯", "冰岛", "莱索托",
               "巴哈马", "意大利", "菲律宾", "索马里", "印尼", "阿塞拜疆", "肯尼亚", "巴巴多斯", "牙买加", "塞尔维亚", "列支敦士登", "密克罗尼西亚", "马其顿", "新几内亚",
               "黎巴嫩", "斐济", "莫桑比克", "厄立特里亚", "圣马力诺", "布基纳法索", "捷克", "芬兰", "科摩罗", "克罗地亚", "加拿大", "安提瓜和巴布达", "马达加斯加",
               "乌克兰", "图瓦卢", "圣文森特和格林纳丁斯", "多米尼加", "哥斯达黎加", "基里巴斯", "斯威士兰", "巴拉圭", "帕劳", "马拉维", "萨尔瓦多", "尼加拉瓜", "海地",
               "南苏丹", "伯利兹", "危地马拉", "洪都拉斯", "黑山共和国", "圣基茨和尼维斯","梵蒂冈", "马绍尔群岛"]

# Create url encoded search list based on the word list you have gaven
urlencoded_search_list = url_encoding(search_list)
urls = create_url_list(urlencoded_search_list)


# A word is given up after this many consecutive page requests fail.
# Normal request failures still keep the old behaviour: count them, rest for
# an hour once the exception count passes six, sleep 50 seconds and try the
# next page.  Only when nothing has succeeded for many pages in a row do we
# stop the word, and that outcome is recorded instead of silently moving on.
MAX_CONSECUTIVE_REQUEST_FAILURES = 12


def _outcome_record(word, outcome, last_success_page, page_count):
    return {
        "word": word,
        "outcome": outcome,
        "last_page": last_success_page,
        "page_count": page_count,
    }


def build_daily_report(report_date, total_page_count, success_page_count,
                       failed_word_count, no_data_word_count,
                       start_time, end_time, word_outcomes):
    """Build the daily log/email body.

    The original fields (total page, start/end time, duration) are kept and
    followed by the successful page total, the failed word count, the
    no-data word count and one line per word: word, outcome, last successful
    page number.  "Failed" words are those abandoned after request errors or
    a write failure; "no data" means the word genuinely had no result.
    """
    daily_report = (
        report_date + ", Total page: " + str(total_page_count) +
        ", Starttime: " + str(start_time) +
        ", Endtime: " + str(end_time) +
        ", During: " + str(end_time - start_time) + "\n" +
        "Success page: " + str(success_page_count) +
        ", Failed word: " + str(failed_word_count) +
        ", No data word: " + str(no_data_word_count) + "\n" +
        "Per-word outcome (word\toutcome\tlast success page):\n"
    )
    for item in word_outcomes:
        daily_report += (item["word"] + "\t" + item["outcome"] + "\t" +
                         str(item["last_page"]) + "\n")
    return daily_report


def run_daily_mission(day_count=0, data_root="../WBTestdata/", logfile_path="mylog.txt",
                      word_list=None, word_urls=None,
                      clock=None, sleep_fn=None, request_fn=None,
                      write_fn=None, mail_fn=None,
                      page_cap=300,
                      consecutive_failure_limit=MAX_CONSECUTIVE_REQUEST_FAILURES):
    """Run one full day's crawl and write the daily report.

    The keyword arguments are seams for local verification; production calls
    only use the defaults.  The function returns a summary dict with the
    report text and the per-word outcomes.
    """
    if clock is None:
        clock = datetime.now
    if sleep_fn is None:
        sleep_fn = time.sleep
    if request_fn is None:
        request_fn = requests.get
    if write_fn is None:
        write_fn = write_page_file
    if mail_fn is None:
        mail_fn = send_email
    if word_list is None:
        word_list = search_list
    if word_urls is None:
        word_urls = urls

    # you can personalize your own report email here
    # send an email to you when everyday's mission start
    start_title = "Program start!"
    start_report = str(clock())
    mail_fn(user, pwd, recipient, start_title, start_report)

    # define folder path
    now = clock()
    yesterday_folder = os.path.join(data_root, (now - timedelta(days=1)).strftime("%m-%d"))
    today_folder = os.path.join(data_root, now.strftime("%m-%d"))

    # Testify if today's folder exist, if not, create
    if not os.path.exists(today_folder):
        print "Today's first run! Create new folder."
        ensure_folder(today_folder)

    # start mission, set 0, print out mission information: start time, date of today, how many days this program has run
    word_count = 0
    total_page_count = 0
    today_start_time = now
    print "Start time: ", today_start_time
    print "Today is: " + today_start_time.strftime("%m-%d")
    print "daycount: ", day_count

    # Print out welcome message
    if not os.path.exists(yesterday_folder):
        print "No data from yesterday, the first run!"
    else:
        print "Detect ysterday's data! Welcome back!"

    word_outcomes = []

    # loop for every word in the list
    for country in range(len(word_list)):
        word_count += 1
        unico_this_word = word_list[country]  # get current country in UNICODE
        this_baseurl = word_urls[country]  # create base url: without page number
        initial_page_number = 1  # define start page
        str_initial_page_number = str(initial_page_number)
        exception_count = 0  # exception count, kept across the whole word
        consecutive_request_failures = 0
        last_success_page = 0
        this_word_page_count = 0
        outcome = OUTCOME_COMPLETED

        # loop for adding page number and requesting them in succession
        # The date below all follow format [04-02], check functions.py for more information
        for i in range(initial_page_number, initial_page_number + page_cap):  # no more than 300 pages per word
            this_page_number = str(i)
            this_url = this_baseurl + this_page_number  # generate current url with current page number
            print "This is word number", word_count, ", the word is ", unico_this_word, ", this is page ", this_page_number

            # Get and write down JSON data into txt file with structured file name
            try:
                req = request_fn(this_url, headers=headers, timeout=4)
                content = req.content
                if content is None or len(content) == 0:
                    # Never store an empty body or count it as a good page.
                    raise EmptyContentError("empty response body")

                # Read the page's end time before writing, so an unusable
                # response is not saved as if it were a complete page.
                this_end_time = get_this_endtime_text(content)

                # Original behaviour: a page that was received counts here.
                # The stricter success total below only counts written pages.
                total_page_count += 1

                # Write the page, retrying once.  The folder and file name use
                # the actual write time of each attempt, so pages written after
                # midnight go into the new day's folder.
                write_ok = False
                for attempt in range(1, 3):
                    try:
                        this_file_path = write_fn(data_root, unico_this_word, i, content,
                                                  now=clock())
                        write_ok = True
                        break
                    except Exception, e:
                        print "Writing page failed (attempt " + str(attempt) + "): ", e

                if not write_ok:
                    print "Can not write this page after retry, give up this word: ", unico_this_word
                    outcome = OUTCOME_WRITE_FAILED
                    break

                print "page written: ", this_file_path
                this_word_page_count += 1
                last_success_page = i
                consecutive_request_failures = 0

                # Stop this word once the last weibo on the page is older than
                # the day two days ago.  The boundary is calculated at check
                # time, not at program start, so a run across midnight does not
                # compare against the wrong day.
                end_date = days_ago_text(2, clock())
                if end_date > this_end_time:
                    print "current endtime: ", this_end_time
                    print "We reached 2 days ago's data! Now sleep a while and call for next country!"
                    outcome = OUTCOME_COMPLETED
                    break
                else:
                    print "current page's endtime: ", this_end_time
                    print "Not enough! Sleep a while and continue requesting for next page!"
                    sleep_fn(random.randint(2, 8))

            # This situation happens when there is no weibo exist for the word your try to search
            # Pleas double check on the web page
            except NoDataError:
                print "There is no data! to the next country!"
                if last_success_page == 0:
                    outcome = OUTCOME_NO_DATA
                else:
                    # Results ran out after some good pages: the word was
                    # crawled to the end of what the search offers.
                    outcome = OUTCOME_COMPLETED
                break

            # Other than no-data, it could also be request rejecting due to high frequency
            # or an unusable/truncated response.
            except Exception, e:
                exception_count += 1
                consecutive_request_failures += 1
                if exception_count > 6:
                    print "ehhhhh! I have failed 5 times for this country, I got to stop really long! 1 hour!"

                    # you can personalize your own report email here
                    # send an email to you when exception received more than 6 time
                    error_title = "Error!"
                    error_report = str(clock())
                    mail_fn(user, pwd, recipient, error_title, error_report)

                    exception_count = 0  # Set exception count back to 0 and sleep for 1 hour
                    sleep_fn(600)

                if consecutive_request_failures >= consecutive_failure_limit:
                    print "Requests kept failing for this word, give up: ", unico_this_word
                    outcome = OUTCOME_REQUEST_ABANDONED
                    break

                print "request has been rejected or failed! Sleep 1 minutes and try next page!"
                print e
                sleep_fn(50)
                continue
        else:
            # The page cap was reached without a natural stop.  If not a
            # single page was written, the word never got usable data.
            if last_success_page == 0:
                outcome = OUTCOME_REQUEST_ABANDONED
            else:
                outcome = OUTCOME_COMPLETED

        print "Finish: " + unico_this_word + " sleep 10 seconds"
        word_outcomes.append(_outcome_record(unico_this_word, outcome,
                                            last_success_page, this_word_page_count))
        sleep_fn(10)

    # Print out today's mission information
    print "Finish today's work, sleep for tomorrow..."
    today_end_time = clock()
    sleep_time = sleep_how_long(24, today_start_time, today_end_time)
    print "Today's sleep time is: ", sleep_time
    print "Today start at: ", today_start_time
    print "Today end at: ", today_end_time
    print "Duration: ", today_end_time - today_start_time
    print "Total page: ", total_page_count

    success_page_count = sum(item["page_count"] for item in word_outcomes)
    failed_word_count = sum(1 for item in word_outcomes if item["outcome"] in FAILED_OUTCOMES)
    no_data_word_count = sum(1 for item in word_outcomes if item["outcome"] == OUTCOME_NO_DATA)

    # you can personalize your own report email here
    # send an email to you when everyday's mission finished
    daily_title = today_end_time.strftime("%m-%d") + "  Report"
    daily_report = build_daily_report(today_end_time.strftime("%m-%d"), total_page_count,
                                      success_page_count, failed_word_count,
                                      no_data_word_count,
                                      today_start_time, today_end_time, word_outcomes)
    #send_email(user, pwd, recipient, subject, body)
    mail_fn(user, pwd, recipient, daily_title, daily_report)

    # Create a txt file to save daily report
    logfile = logfile_path
    log_folder = os.path.dirname(logfile)
    if log_folder:
        ensure_folder(log_folder)
    # The report now contains Chinese word names; append_report stores it as
    # UTF-8 so mylog.txt reads the same regardless of the system locale.
    append_report(logfile, daily_report)
    print "Log output finished..."

    return {
        "total_page_count": total_page_count,
        "success_page_count": success_page_count,
        "failed_word_count": failed_word_count,
        "no_data_word_count": no_data_word_count,
        "word_outcomes": word_outcomes,
        "word_count": word_count,
        "start_time": today_start_time,
        "end_time": today_end_time,
        "sleep_time": sleep_time,
        "report": daily_report,
    }


def main():
    # loop for none stop run
    day_count = 0
    while 1:
        summary = run_daily_mission(day_count=day_count)
        day_count += 1
        time.sleep(summary["sleep_time"])


if __name__ == "__main__":
    main()
