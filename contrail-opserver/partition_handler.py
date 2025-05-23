#!/usr/bin/python3

from gevent import monkey
monkey.patch_all()
import logging
import gevent
from kafka import KafkaConsumer, structs, errors
import os
import ast
import json
import copy
import traceback
import cfgm_common
import uuid
import struct
import socket
from pysandesh.util import UTCTimestampUsec
import select
import redis
import errno
import time
from collections import namedtuple
from .strict_redis_wrapper import StrictRedisWrapper
from .opserver_util import convert_to_string

PartInfo = namedtuple("PartInfo",["ip_address","instance_id","redis_ip","redis_agg_db","acq_time","port"])

def sse_pack(d):
    """Pack data in SSE format"""
    buffer = ''
    for k in ['event','data']:
        if k in list(d.keys()):
            buffer += '%s: %s\n' % (k, d[k])
    return buffer + '\n'

class UveCacheProcessor(object):
    def __init__(self, logger, rpass, redis_ssl_params):
        self._logger = logger
        self._rpass = rpass
        self._redis_ssl_params = redis_ssl_params;
        self._partkeys = {}
        self._typekeys = {}
        self._uvedb = {} 
        self._agp = {}
        self._agg_redis_map = {}

    def _get_agg_redis_instance(self, ip, port, redis_agg_db):
        agg_redis = self._agg_redis_map.get((ip, port, redis_agg_db))
        if not agg_redis:
            agg_redis = StrictRedisWrapper(host=ip, port=port,
                password=self._rpass, db=redis_agg_db, socket_timeout=30, **self._redis_ssl_params)
            self._agg_redis_map[(ip, port, redis_agg_db)] = agg_redis
        return agg_redis
    # end _get_agg_redis_instance

    def update_agp(self, agp):
        self._agp = agp

    def get_cache_list(self, tables, filters, patterns, keysonly):
        if not tables:
            tables = list(self._uvedb.keys())
        filters = filters or {}
        tfilter = filters.get('cfilt')
        ackfilter = filters.get('ackfilt')
        uve_list = {}
        try:
            tqual = {}
            tfilter = tfilter or {}

            # Build a per-table set of all keys matching tfilter
            for typ in tfilter:
                for table in tables:
                    if not table in tqual:
                        tqual[table] = set()
                    if typ in self._typekeys:
                        if table in self._typekeys[typ]:
                            tqual[table].update(self._typekeys[typ][table])

            for table in tables:
                if not table in self._uvedb:
                    continue  
                barekeys = set()
                for bk in list(self._uvedb[table].keys()):
                    if len(tfilter) != 0:
                        if not bk in tqual[table]:
                            continue
                    if patterns:
                        kfilter_match = False
                        for pattern in patterns:
                            if pattern.match(bk):
                                kfilter_match = True
                                break
                        if not kfilter_match:
                            continue
                    barekeys.add(bk)
                    
                brsp = self._get_uve_content(table, barekeys,\
                        tfilter, ackfilter, keysonly)
                if len(brsp) != 0:
                    if keysonly:
                        uve_list[table] = set(brsp.keys())
                    else:
                        uve_list[table] = brsp
        except Exception as ex:
            template = "Exception {0} in uve list proc. Arguments:\n{1!r}"
            messag = template.format(type(ex).__name__, ex.args)
            self._logger.error("%s : traceback %s" % \
                              (messag, traceback.format_exc()))
        return uve_list

    def _get_uve_content(self, table, barekeys, tfilter, ackfilter, keysonly):
        brsp = {}
        uveparts = {}
        for barekey in barekeys:
            part = self._uvedb[table][barekey]["__SOURCE__"]["partition"]
            if not part in uveparts:
                uveparts[part] = set()
            uveparts[part].add(barekey)
        
        for pkey,pvalue in uveparts.items():
            pi = self._agp[pkey]
            lredis = self._get_agg_redis_instance(pi.ip_address, pi.port, pi.redis_agg_db)
            ppe = lredis.pipeline()
            luves = list(uveparts[pkey])
            for elem in luves:
                if len(tfilter) != 0:
                    ltypes = list(tfilter.keys())
                    ppe.hmget("AGPARTVALUES:%s:%d:%s:%s" % \
                        (pi.instance_id, pkey, table, elem),
                        *ltypes)
                else:
                    ppe.hgetall("AGPARTVALUES:%s:%d:%s:%s" % \
                        (pi.instance_id, pkey, table, elem))
            pperes = ppe.execute()
            for uidx in range(0,len(luves)):
                uvestruct = {}
                if len(tfilter) != 0:
                    for tidx in range(0,len(ltypes)):
                        if not pperes[uidx][tidx]:
                            continue
                        afilter_list = tfilter[ltypes[tidx]]
                        ppeval = json.loads(pperes[uidx][tidx])
                        if len(afilter_list) == 0:
                            uvestruct[ltypes[tidx]] = ppeval
                        else:      
                            for akey, aval in ppeval.items():
                                if akey not in afilter_list:
                                    continue
                                else:
                                    if not ltypes[tidx] in uvestruct:
                                        uvestruct[ltypes[tidx]] = {}
                                    uvestruct[ltypes[tidx]][akey] = aval
                else:
                    for tk,tv in pperes[uidx].items():
                        uvestruct[tk] = json.loads(tv)

                if ackfilter is not None:
                    if "UVEAlarms" in uvestruct and \
                            "alarms" in uvestruct["UVEAlarms"]:
                        alarms = []
                        for alarm in uvestruct["UVEAlarms"]["alarms"]:
                            ack = "false"
                            if "ack" in alarm:
                                if alarm["ack"]:
                                    ack = "true"
                                else:
                                    ack = "false"
                            if ack == ackfilter:
                                alarms.append(alarm)
                        if not len(alarms):
                            del uvestruct["UVEAlarms"]
                        else:
                            uvestruct["UVEAlarms"]["alarms"] = alarms

                if len(uvestruct) != 0: 
                    if keysonly:
                        brsp[luves[uidx]] = None
                    else:
                        brsp[luves[uidx]] = uvestruct
        return brsp
      
    def get_cache_uve(self, key, filters):
        rsp = {}
        try:
            filters = filters or {}
            tfilter = filters.get('cfilt')
            tfilter = tfilter or {}
            ackfilter = filters.get('ackfilt')

            barekey = key.split(":",1)[1]
            table = key.split(":",1)[0]
             
            if table not in self._uvedb:
                return rsp
            if barekey not in self._uvedb[table]:
                return rsp
            
            brsp = self._get_uve_content(table, set([barekey]),\
                    tfilter, ackfilter, False)
            if barekey in brsp:
                rsp = brsp[barekey]

        except Exception as ex:
            template = "Exception {0} in uve cache proc. Arguments:\n{1!r}"
            messag = template.format(type(ex).__name__, ex.args)
            self._logger.error("%s : traceback %s" % \
                              (messag, traceback.format_exc()))
        return rsp

    def store_uve(self, partno, pi, key, typ, value):
        barekey = key.split(":",1)[1]
        table = key.split(":",1)[0]

        if partno not in self._partkeys:
            self._partkeys[partno] = set()

        self._partkeys[partno].add(key)

        if table not in self._uvedb:
            self._uvedb[table] = {}
        if barekey not in self._uvedb[table]:
            self._uvedb[table][barekey] = {}

        if typ is None:
            # delete the entire UVE
            self._partkeys[partno].remove("%s:%s" % \
                (table, barekey))
            for typ1 in list(self._typekeys.keys()):
                if table in self._typekeys[typ1]:
                    if barekey in self._typekeys[typ1][table]:
                        self._typekeys[typ1][table].remove(barekey)
                    if len(self._typekeys[typ1][table]) == 0:
                        del self._typekeys[typ1][table]
            del self._uvedb[table][barekey]
        else:
            if not typ in self._typekeys:
                self._typekeys[typ] = {}
            if not typ in self._uvedb[table][barekey]:
                self._uvedb[table][barekey][typ] = None
            if value is None:
                # remove one type of this UVE
                del self._uvedb[table][barekey][typ]
                if table in self._typekeys[typ]:
                    if barekey in self._typekeys[typ][table]:
                        self._typekeys[typ][table].remove(barekey)
                    if len(self._typekeys[typ][table]) == 0:
                        del self._typekeys[typ][table]
            else:
                self._uvedb[table][barekey][typ] = value
                if not table in self._typekeys[typ]:
                    self._typekeys[typ][table] = set()
                self._typekeys[typ][table].add(barekey)
            self._uvedb[table][barekey]["__SOURCE__"] = \
                    {'instance_id':pi.instance_id, 'ip_address':pi.ip_address, \
                     'partition':partno}

    def clear_partition(self, partno, clear_cb):

        if partno not in self._partkeys:
            self._partkeys[partno] = set()
            return

        for key in self._partkeys[partno]:
            barekey = key.split(":",1)[1]
            table = key.split(":",1)[0]

            del self._uvedb[table][barekey]
            
            # Look in the "types" index and remove this UVE
            for tkey in list(self._typekeys.keys()):
                if table in self._typekeys[tkey]:
                    if barekey in self._typekeys[tkey][table]:
                        self._typekeys[tkey][table].remove(barekey)
                        if len(self._typekeys[tkey][table]) == 0:
                            del self._typekeys[tkey][table]
                        if len(self._typekeys[tkey]) == 0:
                            del self._typekeys[tkey]
            clear_cb(key) 
        self._partkeys[partno] = set()

    def get_uvedb_cache_tables(self):
        return list(self._uvedb.keys())
    # end get_uvedb_cache_tables

    def get_uvedb_cache_table_keys(self, table):
        try:
            return list(self._uvedb[table].keys())
        except KeyError:
            return []
    # end get_uvedb_cache_table_keys

    def get_uvedb_cache_uve(self, table, uve_key):
        try:
            return self._uvedb[table][uve_key]
        except KeyError:
            return None
    # end get_uvedb_cache_uve


# end class UveCacheProcessor


class UveStreamPart(gevent.Greenlet):
    def __init__(self, partno, logger, cb, pi, rpass, redis_ssl_params, content = True, 
                tablefilt = None, cfilter = None, patterns = None, token = None):
        gevent.Greenlet.__init__(self)
        self._logger = logger
        self._cb = cb
        self._pi = pi
        self._partno = partno
        # We need to keep track of UVE contents only for streaming case
        self._content = content
        self._rpass = rpass
        self._redis_ssl_params = redis_ssl_params
        self._tablefilt = None
        if tablefilt:
            self._tablefilt = set(tablefilt)
        self._cfilter = None
        if cfilter:
            self._cfilter = set(cfilter.keys())
        self._patterns = patterns
        self._token = token
        self._token_info = None
        if token and 'token_info' in token:
            self._token_info = token['token_info']
        self._uvecache = {}

    def is_uve_read_permitted(self, uves):
        """
        Check for permissions in ContrailConfig structure for given user
        """
        if not self._token or self._token['is_global_read_only_role']:
            return True
        if "ContrailConfig" in list(uves.keys()):
            cc = json.loads(uves["ContrailConfig"])
            perms2 = ast.literal_eval(cc['elements']['perms2'])
            owner = perms2['owner'].replace('-','')
            perms = perms2['owner_access'] << 6
            perms |= perms2['global_access']
            mask = 0o7
            mode = 4
            share = perms2['share']
            if 'token' in self._token_info:
                token = self._token_info['token']
                if 'project' in  list(token.keys()):
                    tenant = token['project']['id']
                    tenant = tenant.replace('-','')
                    tenant_name = token['project']['name']
                    domain = token['project']['domain']['id']
                    if tenant == owner:
                        mask |= 0o700
                    # grant access if shared with tenant or domain
                    for item in share:
                        (share_type, share_uuid) = cfgm_common.utils.\
                                shareinfo_from_perms2_tenant(item['tenant'])
                        share_uuid = share_uuid.replace('-','')
                        if ((share_type == 'tenant' and tenant == share_uuid)\
                                 or (share_type == 'domain' and domain == \
                                     share_uuid)):
                            perms |= item['tenant_access'] << 3
                            mask |= 0o070
                            break
                    mode_mask = mode | mode << 3 | mode << 6
                    ok = (mask & perms & mode_mask)
                    if not ok:
                        self._logger.error("no permissins for %s" %tenant_name)
                        return False
                    else:
                        return True
                else:
                    self._logger.error("no project in token %s" %token)
            else:
                self._logger.error("no token specified %s" %self._token_info)
        else:
            self._logger.error("no ContrailConfig structure %s" %list(uves.keys()))
        return False
    # end is_uve_read_permitted

    def syncpart(self, redish):
        inst = self._pi.instance_id
        part = self._partno
        keys = list(redish.smembers("AGPARTKEYS:%s:%d" % (inst, part)))
        ppe = redish.pipeline()
        lkeys = []
        for key in keys:
            table, barekey = key.split(":",1)
            if self._tablefilt:
                if not table in self._tablefilt:
                    continue
            if self._patterns:
                kfilter_match = False
                for pattern in self._patterns:
                    if pattern.match(barekey):
                        kfilter_match = True
                        break
                if not kfilter_match:
                    continue
            lkeys.append(key)
            # We need to load full UVE contents for streaming case
            # For DBCache case, we only need the struct types
            if self._content:
                ppe.hgetall("AGPARTVALUES:%s:%d:%s" % (inst, part, key))
                if not key in self._uvecache:
                    self._uvecache[key] = {}
            else:
                ppe.hkeys("AGPARTVALUES:%s:%d:%s" % (inst, part, key))
        pperes = ppe.execute()
        idx=0
        for res in pperes:
            if self._content:
                if self._token is not None:
                    if not self.is_uve_read_permitted(res):
                        idx += 1
                        continue
                for tk,tv in res.items():
                    self._uvecache[lkeys[idx]][tk] = tv
                    if self._cfilter:
                        if not tk in self._cfilter:
                            continue

                    self._cb(self._partno, self._pi, lkeys[idx], tk, json.loads(tv))
            else:
                for telem in res:
                    if self._cfilter:
                        if not telem in self._cfilter:
                            continue
                    self._cb(self._partno, self._pi, lkeys[idx], telem, {})

            idx += 1
        
    def _run(self):
        lredis = None
        pb = None
        pause = False
        self.redis_prev_time = 0
        while True:
            try:
                if pause:
                    gevent.sleep(2)
                    pause = False
                lredis = StrictRedisWrapper(
                        host=self._pi.redis_ip,
                        port=self._pi.port,
                        password=self._rpass,
                        db=self._pi.redis_agg_db, socket_timeout=30,
                        **self._redis_ssl_params)
                pb = lredis.pubsub()
                inst = self._pi.instance_id
                part = self._partno
                pb.subscribe('AGPARTPUB:%s:%d' % (inst, part))
                self.syncpart(lredis)
                while True:
                    message = pb.get_message()
                    if not message:
                        gevent.sleep(0.001)
                        continue
                    if message["type"] != "message":
                        gevent.sleep(0)
                        continue
                    dataline = message["data"]
                    try:
                        elems = json.loads(dataline)
                    except:
                        self._logger.error("AggUVE Parsing failed: %s" % str(message))
                        gevent.sleep(0)
                        continue
                    else:
                         self._logger.info("AggUVE loading: %s" % str(elems))
                    if self._content:
                        ppe = lredis.pipeline()
                    lelems = []
                    for elem in elems:
                        table, barekey = elem["key"].split(":",1)
                        if self._tablefilt:
                            if not table in self._tablefilt:
                                gevent.sleep(0)
                                continue
                        if self._patterns:
                            kfilter_match = False
                            for pattern in self._patterns:
                                if pattern.match(barekey):
                                    kfilter_match = True
                                    break
                            if not kfilter_match:
                                gevent.sleep(0)
                                continue
                        if self._cfilter:
                            if elem["type"] not in self._cfilter:
                                gevent.sleep(0)
                                continue
                        lelems.append(elem)
                        if self._content:
                            # This UVE was deleted
                            if elem["type"] is None:
                                ppe.exists("AGPARTVALUES:%s:%d:%s" % \
                                    (inst, part, elem["key"]))
                            else:
                                ppe.hget("AGPARTVALUES:%s:%d:%s" % \
                                    (inst, part, elem["key"]), elem["type"])

                    # We need to execute this pipeline read only if we are
                    # keeping track of UVE contents (streaming case)
                    if self._content:
                        pperes = ppe.execute()
                    idx = 0
                    for elem in lelems:

                        key = elem["key"]
                        typ = elem["type"]
                        vdata = None

                        if not typ is None:
                            if self._content:
                                if not key in self._uvecache:
                                    self._uvecache[key] = {}

                                vjson = pperes[idx]
                                if vjson is None:
                                    vdata = None
                                    if typ in self._uvecache[key]:
                                        del self._uvecache[key][typ]
                                else:
                                    vdata = json.loads(vjson)
                                    self._uvecache[key][typ] = vjson
                                if self._token is not None:
                                    if not self.is_uve_read_permitted(\
                                            self._uvecache[key]):
                                        gevent.sleep(0)
                                        continue
                            else:
                                vdata = {}
                        self._cb(self._partno, self._pi, key, typ, vdata)
                        idx += 1
                    gevent.sleep(0)
            except gevent.GreenletExit:
                break
            except (redis.exceptions.ConnectionError,
                    redis.exceptions.TimeoutError):
                pass
            except Exception as ex:
                self.redis_cur_time = time.time()
                if self.redis_prev_time == 0 or self.redis_cur_time - self.redis_prev_time > 60:
                    self.redis_prev_time = self.redis_cur_time
                    template = "Exception {0} in uve stream proc. Arguments:\n{1!r}"
                    messag = template.format(type(ex).__name__, ex.args)
                    self._logger.error("[%s:%d] AlarmGen %s,%d %s : traceback %s" % \
                                      (self._pi.ip_address, self._pi.port, \
                                       self._pi.instance_id, self._partno, \
                                       messag, traceback.format_exc()))
                else:
                    template = "Exception {0} in uve stream proc. Arguments:\n{1!r}"
                    messag = template.format(type(ex).__name__, ex.args)
                    self._logger.error("[%s:%d] AlarmGen %s,%d %s" % \
                                      (self._pi.ip_address, self._pi.port, \
                                       self._pi.instance_id, self._partno, \
                                       messag))
            finally:
                lredis = None
                if pb is not None:
                    pb.close()
                    pb = None
                    pause = True
        return None

class UveStreamer(gevent.Greenlet):
    def __init__(self, logger, q, rfile, agp_cb, rpass, redis_ssl_params, \
            tablefilt = None, cfilter = None, patterns = None,
            USP_class = UveStreamPart, token=None):
        gevent.Greenlet.__init__(self)
        self._logger = logger
        self._q = q
        self._rfile = rfile
        self._agp_cb = agp_cb
        self._agp = {}
        self._parts = {}
        self._rpass = rpass
        self._redis_ssl_params = redis_ssl_params
        self._ccb = None
        self._uvedbcache = UveCacheProcessor(self._logger, rpass, redis_ssl_params)
        self._USP_class = USP_class
        self._tablefilt = tablefilt
        self._cfilter = cfilter
        self._patterns = patterns
        self._token = token

    def get_uve(self, key, filters=None):
        return False, self._uvedbcache.get_cache_uve(key, filters)

    def get_uve_list(self, utab, filters, patterns, keysonly = True):
        return self._uvedbcache.get_cache_list(utab, filters, patterns, keysonly)

    def get_uvedb_cache_tables(self):
        return self._uvedbcache.get_uvedb_cache_tables()
    # end get_uvedb_cache_tables

    def get_uvedb_cache_table_keys(self, table):
        return self._uvedbcache.get_uvedb_cache_table_keys(table)
    # end get_uvedb_cache_table_keys

    def get_uvedb_cache_uve(self, table, uve_key):
        return self._uvedbcache.get_uvedb_cache_uve(table, uve_key)
    # end get_uvedb_cache_uve

    def clear_callback(self, key):
        if self._q:
            dt = {'key':key, 'type':None}
            msg = {'event': 'update', 'data':json.dumps(dt)}
            self._q.put(sse_pack(msg))

    def partition_callback(self, partition, pi, key, type, value):
        # gevent is non-premptive; we don't need locks
        if self._q:
            dt = {'key':key, 'type':type}
            if not type is None:
                dt['value'] = value
            msg = {'event': 'update', 'data':json.dumps(dt)}
            self._q.put(sse_pack(msg))
            # If this stream is being used for SSE, we have the UVE value,
            # but do not need to report it to the cache
            if not value is None:
                value = {}

        self._uvedbcache.store_uve(partition, pi, key, type, value)
        
    def set_cleanup_callback(self, cb):
        self._ccb = cb

    def _run(self):
        inputs = [ self._rfile ]
        outputs = [ ]
        if self._q:
            msg = {'event': 'init', 'data':json.dumps(None)}
            self._q.put(sse_pack(msg))
        self._logger.info("Starting UveStreamer")
        while True:
            try:
                if self._rfile is not None:
                    readable, writable, exceptional = \
                        select.select(inputs, outputs, inputs, 1)
                    if (readable or writable or exceptional):
                        break
                else:
                    gevent.sleep(1)
                newagp = self._agp_cb()
                set_new, set_old = set(newagp.keys()), set(self._agp.keys())
                intersect = set_new.intersection(set_old)
                # deleted parts
                for elem in set_old - intersect:
                    self.partition_stop(elem)
                    self._uvedbcache.clear_partition(elem, self.clear_callback)
                # new parts
                for elem in set_new - intersect:
                    self._uvedbcache.clear_partition(elem, self.clear_callback)
                    self.partition_start(elem, newagp[elem])
                # changed parts
                for elem in intersect:
                    if self._agp[elem] != newagp[elem]:
                        self.partition_stop(elem)
                        self._uvedbcache.clear_partition(elem, self.clear_callback)
                        self.partition_start(elem, newagp[elem])
                self._agp = copy.deepcopy(newagp)
                self._uvedbcache.update_agp(self._agp)
            except gevent.GreenletExit:
                break
        self._logger.info("Stopping UveStreamer")
        for part, pi in self._agp.items():
            self.partition_stop(part)
            self._uvedbcache.clear_partition(elem, self.clear_callback)
        if self._q:
            msg = {'event': 'stop', 'data':json.dumps(None)}
            self._q.put(sse_pack(msg))
        if callable(self._ccb):
            self._ccb(self) #remove myself

    def partition_start(self, partno, pi):
        self._logger.info("Starting agguve part %d using %s" % (partno, pi))
        # If we are doing streaming, full UVE contents are needed
        # Otherwise, we only need key/type information for DBCache case
        if self._q:
            content = True
        else:
            content = False
        self._parts[partno] = self._USP_class(partno, self._logger,
            self.partition_callback, pi, self._rpass, self._redis_ssl_params, content,
            self._tablefilt, self._cfilter, self._patterns, self._token)
        self._parts[partno].start()

    def partition_stop(self, partno):
        self._logger.info("Stopping agguve part %d" % partno)
        self._parts[partno].kill()
        del self._parts[partno]

class PartitionHandler(gevent.Greenlet):
    def __init__(self, brokers, group, topic, logger, limit, kafka_use_ssl,
                 kafka_ssl_params):
        gevent.Greenlet.__init__(self)
        self._brokers = brokers
        self._group = group
        self._topic = topic
        self._logger = logger
        self._limit = limit
        self._uvedb = {}
        self._partoffset = 0
        self._kfk = None
        self._failed = False
        self._kafka_use_ssl = kafka_use_ssl
        self._kafka_ssl_params = kafka_ssl_params

    def failed(self):
        return self._failed

    def resource_check(self):
        self._logger.info("%s Resource check" % self._topic)

    def msg_handler(self, mlist):
        self._logger.info("%s Reading %s" % (self._topic, str(mlist)))
        return True

    def _run(self):
        pcount = 0
        pause = False
        self.part_prev_time = 0
        while True:
            try:
                if pause:
                    gevent.sleep(5)
                    pause = False
                self._logger.info("Newer KafkaClient %s" % self._topic)
                self._failed = False
                try:
                    if not self._kafka_use_ssl:
                        consumer = KafkaConsumer(
                             bootstrap_servers=self._brokers.split(','),
                             group_id=None)
                    else:
                        consumer = KafkaConsumer(
                             bootstrap_servers=self._brokers.split(','),
                             group_id=None,
                             security_protocol='SSL',
                             ssl_check_hostname=False,
                             **self._kafka_ssl_params)
                    consumer.assign([structs.TopicPartition(self._topic,0)])
                except Exception as ex:
                    self.part_cur_time = time.time()
                    if self.part_prev_time == 0 or self.part_cur_time - self.part_prev_time > 60:
                        self.part_prev_time = self.part_cur_time
                        template = "Consumer Failure {0} occured. Arguments:\n{1!r}"
                        messag = template.format(type(ex).__name__, ex.args)
                        self._logger.error("Error: %s trace %s" % \
                            (messag, traceback.format_exc()))
                    else:
                        template = "Consumer Failure {0} occured. Arguments:\n{1!r}"
                        messag = template.format(type(ex).__name__, ex.args)
                        self._logger.error("Error: %s" % (messag))
                    self._failed = True
                    raise RuntimeError(messag)

                self._logger.info("Starting %s at position %d" % \
                        (self._topic, consumer.position(structs.TopicPartition(self._topic,0))))

                if self._limit:
                    raise gevent.GreenletExit

                while True:
                    try:
                        #Alarmgen should not poll all records present in Kafka at once
                        #That can cause the CPU %age to increase and hence, miss Zookeeper timeout
                        #max_record value is added,so that only those many records are fetched at once
                        mdict = consumer.poll(max_records=50)
                        gevent.sleep(0.1)
                        self.resource_check()
                        if len(mdict):
                            counts = {}
                            for tp,tv in mdict.items():
                                if tp not in counts:
                                    counts[tp] = 0
                                counts[tp] += len(tv)
                                if not self.msg_handler(tv):
                                    raise gevent.GreenletExit
                                pcount += len(tv)
                            self._logger.debug("poll for topic %s : %s" % (self._topic, str(counts)))
                        else:
                            gevent.sleep(0.5)

                    except TypeError as ex:
                        self._logger.error("Type Error: %s trace %s" % \
                                (str(ex.args), traceback.format_exc()))
                        gevent.sleep(0.1)
                    except errors.FailedPayloadsError as ex:
                        self._logger.error("Payload Error: %s" %  str(ex.args))
                        gevent.sleep(0.1)
            except gevent.GreenletExit:
                break
            except AssertionError as ex:
                self._partoffset = ex
                break
            except Exception as ex:
                self.part_cur_time = time.time()
                if self.part_prev_time == 0 or self.part_cur_time - self.part_prev_time > 60:
                    self.part_prev_time = self.part_cur_time
                    template = "An exception of type {0} occured. Arguments:\n{1!r}"
                    messag = template.format(type(ex).__name__, ex.args)
                    self._logger.error("%s %s : traceback %s" % \
                                      (self._topic, messag, traceback.format_exc()))
                else:
                    template = "An exception of type {0} occured. Arguments:\n{1!r}"
                    messag = template.format(type(ex).__name__, ex.args)
                    self._logger.error("%s %s" % (self._topic, messag))
                self.stop_partition()
                self._failed = True
                pause = True
                if hasattr(ex,'errno'):
                    # This is an unrecoverable error
                    if ex.errno == errno.EMFILE:
                       raise SystemExit(1)

        self._logger.info("Stopping %s pcount %d" % (self._topic, pcount))
        partdb = self.stop_partition()
        return self._partoffset, partdb

class UveStreamProc(PartitionHandler):
    # Arguments:
    #
    #  brokers   : broker list for kafka bootstrap
    #  partition : partition number
    #  uve_topic : Topic to consume
    #  logger    : logging object to use
    #  callback  : Callback function for reporting the set of the UVEs
    #              that may have changed for a given notification
    #  rsc       : Callback function to check on collector status
    #              and get sync contents for new collectors
    #  aginst    : instance_id of alarmgen
    #  rport     : redis server port
    def __init__(self, brokers, partition, uve_topic, logger, callback,
            host_ip, rsc, aginst, rport, kafka_use_ssl, kafka_ssl_params,
            group="-workers"):
        super(UveStreamProc, self).__init__(brokers, group,
            uve_topic, logger, False, kafka_use_ssl, kafka_ssl_params)
        self._uvedb = {}
        self._uvein = {}
        self._callback = callback
        self._partno = partition
        self._host_ip = host_ip
        self._ip_code, = struct.unpack('>I', socket.inet_pton(
                                        socket.AF_INET, host_ip))
        self.disc_rset = set()
        self._resource_cb = rsc
        self._aginst = aginst
        self._acq_time = UTCTimestampUsec()
        self._up = True
        self._rport = rport

    def reset_acq_time(self):
        self._acq_time = UTCTimestampUsec()

    def acq_time(self):
        return self._acq_time

    def resource_check(self):
        '''
        This function compares the known collectors with the
        list from discovery, and syncs UVE keys accordingly
        '''
        newset , coll_delete, chg_res = self._resource_cb(self._partno, self.disc_rset)
        for coll in coll_delete:
            self._logger.info("Part %d lost collector %s" % (self._partno, coll))
            self.stop_partition(coll)
        if len(chg_res):
            self.start_partition(chg_res)
        self.disc_rset = newset

    def stop_partition(self, kcoll=None):
        clist = []
        if not kcoll:
            clist = list(self._uvedb.keys())
        else:
            clist = [kcoll]
        self._logger.info("Stopping part %d collectors %s" % \
                (self._partno,clist))

        partdb = {}
        chg = {}
        for coll in clist:
            partdb[coll] = {}
            for gen in list(self._uvedb[coll].keys()):
                partdb[coll][gen] = {}
                for tab in list(self._uvedb[coll][gen].keys()):
                    for rkey in list(self._uvedb[coll][gen][tab].keys()):
                        uk = tab + ":" + rkey
                        chg[uk] = None
                        partdb[coll][gen][uk] = \
                            set(self._uvedb[coll][gen][tab][rkey].keys())

            del self._uvedb[coll]
        self._logger.info("Stopping part %d UVEs %s" % \
                (self._partno,str(list(chg.keys()))))
        if kcoll:
            self._callback(self._partno, chg)
        else:
            # If all collectors are being cleared, clear resoures too
            self.disc_rset = set()
            self._up = False

        return partdb

    def start_partition(self, cbdb):
        ''' This function loads the initial UVE database.
            for the partition
        '''
        self._up = True
        self._logger.info("Starting part %d collectors %s" % \
                (self._partno, str(list(cbdb.keys()))))
        uves  = {}
        for kcoll,coll in cbdb.items():
            self._uvedb[kcoll] = {}
            for kgen,gen in coll.items():
                self._uvedb[kcoll][kgen] = {}
                for kk in list(gen.keys()):
                    tabl = kk.split(":",1)
                    tab = tabl[0]
                    rkey = tabl[1]
                    if not tab in self._uvedb[kcoll][kgen]:
                        self._uvedb[kcoll][kgen][tab] = {}
                    self._uvedb[kcoll][kgen][tab][rkey] = {}

                    if not kk in uves:
                        uves[kk] = None
                    for typ, contents in gen[kk].items():
                        self._uvedb[kcoll][kgen][tab][rkey][typ] = {}
                        self._uvedb[kcoll][kgen][tab][rkey][typ]["c"] = 0
                        self._uvedb[kcoll][kgen][tab][rkey][typ]["u"] = \
                                uuid.uuid1(self._ip_code)
                        # TODO: for loading only specific types:
                        #       uves[kk][typ] = contents
                    
        self._logger.info("Starting part %d UVEs %d" % \
                           (self._partno, len(uves)))
        self._callback(self._partno, uves)

    def contents(self):
        return self._uvedb

    def stats(self):
        ''' Return the UVE incoming stats collected over 
            the last time period for this partition 
            Also, the stats should be cleared to prepare
            for the next period of collection.
        '''
        ret_in  = copy.deepcopy(self._uvein)
        self._uvein = {}
        return ret_in

    def msg_handler(self, mlist):
        for mm in mlist:
            if not self.msg_handler_single(mm):
                self._logger.info("%s could not handle %s" % \
                    (self._topic, str(mm)))
                return False
        return True

    def msg_handler_single(self, om):
        self._partoffset = om.offset
        chg = {}
        try:
            params = convert_to_string(om.key).split("|")
            gen = params[2]
            coll = params[3]
            uv = {}
            uv["type"] = params[1]
            uv["key"] = params[0]
            if om.value is None or len(om.value) == 0:
                uv["value"] = None
            else:
                uv["value"] = json.loads(convert_to_string(om.value))

            if coll not in self._uvedb:
                # This partition is not synced yet.
                # Ignore this message
                self._logger.debug("%s Ignoring UVE %s" % (self._topic, str(om)))
                return True

            if gen not in self._uvedb[coll]:
                self._uvedb[coll][gen] = {}

            tabl = uv["key"].split(":",1)
            tab = tabl[0]
            rkey = tabl[1]
            
            if tab not in self._uvedb[coll][gen]:
                self._uvedb[coll][gen][tab] = {}

            if not rkey in self._uvedb[coll][gen][tab]:
                self._uvedb[coll][gen][tab][rkey] = {}
     
            removed = False

            # uv["type"] and uv["value"] can be decoded as follows:

            # uv["type"] refers to a struct name

            # uv["value"] can be one of the following:
            # - None      # This Type has been deleted.
            # - {}        # The Type has a value, which is 
            #               not available in this message.
            #               (this option is only for raw UVE updates)
            # - {<Value>} # The Value of the Type
            #               (this option is only for agg UVE updates)

            if uv["value"] is None:
                if uv["type"] in self._uvedb[coll][gen][tab][rkey]:
                    del self._uvedb[coll][gen][tab][rkey][uv["type"]]
                if not len(self._uvedb[coll][gen][tab][rkey]):
                    del self._uvedb[coll][gen][tab][rkey]
                removed = True

            if not removed: 
                if uv["type"] in self._uvedb[coll][gen][tab][rkey]:
                    self._uvedb[coll][gen][tab][rkey][uv["type"]]["c"] +=1
                else:
                    self._uvedb[coll][gen][tab][rkey][uv["type"]] = {}
                    self._uvedb[coll][gen][tab][rkey][uv["type"]]["c"] = 1
                    self._uvedb[coll][gen][tab][rkey][uv["type"]]["u"] = \
                        uuid.uuid1(self._ip_code)
            chg[uv["key"]] = { uv["type"] : uv["value"] }

            # Record stats on the input UVE Notifications
            if tab not in self._uvein:
                self._uvein[tab] = {}
            if coll not in self._uvein[tab]:
                self._uvein[tab][coll] = {}
            if gen not in self._uvein[tab][coll]:
                self._uvein[tab][coll][gen] = {}
            if uv["type"] not in self._uvein[tab][coll][gen]:
                self._uvein[tab][coll][gen][uv["type"]] = 1
            else:
                self._uvein[tab][coll][gen][uv["type"]] += 1

        except Exception as ex:
            template = "An exception of type {0} in uve proc . Arguments:\n{1!r}"
            messag = template.format(type(ex).__name__, ex.args)
            self._logger.info("%s" % messag)
            return False
        else:
            self._callback(self._partno, chg)
        return True

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s')

    workers = {}
    brokers = "localhost:9092,localhost:9093,localhost:9094"
    group = "workers"

    kafka = KafkaClient(brokers,str(os.getpid()))
    cons = SimpleConsumer(kafka, group, "ctrl")
    cons.provide_partition_info()
    print("Starting control")
    end_ready = False
    while end_ready == False:
        try:
            while True:
                part, mmm = cons.get_message(timeout=None)
                mm = mmm.message
                print("Consumed ctrl " + str(mm))
                if mm.value == "start":
                    if mm.key in workers:
                        print("Dup partition %s" % mm.key)
                        raise ValueError
                    else:
                        ph = UveStreamProc(brokers, int(mm.key), "uve-" + mm.key, "alarm-x" + mm.key, logging)
                        ph.start()
                        workers[int(mm.key)] = ph
                elif mm.value == "stop":
                    #import pdb; pdb.set_trace()
                    if int(mm.key) in workers:
                        ph = workers[int(mm.key)]
                        gevent.kill(ph)
                        res,db = ph.get()
                        print("Returned " + str(res))
                        print("State :")
                        for k,v in db.items():
                            print("%s -> %s" % (k,str(v)))
                        del workers[int(mm.key)]
                else:
                    end_ready = True
                    cons.commit()
                    gevent.sleep(2)
                    break
        except TypeError:
            gevent.sleep(0.1)
        except errors.FailedPayloadsError as ex:
            print("Payload Error: " + str(ex.args))
            gevent.sleep(0.1)
    lw=[]
    for key, value in workers.items():
        gevent.kill(value)
        lw.append(value)

    gevent.joinall(lw)
    print("Ending Consumers")

