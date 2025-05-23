#!/usr/bin/python3

#
# Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
#

#
# mockredis
#
# This module helps start and stop redis instances for unit-testing
# redis must be pre-installed for this to work
#

import os
import subprocess
import logging
import socket
import time
import redis

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

redis_ver = '2.6.13'
redis_bdir = '/tmp/cache-systemless_test'
redis_url = redis_bdir + '/redis-'+redis_ver+'.tar.gz'
redis_exe = redis_bdir + '/bin/redis-server'

def install_redis():
    if not os.path.exists(redis_url):
        if not os.path.exists(redis_bdir):
            output,_ = call_command_("mkdir " + redis_bdir)
        process = subprocess.Popen(['wget', '-P', redis_bdir,
                                    'https://github.com/OpenSDN-io/tf-third-party-cache/raw/master/redis/redis-'\
                                    + redis_ver + '.tar.gz'],
                                   cwd=redis_bdir)
        process.wait()
        if process.returncode is not 0:
            raise SystemError('wget '+redis_url)
    if not os.path.exists(redis_bdir + '/redis-'+redis_ver):
        process = subprocess.Popen(['tar', 'xzvf', redis_url],
                                   cwd=redis_bdir)
        process.wait()
        if process.returncode is not 0:
            raise SystemError('untar '+redis_url)
    if not os.path.exists(redis_exe):
        process = subprocess.Popen(['make', 'PREFIX=' + redis_bdir, 'MALLOC=libc', 'install'],
                                   cwd=redis_bdir + '/redis-'+redis_ver)
        process.wait()
        if process.returncode is not 0:
            raise SystemError('install '+redis_url)

def get_redis_path():
    if not os.path.exists(redis_exe):
        install_redis()
    return redis_exe

def redis_version():
    '''
    Determine redis-server version
    '''
    return 2.6
'''
    command = "redis-server --version"
    logging.info('redis_version call 1')
    process = subprocess.Popen(command.split(' '), stdout=subprocess.PIPE)
    logging.info('redis_version call 2')
    output, _ = process.communicate()
    if "v=2.6" in output[0]:
        return 2.6
    else:
        return 2.4
'''


def start_redis(port, password=None):
    '''
    Client uses this function to start an instance of redis
    Arguments:
        cport : An unused TCP port for redis to use as the client port
    '''
    exe = get_redis_path()
    version = redis_version()
    if version == 2.6:
        redis_conf = "redis.26.conf"
    else:
        redis_conf = "redis.24.conf"

    conftemplate = os.path.dirname(os.path.abspath(__file__)) + "/" +\
        redis_conf
    redisbase = "/tmp/redis.%s.%d/" % (os.getenv('USER', 'None'), port)
    output, _ = call_command_("rm -rf " + redisbase)
    output, _ = call_command_("mkdir " + redisbase)
    output, _ = call_command_("mkdir " + redisbase + "cache")
    logging.info('Redis Port %d' % port)

    output, _ = call_command_("cp " + conftemplate + " " + redisbase +
                              redis_conf)
    replace_string_(redisbase + redis_conf,
                    [("/var/run/redis_6379.pid", redisbase + "pid"),
                     ("port 6379", "port " + str(port)),
                     ("/var/log/redis_6379.log", redisbase + "log"),
                     ("/var/lib/redis/6379", redisbase + "cache")])
    if password:
       replace_string_(redisbase + redis_conf,[("# requirepass foobared","requirepass " + password)])
    command = exe + " " + redisbase + redis_conf
    subprocess.Popen(command.split(' '),
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    r = redis.StrictRedis(host='localhost', port=port, db=0, password=password)
    done = False
    start_wait = os.getenv('CONTRIAL_ANALYTICS_TEST_MAX_START_WAIT_TIME', 15)
    cnt = 0
    while not done:
        try:
            r.ping()
        except:
            cnt += 1
            if cnt > start_wait:
                logging.info('Redis Failed. Logs below: ')
                with open(redisbase + "log", 'r') as fin:
                    logging.info(fin.read())
                return False
            logging.info('Redis not ready')
            time.sleep(1)
        else:
            done = True
    logging.info('Redis ready')
    return True

def stop_redis(port, password=None):
    '''
    Client uses this function to stop an instance of redis
    This will only work for redis instances that were started by this module
    Arguments:
        cport : The Client Port for the instance of redis to be stopped
    '''
    r = redis.StrictRedis(host='localhost', port=port, db=0, password=password)
    r.shutdown()
    del r
    redisbase = "/tmp/redis.%s.%d/" % (os.getenv('USER', 'None'), port)
    output, _ = call_command_("rm -rf " + redisbase)

def replace_string_(filePath, findreplace):
    "replaces all findStr by repStr in file filePath"
    print(filePath)
    tempName = filePath + '~~~'
    input = open(filePath)
    output = open(tempName, 'w')
    s = input.read()
    for couple in findreplace:
        outtext = s.replace(couple[0], couple[1])
        s = outtext
    output.write(outtext)
    output.close()
    input.close()
    os.rename(tempName, filePath)


def call_command_(command):
    process = subprocess.Popen(command.split(' '),
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    return process.communicate()


if __name__ == "__main__":
    cs = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    cs.bind(("", 0))
    cport = cs.getsockname()[1]
    cs.close()
    start_redis(cport)
