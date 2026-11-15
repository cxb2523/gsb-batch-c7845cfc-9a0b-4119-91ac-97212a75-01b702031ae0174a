#-*- coding:utf-8 -*-
#主程序
#尽量把阻塞式运行改成分段式运行，怕后人看不懂
from sys import argv
import urllib,urllib2
import time
import json
import thread

import submmit_comment
import get_floor
import login_cookie
import up_dynamic
#import check_cookie

#读取配置文件
usage = 0
up_uid =''
username = ''
password = ''
av_number = ''
floor = 0
comment = ''
get_refresh = 0
submmit_refresh = 0
cookie = ''
floor_enabled = 0
thread_total = 5
#连续查楼失败上限：缺省/为0表示不限制，保持原来“一直轮询”的行为
max_get_failures = 0

#json版配置文件
try :
    json_file = open('config.json','r')
    json_conf = json.load(json_file)
    usage = json_conf['usage'] #int
    username = json_conf['username'] #str
    password = json_conf['password'] #str
    up_uid = json_conf['sofa_mode']['up_uid'] #str
    av_number = json_conf['floor_mode']['av_number'] #str
    floor = json_conf['floor_mode']['floor'] #int
    comment = json_conf['comment'] #str
    get_refresh = json_conf['get_refresh'] #int
    submmit_refresh = json_conf['submmit_refresh'] #int
    cookie = json_conf['cookie'] #str
    floor_enabled = json_conf['multithread']['floor_enabled'] #int
    thread_total = json_conf['multithread']['thread'] #int
    #可选配置：连续查楼失败多少次后停止轮询；缺省/非法/<=0 均视为不限制
    try :
        max_get_failures = int(json_conf['max_get_failures'])
        if max_get_failures < 0 :
            max_get_failures = 0
    except (KeyError, TypeError, ValueError) :
        max_get_failures = 0
except KeyError :
    pass

get_sleep_microsecond = float(get_refresh)/1000 #这个可以不用
submmit_sleep_microsecond = float(submmit_refresh)/1000
#cookie或者user/password必须有一个
if not (cookie or (username and password)) :
    raise RuntimeError('No cookie or username.')
if not cookie :
    cookie = login_cookie.get_cookie(login_cookie.get_access_key_kaaass(username,password))

#检测conf有效性
if comment :
    current_floor = get_floor.get_floor(av_number,0)
    if current_floor == get_floor.GET_FLOOR_FAILED :
        raise RuntimeError('Failed to get floors.')
    if floor <= int(current_floor) :
        raise RuntimeError('The floor has been taken.')
    #检测cookie可用性，给av11259766发一条评论试试
    if submmit_comment.submmit_comment('11259766','日常打卡',cookie,'2')[0] != 's' : #发送失败
        raise RuntimeError('Cookie may be not available or need to submmit CAPTCHA.')

#这段比较丑陋，以后会改的吧
#抢沙发模式，单线程
def usage1() :
#if usage == 1 :
    if not up_uid :
        raise RuntimeError('config file error.')
    #不断刷新up主动态，检测aid变化，变化的时候提交评论
    aid = up_dynamic.up_dynamic(up_uid,8)
    while aid == up_dynamic.up_dynamic(up_uid,8) :
        time.sleep(get_sleep_microsecond)
    submmit_status,floor_res = submmit_comment.submmit_comment(av_number,comment,cookie,2)
    if submmit_status == 's' :
        if floor_res == '1' :
            print 'Done.Good luck.'
        else :
            print 'I am sorry.'
    else :
        raise RuntimeError('failed to submmit.')
    raw_input()
    quit

#抢楼模式
#查询楼层（多线程，未测试，不要使用）
global fin_flag #停止信号，造轮子了，python signal不会用
fin_flag = 0
#连续查楼失败计数：拿到失败标记 +1，成功读到楼层清零
global fail_count
fail_count = 0
#轮询停止原因：'success'=楼层条件达成，'fail_limit'=连续失败达到上限
global stop_reason
stop_reason = ''
#保护 fail_count / fin_flag / stop_reason 的锁，多线程模式下所有工作线程共用
state_lock = thread.allocate_lock()

def reset_get_failure_state() :
    '''每轮抢楼开始前重置失败计数和停止信号，单线程/多线程都调用'''
    global fail_count, fin_flag, stop_reason
    state_lock.acquire()
    try :
        fail_count = 0
        fin_flag = 0
        stop_reason = ''
    finally :
        state_lock.release()

def is_failure_limit_reached() :
    state_lock.acquire()
    try :
        return max_get_failures > 0 and fail_count >= max_get_failures
    finally :
        state_lock.release()

def get_stop_reason() :
    state_lock.acquire()
    try :
        return stop_reason
    finally :
        state_lock.release()

def poll_floor_once(av_number) :
    '''查一次楼，返回 (current_floor, query_failed)。
    成功：current_floor 为 int 楼层，query_failed 为 False，连续失败计数清零；
    失败（get_floor 返回 GET_FLOOR_FAILED 标记）：current_floor 为 None，
    query_failed 为 True，连续失败计数 +1；达到配置上限时置停止信号，
    但本函数不抛异常，由调用方决定跳过本轮还是结束轮询。'''
    global fail_count, fin_flag, stop_reason
    current_floor = get_floor.get_floor(av_number , 0)#读取楼层
    state_lock.acquire()
    try :
        if current_floor == get_floor.GET_FLOOR_FAILED :
            fail_count += 1
            cur_fail = fail_count
            if max_get_failures > 0 and fail_count >= max_get_failures :
                fin_flag = 1
                stop_reason = 'fail_limit'
        else :
            fail_count = 0
            cur_fail = 0
    finally :
        state_lock.release()
    if current_floor == get_floor.GET_FLOOR_FAILED :
        if max_get_failures > 0 :
            print '[warn] get_floor failed, consecutive failures: %d/%d' % (cur_fail, max_get_failures)
        else :
            print '[warn] get_floor failed, consecutive failures: %d (no limit configured, keep polling)' % cur_fail
        return None, True
    return int(current_floor), False

#查询楼层（多线程，未测试，不要使用）
def floor_st(av_number , floor , thread_number , lock) :
    global fin_flag
    global stop_reason
    #global total_query
    while True :
        state_lock.acquire()
        stop = fin_flag
        state_lock.release()
        if stop == 1 :#结束标记
            lock.release()
            thread.exit()
        try :
            current_floor , query_failed = poll_floor_once(av_number)#读取楼层
        except Exception , exc :
            #兜底：任何意外异常都不能让工作线程静默死掉（否则锁不释放，主线程会死等）
            print '[warn] floor thread %d got unexpected error: %s, keep polling' % (thread_number , exc)
            time.sleep(get_sleep_microsecond)
            continue
        #print current_floor + '[' + str(thread_number) + ']'
        #total_query += 1
        if query_failed :
            #单次查询失败：跳过本次判断，按 get_refresh 间隔继续轮询，
            #工作线程不退出、不释放锁，主线程也不会死等
            time.sleep(get_sleep_microsecond)
            continue
        if (floor - 4) >= current_floor :
            state_lock.acquire()
            try :
                fin_flag = 1
                if stop_reason == '' :
                    stop_reason = 'success'
            finally :
                state_lock.release()
            lock.release() #释放锁，主线程同步使用
            thread.exit()
        time.sleep(get_sleep_microsecond)

def floor_cycle(av_number , floor , thread_total) : #thread_total=使用的线程数，5左右即可，太快会被反爬
    locks = [] #存储所有锁，数量=线程数
    reset_get_failure_state()
    for tn in range(thread_total) :
        lock = thread.allocate_lock() #acquire上锁，release解锁
        lock.acquire()
        locks.append(lock)
        thread.start_new_thread(floor_st , (av_number , floor , tn , locks[tn] ,)) #产生线程
    #begin_time = time.time()
    while True :
        #print total_query
        time.sleep(0.1) #自己调
        #if total_query >= 3000 :
            #fin_flag = 1
            #break
        state_lock.acquire()
        stop = fin_flag
        state_lock.release()
        if stop == 1 :
            break
    #total_time = str(time.time()-begin_time)
    #time.sleep(1)
    #print 'total time is ' + total_time+ 's'
    #begin_time = time.time()
    #for tn in range(thread_total) : #等待子线程结束，这样不会报错，感觉不用了，吧，最后再写了
        #while locks[tn].locked() :
            #fin_flag = 1
    #print 'wait for thread using ' + str(time.time() - begin_time) + 's'
    #阻塞结束
    return locks #返回所有锁，用于后期等待结束，避免报错
def usage01() :#抢楼，多线程
    global fin_flag
    floor_result = []#接收评论后的返回
    if not (av_number and floor) :
        raise RuntimeError('config file error.')
    first_floor , first_failed = poll_floor_once(av_number)
    if not first_failed and first_floor > floor :
        raise RuntimeError('The floor has been taken.')
    #首次查询失败也直接进入轮询，由连续失败上限决定最终结局
    #查楼阻塞
    locks = floor_cycle(av_number , floor , thread_total)
    if get_stop_reason() == 'fail_limit' :
        #连续失败达到上限：打印可区分的失败结局，不再进入提交阶段，
        #也绝不能误报“I am sorry”或“Done.Good luck.”
        state_lock.acquire()
        try :
            final_fail = fail_count
        finally :
            state_lock.release()
        print '[failed] floor polling aborted: get_floor failed %d consecutive times (limit %d). Network or floor API unavailable.' % (final_fail , max_get_failures)
    else :
        #提交评论，很抱歉，这是单线程模式，以后改
        for times in range(4) : #提交4次
            floor_result.append(submmit_comment.submmit_comment(av_number,comment,cookie,2)[1])
            #print times
            time.sleep(submmit_sleep_microsecond)
        #检测结果
        for floor_range in floor_result :
            if str(floor_range) == str(floor) :
                print 'Done.Good luck.'
                raw_input()
                times = 9982 #9982是随便写的记号
                break
            times -= 1
        if times != 9982 :
            print 'I am sorry.'
            raw_input()
    #等待查楼子线程退出
    for tn in range(thread_total) : #等待子线程结束，这样不会报错，感觉不用了，吧，最后再写了
        while locks[tn].locked() :
            state_lock.acquire()
            fin_flag = 1
            state_lock.release()
            time.sleep(0.01)

def usage00() : #抢楼，单线程
#if usage == 0 :
    #查询楼层，传统阻塞模式，挺慢的
    floor_result = []#接收评论后的返回
    if not (av_number and floor) :
        raise RuntimeError('config file error.')
    reset_get_failure_state()
    first_query_done = False
    while True :
        current_floor , query_failed = poll_floor_once(av_number)
        if query_failed :
            #单次查询失败：跳过本次判断，按 get_refresh 间隔继续轮询
            if is_failure_limit_reached() :
                #连续失败达到上限：可区分的失败结局，而不是误报另外两种结局
                print '[failed] floor polling aborted: get_floor failed %d consecutive times (limit %d). Network or floor API unavailable.' % (fail_count , max_get_failures)
                return
            time.sleep(get_sleep_microsecond)
            continue
        if not first_query_done :
            first_query_done = True
            if current_floor > floor :
                raise RuntimeError('The floor has been taken.')
        if current_floor < floor - 4 : #查楼阻塞
            time.sleep(get_sleep_microsecond)
            continue
        break
    for times in range(4) : #提交4次
        floor_result.append(submmit_comment.submmit_comment(av_number,comment,cookie,2)[1])
        #print times
        time.sleep(submmit_sleep_microsecond)
    #floor_result = [ submmit_comment.submmit_comment(av_number,comment,cookie,2)[1] for ans in range(4) : time.sleep(submmit_sleep_microsecond) ]
    '''
    while times >=1 :
        floor_result[times] = submmit_comment.submmit_comment(av_number,comment,cookie,2)[1]
        time.sleep(submmit_sleep_microsecond)
        times -= 1
    '''
    #while times >=1 :
    #检测提交结果
    for floor_range in floor_result :
        if str(floor_range) == str(floor) :
            print 'Done.Good luck.'
            raw_input()
            times = 9982
            break
        times -= 1
    if times != 9982 :
        print 'I am sorry.'
        raw_input()

if __name__ == '__main__' :
    if usage == 1 :
        usage1()#抢沙发
    elif usage == 0 :
        if floor_enabled == 0 :
            usage00()
        elif floor_enabled == 1 :
            usage01()
