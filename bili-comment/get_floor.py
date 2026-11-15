# -*- coding: utf-8 -*-
#读取当前楼层，需要 av号
import urllib,urllib2
import requests
import json
import time
from sys import argv

#查楼失败标记：请求异常、状态码非200、响应里解析不出楼层时统一返回它，而不是向上抛异常
#调用方（bili-comment.py 的 usage00/usage01）识别到该标记后跳过本轮判断，
#按 get_refresh 间隔继续轮询并累计连续失败次数
GET_FLOOR_FAILED = 'e'

#大概不需要
headerdata = {'Host':'api.bilibili.com',
                        'Connection':'keep-alive',
                        'Cache-Control': 'max-age=0',
                        #'Accept': 'application/json, text/javascript, */*; q=0.01',
                        'Upgrade-Insecure-Requests': '1',
                        'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/64.0.3282.186 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'Accept-Language': 'zh-CN,zh;q=0.9'#,
                        #'Cookie': user_cookie #可以不需要
                        }

#rpid = 0:读取最高楼层
#rpid != 0:读取所在楼层，如果找不到则返回 GET_FLOOR_FAILED
#任何请求异常、非200状态码、响应无法解析的情况也返回 GET_FLOOR_FAILED，不抛未处理异常
#rpid为int，成功返回str，失败返回 GET_FLOOR_FAILED
def get_floor(av_number,rpid) :
    #这里应该可以用https，不过查楼层没必要了
    av_url = 'http://api.bilibili.com/x/reply?oid=' + av_number + '&type=1&pn=1'
    '''#这一段有玄学问题，改用requests
    req = urllib2.Request(av_url,None)
    get_floor_res = urllib2.urlopen(req,timeout = 20)
    if get_floor_res.code != 200 :
        raise RuntimeError('Errorcode:' + str(get_floor_res.code))
    response_floor = get_floor_res.read()
    '''
    #请求异常（超时、连接失败、DNS错误等）不再向上抛，统一转成失败标记
    try :
        get_floor_res = requests.get(av_url,headers = headerdata,timeout = 20)
    except requests.exceptions.RequestException :
        return GET_FLOOR_FAILED
    #状态码非200同样视为本次查询失败，而不是抛异常终止整场抢楼
    if get_floor_res.status_code != 200 :
        return GET_FLOOR_FAILED
    response_floor = get_floor_res.content
    if rpid == 0 :
        key1 = response_floor.find('page')
        if key1 == -1 : #响应结构异常，连page字段都找不到，无法解析楼层
            return GET_FLOOR_FAILED
        key2 = response_floor.find('floor',key1)
        if key2 ==-1 : #连个评论都没有
            return '0'
        key3 = response_floor.find(',',key2)
        if key3 == -1 : #楼层字段存在但内容不完整，无法解析
            return GET_FLOOR_FAILED
        floor_str = response_floor[key2+7 : key3] #手写json解析，呵呵呵，效率还行
        if not floor_str.isdigit() : #解析出来的楼层不是数字，视为解析失败
            return GET_FLOOR_FAILED
        return floor_str
        #json_data = json.loads(response_floor)
        #return json_data['data']['page']['count']
        #return json_data['data']['replies']['floor']
    else :
        key4 = response_floor.find(str(rpid)) #数据太多，用json得到明年了，只爬当前页，20个评论，如果太热门不一定找得到
        if key4 != -1 :
            response_floor.find(str(rpid),key4)
            key5 = response_floor.rfind('floor',0,key4)
            if key5 == -1 : #找到了rpid却解析不出楼层，按失败处理
                return GET_FLOOR_FAILED
            key6 = response_floor.find(',',key5 + 7)
            if key6 == -1 :
                return GET_FLOOR_FAILED
            return response_floor[key5 + 7 : key6]
        else :
            return GET_FLOOR_FAILED

if __name__ == '__main__' :
    #print get_floor('11259766',0)
    #print get_floor('810872',0)
    tn = 0
    while True :
        print (get_floor('810872',0) + ':') , str(tn)
        tn += 1
