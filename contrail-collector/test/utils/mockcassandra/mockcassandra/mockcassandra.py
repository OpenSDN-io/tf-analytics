#!/usr/bin/env python

#
# Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
#

#
# mockcassandra
#
# This module helps start and stop cassandra instances for unit testing
# java must be pre-installed for this to work
#
    
import os
import os.path
import subprocess
import logging
import socket
import time

logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(message)s')

cassandra_bdir = '/tmp/cache-' + os.environ['USER'] + '-systemless_test'


def start_cassandra(cport, sport_arg=None, cassandra_user=None, cassandra_password = None):
    '''
    Client uses this function to start an instance of Cassandra
    Arguments:
        cport : An unused TCP port for Cassandra to use as the client port
    '''
    if not os.path.exists(cassandra_bdir):
        call_command_("mkdir " + cassandra_bdir)

    cassandra_version = '3.10'
    cassandra_url = cassandra_bdir + '/apache-cassandra-'+cassandra_version+'-bin.tar.gz'

    if not os.path.exists(cassandra_bdir):
        call_command_("mkdir " + cassandra_bdir)

    cassandra_download = 'wget -P ' + cassandra_bdir + ' https://github.com/OpenSDN-io/tf-third-party-cache/raw/master/cassandra/'+\
        'apache-cassandra-'+cassandra_version+'-bin.tar.gz'

    if not os.path.exists(cassandra_url):
        process = subprocess.Popen(cassandra_download.split(' '))
        process.wait()
        if process.returncode is not 0:
            return

    basefile = 'apache-cassandra-'+cassandra_version
    tarfile = cassandra_url
    cassbase = "/tmp/cassandra.%s.%d/" % (os.getenv('USER', 'None'), cport)
    confdir = cassbase + basefile + "/conf/"
    call_command_("rm -rf " + cassbase)
    call_command_("mkdir " + cassbase)

    logging.info('Installing cassandra in ' + cassbase)
    os.system("cat " + tarfile + " | tar -xpzf - -C " + cassbase)

    if not sport_arg:
        ss = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ss.bind(("",0))
        sport = ss.getsockname()[1]
    else:
        sport = sport_arg

    js = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    js.bind(("",0))
    jport = js.getsockname()[1]

    o_clients = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    o_clients.bind(("",0))
    o_client_port = o_clients.getsockname()[1]

    cqlport = cport
    thriftport = o_client_port

    logging.info('Cassandra Client Port %d: CQL Port %d, Thrift Port %d' %
        (cport, cqlport, thriftport))

    replace_string_(confdir + "cassandra.yaml", \
        [("rpc_port: 9160","rpc_port: " + str(thriftport)), \
        ("storage_port: 7000","storage_port: " + str(sport)),
        ("native_transport_port: 9042","native_transport_port: " + str(cqlport))])

    if cassandra_user is not None and cassandra_password is not None:
        logging.info('Cassandra setting password')
        replace_string_(confdir + "cassandra.yaml", \
            [("authenticator: AllowAllAuthenticator",  \
              "authenticator: PasswordAuthenticator")])
        replace_string_(confdir + "logback.xml",\
            [('level="INFO"','level="DEBUG"')])

    replace_string_(confdir + "cassandra-env.sh", \
        [('JMX_PORT="7199"', 'JMX_PORT="' + str(jport) + '"')])

    replace_string_(confdir + "cassandra-env.sh", \
        [('#MAX_HEAP_SIZE="4G"', 'MAX_HEAP_SIZE="256M"'), \
        ('#HEAP_NEWSIZE="800M"', 'HEAP_NEWSIZE="100M"'), \
        ('-Xss180k','-Xss256k')])

    if not sport_arg:
        ss.close()

    js.close()
    o_clients.close()

    call_command_(cassbase + basefile + "/bin/cassandra -R -p " + cassbase + "pid")
    assert(verify_cassandra(thriftport, cqlport, cassandra_user, cassandra_password))

    return cassbase, basefile


def stop_cassandra(cport):
    '''
    Client uses this function to stop an instance of Cassandra
    This will only work for cassandra instances that were started by this module
    Arguments:
        cport : The Client Port for the instance of cassandra to be stopped
    '''
    cassbase = "/tmp/cassandra.%s.%d/" % (os.getenv('USER', 'None'), cport)
    input = open(cassbase + "pid")
    s=input.read()
    logging.info('Killing Cassandra pid %d' % int(s))
    call_command_("kill -9 %d" % int(s))
    call_command_("rm -rf " + cassbase)
    
def replace_string_(filePath, findreplace):
    "replaces all findStr by repStr in file filePath"
    print(filePath)
    tempName=filePath+'~~~'
    input = open(filePath)
    output = open(tempName,'w')
    s=input.read()
    for couple in findreplace:
        outtext=s.replace(couple[0],couple[1])
        s=outtext
    output.write(outtext)
    output.close()
    input.close()
    os.rename(tempName,filePath)


def verify_cassandra(thriftport, cqlport, cassandra_user, cassandra_password):
    retry_threshold = 10
    retry = 1
    cassbase = "/tmp/cassandra.%s.%d/" % (os.getenv('USER', 'None'), cqlport)
    cql_command = cassbase + "apache-cassandra-3.10/bin/cqlsh " + "127.0.0.1 " + str(cqlport) + " -e \"show version\""
    while retry < retry_threshold:
        process = subprocess.Popen(cql_command.split(' '))
        process.wait()
        if process.returncode == 0:
            return True
        retry = retry + 1
        time.sleep(5)
    return False


def call_command_(command):
    process = subprocess.Popen(command.split(' '),
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    if stderr != "":
        logging.error('command fails: ' + command)
        logging.error(stdout)
        logging.error(stderr)


if __name__ == "__main__":
    cs = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    cs.bind(("",0))
    cport = cs.getsockname()[1]
    cs.close()
    start_cassandra(cport)


